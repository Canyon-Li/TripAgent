"""create capabilities (Capability Registry, §7.4 / §9 DDL)

Revision ID: c5e01a4b7d02
Revises: a4f9c2d81e07
Create Date: 2026-11-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5e01a4b7d02'
down_revision: Union[str, Sequence[str], None] = 'a4f9c2d81e07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('capabilities',
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('version', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),            # tool | solver | verifier | llm_adapter
    sa.Column('input_schema', sa.String(), nullable=False),
    sa.Column('output_schema', sa.String(), nullable=False),
    sa.Column('endpoint', sa.String(), nullable=False),
    sa.Column('healthcheck', sa.String(), nullable=False),
    sa.Column('cost_estimate', sa.Float(), nullable=True),
    sa.Column('tags', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False, server_default='active'),
    sa.Column('traffic_weight', sa.Integer(), nullable=False, server_default='100'),
    sa.PrimaryKeyConstraint('name', 'version')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('capabilities')
