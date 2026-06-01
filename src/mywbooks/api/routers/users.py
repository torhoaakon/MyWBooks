import uuid

import sqlalchemy as sqla
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from authx.schema import RequestToken
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy.orm import Session

from mywbooks import models
from mywbooks.api.auth import CurrentUser, authx, get_or_create_user_by_sub
from mywbooks.db import get_db

router = APIRouter()

ph = PasswordHasher()


def get_password_hash(password: str) -> str:
    return ph.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        ph.verify(hashed_password, plain_password)
        return True
    except VerifyMismatchError:
        return False


# ####
# ## Schemas
# ####


class UserRegisterBody(BaseModel):
    email: EmailStr
    password: str


class UserLoginBody(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str


class DeviceConfig(BaseModel):
    kindle_email: EmailStr | None = None
    sender_email: str | None = None


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


# ####
# ## Routes
# ####


@router.post("/register", response_model=UserOut, status_code=201)
async def register_user(
    body: UserRegisterBody, db: Session = Depends(get_db)
) -> UserOut:
    # Check if user already exists
    existing_user = db.execute(
        sqla.select(models.User).where(models.User.email == body.email)
    ).scalar_one_or_none()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists",
        )

    # Create new local user
    hashed_password = get_password_hash(body.password)
    new_user = models.User(
        email=body.email,
        hashed_password=hashed_password,
        auth_provider="local",
        auth_subject=str(uuid.uuid4()),  # Generate a stable, opaque UUID
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return UserOut.model_validate(new_user)


@router.post("/login", response_model=Token)
async def login_user(body: UserLoginBody, db: Session = Depends(get_db)) -> Token:
    # 1. Find user by email
    user = db.execute(
        sqla.select(models.User).where(models.User.email == body.email)
    ).scalar_one_or_none()

    # 2. Case: User doesn't exist at all
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found with this email. Please register first.",
        )

    # 3. Case: User exists but not for local provider (e.g., Supabase/Google only)
    if user.auth_provider != "local" or not user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account uses an external provider (Google/GitHub). Please sign in with that provider or create a local password.",
        )

    # 4. Verify password
    if not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password. Please try again.",
        )

    # 5. Create access + refresh tokens
    access_token = authx.create_access_token(
        uid=user.auth_subject, data={"email": user.email}
    )
    refresh_token = authx.create_refresh_token(uid=user.auth_subject)

    return Token(access_token=access_token, refresh_token=refresh_token, token_type="bearer")


class RefreshBody(BaseModel):
    refresh_token: str


class AccessToken(BaseModel):
    access_token: str
    token_type: str


@router.post("/refresh", response_model=AccessToken)
async def refresh_access_token(body: RefreshBody, db: Session = Depends(get_db)) -> AccessToken:
    """Exchange a valid refresh token for a new access token."""
    try:
        req_token = RequestToken(token=body.refresh_token, type="refresh", location="headers")
        payload = authx.verify_token(req_token, verify_type=True)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Verify the subject still exists in our DB
    user = db.execute(
        sqla.select(models.User).where(
            models.User.auth_provider == "local",
            models.User.auth_subject == payload.sub,
        )
    ).scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    access_token = authx.create_access_token(
        uid=user.auth_subject, data={"email": user.email}
    )
    return AccessToken(access_token=access_token, token_type="bearer")


@router.post("/set_kindle_email", status_code=201)
async def set_kindle_email(
    kindle_email: EmailStr, user: CurrentUser, db: Session = Depends(get_db)
):
    # TODO: Warning about: adding email to "Approved Personal Document E-mail List" on Amazon
    local_user = get_or_create_user_by_sub(db, user)

    local_user.kindle_email = str(kindle_email)
    db.commit()

    return {"ok": True}


@router.get("/profile", response_model=UserOut)
async def get_user_profile(
    user: CurrentUser, db: Session = Depends(get_db)
) -> UserOut:
    local_user = get_or_create_user_by_sub(db, user)
    return UserOut.model_validate(local_user)


@router.get("/device", response_model=DeviceConfig)
async def get_device_config(
    user: CurrentUser, db: Session = Depends(get_db)
) -> DeviceConfig:
    """Return the current user's device delivery configuration."""
    import os
    local_user = get_or_create_user_by_sub(db, user)
    return DeviceConfig(
        kindle_email=local_user.kindle_email,
        sender_email=os.getenv("SMTP_USER") or None,
    )


@router.put("/device", response_model=DeviceConfig)
async def set_device_config(
    body: DeviceConfig, user: CurrentUser, db: Session = Depends(get_db)
) -> DeviceConfig:
    """Set (or clear) the user's device delivery email."""
    local_user = get_or_create_user_by_sub(db, user)
    local_user.kindle_email = body.kindle_email
    db.commit()
    return DeviceConfig(kindle_email=local_user.kindle_email)
