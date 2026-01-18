from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import lru_cache
from pathlib import Path


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
        raise ValueError("jP settings are not configured.")

    if not port.isdigit():  # type: ignore
        raise ValueError("SMTP_PORT is not intager.")

    return smtp_settings(host, int(port), user, pswd)  # type: ignore


def send_ebook_email(
    recipient_email: str,
    ebook_path: Path,
    book_title: str,
) -> None:
    """
    Sends an email with the generated ebook as an attachment.
    """

    cfg = get_smtp()

    msg = MIMEMultipart()
    msg["Subject"] = f"Your ebook: {book_title}"
    msg["From"] = cfg.user or ""
    msg["To"] = recipient_email

    # Attach a simple body
    msg.attach(MIMEText("Please find your ebook attached.", "plain"))

    # Attach the ebook
    with open(ebook_path, "rb") as f:
        part = MIMEApplication(f.read(), Name=ebook_path.name)
    part["Content-Disposition"] = f'attachment; filename="{ebook_path.name}"'
    msg.attach(part)

    # Send the email
    with smtplib.SMTP(cfg.host, cfg.port) as server:
        server.starttls()
        server.login(cfg.user, cfg.pswd)
        server.send_message(msg)
