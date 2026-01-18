from __future__ import annotations

from typing import Any, AsyncGenerator

import dotenv

dotenv.load_dotenv()

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..db import init_db
from .auth import CurrentUser
from .routers import books, tasks, users


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    init_db()
    yield


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

app.include_router(api_router)


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
