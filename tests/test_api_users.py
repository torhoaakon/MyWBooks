# tests/test_api_users.py
import pytest
from sqlalchemy import select
from mywbooks import models

def test_register_user_success(client, db_session):
    email = "newuser@example.com"
    password = "securepassword123"
    
    resp = client.post("/api/user/register", json={"email": email, "password": password})
    
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == email
    assert "id" in data
    
    # Verify in DB
    user = db_session.execute(
        select(models.User).where(models.User.email == email)
    ).scalar_one()
    assert user.auth_provider == "local"
    assert user.hashed_password is not None
    assert user.hashed_password != password  # Should be hashed

def test_register_user_already_exists(client, db_session):
    email = "existing@example.com"
    user = models.User(email=email, auth_provider="local", auth_subject="sub123")
    db_session.add(user)
    db_session.commit()
    
    resp = client.post("/api/user/register", json={"email": email, "password": "password123"})
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]

def test_login_user_success(client, db_session):
    email = "login-test@example.com"
    password = "mypassword"
    
    # 1. Register first
    client.post("/api/user/register", json={"email": email, "password": password})
    
    # 2. Login
    resp = client.post("/api/user/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"

def test_login_user_not_found(client):
    resp = client.post("/api/user/login", json={"email": "nonexistent@example.com", "password": "any"})
    assert resp.status_code == 404
    assert "register first" in resp.json()["detail"]

def test_login_user_wrong_password(client, db_session):
    email = "wrong-pass@example.com"
    client.post("/api/user/register", json={"email": email, "password": "correct-password"})
    
    resp = client.post("/api/user/login", json={"email": email, "password": "wrong-password"})
    assert resp.status_code == 401
    assert "Incorrect password" in resp.json()["detail"]

def test_refresh_token_returns_new_access_token(client, db_session):
    email = "refresh-test@example.com"
    client.post("/api/user/register", json={"email": email, "password": "password"})
    login_resp = client.post("/api/user/login", json={"email": email, "password": "password"})
    refresh_token = login_resp.json()["refresh_token"]

    resp = client.post("/api/user/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    # Should not include a refresh token — this endpoint only issues access tokens
    assert "refresh_token" not in data


def test_refresh_token_invalid(client):
    resp = client.post("/api/user/refresh", json={"refresh_token": "not-a-valid-token"})
    assert resp.status_code == 401


def test_refresh_token_new_access_token_works(client, db_session):
    """Access token issued via /refresh is accepted by protected endpoints."""
    email = "refresh-use-test@example.com"
    client.post("/api/user/register", json={"email": email, "password": "password"})
    login_resp = client.post("/api/user/login", json={"email": email, "password": "password"})
    refresh_token = login_resp.json()["refresh_token"]

    new_token = client.post("/api/user/refresh", json={"refresh_token": refresh_token}).json()["access_token"]

    resp = client.get("/api/user/profile", headers={"Authorization": f"Bearer {new_token}"})
    assert resp.status_code == 200
    assert "id" in resp.json()


def test_protected_route_with_local_token(client):
    # 1. Register and Login to get token
    email = "auth-test@example.com"
    client.post("/api/user/register", json={"email": email, "password": "password"})
    login_resp = client.post("/api/user/login", json={"email": email, "password": "password"})
    token = login_resp.json()["access_token"]
    
    # 2. Call a protected route (e.g., /api/user/profile)
    resp = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert "id" in resp.json()
