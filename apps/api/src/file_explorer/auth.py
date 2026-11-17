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
        # The demo's front door. Public by definition: it exists to hand out a
        # session to somebody who has none.
        "/api/v1/auth/guest",
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
    """Resolve the session. **Decide nothing and raise nothing.**

    This is one of a pair, and the split is the whole design.

    The first version resolved and refused in one hook registered before
    `limiter.init_app`, which is where the key has to be settled. But Flask
    walks `before_request` functions in order and an exception leaves the loop
    -- so **the limiter's own hook never ran for a request that failed here**,
    and anything presenting a bad cookie or no CSRF token was never counted. A
    script could hammer a protected route indefinitely at no cost.

    So: this hook writes the answer onto `g` and returns. The limiter reads it
    and counts. `enforce_authentication`, registered *after* the limiter,
    raises. Three jobs, in the order they have to happen in.

    The 401-before-422 property survives, because all three are still
    `before_request` and the view's `@blp.arguments` has not run yet.
    """
    g.actor_id = None
    g.csrf_token = None
    g.auth_failure = None

    rule = request.url_rule
    if rule is None or rule.rule in PUBLIC_RULES:
        return

    service = SessionService(db.session)
    resolved = service.resolve(request.cookies.get(SESSION_COOKIE, ""))
    if resolved is None:
        # An expired row may have been deleted while resolving it. Committing
        # here keeps that tidy-up even though the request is about to fail.
        db.session.commit()
        g.auth_failure = "unauthenticated"
        return

    if request.method not in SAFE_METHODS and not _csrf_token_matches(resolved.csrf_token):
        db.session.commit()
        g.auth_failure = "csrf"
        return

    g.actor_id = resolved.actor_id
    g.csrf_token = resolved.csrf_token
    db.session.commit()


def _csrf_token_matches(expected: str) -> bool:
    """Constant time, and it has to survive a header the caller controls.

    `hmac.compare_digest` rather than `==`, because the comparison is against a
    value an attacker can guess at repeatedly.

    The `isascii()` guard is not belt and braces. Werkzeug decodes WSGI header
    values as latin-1, so any byte above 0x7F arrives as a `str` carrying
    non-ASCII code points -- and `compare_digest` raises `TypeError` on exactly
    that. The `TypeError` escapes to the last-resort handler and the caller
    gets **500 where 403 was meant**, which is a cheaper request to send and a
    noisier one to receive.
    """
    supplied = request.headers.get(CSRF_HEADER, "")
    if not supplied or not supplied.isascii():
        return False
    return hmac.compare_digest(supplied, expected)


def enforce_authentication() -> None:
    """Refuse, now that the limiter has had its turn.

    Registered after `limiter.init_app` on purpose -- see `authenticate`.
    """
    failure = getattr(g, "auth_failure", None)
    if failure == "csrf":
        raise Forbidden("A valid CSRF token is required for this request.")
    if failure is not None:
        raise unauthenticated()


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


def set_session_cookie(
    response: Response, token: str, *, secure: bool, samesite: str, max_age: int
) -> None:
    """Attach the session cookie.

    `httponly` is not configurable. A session cookie a script can read is the
    thing D-05 exists to avoid, and a setting for it is a setting someone turns
    off while debugging.

    `max_age` is not optional either, and leaving it out was a real defect
    rather than a default. Without it Werkzeug emits a **session cookie**,
    discarded when the browser process ends -- while the login response says
    `expires_at` fourteen days out. The row would live for a fortnight and the
    credential for as long as the tab, so closing the browser signed you out
    and the API went on holding a session nothing could present.
    """
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
        max_age=max_age,
    )


def clear_session_cookie(response: Response, *, secure: bool, samesite: str) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
    )


def register_resolver(app: Flask) -> None:
    """Hook one: resolve. Must be registered **before** `limiter.init_app`."""
    app.before_request(authenticate)


def register_enforcer(app: Flask) -> None:
    """Hook three: refuse. Must be registered **after** `limiter.init_app`."""
    app.before_request(enforce_authentication)
