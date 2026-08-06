"""LLM 服务 - 通义千问 Max SSE 调用 + PII 脱敏 + 降级规则模板（D03 4.4 + ADR-003）.

调用流程：
  1. PII 脱敏：员工姓名 → "员工A"，身份证/手机号移除，部门/岗位保留
  2. Prompt 构造（system + user）
  3. 调用通义千问 Max（DashScope SSE），失败回退 DeepSeek-V3，再失败降级规则模板
  4. SSE 流式响应前端

OpenAI 路径默认禁用（OPENAI_ENABLED=false），需数据出境评估通过后启用。
"""
import asyncio
import json
from collections.abc import AsyncGenerator

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 通义千问 DashScope 兼容 OpenAI 协议的 endpoint
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

SYSTEM_PROMPT = (
    "你是 HR 保留专家，基于 SHAP 归因因子生成个性化保留建议。"
    "请用中文输出，覆盖调薪/转岗/培训/辅导维度，每条建议具体可执行。"
)


def sanitize_pii(employee: dict) -> dict:
    """PII 脱敏（D03 4.4 + D04 7.2）.

    规则：
      - 员工姓名 → "员工A"
      - 身份证号 → 移除
      - 手机号 → 移除
      - 部门/岗位 → 保留
      - 薪资绝对值 → 移除（仅保留薪资分位）
    """
    sanitized = dict(employee)
    sanitized["name"] = "员工A"
    sanitized.pop("id_card", None)
    sanitized.pop("phone", None)
    sanitized.pop("salary", None)
    sanitized.pop("salary_encrypted", None)
    sanitized.pop("id_card_encrypted", None)
    sanitized.pop("phone_encrypted", None)
    # V1.1 公平性字段同样脱敏（不进入 LLM）
    sanitized.pop("ethnicity", None)
    sanitized.pop("disability", None)
    sanitized.pop("ethnicity_encrypted", None)
    sanitized.pop("disability_encrypted", None)
    return sanitized


def build_prompt(sanitized_employee: dict, shap_factors: list[dict], risk_score: int) -> str:
    """构造 user prompt（D03 4.4 示例）."""
    name = sanitized_employee.get("name", "员工A")
    dept = sanitized_employee.get("department_name", "未指定部门")
    position = sanitized_employee.get("position", "未指定岗位")
    factors_str = " / ".join(
        f"{f.get('display_name', f.get('feature'))}({f.get('value', '?')})"
        for f in shap_factors[:3]
    )
    return (
        f"{name} 是 {dept}{position}，离职风险分 {risk_score}/100。"
        f"Top3 归因: {factors_str}。"
        f"请生成 3 条具体保留建议，覆盖调薪/转岗/培训/辅导。"
    )


def _fallback_template(sanitized_employee: dict, shap_factors: list[dict], risk_score: int) -> str:
    """LLM 不可用时的降级规则模板（D03 4.4 降级 + ADR-003）."""
    factors = {f.get("feature"): f for f in shap_factors}
    parts = [f"基于规则模板生成保留建议（风险分 {risk_score}）：\n\n"]
    if "salary_percentile" in factors and factors["salary_percentile"].get("contribution", 0) < 0:
        parts.append("1. **调薪建议**：薪资分位偏低，建议调薪 15%-20% 提升至 60 分位以上。\n")
    else:
        parts.append("1. **调薪建议**：参考部门中位数进行小幅调薪 5%-10%。\n")
    if "promotion_gap_months" in factors:
        parts.append("2. **晋升/转岗**：晋升间隔偏长，建议启动晋升评估或横向转岗机会。\n")
    else:
        parts.append("2. **培训发展**：制定个性化培训计划，明确晋升路径。\n")
    parts.append("3. **辅导支持**：安排直属经理 1v1 辅导，每月跟进员工状态。\n")
    return "".join(parts)


class LLMService:
    """LLM 建议服务 - 主通义千问 Max，备 DeepSeek-V3，OpenAI 默认禁用."""

    @classmethod
    async def stream_advice(
        cls,
        sanitized_employee: dict,
        shap_factors: list[dict],
        risk_score: int,
    ) -> AsyncGenerator[dict, None]:
        """SSE 流式生成保留建议.

        yields:
            {"chunk": "文本片段"}  - 文本流
            {"metadata": {...}}    - 元数据（tokens/latency/model）
            {"done": True}         - 结束标记
        """
        prompt = build_prompt(sanitized_employee, shap_factors, risk_score)
        model_name = settings.LLM_PRIMARY

        # 尝试通义千问 Max（DashScope 兼容 OpenAI 协议）
        # 精确捕获外部 LLM 边界可预期异常：HTTP 传输 / 响应 JSON 解析 / 配置缺失
        try:
            async for chunk in cls._call_dashscope_sse(prompt, model_name):
                yield chunk
            return
        except (httpx.HTTPError, json.JSONDecodeError, RuntimeError) as e:
            logger.warning("通义千问 Max 调用失败，尝试备用 LLM: %s", e)

        # 备用：DeepSeek-V3
        if settings.LLM_FALLBACK:
            try:
                async for chunk in cls._call_dashscope_sse(prompt, settings.LLM_FALLBACK):
                    yield chunk
                return
            except (httpx.HTTPError, json.JSONDecodeError, RuntimeError) as e:
                logger.warning("备用 LLM 调用失败，降级规则模板: %s", e)

        # 最终降级：规则模板（D03 4.4）
        template = _fallback_template(sanitized_employee, shap_factors, risk_score)
        # 模拟流式输出（按段落切分）
        for segment in template.split("\n\n"):
            if segment.strip():
                yield {"chunk": segment + "\n\n"}
                await asyncio.sleep(0.05)
        yield {"metadata": {"tokens_used": 0, "model": "rule-template", "latency_ms": 50}}
        yield {"done": True}

    @classmethod
    async def _call_dashscope_sse(
        cls, prompt: str, model: str
    ) -> AsyncGenerator[dict, None]:
        """调用 DashScope 兼容 OpenAI 协议的 SSE 接口（通义千问 Max）."""
        if not settings.DASHSCOPE_API_KEY:
            raise RuntimeError("DASHSCOPE_API_KEY 未配置")

        headers = {
            "Authorization": f"Bearer {settings.DASHSCOPE_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client, client.stream(
            "POST",
            f"{DASHSCOPE_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    yield {"done": True}
                    return
                try:
                    obj = json.loads(data)
                    choices = obj.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield {"chunk": content}
                except json.JSONDecodeError:
                    logger.debug("SSE 行 JSON 解析失败，跳过: %r", data)
                    continue
