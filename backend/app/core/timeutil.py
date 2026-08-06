"""业务日历日工具 - 统一 Asia/Shanghai 时区（DTZ011 审计修复）.

业务规则（离职日期校验、数据保留截止日、年龄/工龄计算）依赖"今天"的
语义应为公司所在地的日历日，而非部署主机时区。统一走本模块，保证
主机即使运行在 UTC，业务日期边界也不偏移。
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

_TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")


def today() -> date:
    """返回业务日历日（Asia/Shanghai，公司所在地）."""
    return datetime.now(_TZ_SHANGHAI).date()
