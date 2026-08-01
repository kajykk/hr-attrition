"""冒烟种子：租户 + admin 用户（含 TOTP secret），密码 hra-admin-2026."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import async_session_factory
from app.models.tenant import Tenant
from app.models.user import ROLE_ADMIN, User


async def main():
    async with async_session_factory() as db:
        tenant = (await db.execute(select(Tenant).where(Tenant.code == "hra-demo"))).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name="HRA 演示租户", code="hra-demo", plan="enterprise")
            db.add(tenant)
            await db.flush()
            print("TENANT created:", tenant.id)

        admin = (await db.execute(select(User).where(User.email == "admin@hra-demo.com"))).scalar_one_or_none()
        if admin is None:
            # 兼容旧种子（admin@hra.local 被 EmailStr 保留域名校验拒绝）
            legacy = (await db.execute(select(User).where(User.email == "admin@hra.local"))).scalar_one_or_none()
            if legacy is not None:
                await db.delete(legacy)
                await db.flush()
            import pyotp

            totp_secret = pyotp.random_base32()
            admin = User(
                tenant_id=tenant.id,
                email="admin@hra-demo.com",
                password_hash=hash_password("hra-admin-2026"),
                name="系统管理员",
                role=ROLE_ADMIN,
                status="active",
                totp_secret=totp_secret,
            )
            db.add(admin)
            await db.flush()
            print("ADMIN created:", admin.id)
            print("TOTP_SECRET:", totp_secret)
        else:
            print("ADMIN already exists:", admin.id)
        await db.commit()


asyncio.run(main())
