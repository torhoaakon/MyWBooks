"""Add tags to books

Revision ID: c7e1a2b3d4f5
Revises: a3f8b2c9d1e4
Create Date: 2026-04-11 00:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e1a2b3d4f5'
down_revision: Union[str, Sequence[str], None] = 'a3f8b2c9d1e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('books', sa.Column('tags', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('books', 'tags')
