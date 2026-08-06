"""WebSocket 端点 - 实时风险推送（D05 3.5 + D04 4.2）.

路由：
  - WS /ws/risk：实时风险推送
    连接时需带 JWT token（query param `token=`），解析 token 获取 tenant_id。

连接管理：
  - 用 dict 维护 {tenant_id: set[WebSocket]}，支持多租户隔离
  - broadcast_risk_update(tenant_id, message)：向指定租户的所有连接推送
  - 无连接时 broadcast 静默跳过
"""
from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from jose import JWTError

from app.core.logging import get_logger
from app.core.security import decode_token
from app.core.tenant import TenantContext, clear_tenant_context, set_tenant_context

logger = get_logger(__name__)

router = APIRouter()


# ===== 连接管理：{tenant_id: set[WebSocket]}（多租户隔离） =====
_connections: dict[str, set[WebSocket]] = {}


def _add_connection(tenant_id: str, ws: WebSocket) -> None:
    """注册 WebSocket 连接到租户池."""
    _connections.setdefault(tenant_id, set()).add(ws)


def _remove_connection(tenant_id: str, ws: WebSocket) -> None:
    """从租户池移除连接."""
    pool = _connections.get(tenant_id)
    if pool is None:
        return
    pool.discard(ws)
    if not pool:
        _connections.pop(tenant_id, None)


async def broadcast_risk_update(tenant_id: str, message: dict) -> None:
    """向指定租户的所有 WebSocket 连接推送风险更新.

    无连接时静默跳过；单连接发送失败时移除该连接。

    Args:
        tenant_id: 租户 ID（字符串形式）
        message: 推送消息 dict，例如
            {"type": "risk_update", "employee_id": "...", "risk_score": 75, "risk_level": "medium_high"}
    """
    pool = _connections.get(tenant_id)
    if not pool:
        # 无连接，静默跳过
        return

    payload = json.dumps(message, ensure_ascii=False)
    # 复制一份避免遍历过程中集合变化
    dead: list[WebSocket] = []
    for ws in list(pool):
        try:
            await ws.send_text(payload)
        except Exception as e:  # noqa: BLE001
            logger.debug("WebSocket 推送失败，移除连接 | err=%s", e)
            dead.append(ws)

    for ws in dead:
        _remove_connection(tenant_id, ws)


@router.websocket("/ws/risk")
async def risk_websocket(websocket: WebSocket, token: str | None = None):
    """实时风险推送 WebSocket 端点.

    连接时需带 JWT token（query param `token=`），解析 token 获取 tenant_id。
    鉴权失败时关闭连接（code 1008）。

    推送消息格式：
        {"type": "risk_update", "employee_id": "...", "risk_score": 75, "risk_level": "medium_high"}
    """
    # 1. 校验 token
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="缺少 token 参数")
        return

    try:
        payload = decode_token(token)
    except JWTError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="token 无效或已过期")
        return

    if payload.get("type") != "access":
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="token 类型错误")
        return

    tenant_id = str(payload.get("tenant_id", ""))
    if not tenant_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="token 缺少 tenant_id")
        return

    # 2. 接受连接并注册到租户池
    await websocket.accept()
    _add_connection(tenant_id, websocket)
    logger.info("WebSocket 连接建立 | tenant_id=%s", tenant_id)

    # 设置租户上下文（便于后续业务逻辑使用）
    set_tenant_context(
        TenantContext(
            tenant_id=tenant_id,
            user_id=str(payload.get("sub", "")),
            role=payload.get("role"),
        )
    )

    try:
        # 3. 保持连接，等待客户端消息（用于心跳/订阅控制）
        while True:
            # 接收客户端消息（心跳或控制指令；此处仅保持连接，不处理具体内容）
            try:
                data = await websocket.receive_text()
                # 简单心跳响应
                if data == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        logger.info("WebSocket 连接断开 | tenant_id=%s", tenant_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("WebSocket 异常 | tenant_id=%s | err=%s", tenant_id, e)
    finally:
        _remove_connection(tenant_id, websocket)
        clear_tenant_context()
