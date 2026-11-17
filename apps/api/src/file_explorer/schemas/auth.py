"""Request and response shapes for the session routes.

The same rule as `node.py`: unknown fields are rejected rather than ignored.
It matters more here, because a credential endpoint that silently drops a field
is a credential endpoint whose behaviour nobody can read off the request.
"""

from __future__ import annotations

from marshmallow import EXCLUDE, RAISE, Schema, fields, validate

from file_explorer.names import MAX_EMAIL_LENGTH

# Long enough that a passphrase is the obvious choice and short enough that the
# hash's input is bounded. Argon2 has no practical input limit, but an unbounded
# field is an unbounded allocation in front of a memory-hard function.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256


class CredentialsSchema(Schema):
    """What register and login both take.

    There is no composition rule -- no "one uppercase, one digit, one symbol".
    Length is the only property that reliably predicts strength, and the rest
    pushes people towards `Password1!`, which is twelve characters of theatre.
    """

    class Meta:
        unknown = RAISE

    email = fields.Email(required=True, validate=validate.Length(max=MAX_EMAIL_LENGTH))
    password = fields.String(
        required=True,
        load_only=True,
        validate=validate.Length(min=MIN_PASSWORD_LENGTH, max=MAX_PASSWORD_LENGTH),
    )


class SessionSchema(Schema):
    """What login returns.

    No token. The session id went into an httpOnly cookie and must never appear
    in a body, where a script could read it and a log could keep it.
    """

    class Meta:
        unknown = EXCLUDE

    csrf_token = fields.String(required=True)
    expires_at = fields.DateTime(required=True)


class WhoAmISchema(Schema):
    """What `/auth/me` returns."""

    class Meta:
        unknown = EXCLUDE

    id = fields.Integer(required=True)
    email = fields.String(required=True)
    csrf_token = fields.String(required=True)


class RegistrationAcceptedSchema(Schema):
    """What `/auth/register` returns, whether or not anything was created.

    Its own schema rather than `WhoAmISchema(only=("registered",))`: a schema
    reused with `only` generates a *second* component in the OpenAPI document,
    named `WhoAmI1`, and a published contract with a numbered duplicate in it
    is the kind of detail a reviewer notices before anything else.
    """

    class Meta:
        unknown = EXCLUDE

    accepted = fields.Boolean(required=True)
