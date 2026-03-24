# src/mywbooks/api/auth.py
import os
from functools import lru_cache
from typing import Annotated, Any, TypedDict

import jwt
from authx import AuthX, AuthXConfig
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from mywbooks import models

# --- AuthX Configuration (Local) ---
AUTHX_SECRET_KEY = os.getenv("AUTHX_SECRET_KEY", "change-me-in-production")
authx_config = AuthXConfig(
    JWT_SECRET_KEY=AUTHX_SECRET_KEY,
    JWT_ALGORITHM="HS256",
    JWT_TOKEN_LOCATION=["headers"],
)
authx = AuthX(config=authx_config)


# --- Supabase Configuration (External) ---
ISSUER = os.getenv("SUPABASE_ISSUER", "").rstrip("/")
AUDIENCE = os.getenv("SUPABASE_AUDIENCE", "authenticated")
JWKS_URL = os.getenv("SUPABASE_JWKS_URL", "").strip()
JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "").strip()  # for HS256 projects

# Note: We don't raise RuntimeError anymore, just disable Supabase if envs are missing
SUPABASE_ENABLED = bool(ISSUER and (JWKS_URL or JWT_SECRET))


bearer = HTTPBearer(auto_error=True)


class UserClaims(TypedDict, total=False):
    sub: str
    email: str
    role: str
    aud: str
    provider: str  # Added to distinguish "local" vs "supabase"


@lru_cache
def _jwks_client() -> PyJWKClient | None:
    return PyJWKClient(JWKS_URL) if JWKS_URL else None


def _decode_supabase_jwt(token: str) -> UserClaims:
    """Decode a Supabase JWT, auto-detecting algorithm from header."""
    if not SUPABASE_ENABLED:
        raise ValueError("Supabase authentication is not configured.")

    header = jwt.get_unverified_header(token)
    alg = header.get("alg")

    if alg == "HS256":
        if not JWT_SECRET:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="HS256 token but SUPABASE_JWT_SECRET not set",
            )

        res = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat"], "verify_signature": True},
        )
        return UserClaims(**res, provider="supabase")  # type: ignore

    if alg in {"RS256", "RS512", "ES256", "ES384"}:
        if not JWKS_URL:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{alg} token but SUPABASE_JWKS_URL not set",
            )
        signing_key = _jwks_client().get_signing_key_from_jwt(token).key  # type: ignore
        res = jwt.decode(
            token,
            signing_key,
            algorithms=[alg],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat"], "verify_signature": True},
        )
        return UserClaims(**res, provider="supabase")  # type: ignore

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Unsupported alg: {alg}"
    )


def verify_jwt(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> UserClaims:
    token = cred.credentials

    # 1. Try Local Auth (AuthX)
    try:
        # AuthX.get_payload returns the payload dict if valid, else raises
        payload = authx.get_payload(token)
        # AuthX typically uses 'sub' for the user identifier
        return UserClaims(
            sub=payload.get("sub", ""),
            email=payload.get("email", ""),
            provider="local",
        )
    except Exception:
        # 2. Fall back to Supabase if local fails
        if not SUPABASE_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token (Local auth failed and Supabase is not configured)",
            )
        try:
            return _decode_supabase_jwt(token)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}"
            )


def get_or_create_user_by_sub(db: Session, claims: UserClaims) -> models.User:
    sub = claims.get("sub")
    provider = claims.get("provider", "local")

    if not sub:
        raise HTTPException(status_code=401, detail="JWT missing 'sub' claim")

    u = db.execute(
        select(models.User).where(
            models.User.auth_provider == provider, models.User.auth_subject == sub
        )
    ).scalar_one_or_none()

    if u:
        # keep email fresh if present
        email = claims.get("email")
        if email and u.email != email:
            u.email = email
            db.commit()
        return u

    # First time we see this subject: create a row.
    u = models.User(
        auth_provider=provider,
        auth_subject=sub,
        email=claims.get("email"),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


CurrentUser = Annotated[UserClaims, Depends(verify_jwt)]
