"""Ask the managed database about itself, and print nothing that identifies it.

Run by `.github/workflows/db-facts.yml`, by hand only. This is the one thing in
the repository that connects to the deployed database, so three rules hold it:

  * every statement is read-only -- `SHOW`, and a `SELECT` against `pg_database`;
  * the connection is built from `engine_options()` rather than a copy of it, so
    what this proves is what the application will actually send;
  * nothing reaches stdout without passing through `redact`.

The third rule is the one worth explaining. This repository is public and so are
its workflow logs. A failed connection prints the host it could not reach, and
the host is the one part of the URL that names the project. GitHub masks a
secret's exact value in a log; it does not mask a substring of one, and the
hostname is a substring. So the hostname, the role and the password are stripped
from every line this writes, including the lines that come out of a traceback.

Three questions it exists to answer:

  Q-5  what `datcollate` is, because both CI service containers initdb with
       `--locale=C` and therefore prove nothing about ordering on a database
       whose cluster default is a real locale;
  Q-6  whether this endpoint accepts the startup options the engine sends --
       the pooled endpoint is PgBouncer-shaped and may not;
  and whether the thing is reachable at all from outside a browser.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

# `file_explorer.config` reads DATABASE_URL in a class body, so the import below
# raises KeyError without one. That is the right behaviour for the application --
# a deployment that forgot its database should not start -- and here it only
# means the value has to be in place before the import rather than after it. The
# placeholder keeps the import working when the secret is missing, so `main` can
# say so plainly instead of dying in a traceback.
os.environ.setdefault("DATABASE_URL", os.environ.get("NEON_DATABASE_URL", "unset"))

import psycopg  # noqa: E402

from file_explorer import limits  # noqa: E402
from file_explorer.config import engine_options  # noqa: E402

# Everything the URL carries that must never appear in a public log. Built from
# the URL itself rather than guessed, plus two patterns for the shapes a
# provider's hostname takes, so a redaction still happens if the parse fails.
GENERIC = (
    re.compile(r"ep-[a-z0-9-]+", re.IGNORECASE),
    re.compile(r"[a-z0-9-]+\.[a-z0-9-]+\.(?:aws|azure|gcp)\.neon\.tech", re.IGNORECASE),
)

QUERIES = (
    ("server version", "SELECT current_setting('server_version')"),
    ("version number", "SELECT current_setting('server_version_num')"),
    ("datcollate", "SELECT datcollate FROM pg_database WHERE datname = current_database()"),
    ("datctype", "SELECT datctype FROM pg_database WHERE datname = current_database()"),
    (
        "encoding",
        "SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname = current_database()",
    ),
    (
        "collation provider",
        "SELECT datlocprovider FROM pg_database WHERE datname = current_database()",
    ),
    ("max_connections", "SELECT current_setting('max_connections')"),
    ("isolation level", "SELECT current_setting('default_transaction_isolation')"),
    ("statement_timeout", "SELECT current_setting('statement_timeout')"),
    ("lock_timeout", "SELECT current_setting('lock_timeout')"),
    ("idle in transaction", "SELECT current_setting('idle_in_transaction_session_timeout')"),
    ("current database", "SELECT current_database()"),
)


def build_redactor(url: str):
    """Return a function that strips every identifying part of `url` from a string."""
    parts = urlsplit(url)
    # Longest first, so replacing the role does not leave a fragment of the
    # hostname behind when the two share a prefix.
    secrets = sorted(
        (s for s in (parts.password, parts.username, parts.hostname) if s),
        key=len,
        reverse=True,
    )
    # Deliberately not redacted: the region. It is in `docs/ARCHITECTURE.md`
    # already, it identifies nothing, and `ep-...` -- the part that does name
    # the project -- is covered by GENERIC even when an error prints the first
    # host label on its own.

    def redact(text: str) -> str:
        for secret in secrets:
            text = text.replace(secret, "[redacted]")
        for pattern in GENERIC:
            text = pattern.sub("[redacted]", text)
        return text

    return redact


def options_string() -> str:
    """The exact `options` the application's engine sends, not a copy of it."""
    return str(
        engine_options(
            {
                "POOL_SIZE": 5,
                "MAX_OVERFLOW": 5,
                "STATEMENT_TIMEOUT_MS": limits.STATEMENT_TIMEOUT_MS,
                "LOCK_TIMEOUT_MS": limits.LOCK_TIMEOUT_MS,
                "IDLE_IN_TRANSACTION_TIMEOUT_MS": limits.IDLE_IN_TRANSACTION_TIMEOUT_MS,
            }
        )["connect_args"]["options"]
    )


def ask(url: str, options: str | None) -> list[tuple[str, str]]:
    kwargs = {"options": options} if options else {}
    with psycopg.connect(url, connect_timeout=15, **kwargs) as conn:
        conn.read_only = True
        rows = []
        with conn.cursor() as cur:
            for label, sql in QUERIES:
                cur.execute(sql)
                result = cur.fetchone()
                rows.append((label, "none" if result is None else str(result[0])))
        return rows


def main() -> int:
    url = os.environ.get("NEON_DATABASE_URL", "")
    if not url:
        print("NEON_DATABASE_URL is not set -- add it as a repository secret")
        return 2

    redact = build_redactor(url)
    parts = urlsplit(url)
    host = parts.hostname or ""
    print(f"endpoint kind     {'pooled (-pooler)' if '-pooler' in host else 'direct'}")
    print(f"region            {host.split('.')[1] if host.count('.') > 1 else 'unknown'}")
    print(f"query parameters  {parts.query or 'none'}")
    print()

    options = options_string()
    print(f"startup options   {options}")
    try:
        rows = ask(url, options)
        accepted = True
    except Exception as exc:  # noqa: BLE001 -- the message is the finding
        print(f"  failed with the options attached: {redact(f'{type(exc).__name__}: {exc}')}")
        print("  retrying without them, to tell a rejected option from an unreachable host")
        try:
            rows = ask(url, None)
        except Exception as second:  # noqa: BLE001
            print(f"  unreachable without them too: {redact(f'{type(second).__name__}: {second}')}")
            return 1
        accepted = False
    else:
        print("  accepted")
    print()

    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label.ljust(width)}  {redact(value)}")

    print()
    if not accepted:
        print("Q-6 ANSWERED: this endpoint rejects the engine's startup options.")
        print("  The timeouts would have to move to a per-session SET or to the role default.")
    collation = dict(rows).get("datcollate", "")
    if collation in ("C", "C.UTF-8", "POSIX"):
        print(f"Q-5 ANSWERED: the cluster default is {collation}, which already is byte order.")
    else:
        print(f"Q-5 ANSWERED: the cluster default is {collation}, which is NOT byte order.")
        print("  Migration 0002's pinned indexes are the only thing making ordering stable,")
        print("  and nothing in CI has ever exercised that -- see task M1.12.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
