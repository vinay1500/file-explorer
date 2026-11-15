"""Every bound in one place.

These were spread across three modules and duplicated inside
`SQLALCHEMY_ENGINE_OPTIONS`, which re-read the environment instead of the
constants directly above it -- so setting `Config.STATEMENT_TIMEOUT_MS` in code
changed nothing and the engine quietly kept the old value.

Gate 1 recorded it as "four config values are read by nothing". The fix is not
to read them in four more places; it is to have one definition that the config,
the service and the validator all point at.

Nothing here imports anything from the package, so it can be imported from
anywhere without a cycle.
"""

from __future__ import annotations

# I7. A tree with no depth limit is a denial-of-service vector: a script can
# create 100,000 nested folders through the normal API and any recursive query
# over that chain then exhausts the connection.
#
# It is also what keeps the cycle check sound -- both recursive walks stop at
# this value, so a tree deeper than it makes `ancestry` return a partial chain
# and `is_ancestor_of` miss a real ancestor. See book chapter 9.
MAX_TREE_DEPTH = 64

# A per-owner ceiling, so one account cannot fill the database. Enforced in
# `create` rather than by the database, because a counting constraint would
# have to serialise every insert for that owner.
MAX_NODES_PER_USER = 50_000

# Pagination. The default is what a caller gets for not asking; the maximum is
# what they get for asking for everything.
DEFAULT_PAGE = 50
MAX_PAGE = 200

# The `name` column is VARCHAR(255), and PostgreSQL counts characters rather
# than bytes. Checked after NFC, because normalising can change the length.
MAX_NAME_LENGTH = 255

# Statement and lock budgets, in milliseconds.
#
# lock_timeout below statement_timeout on purpose: waiting on someone else's
# lock should give up sooner than doing real work.
STATEMENT_TIMEOUT_MS = 5000
LOCK_TIMEOUT_MS = 3000
IDLE_IN_TRANSACTION_TIMEOUT_MS = 10000
