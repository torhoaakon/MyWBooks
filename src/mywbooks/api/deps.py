from typing import cast

from arq.connections import ArqRedis
from fastapi import Request


def get_arq_pool(request: Request) -> ArqRedis:
    print("Hey", request)

    res = cast(ArqRedis, request.app.state.arq_pool)
    print("Hey", repr(request))
    return res

    return cast(ArqRedis, request.app.state.arq_pool)
