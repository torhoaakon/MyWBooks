import uuid

import sqlalchemy as sqla
from fastapi import APIRouter, Depends, HTTPException, status
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy.orm import Session

from mywbooks import models
from mywbooks.api.auth import CurrentUser, authx, get_or_create_user_by_sub
from mywbooks.db import get_db

router = APIRouter()

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


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


class Token(BaseModel):
    access_token: str
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

    # 5. Create access token using AuthX
    access_token = authx.create_access_token(
        uid=user.auth_subject, data={"email": user.email}
    )

    return Token(access_token=access_token, token_type="bearer")


@router.post("/set_kindle_email", status_code=201)
async def set_kindle_email(
    kindle_email: EmailStr, user: CurrentUser, db: Session = Depends(get_db)
):
    # TODO: Warning about: adding email to "Approved Personal Document E-mail List" on Amazon
    local_user = get_or_create_user_by_sub(db, user)

    local_user.kindle_email = str(kindle_email)
    db.commit()

    return {"ok": True}
