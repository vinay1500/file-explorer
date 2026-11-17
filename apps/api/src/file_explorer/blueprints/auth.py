"""Register, sign in, sign out, and ask who you are.

Four routes, and most of the thinking in them is about what they refuse to say.

## Registration and login answer the same way about the same facts

`SECURITY.md` §2.6 says there is no user enumeration. The usual implementation
breaks that in two places at once:

* **Registration** returns 409 for an address already taken, which is a plain
  oracle: anyone can ask whether an address has an account here.
* **Login** returns 401 for both an unknown address and a wrong password, which
  is correct, and then takes a tenth as long for the unknown one -- because the
  wrong-password path runs Argon2 and the unknown-address path returns straight
  away. The status codes match and the clock does not.

Both are closed here. Registration answers **202 with the same body** whether
the account was created or the address was already in use, and login hashes a
throwaway password against a fixed dummy hash when it finds no row, so the two
paths do the same work. Neither is free -- §2.6's cost is that a user who
mistypes their own address at registration is told nothing -- and that is the
trade the document already chose.

The honest limit, recorded rather than claimed away: this equalises the
*server's* work. It does not control what a loaded instance, a cold compute or
the network does to the measurement. The claim is "the obvious oracle is gone",
not "timing cannot leak".
"""

from __future__ import annotations

from typing import Any, ClassVar

from flask import Response, current_app, jsonify, make_response, request
from flask.views import MethodView
from flask_smorest import Blueprint
from sqlalchemy import select

from file_explorer import auth, passwords
from file_explorer.extensions import db, limiter
from file_explorer.models.user import User
from file_explorer.names import normalise_email
from file_explorer.problems import PROBLEM_CONTENT_TYPE
from file_explorer.schemas.auth import CredentialsSchema, SessionSchema, WhoAmISchema
from file_explorer.schemas.node import ProblemSchema
from file_explorer.services.sessions import SessionService

blp = Blueprint(
    "auth",
    __name__,
    url_prefix="/api/v1/auth",
    description="Sessions: registering, signing in and signing out.",
)

# Verified against when no user row is found, so the unknown-address path does
# the same Argon2 work as the wrong-password path. Generated at import, once:
# a constant in the source would be one more thing to keep in step with the
# parameters, and generating it per request would double every login's cost.
_DUMMY_HASH = passwords.hash_password("dummy password, never a real credential")

# Tighter than the global default, and per route. Credential endpoints are the
# ones worth spending a limit on.
AUTH_RATE_LIMIT = "10 per minute"


def _cookie_settings() -> dict[str, Any]:
    return {
        "secure": bool(current_app.config["AUTH_COOKIE_SECURE"]),
        "samesite": str(current_app.config["AUTH_COOKIE_SAMESITE"]),
    }


@blp.route("/register")
class Register(MethodView):
    decorators: ClassVar[list[Any]] = [limiter.limit(AUTH_RATE_LIMIT)]

    @blp.arguments(CredentialsSchema)
    @blp.response(202, WhoAmISchema(only=("registered",)))
    @blp.alt_response(422, schema=ProblemSchema, content_type=PROBLEM_CONTENT_TYPE)
    @blp.alt_response(429, schema=ProblemSchema, content_type=PROBLEM_CONTENT_TYPE)
    def post(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create an account, or say nothing if the address is taken.

        **202 either way, with an identical body.** A 409 here would be a
        working answer to "does this person have an account", available to
        anyone, with no session required.
        """
        email = normalise_email(body["email"])
        encoded = passwords.hash_password(body["password"])

        existing = db.session.execute(
            select(User.id).where(User.email == email)
        ).scalar_one_or_none()
        if existing is None:
            db.session.add(User(email=email, password_hash=encoded))
            db.session.commit()

        return {"registered": True}


@blp.route("/login")
class Login(MethodView):
    decorators: ClassVar[list[Any]] = [limiter.limit(AUTH_RATE_LIMIT)]

    @blp.arguments(CredentialsSchema)
    @blp.alt_response(401, schema=ProblemSchema, content_type=PROBLEM_CONTENT_TYPE)
    @blp.alt_response(422, schema=ProblemSchema, content_type=PROBLEM_CONTENT_TYPE)
    @blp.alt_response(429, schema=ProblemSchema, content_type=PROBLEM_CONTENT_TYPE)
    def post(self, body: dict[str, Any]) -> Response:
        """Open a session. 200 with the CSRF token, or 401."""
        email = normalise_email(body["email"])
        user = db.session.execute(select(User).where(User.email == email)).scalar_one_or_none()

        stored = user.password_hash if user is not None else _DUMMY_HASH
        ok = passwords.verify_password(stored, body["password"])
        if user is None or not ok:
            # One branch, one answer. Reaching the dummy verification above is
            # what keeps the two reasons indistinguishable on the clock.
            raise auth.unauthenticated()

        if passwords.needs_rehash(user.password_hash):
            user.password_hash = passwords.hash_password(body["password"])

        issued = SessionService(db.session).begin(user.id)
        db.session.commit()

        payload = SessionSchema().dump(
            {"csrf_token": issued.csrf_token, "expires_at": issued.expires_at}
        )
        response = make_response(jsonify(payload), 200)
        auth.set_session_cookie(response, issued.token, **_cookie_settings())
        return response


@blp.route("/logout")
class Logout(MethodView):
    @blp.response(204)
    @blp.alt_response(401, schema=ProblemSchema, content_type=PROBLEM_CONTENT_TYPE)
    @blp.alt_response(403, schema=ProblemSchema, content_type=PROBLEM_CONTENT_TYPE)
    def post(self) -> Response:
        """End this session.

        A `DELETE` against the table, which is the sentence a stateless token
        cannot say: clearing the cookie asks the browser to forget a credential
        and does nothing about a copy already taken out of one.
        """
        SessionService(db.session).revoke(request.cookies.get(auth.SESSION_COOKIE, ""))
        db.session.commit()

        response = make_response("", 204)
        auth.clear_session_cookie(response, **_cookie_settings())
        return response


@blp.route("/me")
class Me(MethodView):
    @blp.response(200, WhoAmISchema)
    @blp.alt_response(401, schema=ProblemSchema, content_type=PROBLEM_CONTENT_TYPE)
    def get(self) -> dict[str, Any]:
        """Who this session belongs to, and the CSRF token to echo back.

        The page calls this on load. Returning the CSRF token here is what lets
        a reloaded tab go on making mutations without signing in again -- the
        session cookie survived the reload and the in-memory token did not.
        """
        actor = auth.require_actor()
        user = db.session.get(User, actor)
        return {
            "id": actor,
            "email": user.email if user is not None else "",
            "csrf_token": auth.current_csrf_token(),
            "registered": True,
        }
