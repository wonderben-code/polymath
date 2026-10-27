from alembic import context
from sqlalchemy import create_engine, pool

from polymath import access_models, budget_models, evidence_models, memory_models, models  # noqa: F401
from polymath.config import Settings
from polymath.database import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=Settings().database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        Settings().database_url.get_secret_value(),
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 3},
        hide_parameters=True,
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
