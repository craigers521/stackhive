"""ztp artifact purged flag

Revision ID: c4e81f2b7a03
Revises: 2ae30237f0a2
Create Date: 2026-08-19 14:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4e81f2b7a03'
down_revision = '2ae30237f0a2'
branch_labels = None
depends_on = None


def upgrade():
    """Add the ZTPProvision.artifact_purged bookkeeping column."""
    op.add_column('ztp_provisions', sa.Column('artifact_purged', sa.Boolean(), server_default='0', nullable=False))


def downgrade():
    """Remove the ZTPProvision.artifact_purged column."""
    op.drop_column('ztp_provisions', 'artifact_purged')
