from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl, Json, model_validator
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from mywbooks import models
from mywbooks.api.auth import CurrentUser, get_or_create_user_by_sub
from mywbooks.api.deps import get_arq_pool, get_dm
from mywbooks.async_download_manager import AsyncDownloadManager
from mywbooks.book import EPUB_DIR
from mywbooks.db import get_db
from mywbooks.library import add_book_to_user
from mywbooks.services import ingest
from mywbooks.tasks import schedule_task

router = APIRouter()


# ####
# ## Schemas
# ####


class AddRoyalRoadBody(BaseModel):
    url: Optional[HttpUrl] = None
    fiction_id: Optional[int] = None

    @model_validator(mode="after")
    def check_at_least_one(self) -> "AddRoyalRoadBody":
        if not self.url and not self.fiction_id:
            raise ValueError("Either 'url' or 'fiction_id' must be provided")
        return self


class DownloadBookNowBody(BaseModel):
    chapters: list[int] | None = (
        None  # This is referring to the chapter id.  TODO:  We could enable custom order
    )

    title: Optional[str] = None
    cover_img: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None  # Ignore for now
    # May add more meta

    include_images: Optional[bool] = None
    include_chapter_titles: Optional[bool] = None
    image_resize_max: Optional[int] = None
    epub_css_filepath: Optional[str] = None

    # On finished
    # send_by_email: Optional[bool]  TODO


class BookOut(BaseModel):
    id: int
    provider: str
    provider_fiction_uid: str
    source_url: str
    title: str
    author: Optional[str] = None
    language: Optional[str] = None
    cover_url: Optional[str] = None

    @classmethod
    def from_model(cls, b: models.Book) -> "BookOut":
        return cls(
            id=b.id,
            provider=(
                b.provider.value if hasattr(b.provider, "value") else str(b.provider)
            ),
            provider_fiction_uid=b.provider_fiction_uid,
            source_url=b.source_url,
            title=b.title,
            author=b.author,
            language=b.language,
            cover_url=b.cover_url,
        )


class DownloadOptions(BaseModel):
    include_images: bool = True
    include_chapter_titles: bool = True
    include_description: bool = False
    custom_title: str | None = None
    custom_cover_url: str | None = None


class BookDetailOut(BaseModel):
    id: int
    provider: str
    source_url: str
    title: str
    author: str | None = None
    language: str | None = None
    cover_url: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    chapter_count: int
    user_options: DownloadOptions


class ChapterOut(BaseModel):
    id: int
    index: int
    title: str
    is_fetched: bool
    fetched_at: datetime | None = None
    created_at: datetime


class ChaptersPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ChapterOut]


class SaveOptionsBody(BaseModel):
    options: DownloadOptions


# ==== Response Messages ====


class ResponseMsg(BaseModel):
    ok: bool


class DownloadBookNowResponse(ResponseMsg):
    task_id: int
    task_status: models.TaskStatus


class SendByEmailResponse(ResponseMsg):
    task_id: int
    task_status: models.TaskStatus


# --- Helpers ------------------------------------------------------------------


# --- Routes -------------------------------------------------------------------


@router.post("/royalroad", response_model=BookOut, status_code=201)
async def add_royalroad_book(
    body: AddRoyalRoadBody,
    user: CurrentUser,
    db: Session = Depends(get_db),
    dm: AsyncDownloadManager = Depends(get_dm),
) -> BookOut:
    """
    Upsert a RoyalRoad book (by fiction URL or fiction_id) and subscribe the current user.
    """

    # 1) Upsert book via your ingest helpers
    if body.url:
        book_id = await ingest.upsert_royalroad_book_from_url(db, body.url._url, dm)
    else:
        # If your ingest exposes a dedicated fiction-id helper, use it.
        # Otherwise, synthesize a URL (works with your current ingest).
        url = f"https://www.royalroad.com/fiction/{body.fiction_id}"
        book_id = await ingest.upsert_royalroad_book_from_url(db, url, dm)

    # 2) Map Supabase user → local User and subscribe
    local_user = get_or_create_user_by_sub(db, user)
    add_book_to_user(db, local_user.id, book_id)

    # 3) Return the book
    book = db.get(models.Book, book_id)
    if not book:
        raise HTTPException(status_code=500, detail="Book upserted but not found.")
    return BookOut.from_model(book)


