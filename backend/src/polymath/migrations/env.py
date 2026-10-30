from alembic import context
from sqlalchemy import create_engine, pool

from polymath import (  # noqa: F401
    access_models,
    artifact_models,
    assessment_models,
    budget_models,
    converge_models,
    discovery_models,
    evidence_models,
    explore_collection_models,
    frontier_models,
    full_text_models,
    ingestion_models,
    lineage_models,
    memory_models,
    meta_probe_models,
    meta_research_models,
    models,
    operator_models,
    public_research_models,
    public_resource_models,
    publication_job_models,
    publication_models,
    publishing_models,
    readiness_models,
    scientific_context_models,
    sharing_models,
    visitor_zenodo_models,
)
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
