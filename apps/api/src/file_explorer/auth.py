"""Who is making this request.

Today the answer is a configured id and nothing reads a header. That is the
whole design: **M5 changes the body of one function.**

The alternative -- adding authentication later by threading an actor through
every route -- is how a service ends up with two or three routes that forgot,
and those are exactly the ones that let one user read another's tree. Every
route already calls `require_actor()`; swapping what it returns is a diff in
one place.

For the same reason `401` is documented on every route from day one. A client
generated against this OpenAPI document already handles being unauthenticated,
so M5 is not a breaking change for it either.
"""

from __future__ import annotations

from flask import current_app
from werkzeug.datastructures import WWWAuthenticate
from werkzeug.exceptions import Unauthorized


def require_actor() -> int:
    """The id of the user making this request, or 401.

    **This does not authenticate anything yet.** It returns the configured
    development actor, so the API behaves as though one fixed user is signed
    in. `DEV_ACTOR_ID` has no default: a deployment that forgets to set it
    fails closed with a 401 rather than open on somebody's tree.
    """
    actor = current_app.config.get("DEV_ACTOR_ID")
    if actor is None:
        # RFC 9110 requires a 401 to carry WWW-Authenticate. The scheme named
        # here is the one M5 will use, so a client that follows the challenge
        # today is already pointed at the right mechanism.
        #
        # The description says nothing about how this service is configured.
        # The earlier one named the missing setting, which told an
        # unauthenticated caller about the deployment's auth posture.
        challenge = WWWAuthenticate("Bearer")
        challenge.realm = "file-explorer"
        raise Unauthorized("Authentication is required.", www_authenticate=challenge)
    return int(actor)
