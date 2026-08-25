"""PII 主密钥轮换：批量重加密存量员工 PII 列（ADR-007 季度轮换配套工具）.

前置条件：
  1. 新钥已写入 PII_FERNET_KEY；被轮换的旧钥（可多个，逗号分隔，新→旧）
     写入 PII_PREVIOUS_KEYS（应用侧解密回退链依赖它）；
  2. 数据库可达（DATABASE_URL）。

用法（backend 目录下）：
  python scripts/rotate_pii_key.py --dry-run            # 仅统计，不写库
  python scripts/rotate_pii_key.py --batch-size 500     # 正式重加密，分批 commit

密钥输入：优先读环境变量 PII_ROTATION_NEW_KEY / PII_ROTATION_OLD_KEYS，
未设置时 getpass 交互输入（终端不回显）。

说明：
  - 脚本用「新钥 + 旧钥链」本地解密每个存量单元格：能被新钥直接解开的视为
    已重加密（跳过），由旧钥命中的则用新钥重写；
  - 解密失败的单元格保留原值并计入 errors（不破坏数据），退出码非 0；
  - 仅做数据重加密，无 DB 结构变更（因此无需新增 Alembic 迁移）。
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.employee import Employee

# D04 3.1 PII 加密清单（employees 表）
PII_COLUMNS: tuple[str, ...] = (
    "name_encrypted",
    "id_card_encrypted",
    "phone_encrypted",
    "salary_encrypted",
    "ethnicity_encrypted",
    "disability_encrypted",
)


def _parse_old_keys(raw: str) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        part = part.strip()
        if part and part not in seen:
            seen.add(part)
            keys.append(part)
    return keys


def build_key_chain(new_raw: str, old_raw: str) -> list[Fernet]:
    """构建 [新钥, *旧钥] Fernet 链；任一密钥非法或新旧重复时终止进程."""
    old_keys = _parse_old_keys(old_raw)
    if new_raw.strip() in old_keys:
        print("[FATAL] 新主钥不得出现在历史钥列表中，中止。")
        sys.exit(2)
    entries = [("new", new_raw)] + [("old", k) for k in old_keys]
    if len(entries) < 2:
        print("[FATAL] 至少需要一个历史钥（否则无需重加密），中止。")
        sys.exit(2)
    chain: list[Fernet] = []
    for role, raw in entries:
        try:
            chain.append(Fernet(raw.strip().encode("utf-8")))
        except (ValueError, TypeError) as exc:
            print(f"[FATAL] {role} 密钥非法（{type(exc).__name__}），中止。")
            sys.exit(2)
    return chain


def decrypt_cell(value: str, chain: list[Fernet]) -> tuple[int, str] | None:
    """返回 (命中密钥下标, 明文)；全部失败返回 None.

    下标 0 表示已是新钥密文（跳过），>=1 表示需用新钥重加密。
    """
    for idx, fernet in enumerate(chain):
        plaintext = try_decrypt(fernet, value)
        if plaintext is not None:
            return idx, plaintext
    return None


def try_decrypt(fernet: Fernet, value: str) -> str | None:
    """单钥尝试解密；失败返回 None（不抛异常）."""
    try:
        return fernet.decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def load_keys() -> tuple[str, str]:
    """环境变量优先，缺省 getpass 交互输入."""
    new_raw = os.environ.get("PII_ROTATION_NEW_KEY", "")
    old_raw = os.environ.get("PII_ROTATION_OLD_KEYS", "")
    if not new_raw:
        new_raw = getpass.getpass("PII 新主钥 (PII_ROTATION_NEW_KEY): ").strip()
    if not old_raw:
        old_raw = getpass.getpass(
            "PII 历史钥，逗号分隔、新→旧 (PII_ROTATION_OLD_KEYS): "
        ).strip()
    return new_raw, old_raw


async def rotate(batch_size: int, dry_run: bool) -> int:
    """分批扫描 employees 并重加密 PII 列；返回进程退出码."""
    new_raw, old_raw = load_keys()
    chain = build_key_chain(new_raw, old_raw)

    # 统计：每列 {reencrypted / skipped / errors} + 行级计数
    stats: dict[str, dict[str, int]] = {
        col: {"reencrypted": 0, "skipped": 0, "errors": 0} for col in PII_COLUMNS
    }
    rows_scanned = 0
    error_rows: list[str] = []

    async with async_session_factory() as db:
        last_id: object | None = None
        while True:
            stmt = select(Employee).order_by(Employee.id).limit(batch_size)
            if last_id is not None:
                stmt = stmt.where(Employee.id > last_id)
            employees = list((await db.execute(stmt)).scalars().all())
            if not employees:
                break

            batch_dirty = False
            for emp in employees:
                rows_scanned += 1
                for col in PII_COLUMNS:
                    value = getattr(emp, col)
                    if not value:
                        continue
                    hit = decrypt_cell(value, chain)
                    if hit is None:
                        stats[col]["errors"] += 1
                        if len(error_rows) < 10 and str(emp.employee_no) not in error_rows:
                            error_rows.append(str(emp.employee_no))
                        continue
                    hit_idx, plaintext = hit
                    if hit_idx == 0:
                        stats[col]["skipped"] += 1
                        continue
                    # 旧钥命中 → 用新钥重写（chain[0] 即当前主钥）
                    setattr(
                        emp,
                        col,
                        chain[0].encrypt(plaintext.encode("utf-8")).decode("utf-8"),
                    )
                    stats[col]["reencrypted"] += 1
                    batch_dirty = True

                last_id = emp.id

            if dry_run:
                await db.rollback()
            elif batch_dirty:
                await db.commit()  # 分批 commit，避免长事务
                print(f"committed batch up to id={last_id}")

    # ===== 输出统计 =====
    mode = "DRY-RUN（未写库）" if dry_run else "APPLIED"
    print(f"\n===== PII 重加密完成 [{mode}] =====")
    print(f"rows scanned: {rows_scanned}")
    for col, s in stats.items():
        print(
            f"  {col:<22} reencrypted={s['reencrypted']:<8} "
            f"skipped={s['skipped']:<8} errors={s['errors']}"
        )
    total_errors = sum(s["errors"] for s in stats.values())
    if error_rows:
        print(f"error sample employee_no (≤10): {', '.join(error_rows)}")
    if total_errors:
        print(
            "[WARN] 存在无法解密的单元格（密钥缺失或数据损坏），"
            "请补齐 PII_PREVIOUS_KEYS 后重跑。"
        )
        return 1
    print("下一步：确认应用日志无 legacy_key_used 后，清空 PII_PREVIOUS_KEYS。")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="批量重加密存量员工 PII 列（主密钥轮换）")
    parser.add_argument("--batch-size", type=int, default=500, help="每批行数（默认 500）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    sys.exit(asyncio.run(rotate(args.batch_size, args.dry_run)))


if __name__ == "__main__":
    main()
