from fastapi import APIRouter, Depends
from pydantic import EmailStr
from sqlalchemy.orm import Session

from mywbooks.api.auth import CurrentUser, get_or_create_user_by_sub
from mywbooks.db import get_db

router = APIRouter()


# ####
# ## Routes
# ####


@router.post("/set_kindle_email", status_code=201)
async def add_royalroad_book(
    kindle_email: EmailStr, user: CurrentUser, db: Session = Depends(get_db)
):

    # TODO: Warning about: adding email to "Approved Personal Document E-mail List" on Amazon

    local_user = get_or_create_user_by_sub(db, user)

    local_user.kindle_email = str(kindle_email)
    db.commit()
