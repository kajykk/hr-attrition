"""WebSocket 端点 - 实时风险推送（D05 3.5 + D04 4.2）.

路由：
  - WS /ws/risk：实时风险推送
    连接时需带 JWT token（query param `token=`），解析 token 获取 tenant_id。

连接管理：
  - 用 dict 维护 {tenant_id: set[WebSocket]}，支持多租户隔离
  - 连接级元数据 _conn_meta（user_id/role）用于推送侧按角色过滤
  - broadcast_risk_update(tenant_id, message)：向指定租户的连接推送；
    employee 角色仅接收显式指向本人（target_user_id 匹配）的消息
  - 无连接时 broadcast 静默跳过

安全约束（审查修复）：
  - 建连时校验用户 status=active 且未软删（禁用账号立即断开）
  - 推送按角色过滤：员工不得收到他人风险信息（最小知情）
"""
from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from jose import JWTError
from sqlalchemy import select

from app.core.logging import get_logger
from app.core.security import decode_token
from app.core.tenant import TenantContext, clear_tenant_context, set_tenant_context

logger = get_logger(__name__)

router = APIRouter()

# 员工角色：仅接收显式指向本人的推送（message.target_user_id == user_id）
_EMPLOYEE_ROLE = "employee"


# ===== 连接管理：{tenant_id: set[WebSocket]}（多租户隔离） =====
_connections: dict[str, set[WebSocket]] = {}
# 连接元数据：{WebSocket: {"user_id": str, "role": str}}（与上面池同步增删）
_conn_meta: dict[WebSocket, dict] = {}


def _add_connection(
    tenant_id: str,
    ws: WebSocket,
    *,
    user_id: str | None = None,
    role: str | None = None,
) -> None:
    """注册 WebSocket 连接到租户池（附带角色元数据供推送过滤）."""
    _connections.setdefault(tenant_id, set()).add(ws)
    _conn_meta[ws] = {"user_id": user_id or "", "role": role or ""}


def _remove_connection(tenant_id: str, ws: WebSocket) -> None:
    """从租户池移除连接（含元数据）."""
    _conn_meta.pop(ws, None)
    pool = _connections.get(tenant_id)
    if pool is None:
        return
    pool.discard(ws)
    if not pool:
        _connections.pop(tenant_id, None)


async def broadcast_risk_update(tenant_id: str, message: dict) -> None:
    """向指定租户的 WebSocket 连接推送风险更新.

    无连接时静默跳过；单连接发送失败时移除该连接。

    角色过滤（最小知情）：
      - employee 角色仅接收 message["target_user_id"] 与其 user_id 一致的推送
      - 其他角色（admin/hr_manager/hrbp/manager）接收租户内全部推送
      - 未登记元数据的连接（历史兼容）按内部通道处理，全量接收

    Args:
        tenant_id: 租户 ID（字符串形式）
        message: 推送消息 dict，例如
            {"type": "risk_update", "employee_id": "...", "risk_score": 75,
             "risk_level": "medium_high", "target_user_id": "可选，定向接收者"}
    """
    pool = _connections.get(tenant_id)
    if not pool:
        # 无连接，静默跳过
        return

    payload = json.dumps(message, ensure_ascii=False)
    target = str(message.get("target_user_id", ""))
    # 复制一份避免遍历过程中集合变化
    dead: list[WebSocket] = []
    for ws in list(pool):
        meta = _conn_meta.get(ws)
        if meta and meta.get("role") == _EMPLOYEE_ROLE:
            # 员工仅收本人相关；无明确指向的广播不下发（防越权知悉）
            if not target or target != meta.get("user_id"):
                continue
        try:
            await ws.send_text(payload)
        except Exception as e:  # noqa: BLE001
            logger.debug("WebSocket 推送失败，移除连接 | err=%s", e)
            dead.append(ws)

    for ws in dead:
        _remove_connection(tenant_id, ws)


async def _check_user_active(user_id: str) -> bool:
    """校验用户存在、未软删且 status=active（禁用账号拒绝建连）.

    DB 异常时 fail-closed（返回 False）：宁可断连也不放行已禁用身份。
    """
    from app.db.session import async_session_factory
    from app.models.user import User

    try:
        async with async_session_factory() as db:
            stmt = select(User).where(
                User.id == user_id,
                User.deleted_at.is_(None),
            )
            user = (await db.execute(stmt)).scalar_one_or_none()
            return user is not None and user.status == "active"
    except Exception as e:  # noqa: BLE001
        logger.warning("WebSocket 用户状态校验失败（fail-closed 断开） | err=%s", e)
        return False


@router.websocket("/ws/risk")
async def risk_websocket(websocket: WebSocket, token: str | None = None):
    """实时风险推送 WebSocket 端点.

    连接时需带 JWT token（query param `token=`），解析 token 获取 tenant_id。
    鉴权失败 / 用户非 active 时关闭连接（code 1008）。

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
    user_id = str(payload.get("sub", ""))
    role = payload.get("role")
    if not tenant_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="token 缺少 tenant_id")
        return

    # 2. 校验用户状态（禁用/删除账号即使持有效 token 也拒绝建连）
    if not await _check_user_active(user_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="账号不可用或已禁用")
        return

    # 3. 接受连接并注册到租户池（附角色元数据，供推送过滤）
    await websocket.accept()
    _add_connection(tenant_id, websocket, user_id=user_id, role=str(role or ""))
    logger.info("WebSocket 连接建立 | tenant_id=%s | role=%s", tenant_id, role)

    # 设置租户上下文（便于后续业务逻辑使用）
    set_tenant_context(TenantContext(tenant_id=tenant_id, user_id=user_id, role=role))

    try:
        # 4. 保持连接，等待客户端消息（裸文本 "ping" 心跳 → JSON pong 响应）
        while True:
            try:
                data = await websocket.receive_text()
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
