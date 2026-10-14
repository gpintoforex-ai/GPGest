"""Default supplier category, including local overrides for imported entities."""
import importlib
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
        op.add_column("business_entities", sa.Column("category_id", sa.String(36), nullable=True), schema=schema)
        op.create_foreign_key("business_entity_category", "business_entities", "categories", ["category_id"], ["id"], source_schema=schema, referent_schema=schema)


def downgrade():
    connection = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
        op.drop_constraint("business_entity_category", "business_entities", schema=schema, type_="foreignkey")
        op.drop_column("business_entities", "category_id", schema=schema)
