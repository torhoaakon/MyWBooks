from __future__ import annotations

import os
from dataclasses import dataclass
from email.message import EmailMessage
from functools import lru_cache
from pathlib import Path

import aiosmtplib
import anyio


@dataclass
class smtp_settings:
    host: str
    port: int
    user: str
    pswd: str


@lru_cache
def get_smtp() -> smtp_settings:
    host = os.getenv("SMTP_HOST")
    port = os.getenv("SMTP_PORT")
    user = os.getenv("SMTP_USER")
    pswd = os.getenv("SMTP_PASSWORD")

    if not all([host, port, user, pswd]):
        raise ValueError("SMTP settings are not configured.")

    if not port.isdigit():  # type: ignore
        raise ValueError("SMTP_PORT is not intager.")

    return smtp_settings(host, int(port), user, pswd)  # type: ignore


def build_ebook_email(
    *,
    sender: str,
    recipient_email: str,
    file_content: bytes,
    filename: str,
    book_title: str,
) -> EmailMessage:
    """
    Build the ebook email. EmailMessage (default policy) encodes non-ASCII
    filenames per RFC 2231 (filename*=utf-8''...). Hand-writing the
    Content-Disposition header made Python RFC 2047-encode the whole header,
    which Amazon's Send to Kindle silently rejects.
    """
    msg = EmailMessage()
    msg["Subject"] = f"Your ebook: {book_title}"
    msg["From"] = sender
    msg["To"] = recipient_email
    msg.set_content("Please find your ebook attached.")
    msg.add_attachment(
        file_content,
        maintype="application",
        subtype="epub+zip",
        filename=filename,
    )
    return msg


async def send_ebook_email(
    recipient_email: str,
    ebook_path: Path,
    book_title: str,
) -> None:
    """
    Sends an email with the generated ebook as an attachment asynchronously.
    """

    cfg = get_smtp()

    async with await anyio.open_file(ebook_path, "rb") as f:
        file_content = await f.read()

    msg = build_ebook_email(
        sender=cfg.user,
        recipient_email=recipient_email,
        file_content=file_content,
        filename=ebook_path.name,
        book_title=book_title,
    )

    # Send the email asynchronously
    await aiosmtplib.send(
        msg,
        hostname=cfg.host,
        port=cfg.port,
        username=cfg.user,
        password=cfg.pswd,
        use_tls=False,
        start_tls=True,
    )
