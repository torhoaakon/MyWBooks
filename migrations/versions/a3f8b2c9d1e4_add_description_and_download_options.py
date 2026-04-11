"""Add description to books and download_options to book_users

Revision ID: a3f8b2c9d1e4
Revises: f5510c895367
Create Date: 2026-04-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f8b2c9d1e4'
down_revision: Union[str, Sequence[str], None] = 'f5510c895367'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('books', sa.Column('description', sa.Text(), nullable=True))
    op.add_column('book_users', sa.Column('download_options', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('books', 'description')
    op.drop_column('book_users', 'download_options')
