"""Who is making this request, and whether they asked for it on purpose.

Two questions, one place, because every route already calls one function to ask
the first and the second has to be answered for the same reason at the same
moment.

## The seam did what it was built for

Until M5 this module returned a configured id and read no cookie. The whole
point of that shape was that **M5 changes the body of one function** rather than
threading an actor through every route -- which is how a service ends up with
two or three routes that forgot, and those are exactly the ones that let one
user read another's tree.

It worked, and the diff is this file plus the fixtures. No route changed.

## `DEV_ACTOR_ID` is gone, and its deletion is part of this change

Gate 1 found it as a BLOCK, independently, in all three dimensions. Both
plausible ways of keeping it fail open: read the config first and every request
is user 1 regardless of who signed in; read it as a fallback and every
*unauthenticated* request is. `nodes` has no `created_by` and no audit table, so
a row created either way is byte-identical to one user 1 made -- it cannot be
found afterwards, so it cannot be cleaned up.

What made it likely rather than hypothetical: **every HTTP test authenticated by
assigning `app.config["DEV_ACTOR_ID"]`**. Rewriting this function breaks all of
them at once, and the one-line shim that makes them pass again *is* the bypass.
So the setting is deleted in the same commit, from `config.py`, from
`docker-compose.yml` -- which defaulted it to `1` under a comment saying the app
had no default -- and from `.env.example`. There is no flag and no `app.debug`
guard. Tests mint a real session.

## Why authentication runs in `before_request`

`@blp.arguments` is the outermost decorator on every mutating route, so webargs
validated the body before the view's first line ran. An unauthenticated caller
sending a bad body got **422, not 401** -- which also told them the schema.
Moving the check into a `before_request` puts it ahead of routing's own work for
every route at once, and `require_actor()` then reads the answer.

## What choosing a session row removed

Gate 1 enumerated seven ways a token-reading `require_actor()` could return an
id it should not: an unverified peek at claims, a raised leeway, a token for a
deleted user, a subject spelled `1_0` (which `int()` reads as 10), a non-numeric
subject, a refresh token accepted on a resource route, and a token signed by
some other service over the same key.

**Six of the seven do not exist here**, because nothing parses a
caller-influenced string into an id: the actor comes out of a `bigint` column
through a foreign key. The seventh -- a session whose user has been deleted --
is closed by `ON DELETE CASCADE`. That is the clearest argument for D-21 that
the gate produced, and it was not the argument anyone started with.
"""

from __future__ import annotations

import hmac

from flask import Flask, Response, g, request
from werkzeug.datastructures import WWWAuthenticate
from werkzeug.exceptions import Forbidden, Unauthorized

from file_explorer.extensions import db
from file_explorer.services.sessions import SessionService

#: The cookie the session token travels in. `__Host-` is not used: it forbids
#: `Path`, which the refresh route would want, and it is rejected outright over
#: plain HTTP, which breaks the Compose stack for no gain over `Secure`.
SESSION_COOKIE = "fe_session"

#: Where the page echoes the CSRF token back. A header rather than a form field
#: because every request this API takes is JSON, and a header cannot be set by
#: a cross-origin form post.
CSRF_HEADER = "X-CSRF-Token"

#: Methods that cannot change anything, and therefore need no CSRF token.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Routes reachable without a session. Everything else requires one.
#:
#: Matched on the **rule** rather than on the endpoint name, because an
#: endpoint name is derived from a view function or a `MethodView` subclass and
#: changes when either is renamed -- silently, and in the direction that opens
#: a route. The rule is the thing a reviewer can read off the URL.
#:
#: An allowlist, and short on purpose. The failure it prevents is a route added
#: later that nobody remembers to protect: the default has to be
#: "authenticated" so that forgetting is a 401 rather than a leak.
PUBLIC_RULES = frozenset(
    {
        "/api/v1/health",
        # Sign in and sign up, which by definition cannot require a session.
        # `logout` and `me` are under the same prefix and both require one.
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        # flask-smorest serves the generated document here.
        "/api/openapi.json",
        "/static/<path:filename>",
    }
)


def unauthenticated() -> Unauthorized:
    """A 401 that says how to authenticate and nothing about this deployment.

    RFC 9110 requires a 401 to carry `WWW-Authenticate`. The scheme is named so
    a client knows what to do; the description says nothing else, because an
    earlier version named the missing setting and that told an unauthenticated
    caller about the deployment's configuration.
    """
    challenge = WWWAuthenticate("Cookie")
    challenge.realm = "file-explorer"
    return Unauthorized("Authentication is required.", www_authenticate=challenge)


def authenticate() -> None:
    """Resolve the session before anything else looks at the request.

    Registered as a `before_request`, so it runs ahead of body validation and a
    missing session is a 401 rather than a 422 about a field the caller was
    never going to be allowed to send.
    """
    g.actor_id = None
    g.csrf_token = None

    rule = request.url_rule
    if rule is None or rule.rule in PUBLIC_RULES:
        return

    service = SessionService(db.session)
    resolved = service.resolve(request.cookies.get(SESSION_COOKIE, ""))
    if resolved is None:
        # An expired row may have been deleted while resolving it. Committing
        # here keeps that tidy-up even though the request is about to fail.
        db.session.commit()
        raise unauthenticated()

    if request.method not in SAFE_METHODS:
        supplied = request.headers.get(CSRF_HEADER, "")
        # Constant time, and `hmac.compare_digest` rather than `==` -- the
        # comparison is against a value an attacker can guess at repeatedly.
        if not supplied or not hmac.compare_digest(supplied, resolved.csrf_token):
            db.session.commit()
            raise Forbidden("A valid CSRF token is required for this request.")

    g.actor_id = resolved.actor_id
    g.csrf_token = resolved.csrf_token
    db.session.commit()


def require_actor() -> int:
    """The id of the user making this request, or 401.

    `authenticate` has already run and has already raised if there is no live
    session, so in practice this reads an answer rather than computing one. It
    stays a function, and every route goes on calling it, for two reasons: it is
    the seam that made this milestone a one-file change, and it fails closed if
    a future route is ever reached without the hook -- which is a mistake worth
    answering with a 401 rather than an `AttributeError`.
    """
    actor = getattr(g, "actor_id", None)
    if actor is None:
        raise unauthenticated()
    return int(actor)


def current_csrf_token() -> str:
    """The token the page must echo back on a mutation."""
    token = getattr(g, "csrf_token", None)
    if token is None:
        raise unauthenticated()
    return str(token)


def set_session_cookie(response: Response, token: str, *, secure: bool, samesite: str) -> None:
    """Attach the session cookie.

    `httponly` is not configurable. A session cookie a script can read is the
    thing D-05 exists to avoid, and a setting for it is a setting someone turns
    off while debugging.
    """
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
    )


def clear_session_cookie(response: Response, *, secure: bool, samesite: str) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
    )


def register(app: Flask) -> None:
    app.before_request(authenticate)
