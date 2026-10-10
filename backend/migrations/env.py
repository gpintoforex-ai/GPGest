from alembic import context
from app.db import engine
from app.models import Base
from app.platform_models import PlatformBase

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=[Base.metadata, PlatformBase.metadata])
    with context.begin_transaction():
        context.run_migrations()
