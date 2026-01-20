from __future__ import annotations

import logging
import os
from typing import Any

from arq.connections import RedisSettings

from .async_download_manager import AsyncDownloadManager
from .tasks import REGISTERED_TASK_FUNCTIONS, CtxType


async def startup(ctx: dict[Any, Any]) -> None:
    logging.basicConfig(level=logging.INFO)
    ctx["dm"] = AsyncDownloadManager()


async def shutdown(ctx: CtxType) -> None:
    dm: AsyncDownloadManager = ctx["dm"]
    await dm.close()


class WorkerSettings:
    functions = REGISTERED_TASK_FUNCTIONS
    redis_settings = RedisSettings.from_dsn(
        os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    )
    on_startup = startup
    on_shutdown = shutdown
