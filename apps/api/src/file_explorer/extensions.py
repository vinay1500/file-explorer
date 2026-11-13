"""Shared extension instances.

Kept in their own module so models, the app factory and Alembic can each import
them without importing each other.
"""

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Explicit naming so constraint names are deterministic rather than whatever
# the database happens to generate. Without this, Alembic autogenerate produces
# a different name on every machine and migrations stop being reproducible --
# and an unnamed constraint cannot be dropped by name in a later migration.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


db = SQLAlchemy(model_class=Base)
migrate = Migrate()
