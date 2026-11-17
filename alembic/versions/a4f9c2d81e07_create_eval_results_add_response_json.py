"""create eval_results and add runs.response_json

Revision ID: a4f9c2d81e07
Revises: 7e23cf1be135
Create Date: 2026-11-17 22:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4f9c2d81e07'
down_revision: Union[str, Sequence[str], None] = '7e23cf1be135'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('eval_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('git_sha', sa.String(), nullable=False),
    sa.Column('case_id', sa.String(), nullable=False),
    sa.Column('metric', sa.String(), nullable=False),
    sa.Column('passed', sa.Integer(), nullable=False),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.add_column('runs', sa.Column('response_json', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('runs', 'response_json')
    op.drop_table('eval_results')