@router.get("", response_model=list[BookOut])
def list_my_books(user: CurrentUser, db: Session = Depends(get_db)) -> list[BookOut]:
    """
    List books the current user has in their library (subscriptions).
    """
    local_user = get_or_create_user_by_sub(db, user)

    q = (
        select(models.Book)
        .join(models.BookUser, models.BookUser.book_id == models.Book.id)
        .where(
            models.BookUser.user_id == local_user.id, models.BookUser.in_library == True
        )  # noqa: E712
        .order_by(models.Book.title.asc())
    )
    rows = db.execute(q).scalars().all()
    return [BookOut.from_model(b) for b in rows]


@router.get("/{book_id}", response_model=BookDetailOut)
def get_book_detail(
    book_id: int, user: CurrentUser, db: Session = Depends(get_db)
) -> BookDetailOut:
    """Full book detail: metadata, description, chapter count, and saved user options."""
    local_user = get_or_create_user_by_sub(db, user)

    book = db.get(models.Book, book_id)
    if not book:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

    link = db.execute(
        select(models.BookUser).where(
            models.BookUser.user_id == local_user.id,
            models.BookUser.book_id == book_id,
            models.BookUser.in_library == True,  # noqa: E712
        )
    ).scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

    chapter_count: int = (
        db.execute(
            select(func.count()).select_from(models.Chapter).where(
                models.Chapter.book_id == book_id
            )
        ).scalar()
        or 0
    )

    user_options = DownloadOptions(**(link.download_options or {}))

    return BookDetailOut(
        id=book.id,
        provider=book.provider.value if hasattr(book.provider, "value") else str(book.provider),
        source_url=book.source_url,
        title=book.title,
        author=book.author,
        language=book.language,
        cover_url=book.cover_url,
        description=book.description,
        tags=book.tags,
        chapter_count=chapter_count,
        user_options=user_options,
    )


@router.get("/{book_id}/chapters", response_model=ChaptersPage)
def get_book_chapters(
    book_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=250, ge=1, le=1000),
    sort: str = Query(default="asc", pattern="^(asc|desc)$"),
) -> ChaptersPage:
    """Paginated chapter list for a book. Frontend typically fetches page_size=250 and pages locally."""
    local_user = get_or_create_user_by_sub(db, user)

    link = db.execute(
        select(models.BookUser).where(
            models.BookUser.user_id == local_user.id,
            models.BookUser.book_id == book_id,
            models.BookUser.in_library == True,  # noqa: E712
        )
    ).scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

    total: int = (
        db.execute(
            select(func.count()).select_from(models.Chapter).where(
                models.Chapter.book_id == book_id
            )
        ).scalar()
        or 0
    )

    order = asc(models.Chapter.index) if sort == "asc" else desc(models.Chapter.index)
    chapters = db.scalars(
        select(models.Chapter)
        .where(models.Chapter.book_id == book_id)
        .order_by(order)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return ChaptersPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[
            ChapterOut(
                id=ch.id,
                index=ch.index,
                title=ch.title,
                is_fetched=ch.is_fetched,
                fetched_at=ch.fetched_at,
                created_at=ch.created_at,
            )
            for ch in chapters
        ],
    )


