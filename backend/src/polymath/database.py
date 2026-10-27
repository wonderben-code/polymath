"""Database creation is explicit; importing the application never connects."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase

from polymath.config import Settings


class Base(DeclarativeBase):
    pass


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
        hide_parameters=True,
    )
