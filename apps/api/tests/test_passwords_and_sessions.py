"""Password hashing and the session table, before anything uses them.

Gate 1 on M5 produced three BLOCKs. Two of them live here, and both are the same
shape: a value the schema permitted and no code could handle.

Every test in this file was written before the code it tests, which is the rule
the M2 gate produced -- a finding with no test is a finding that comes back.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from file_explorer import passwords
from file_explorer.models import session as session_model

# --- the schema is actually there ----------------------------------------


@pytest.mark.invariant
def test_the_migrations_are_committed_not_just_run(session: OrmSession) -> None:
    """Running every migration and committing none of them looks identical in a log.

    It happened. Raising the migration timeouts was written as two statements
    before `context.configure`, which looks equivalent to running them inside
    `context.begin_transaction()` and is not: in SQLAlchemy 2.x the first
    `connection.execute()` implicitly begins a transaction, so alembic's context
    manager no longer owned one, nothing committed, and closing the connection
    rolled the whole upgrade back. Alembic logged "Running upgrade" for all
    three migrations and the database was empty.

    Two hundred tests then failed with `relation "users" does not exist`, which
    names the symptom and not the cause. This names the cause.
    """
    for table in ("users", "nodes", "sessions"):
        exists = session.execute(
            text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}
        ).scalar_one()
        assert exists, f"{table} does not exist: the migrations ran and were not committed"

    # The newest migration on disk, rather than a number typed here. The first
    # version pinned "0003" and went red the day 0004 was added, which is a
    # guard objecting to the thing it was written to allow.
    newest = max(
        p.name.split("_")[0]
        for p in (Path(__file__).resolve().parents[1] / "migrations" / "versions").glob("*.py")
    )
    version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == newest, (
        f"alembic_version says {version!r} and the newest migration on disk is {newest!r}, "
        "so a migration did not stick"
    )


# --- the hash itself ------------------------------------------------------


@pytest.mark.invariant
def test_a_password_verifies_against_its_own_hash() -> None:
    encoded = passwords.hash_password("correct horse battery staple")
    assert passwords.verify_password(encoded, "correct horse battery staple")
    assert not passwords.verify_password(encoded, "correct horse battery stapl")


@pytest.mark.invariant
def test_the_hash_carries_the_parameters_it_was_made_with() -> None:
    """An encoded hash is self-describing, which is why raising the cost is safe.

    If this prefix ever changes, `ck_users_password_hash_is_argon2id` stops
    matching and inserts start failing -- so the constraint and this assertion
    are two halves of the same statement.
    """
    encoded = passwords.hash_password("x")
    assert encoded.startswith("$argon2id$")
    assert f"m={passwords.MEMORY_COST_KIB}" in encoded
    assert f"t={passwords.TIME_COST}" in encoded
    assert f"p={passwords.PARALLELISM}" in encoded


@pytest.mark.invariant
def test_the_parameters_are_the_published_floor_not_the_library_default() -> None:
    """19 MiB, 2 iterations, 1 lane -- OWASP's minimum for argon2id.

    Pinned because the number that matters is the one chosen for a 512 MB
    shared instance, not the one the library ships. A future change should be
    deliberate and should move this line with it.
    """
    assert (passwords.MEMORY_COST_KIB, passwords.TIME_COST, passwords.PARALLELISM) == (19456, 2, 1)


@pytest.mark.invariant
@pytest.mark.parametrize(
    "stored",
    [
        "",
        "hunter2",
        "$argon2id$",
        "$2b$12$abcdefghijklmnopqrstuv",
        "$argon2id$v=19$m=19456,t=2,p=1$bm90YmFzZTY0IQ",
    ],
)
def test_an_unverifiable_stored_hash_is_a_failure_not_an_exception(stored: str) -> None:
    """The BLOCK, as a test.

    `verify()` raises `InvalidHashError` on a value it cannot parse, and
    **`InvalidHashError` descends from `ValueError`, not from `Argon2Error`** --
    so `except VerificationError`, which is the handler anyone writes first,
    does not catch it and the request becomes a 500.

    That mattered because until migration 0003 every row in `users` held an
    empty hash, so the obvious handler would have turned every real login into
    a 500 -- and that 500 is an existence oracle, since an unknown address
    answers 401. The empty-hash branch also returns before any Argon2 work, so
    it is the same oracle in the timing channel.

    The rule this encodes: a stored value the library refuses is an
    authentication failure, because **the caller supplied nothing wrong**.
    """
    assert passwords.verify_password(stored, "anything") is False


@pytest.mark.invariant
def test_needs_rehash_does_not_decide_whether_a_hash_is_usable() -> None:
    assert passwords.needs_rehash(passwords.hash_password("x")) is False
    assert passwords.needs_rehash("not a hash at all") is False


# --- the session token ----------------------------------------------------


@pytest.mark.invariant
def test_the_cookie_value_is_never_what_is_stored() -> None:
    """A read of `sessions` must not yield anything a caller can present."""
    token = session_model.new_token()
    digest = session_model.token_digest(token)

    assert digest == hashlib.sha256(token.encode("utf-8")).digest()
    assert len(digest) == 32
    assert token.encode("utf-8") not in digest
    assert token != digest.hex()


@pytest.mark.invariant
def test_two_tokens_are_never_the_same() -> None:
    tokens = {session_model.new_token() for _ in range(200)}
    csrf = {session_model.new_csrf_token() for _ in range(200)}
    assert len(tokens) == 200
    assert len(csrf) == 200
    # 32 bytes, urlsafe-base64: 43 characters with no padding.
    assert all(len(t) >= 43 for t in tokens)
    assert session_model.TOKEN_BYTES == 32
    assert session_model.CSRF_BYTES == 32


@pytest.mark.invariant
def test_a_session_repr_carries_neither_secret() -> None:
    """A repr ends up in a log, a traceback and a debugger, unasked.

    The token hash is not a credential -- that is the point of storing a hash --
    but a log full of them is a list of who was signed in and when, which is
    more than a `__repr__` should decide to publish. The CSRF token is not a
    credential on its own either, and belongs in a log even less.
    """
    token = session_model.new_token()
    csrf = session_model.new_csrf_token()
    row = session_model.Session(
        token_hash=session_model.token_digest(token),
        user_id=7,
        csrf_token=csrf,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    printed = repr(row)
    assert printed == "<Session user_id=7>"
    assert token not in printed
    assert csrf not in printed
    assert session_model.token_digest(token).hex() not in printed


# --- what the database refuses --------------------------------------------


@pytest.mark.invariant
def test_users_cannot_hold_a_password_hash_that_is_not_one(session: OrmSession) -> None:
    """`<> ''` would not have caught `hunter2`. The constraint is the encoding."""
    for bad in ("", "hunter2", "$2b$12$notargon"):
        with pytest.raises(IntegrityError):
            session.execute(
                text("INSERT INTO users (email, password_hash) VALUES (:e, :h)"),
                {"e": f"bad-{bad!r}@example.com", "h": bad},
            )
        session.rollback()


@pytest.mark.invariant
def test_password_hash_has_no_default_to_fall_back_on(session: OrmSession) -> None:
    """The original defect was the default, not the value anyone wrote."""
    with pytest.raises(IntegrityError):
        session.execute(text("INSERT INTO users (email) VALUES ('nodefault@example.com')"))
    session.rollback()


@pytest.mark.invariant
def test_a_session_cannot_outlive_the_user_it_names(
    session: OrmSession, make_user: Callable[..., int]
) -> None:
    """`ON DELETE CASCADE`, and the reason D-21 is worth the table.

    A signed token for a deleted account keeps verifying until it expires. A
    row does not.
    """
    uid = make_user("doomed@example.com")
    session.execute(
        text(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at) "
            "VALUES (:h, :u, :c, :e)"
        ),
        {
            "h": session_model.token_digest(session_model.new_token()),
            "u": uid,
            "c": session_model.new_csrf_token(),
            "e": datetime.now(UTC) + timedelta(days=1),
        },
    )
    live = session.execute(
        text("SELECT count(*) FROM sessions WHERE user_id = :u"), {"u": uid}
    ).scalar_one()
    assert live == 1

    session.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
    left = session.execute(
        text("SELECT count(*) FROM sessions WHERE user_id = :u"), {"u": uid}
    ).scalar_one()
    assert left == 0, "deleting a user left a live session behind"


@pytest.mark.invariant
def test_a_session_cannot_expire_before_it_begins(
    session: OrmSession, make_user: Callable[..., int]
) -> None:
    uid = make_user("backwards@example.com")
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at) "
                "VALUES (:h, :u, :c, :e)"
            ),
            {
                "h": session_model.token_digest(session_model.new_token()),
                "u": uid,
                "c": session_model.new_csrf_token(),
                "e": datetime.now(UTC) - timedelta(days=1),
            },
        )
    session.rollback()


@pytest.mark.invariant
def test_a_short_csrf_token_is_refused_by_the_database(
    session: OrmSession, make_user: Callable[..., int]
) -> None:
    """The length floor is in the schema, so a bug in the generator cannot slip past it."""
    uid = make_user("shortcsrf@example.com")
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at) "
                "VALUES (:h, :u, :c, :e)"
            ),
            {
                "h": session_model.token_digest(session_model.new_token()),
                "u": uid,
                "c": "short",
                "e": datetime.now(UTC) + timedelta(days=1),
            },
        )
    session.rollback()
