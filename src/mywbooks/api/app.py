from __future__ import annotations

import dotenv

dotenv.load_dotenv()

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from arq import create_pool
from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from mywbooks.async_download_manager import AsyncDownloadManager

from ..db import get_db, init_db
from .auth import CurrentUser, get_or_create_user_by_sub
from .routers import books, tasks, users


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # The import is here in order to ensure that all the tasks are registered
    from mywbooks.worker import WorkerSettings

    # Startup
    init_db()
    app.state.arq_pool = await create_pool(WorkerSettings.redis_settings)
    app.state.dm = AsyncDownloadManager()

    yield
    # Shutdown
    await app.state.dm.close()
    await app.state.arq_pool.close()


app = FastAPI(
    title="MyWBooks API",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# CORS (adjust to your frontend origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter(prefix="/api")
api_router.include_router(books.router, prefix="/books", tags=["books"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(users.router, prefix="/user", tags=["user"])


@api_router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@api_router.get("/me")
def me(user: CurrentUser) -> dict[str, Any]:
    # Typical claims you’ll see: sub, email, role, aud, exp, iat
    return {
        "sub": user.get("sub"),
        "email": user.get("email"),
        "role": user.get("role"),
        "aud": user.get("aud"),
    }


from fastapi.responses import RedirectResponse

...


@api_router.get("/profile", deprecated=True)
def profile_deprecated() -> RedirectResponse:
    return RedirectResponse(url="/api/user/profile")


app.include_router(api_router)
