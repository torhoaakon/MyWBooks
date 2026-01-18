from typing import cast

from arq.connections import ArqRedis
from fastapi import Request

from mywbooks.api import app
from mywbooks.async_download_manager import AsyncDownloadManager


def get_arq_pool(request: Request) -> ArqRedis:
    return cast(ArqRedis, request.app.state.arq_pool)


def get_dm() -> AsyncDownloadManager:
    return cast(AsyncDownloadManager, app.state.dm)
