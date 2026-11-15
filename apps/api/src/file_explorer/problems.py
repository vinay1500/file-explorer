"""RFC 9457 Problem Details, for every response that is not a success.

The failure this exists to prevent is an API that is careful about the errors
it thought of and falls back to the framework for the rest. A client then gets
a tidy JSON object for a duplicate name and an HTML page for a bad URL, and has
to handle both.

So there are four handlers, and the third and fourth are the ones usually
missing:

1. `TreeError` -- the domain errors. Status and code come from the class.
2. `ValidationError` -- marshmallow, mapped to 422 with the field errors.
3. `HTTPException` -- everything Flask and Werkzeug raise on their own: 404 for
   an unrouted path, 405, 413 over `MAX_CONTENT_LENGTH`, 429 from the rate
   limiter. Without this they are HTML.
4. `Exception` -- the catch-all. Logged in full, answered with nothing. This is
   the one that decides whether an unanticipated bug leaks a stack trace.

Every response carries `application/problem+json`, which is the point of the
RFC: a client can tell a problem document from a successful body by the media
type rather than by guessing from the shape.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Flask, jsonify, request
from flask.wrappers import Response
from marshmallow import ValidationError
from werkzeug.exceptions import HTTPException

from file_explorer.errors import TreeError

log = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"

# `type` is a URI that identifies the *kind* of problem. It does not have to
# resolve, and these deliberately do not: a dereferenceable URL is a promise to
# keep a documentation page alive at a fixed address forever.
TYPE_BASE = "https://github.com/vinay1500/file-explorer/blob/main/docs/errors.md"

# An unknown field's name is echoed back so a client can find its typo. Capped,
# because the key is attacker-controlled and there is no reason to reflect
# kilobytes of it.
MAX_ECHOED_KEY = 64


def _problem(
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    errors: dict[str, Any] | None = None,
) -> tuple[Response, int]:
    body: dict[str, Any] = {
        "type": f"{TYPE_BASE}#{code.lower()}",
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    body["instance"] = request.path

    response = jsonify(body)
    response.status_code = status
    response.content_type = PROBLEM_CONTENT_TYPE
    # Error bodies say something about the caller's own tree, so they must not
    # be cached by anything in front of this service.
    response.headers["Cache-Control"] = "no-store"
    return response, status


def _truncate_keys(messages: Any) -> Any:
    """Cap echoed field names. The values are marshmallow's, not the client's."""
    if not isinstance(messages, dict):
        return messages
    return {str(k)[:MAX_ECHOED_KEY]: _truncate_keys(v) for k, v in messages.items()}


def register(app: Flask) -> None:
    """Attach the four handlers. Order does not matter; specificity does."""

    @app.errorhandler(TreeError)
    def _domain(exc: TreeError) -> tuple[Response, int]:
        # `detail` is the class's own title, never the submitted value. A
        # duplicate-name response that echoes the name tells anyone who can see
        # the response what is already in that folder.
        return _problem(exc.status, exc.code, exc.title)

    @app.errorhandler(ValidationError)
    def _validation(exc: ValidationError) -> tuple[Response, int]:
        return _problem(
            422,
            "VALIDATION_FAILED",
            "Request body is not valid",
            errors=_truncate_keys(exc.messages),
        )

    @app.errorhandler(HTTPException)
    def _http(exc: HTTPException) -> tuple[Response, int]:
        status = exc.code or 500
        # Werkzeug's own description, not ours, and it is written for humans
        # rather than derived from anything the caller sent -- except on 404,
        # where it would otherwise repeat the path. `instance` already has it.
        detail = exc.description if status != 404 else None

        # flask-smorest catches marshmallow's ValidationError inside
        # `@blp.arguments` and re-raises it as an HTTPException with the field
        # messages attached to `.data`. So the handler below never sees those,
        # and without this the 422 for an unknown field arrives with no
        # indication of which field -- which is most of its value.
        data = getattr(exc, "data", None) or {}
        # smorest has used both spellings across versions, so accept either
        # rather than silently producing a 422 that names no field.
        errors = data.get("errors") or data.get("messages")
        return _problem(
            status,
            _HTTP_CODES.get(status, "HTTP_ERROR"),
            exc.name,
            detail,
            _truncate_keys(errors) if errors else None,
        )

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception) -> tuple[Response, int]:
        # Logged with the traceback, answered with nothing. Whatever this was,
        # the caller does not get to learn it from the response body -- that is
        # how a driver error's statement and bound parameters escape.
        log.exception("unhandled error", extra={"path": request.path})
        return _problem(500, "INTERNAL_ERROR", "Internal server error")


_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    406: "NOT_ACCEPTABLE",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_FAILED",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}