@router.get("/{book_id}/tasks")
def get_book_tasks(
    book_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Download tasks scoped to a single book."""
    local_user = get_or_create_user_by_sub(db, user)

    link = db.execute(
        select(models.BookUser).where(
            models.BookUser.user_id == local_user.id,
            models.BookUser.book_id == book_id,
            models.BookUser.in_library == True,  # noqa: E712
        )
    ).scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

    tasks = db.scalars(
        select(models.Task)
        .where(
            models.Task.user_id == local_user.id,
            func.json_extract(models.Task.payload, "$.book_id") == book_id,
        )
        .order_by(models.Task.created_at.desc())
    ).all()

    return [
        {
            "id": t.id,
            "type": t.type,
            "status": t.status,
            "book_id": book_id,
            "payload": t.payload,
            "error": t.error,
            "created_at": t.created_at.isoformat(),
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        }
        for t in tasks
    ]


@router.put("/{book_id}/options", response_model=ResponseMsg)
def save_book_options(
    book_id: int,
    body: DownloadOptions,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> ResponseMsg:
    """Persist per-user, per-book download options."""
    local_user = get_or_create_user_by_sub(db, user)

    link = db.execute(
        select(models.BookUser).where(
            models.BookUser.user_id == local_user.id,
            models.BookUser.book_id == book_id,
            models.BookUser.in_library == True,  # noqa: E712
        )
    ).scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

    link.download_options = body.model_dump()
    db.commit()
    return ResponseMsg(ok=True)


@router.delete("/{book_id}/unsubscribe")
def unsubscribe_book(
    book_id: int, user: CurrentUser, db: Session = Depends(get_db)
) -> ResponseMsg:
    """
    Remove the current user's subscription to a book (keeps the book for others).
    """
    local_user = get_or_create_user_by_sub(db, user)

    # Flip in_library = False if the row exists; otherwise nothing to do.
    link = db.execute(
        select(models.BookUser).where(
            models.BookUser.user_id == local_user.id,
            models.BookUser.book_id == book_id,
        )
    ).scalar_one_or_none()

    if link:
        link.in_library = False
        db.commit()

    return ResponseMsg(ok=True)


# TODO: Here there should be some more generate config
@router.post("/{book_id}/download")
async def download_book_now(
    book_id: int,
    user: CurrentUser,
    body: DownloadBookNowBody | None = None,
    db: Session = Depends(get_db),
    arq_pool: ArqRedis = Depends(get_arq_pool),
) -> DownloadBookNowResponse:
    """
    Queue a download/export job and return a task id the client can poll.
    """
    local_user = get_or_create_user_by_sub(db, user)

    # Check that the user is subscribed
    rel = db.execute(
        select(models.BookUser).where(
            models.BookUser.user_id == local_user.id,
            models.BookUser.book_id == book_id,
            models.BookUser.in_library == True,  # noqa: E712
        )
    ).scalar_one_or_none()
    if not rel:
        raise HTTPException(
            status_code=403, detail="The user is not subscribed to this book."
        )

    payload = {"book_id": book_id}

    if body:
        payload |= body.model_dump()

    task = await schedule_task(
        db,
        arq_pool,
        models.TaskType.DOWNLOAD_BOOK,
        local_user.id,
        payload,
    )

    return DownloadBookNowResponse(ok=True, task_id=task.id, task_status=task.status)


@router.post("/{book_id}/send")
async def send_book_to_device(
    book_id: int,
    user: CurrentUser,
    body: DownloadBookNowBody | None = None,
    db: Session = Depends(get_db),
    arq_pool: ArqRedis = Depends(get_arq_pool),
) -> DownloadBookNowResponse:
    """
    Queue a download + auto-send job for a book. The finished EPUB is emailed to
    the user's configured device address.
    """
    local_user = get_or_create_user_by_sub(db, user)

    if not local_user.kindle_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No device email configured. Set your device email in your profile settings.",
        )

    rel = db.execute(
        select(models.BookUser).where(
            models.BookUser.user_id == local_user.id,
            models.BookUser.book_id == book_id,
            models.BookUser.in_library == True,  # noqa: E712
        )
    ).scalar_one_or_none()
    if not rel:
        raise HTTPException(
            status_code=403, detail="The user is not subscribed to this book."
        )

    payload: dict[str, Any] = {"book_id": book_id}
    if body:
        payload |= body.model_dump()

    # Embed the send-on-finish instruction directly in the download payload.
    # The worker checks for `send_by_email` and dispatches the email task automatically.
    payload["send_by_email"] = {
        "recipient_email": local_user.kindle_email,
        "book_path": "",   # worker fills this in after generating the EPUB
        "book_title": "",  # worker fills this in
    }

    task = await schedule_task(
        db,
        arq_pool,
        models.TaskType.DOWNLOAD_BOOK,
        local_user.id,
        payload,
    )

    return DownloadBookNowResponse(ok=True, task_id=task.id, task_status=task.status)


@router.get("/tasks/{task_id}/send_by_email")
async def send_download_by_email(
    task_id: int,
    user: CurrentUser,
    recipient_email: str | None = None,
    db: Session = Depends(get_db),
    arq_pool: ArqRedis = Depends(get_arq_pool),
) -> SendByEmailResponse:
    local_user = get_or_create_user_by_sub(db, user)

    task: models.Task | None = db.get(models.Task, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    if task.type != models.TaskType.DOWNLOAD_BOOK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task is not a book download task",
        )

    if task.status == models.TaskStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_406_NOT_ACCEPTABLE,
            detail=f"Download failed: {task.error or ''}",
        )

    payload = models.DownloadBookTaskPayload.model_validate(task.payload)

    recipient_email = recipient_email or local_user.kindle_email
    if recipient_email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The user has not provided a recipient email address. Please set your kinlde email address",
        )

    new_payload = models.SendBookTaskPayload(
        recipient_email=recipient_email,
        book_path=payload.output_path or str(EPUB_DIR),
        book_title="",
    )

    if (
        task.status == models.TaskStatus.RUNNING
        or task.status == models.TaskStatus.QUEUED
    ):

        payload.send_by_email = new_payload
        task.payload = payload.model_dump()
        db.commit()  # Ensure payload update is saved

        return SendByEmailResponse(ok=True, task_id=task.id, task_status=task.status)

    elif task.status == models.TaskStatus.SUCCEEDED:
        if payload.output_path is None or not Path(payload.output_path).is_file():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="The download file path is not valid",
            )

        email_task = await schedule_task(
            db,
            arq_pool,
            type=models.TaskType.SEND_BOOK,
            user_id=local_user.id,
            payload=new_payload.model_dump(),
        )

        return SendByEmailResponse(
            ok=True, task_id=email_task.id, task_status=email_task.status
        )

    raise RuntimeError("Unreachable")


@router.get("/tasks/{task_id}/download")
def download_book_for_task(
    task_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> FileResponse:
    local_user = get_or_create_user_by_sub(db, user)

    task: models.Task | None = db.get(models.Task, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Enforce ownership
    if task.user_id != local_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    if task.status != models.TaskStatus.SUCCEEDED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task is not finished yet",
        )

    payload = task.payload or {}
    book_id = payload.get("book_id")
    if not book_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No book_id registered for this task",
        )

    output_path = payload.get("output_path")
    if not output_path:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No output file registered for this task",
        )

    path = Path(output_path).resolve()

    # ensure the file is under our expected epub directory
    try:
        path.relative_to(EPUB_DIR)
    except ValueError as e:
        print(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid output path",
        )

    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="File not found"
        )

    # Optional nicer filename based on book title
    book: models.Book | None = db.get(models.Book, book_id)
    if book and book.title:
        safe_title = "".join(
            c for c in book.title if c.isalnum() or c in (" ", "_", "-")
        )
        filename = f"{safe_title or 'book'}-{book.id}.epub"
    else:
        filename = path.name

    return FileResponse(
        path,
        media_type="application/epub+zip",
        filename=filename,
    )
