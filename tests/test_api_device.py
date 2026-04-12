# tests/test_api_device.py
"""Tests for GET/PUT /user/device endpoints."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mywbooks import models


def test_get_device_config_no_email(client, db_session):
    user = db_session.execute(
        select(models.User).where(models.User.auth_subject == "test-user-sub-123")
    ).scalar_one()
    user.kindle_email = None
    db_session.commit()

    resp = client.get("/api/user/device")
    assert resp.status_code == 200
    assert resp.json()["kindle_email"] is None


def test_set_and_get_device_email(client, db_session):
    resp = client.put("/api/user/device", json={"kindle_email": "mydevice@kindle.com"})
    assert resp.status_code == 200
    assert resp.json()["kindle_email"] == "mydevice@kindle.com"

    resp2 = client.get("/api/user/device")
    assert resp2.json()["kindle_email"] == "mydevice@kindle.com"


def test_clear_device_email(client, db_session):
    # Set first
    client.put("/api/user/device", json={"kindle_email": "set@kindle.com"})

    # Clear
    resp = client.put("/api/user/device", json={"kindle_email": None})
    assert resp.status_code == 200
    assert resp.json()["kindle_email"] is None
