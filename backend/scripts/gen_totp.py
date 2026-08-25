"""生成 admin@hra-demo.com 的当前 TOTP 验证码（本地演示/测试用）.

用法（任选其一）：
    # 方式 A：在 api 容器内执行（推荐，密钥上下文与登录校验完全一致）
    docker cp backend/scripts/gen_totp.py hr-attrition-api-1:/tmp/gen_totp.py
    docker exec hr-attrition-api-1 python /tmp/gen_totp.py

    # 方式 B：本机 venv 执行（需 backend/.env 的 PII_FERNET_KEY 与容器一致）
    cd backend && .venv/Scripts/python.exe scripts/gen_totp.py

注意：不要把打印出的验证码提交到任何公开场合；码 30 秒刷新，verify 容忍上一周期。
"""

import asyncio
import sys
from pathlib import Path

# 兼容两种运行位置：仓库布局（backend/scripts/）与容器内任意路径（依赖镜像 WORKDIR=/app）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_cwd = str(Path.cwd())
if _cwd not in sys.path:
    sys.path.insert(0, _cwd)

from sqlalchemy import select  # noqa: E402

import pyotp  # noqa: E402

from app.core import pii_crypto  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402
from app.models.user import User  # noqa: E402


async def main() -> None:
    async with async_session_factory() as db:
        u = (
            await db.execute(select(User).where(User.email == "admin@hra-demo.com"))
        ).scalar_one_or_none()
        if u is None:
            print("ADMIN_NOT_EXISTS —— 先运行 scripts/smoke_seed.py")
            return
        s = u.totp_secret
        try:
            s = pii_crypto.decrypt(s)
        except Exception:
            # 解密失败说明当前环境的 PII 密钥与写入时不一致，
            # 此时拿到的可能是密文，pyotp 会报 Non-base32——请改用方式 A 在容器内执行。
            pass
        print("TOTP_CODE:", pyotp.TOTP(s).now())


asyncio.run(main())
