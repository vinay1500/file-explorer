"""Alembic environment, driven by the Flask app's configuration.

The database URL comes from the app rather than alembic.ini so there is one
source of truth and no connection string in version control.
"""

from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from flask import current_app
from sqlalchemy import text

config = context.config
if config.config_file_name is not None:
    # `disable_existing_loggers` defaults to True, which sets .disabled on every
    # logger alembic.ini does not name -- the whole `file_explorer` tree and
    # `flask.app` included. A disabled logger drops records before any handler
    # runs, so an in-process `flask db upgrade` silences the application for the
    # life of the process: no retry warnings, no health-check failures, nothing.
    # It cost a day: the move retry loop was running correctly and its log line
    # was being swallowed, so the tests that assert on it saw zero retries.
    fileConfig(config.config_file_name, disable_existing_loggers=False)
logger = logging.getLogger("alembic.env")


def get_engine():
    return current_app.extensions["migrate"].db.engine


def get_metadata():
    return current_app.extensions["migrate"].db.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(get_engine().url).replace("%", "%%"),
        target_metadata=get_metadata(),
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = get_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=get_metadata(),
            # Without this, autogenerate does not notice a column whose type
            # changed, which is a quiet way to lose a constraint.
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            # A migration is not a request and must not inherit a request's
            # budget. `engine_options()` sets `statement_timeout=5s` and
            # `lock_timeout=3s` as connection startup options, because a runaway
            # recursive query on cyclic data would otherwise hold a connection
            # until the process died. Those are the right numbers for a tree
            # query and far too short for DDL: validating a check constraint
            # over a populated table takes as long as the table is long.
            #
            # Raised here rather than in each migration, so a new migration
            # cannot forget. `SET LOCAL` scopes it to this transaction, so no
            # later use of the connection inherits it.
            #
            # **Inside `begin_transaction()`, and that placement is the whole
            # point.** Executing these two statements before `context.configure`
            # looks equivalent and is not: in SQLAlchemy 2.x the first
            # `connection.execute()` implicitly begins a transaction, so
            # alembic's context manager no longer owns one, nothing commits, and
            # closing the connection rolls the whole upgrade back. Every
            # migration logs "Running upgrade" and the schema is empty
            # afterwards -- which is exactly what happened, and what
            # `test_the_migrations_are_committed_not_just_run` now catches.
            connection.execute(text("SET LOCAL statement_timeout = '600s'"))
            connection.execute(text("SET LOCAL lock_timeout = '10s'"))

            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
