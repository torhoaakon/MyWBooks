from email import message_from_bytes, policy

from mywbooks.email_sender import build_ebook_email


def _build(filename: str, title: str = "Amelia Thornheart"):
    return build_ebook_email(
        sender="sender@example.com",
        recipient_email="kindle@kindle.com",
        file_content=b"PK fake epub",
        filename=filename,
        book_title=title,
    )


def test_non_ascii_filename_is_rfc2231_encoded():
    name = "Amelia Thornheart - Ch. 168\u2013170.epub"
    raw = _build(name).as_bytes()

    disposition = next(
        h
        for h in raw.decode("ascii").splitlines()
        if h.startswith("Content-Disposition")
    )
    # The header itself must stay plain: "attachment;" readable, not =?utf-8?...
    assert disposition.startswith("Content-Disposition: attachment;")
    assert (
        "=?utf-8?" not in raw.decode("ascii").split("Content-Disposition", 1)[1][:200]
    )

    parsed = message_from_bytes(raw, policy=policy.default)
    [att] = list(parsed.iter_attachments())
    assert att.get_filename() == name
    assert att.get_content_type() == "application/epub+zip"
    assert att.get_content_disposition() == "attachment"
    assert att.get_content() == b"PK fake epub"


def test_ascii_filename_and_subject():
    raw = _build("Amelia Thornheart - Ch. 167.epub").as_bytes()
    parsed = message_from_bytes(raw, policy=policy.default)
    assert parsed["Subject"] == "Your ebook: Amelia Thornheart"
    [att] = list(parsed.iter_attachments())
    assert att.get_filename() == "Amelia Thornheart - Ch. 167.epub"
