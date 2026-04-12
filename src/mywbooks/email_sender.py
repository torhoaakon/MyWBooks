from __future__ import annotations

import os
from dataclasses import dataclass
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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


async def send_ebook_email(
    recipient_email: str,
    ebook_path: Path,
    book_title: str,
) -> None:
    """
    Sends an email with the generated ebook as an attachment asynchronously.
    """

    cfg = get_smtp()

    msg = MIMEMultipart()
    msg["Subject"] = f"Your ebook: {book_title}"
    msg["From"] = cfg.user or ""
    msg["To"] = recipient_email

    # Attach a simple body
    msg.attach(MIMEText("Please find your ebook attached.", "plain"))

    # Attach the ebook
    async with await anyio.open_file(ebook_path, "rb") as f:
        file_content = await f.read()
        part = MIMEApplication(file_content, Name=ebook_path.name)

    part["Content-Disposition"] = f'attachment; filename="{ebook_path.name}"'
    msg.attach(part)

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
