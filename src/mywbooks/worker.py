from __future__ import annotations

import os

from arq.connections import RedisSettings

from mywbooks.tasks import download_book_task, send_book_task


class WorkerSettings:
    functions = [download_book_task, send_book_task]
    redis_settings = RedisSettings.from_dsn(
        os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    )
