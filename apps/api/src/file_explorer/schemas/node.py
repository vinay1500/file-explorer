"""Request and response shapes.

Validation lives here so the service layer never has to decide whether a value
came from a human or from another part of the program. By the time anything
reaches `TreeService` it has already been through this.

Two things are deliberate and easy to get wrong.

**Unknown fields are rejected, not ignored.** marshmallow raises by default
today, and `Meta.unknown` is set anyway: a silently dropped field is how a
client ships a typo for months and finds out when the behaviour it thought it
was configuring turns out never to have existed.

**`parent_id` and `new_parent_id` are nullable.** `null` means the root, which
is a value rather than an absence, so the field has to be able to carry it.
Making it a plain integer is how move-to-root stops being expressible at all --
and the generated TypeScript client inherits that, which is F-06.
"""

from __future__ import annotations

from marshmallow import EXCLUDE, RAISE, Schema, fields, validate

from file_explorer.limits import MAX_NAME_LENGTH, MAX_PAGE
from file_explorer.services.tree import NODE_TYPES

# The name is checked properly in `names.clean` -- NFC, the invisible-character
# rule, Unicode whitespace. This is only the cheap structural bound, so a
# 10 MB string is rejected before anything tries to normalise it.
_NAME = fields.String(required=True, validate=validate.Length(min=1, max=MAX_NAME_LENGTH))


class _Strict(Schema):
    """Base: reject anything not declared."""

    class Meta:
        unknown = RAISE


class NodeSchema(_Strict):
    """One node, as returned."""

    id = fields.Integer(dump_only=True)
    parent_id = fields.Integer(dump_only=True, allow_none=True)
    name = fields.String(dump_only=True)
    type = fields.String(dump_only=True)

    class Meta:
        # Responses are built by this service, not parsed from a client, so a
        # stray key here is a bug to drop rather than a request to reject.
        unknown = EXCLUDE


class NodeCreateSchema(_Strict):
    parent_id = fields.Integer(
        load_default=None,
        allow_none=True,
        metadata={"description": "null creates a root"},
    )
    name = _NAME
    type = fields.String(required=True, validate=validate.OneOf(sorted(NODE_TYPES)))


class NodeRenameSchema(_Strict):
    name = _NAME


class NodeMoveSchema(_Strict):
    new_parent_id = fields.Integer(
        required=True,
        allow_none=True,
        metadata={"description": "null moves the node to the root"},
    )


class PageQuerySchema(_Strict):
    limit = fields.Integer(load_default=None, validate=validate.Range(min=1, max=MAX_PAGE))
    cursor = fields.String(load_default=None, allow_none=True)


class PageSchema(_Strict):
    items = fields.List(fields.Nested(NodeSchema), dump_only=True)
    next_cursor = fields.String(dump_only=True, allow_none=True)


class BreadcrumbSchema(_Strict):
    id = fields.Integer(dump_only=True)
    name = fields.String(dump_only=True)
    type = fields.String(dump_only=True)


class PathSchema(_Strict):
    path = fields.List(fields.Nested(BreadcrumbSchema), dump_only=True)


class DeletedSchema(_Strict):
    deleted = fields.Integer(dump_only=True, metadata={"description": "subtree size when counted"})


class ProblemSchema(Schema):
    """RFC 9457 Problem Details.

    `type`, `title` and `status` are the required trio; `code` is the
    machine-readable extension this API adds, because a client should branch on
    something stable rather than on prose that may be translated later.

    There is deliberately no field for the underlying error. Whatever the
    database said stays in the log.
    """

    type = fields.String(dump_only=True)
    title = fields.String(dump_only=True)
    status = fields.Integer(dump_only=True)
    code = fields.String(dump_only=True)
    detail = fields.String(dump_only=True)
    instance = fields.String(dump_only=True)
    errors = fields.Dict(dump_only=True)

    class Meta:
        unknown = EXCLUDE
