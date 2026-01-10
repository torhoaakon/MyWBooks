# src/mywbooks/api/auth.py
import os
from functools import lru_cache
from typing import Annotated, Any, TypedDict

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from mywbooks import models

ISSUER = os.getenv("SUPABASE_ISSUER", "").rstrip("/")
AUDIENCE = os.getenv("SUPABASE_AUDIENCE", "authenticated")
JWKS_URL = os.getenv("SUPABASE_JWKS_URL", "").strip()
JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "").strip()  # for HS256 projects

if not ISSUER:
    raise RuntimeError("Missing SUPABASE_ISSUER environment variable.")

if not (JWKS_URL or JWT_SECRET):
    raise RuntimeError(
        "Provide either SUPABASE_JWKS_URL (RS/ES) or SUPABASE_JWT_SECRET (HS256)."
    )

bearer = HTTPBearer(auto_error=True)


class UserClaims(TypedDict, total=False):
    sub: str  # subject (user id)
    email: str
    role: str
    aud: str
    # Unused:
    #  iss: str  # issuer
    #  iat: int  # issued at (epoch seconds)
    #  exp: int  # expiration (epoch seconds)
    #  nbf: int  # not before (epoch seconds)
    #  jti: str  # JWT ID (unique token id)


@lru_cache
def _jwks_client() -> PyJWKClient | None:
    return PyJWKClient(JWKS_URL) if JWKS_URL else None


def _decode_jwt(token: str) -> UserClaims:
    """Decode a Supabase JWT, auto-detecting algorithm from header."""
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
        assert isinstance(res, dict), "Assuming return type is dict"
        return UserClaims(**res)  # type: ignore [typeddict-item]

    if alg in {"RS256", "RS512", "ES256", "ES384"}:
        if not JWKS_URL:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{alg} token but SUPABASE_JWKS_URL not set",
            )
        signing_key = _jwks_client().get_signing_key_from_jwt(token).key  # type: ignore[union-attr]
        res = jwt.decode(
            token,
            signing_key,
            algorithms=[alg],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat"], "verify_signature": True},
        )
        assert isinstance(res, dict), "Assuming return type is dict"
        return UserClaims(**res)  # type: ignore [typeddict-item]

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Unsupported alg: {alg}"
    )


def verify_jwt(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> UserClaims:
    if cred.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token"
        )

    try:
        return _decode_jwt(cred.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token:\n\n {e}"
        )


def get_or_create_user_by_sub(db: Session, claims: UserClaims) -> models.User:
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="JWT missing 'sub' claim")

    # Optional: detect your provider from ISSUER
    provider = "supabase"

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
