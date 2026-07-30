"""Celery 应用 + Beat 调度（漂移检测、公平性日报、数据保留）- D03 ADR-001 + D05 4.2.

复用 DWS Celery 模块思路：
  - broker: Redis
  - backend: Redis
  - beat 调度：
    - 漂移检测：每日 02:00（D03 5.2 离线流）
    - 公平性日报：每日 03:00（D10 7.3）
    - 数据保留清理：每周日 04:00（PIPL 第 47 条 / D11 C-COMP-06）
    - 数据保留状态报告：每月 1 号 05:00（D11 C-COMP 验收）
"""
from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "hra",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.model_governance", "app.tasks.data_retention"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_max_tasks_per_child=200,
    beat_schedule={
        # 漂移检测：每日 02:00（D03 5.2 离线流）
        "drift-detection-daily": {
            "task": "app.tasks.model_governance.detect_drift",
            "schedule": crontab(hour=2, minute=0),
        },
        # 公平性日报：每日 03:00（D10 7.3）
        "fairness-report-daily": {
            "task": "app.tasks.model_governance.fairness_daily_report",
            "schedule": crontab(hour=3, minute=0),
        },
        # 数据保留清理：每周日 04:00（D11 C-COMP-06，离职满 2 年物理删除）
        "data-retention-purge-weekly": {
            "task": "app.tasks.data_retention.purge_departed_employees",
            "schedule": crontab(hour=4, minute=0, day_of_week="sunday"),
        },
        # 数据保留状态报告：每月 1 号 05:00（D11 C-COMP 验收）
        "data-retention-report-monthly": {
            "task": "app.tasks.data_retention.report_retention_status",
            "schedule": crontab(hour=5, minute=0, day_of_month=1),
        },
    },
)
