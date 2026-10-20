"""Central legal publications, acceptances and rights workflow; no tenant data purge."""

from alembic import op
from app.platform_models import (
    LegalAcceptance,
    LegalConfiguration,
    LegalPublication,
    PrivacyRequest,
)
from sqlalchemy import MetaData

revision = "0053"
down_revision = "0052"
branch_labels = depends_on = None
MODELS = (LegalConfiguration, LegalPublication, LegalAcceptance, PrivacyRequest)


def upgrade():
    connection = op.get_bind()
    for model in MODELS:
        model.__table__.to_metadata(MetaData(), schema="public").create(connection, checkfirst=True)


def downgrade():
    connection = op.get_bind()
    for model in reversed(MODELS):
        model.__table__.to_metadata(MetaData(), schema="public").drop(connection, checkfirst=True)
