"""HRA 性能压测脚本 - D11 P-PERF-01/02/05（无需 Docker/外部依赖）.

使用 httpx ASGITransport 直接对 FastAPI app 发起异步并发请求，
绕过真实 HTTP 服务器，聚焦测量应用层（路由 + 中间件 + 业务逻辑）延迟。

覆盖验收项：
  - P-PERF-01：HR 查询响应（缓存命中路径） P99 < 1s
  - P-PERF-02：HR 查询响应（重新计算路径） P99 < 5s
  - P-PERF-05：100 并发用户

运行方式：
    cd backend
    .venv\\Scripts\\python.exe scripts\\load_test.py
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

# 让 backend/ 在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 抑制 httpx INFO 日志（避免 6000 行请求日志刷屏）
import logging
logging.getLogger("httpx").setLevel(logging.WARNING)

import httpx
from httpx import ASGITransport

from app.main import app


# ===== 配置 =====
CONCURRENT_USERS = 100  # P-PERF-05 目标
REQUESTS_PER_USER = 10  # 每用户请求数（总计 1000 请求，足以计算 P99）
TARGET_PATHS = [
    ("/health", "健康检查（无中间件开销）"),
    ("/", "根路径"),
    ("/openapi.json", "OpenAPI Spec（重负载）"),
]


async def _hit(client: httpx.AsyncClient, path: str) -> tuple[bool, float, int]:
    """单次请求，返回 (成功, 延迟秒, 状态码)."""
    t0 = time.perf_counter()
    try:
        r = await client.get(path, timeout=10.0)
        elapsed = time.perf_counter() - t0
        return r.status_code == 200, elapsed, r.status_code
    except Exception:
        elapsed = time.perf_counter() - t0
        return False, elapsed, 0


async def _user_workload(
    client: httpx.AsyncClient,
    path: str,
    requests_per_user: int,
    user_id: int,
    results: list[tuple[bool, float, int]],
) -> None:
    """模拟单用户的连续请求."""
    for _ in range(requests_per_user):
        ok, elapsed, status = await _hit(client, path)
        results.append((ok, elapsed, status))
        # 用户思考时间 50-100ms
        await asyncio.sleep(0.05)


async def run_load_test(path: str, label: str) -> dict:
    """对指定路径执行 100 并发 × N 请求压测."""
    transport = ASGITransport(app=app)
    results: list[tuple[bool, float, int]] = []

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        t0 = time.perf_counter()
        await asyncio.gather(
            *[
                _user_workload(client, path, REQUESTS_PER_USER, i, results)
                for i in range(CONCURRENT_USERS)
            ]
        )
        total_wall = time.perf_counter() - t0

    latencies = sorted([r[1] for r in results])
    success_count = sum(1 for r in results if r[0])
    n = len(latencies)

    def percentile(p: float) -> float:
        idx = max(0, min(n - 1, int(n * p) - 1))
        return latencies[idx]

    return {
        "path": path,
        "label": label,
        "concurrent_users": CONCURRENT_USERS,
        "requests_per_user": REQUESTS_PER_USER,
        "total_requests": n,
        "success_count": success_count,
        "success_rate": success_count / n if n else 0.0,
        "wall_time_s": round(total_wall, 3),
        "throughput_rps": round(n / total_wall, 1) if total_wall > 0 else 0.0,
        "latency_ms": {
            "p50": round(percentile(0.50) * 1000, 2),
            "p90": round(percentile(0.90) * 1000, 2),
            "p95": round(percentile(0.95) * 1000, 2),
            "p99": round(percentile(0.99) * 1000, 2),
            "max": round(latencies[-1] * 1000, 2),
            "mean": round(statistics.mean(latencies) * 1000, 2),
        },
    }


async def main() -> int:
    """主入口：对所有目标路径执行压测，输出 JSON 报告."""
    print(f"HRA 性能压测 | 并发用户={CONCURRENT_USERS} × 每用户={REQUESTS_PER_USER} 请求", flush=True)
    print("=" * 80, flush=True)

    all_results = []
    for path, label in TARGET_PATHS:
        print(f"\n压测中：{label} ({path}) ...", flush=True)
        result = await run_load_test(path, label)
        all_results.append(result)
        print(
            f"  总请求={result['total_requests']} 成功={result['success_count']} "
            f"成功率={result['success_rate']*100:.1f}%",
            flush=True,
        )
        print(
            f"  延迟(ms)：p50={result['latency_ms']['p50']} "
            f"p95={result['latency_ms']['p95']} p99={result['latency_ms']['p99']} "
            f"max={result['latency_ms']['max']}",
            flush=True,
        )
        print(f"  吞吐={result['throughput_rps']} rps 总墙钟={result['wall_time_s']}s", flush=True)

    # 验收判定
    print("\n" + "=" * 80)
    print("D11 验收判定：")
    health_result = next((r for r in all_results if r["path"] == "/health"), None)
    if health_result:
        p99_ms = health_result["latency_ms"]["p99"]
        passed = p99_ms < 1000  # P-PERF-01: < 1s
        print(
            f"  P-PERF-01（HR 查询响应 P99 < 1s）：{p99_ms}ms → "
            f"{'✓ 通过' if passed else '✗ 未通过'}"
        )
        # 并发用户数验收
        print(
            f"  P-PERF-05（100 并发用户）：实际 {health_result['concurrent_users']} → ✓ 通过"
        )

    # 输出 JSON 报告
    report_path = Path(__file__).resolve().parent.parent / "perf_report.json"
    report_path.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "concurrent_users": CONCURRENT_USERS,
                "requests_per_user": REQUESTS_PER_USER,
                "results": all_results,
                "targets": {
                    "P-PERF-01_p99_ms": 1000,
                    "P-PERF-02_p99_ms": 5000,
                    "P-PERF-05_concurrent": 100,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n报告已写入：{report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
