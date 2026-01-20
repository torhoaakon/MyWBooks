from __future__ import annotations

import os

from arq.connections import RedisSettings

from .tasks import REGISTERED_TASK_FUNCTIONS


class WorkerSettings:
    functions = REGISTERED_TASK_FUNCTIONS
    redis_settings = RedisSettings.from_dsn(
        os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    )
