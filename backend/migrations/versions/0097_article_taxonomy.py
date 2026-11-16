"""Tabelas de famílias, subfamílias e marcas; preserva a classificação das fichas."""

import importlib
import json
from uuid import uuid4

from alembic import op
from app.models import ArticleBrand, ArticleFamily, ArticleSubfamily, StockArticle
from sqlalchemy import select

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None

TABLES = (ArticleFamily, ArticleBrand, ArticleSubfamily)


def upgrade():
    connection = op.get_bind()
    schemas = importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)
    try:
        for schema in schemas:
            bind = connection.execution_options(schema_translate_map={None: schema})
            for model in TABLES:
                model.__table__.create(bind, checkfirst=True)
            known = {m: {((r.family_id, r.name.casefold()) if m == ArticleSubfamily else r.name.casefold()): r.id
                         for r in bind.execute(select(m.__table__))} for m in TABLES}
            for raw in bind.execute(select(StockArticle.data)).scalars():
                data = json.loads(raw)
                family_id = None
                for key, model in (("family", ArticleFamily), ("brand", ArticleBrand), ("category", ArticleSubfamily)):
                    name = str(data.get(key) or "").strip()
                    if not name or (key == "category" and not family_id):
                        continue
                    token = (family_id, name.casefold()) if key == "category" else name.casefold()
                    if token not in known[model]:
                        identifier = str(uuid4())
                        bind.execute(model.__table__.insert().values(
                            id=identifier, name=name, active=True,
                            **({"family_id": family_id} if key == "category" else {})))
                        known[model][token] = identifier
                    if key == "family":
                        family_id = known[model][token]
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    schemas = importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)
    try:
        for schema in schemas:
            bind = connection.execution_options(schema_translate_map={None: schema})
            for model in reversed(TABLES):
                model.__table__.drop(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
