from datetime import datetime
from decimal import localcontext
from typing import Any, Sequence

from fastapi import APIRouter, Depends, Query, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mywbooks import models
from mywbooks.api.auth import CurrentUser, get_or_create_user_by_sub
from mywbooks.db import get_db  # adjust path if needed
from mywbooks.models import Task, TaskStatus
from mywbooks.task_cleanup import run_task_cleanup  # adjust imports

router = APIRouter()


# --- Response Schemas ------------------------------------------------

#
# class TaskOut(BaseModel):
#     id: int
#     book_id: int
#     # user_id: int ???
#     status: TaskStatus
#     error: str | None = None
#     created_at: datetime
#     started_at: datetime | None = None
#     finished_at: datetime | None = None
#     attempts: int
#     payload: dict[str, Any] | None = None
#
#     class Config:
#         from_attributes = True  # SQLAlchemy 2.x compatible
#         # or: orm_mode = True  (if you're still on Pydantic v1)


class CleanupResponse(BaseModel):
    ok: bool
    deleted: int


# --- Routes ----------------------------------------------------------


@router.get("")
def list_my_tasks(
    current_user: CurrentUser,
    db: Session = Depends(get_db),
    status: TaskStatus | None = Query(
        default=None,
        description="Optional filter by task status",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Max number of tasks to return",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Offset for pagination",
    ),
) -> Sequence[dict[str, Any]]:
    """
    List all tasks owned by the current user.
    """

    local_user = get_or_create_user_by_sub(db, current_user)

    # 🔐 Ownership filter
    stmt = select(Task).where(Task.user_id == local_user.id)

    # Optional status filter: ?status=SUCCEEDED
    if status is not None:
        stmt = stmt.where(Task.status == status)

    # Order newest first + pagination
    stmt = stmt.order_by(Task.created_at.desc()).offset(offset).limit(limit)

    tasks: Sequence[Task] = db.scalars(stmt).all()

    # print(tasks)

    res = []
    for task in tasks:
        payload = task.payload or {}
        res.append(
            {
                "id": task.id,
                "type": task.type,
                "status": task.status,
                "book_id": payload.get("book_id") or -1,
                "payload": task.payload,
                "error": task.error,
                "created_at": task.created_at.isoformat(),
                "started_at": task.started_at.isoformat() if task.started_at else None,
                "finished_at": (
                    task.finished_at.isoformat() if task.finished_at else None
                ),
            }
        )

    return res


@router.get("/{task_id}")
def get_task(
    task_id: int, user: CurrentUser, db: Session = Depends(get_db)
) -> dict[str, Any]:
    # TODO: Check user
    local_user = get_or_create_user_by_sub(db, user)

    task = db.get(models.Task, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Enforce ownership
    if task.user_id != local_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    return {
        "id": task.id,
        "type": task.type,
        "status": task.status,
        "payload": task.payload,
        "error": task.error,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


@router.delete("/book/{book_id}", response_model=CleanupResponse)
def cleanup_tasks_for_book(
    book_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> CleanupResponse:
    """
    Delete all finished tasks for the given book owned by the current user,
    and run per-task cleanup (e.g. delete EPUB files).
    """
    local_user = get_or_create_user_by_sub(db, current_user)

    stmt = (
        select(Task)
        .where(
            Task.user_id == local_user.id,
            Task.book_id == book_id,
            Task.finished_at.is_not(None),
        )
        .order_by(Task.created_at.desc())
    )

    tasks = db.scalars(stmt).all()

    deleted = 0
    for task in tasks:
        # Run per-task cleanup (e.g. remove EPUB file for SUCCEEDED tasks)
        run_task_cleanup(task)
        db.delete(task)
        deleted += 1

    db.commit()

    return CleanupResponse(ok=True, deleted=deleted)
