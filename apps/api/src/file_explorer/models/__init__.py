"""Model package.

Every model is imported here so that `import file_explorer.models` is enough
for SQLAlchemy to have seen every table. Alembic autogenerate silently misses
tables whose modules were never imported.
"""

from file_explorer.models.node import MAX_NAME_LENGTH, Node, NodeType
from file_explorer.models.session import Session
from file_explorer.models.user import MAX_EMAIL_LENGTH, User

__all__ = [
    "MAX_EMAIL_LENGTH",
    "MAX_NAME_LENGTH",
    "Node",
    "NodeType",
    "Session",
    "User",
]
