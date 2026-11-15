"""The HTTP layer. Thin on purpose.

Every route does the same four things: ask who is calling, decode the inputs,
call one service method, shape the output. No route contains a rule about
trees. If one ever needs to, the rule is in the wrong place -- the service is
what the concurrency tests exercise, and a rule that lives up here is a rule
those tests cannot reach.

**Why `/roots` is its own route** rather than `GET /nodes?parent_id=`. A root
has `parent_id` of `null`, and `null` is not something a query string can
carry: `?parent_id=` is an empty string, which a typed schema rejects with 422,
and omitting it entirely is indistinguishable from "all nodes". Both readings
are defensible, which is the problem. Two routes have one reading each.
"""

from __future__ import annotations

from typing import Any

from flask.views import MethodView
from flask_smorest import Blueprint

from file_explorer import cursors
from file_explorer.app import tree_service
from file_explorer.auth import require_actor
from file_explorer.extensions import db
from file_explorer.schemas.node import (
    DeletedSchema,
    NodeCreateSchema,
    NodeMoveSchema,
    NodeRenameSchema,
    NodeSchema,
    PageQuerySchema,
    PageSchema,
    PathSchema,
    ProblemSchema,
)

blp = Blueprint(
    "nodes",
    __name__,
    url_prefix="/api/v1",
    description="Folders and files, as a tree.",
)

# Documented on every route from day one, because `require_actor` will start
# returning it at M5 and a client generated today should already handle it.
_AUTH = {401: ProblemSchema}


def _page(result: tuple[list[dict[str, object]], tuple[str, int] | None]) -> dict[str, Any]:
    items, cursor = result
    return {"items": items, "next_cursor": cursors.encode(cursor) if cursor else None}


@blp.route("/roots")
class Roots(MethodView):
    @blp.arguments(PageQuerySchema, location="query")
    @blp.response(200, PageSchema)
    @blp.alt_response(422, schema=ProblemSchema)
    @blp.doc(responses=_AUTH)
    def get(self, query: dict[str, Any]) -> dict[str, Any]:
        """One page of the caller's root folders and files."""
        actor = require_actor()
        svc = tree_service()
        after = cursors.decode(query.get("cursor"))
        limit = query.get("limit")
        return _page(
            svc.list_roots(actor, limit, after) if limit else svc.list_roots(actor, after=after)
        )


@blp.route("/nodes")
class Nodes(MethodView):
    @blp.arguments(NodeCreateSchema)
    @blp.response(201, NodeSchema)
    @blp.alt_response(404, schema=ProblemSchema)
    @blp.alt_response(409, schema=ProblemSchema)
    @blp.alt_response(422, schema=ProblemSchema)
    @blp.doc(responses=_AUTH)
    def post(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create a folder or a file. `parent_id` of null creates a root."""
        actor = require_actor()
        svc = tree_service()
        node_id = svc.create(actor, body["parent_id"], body["name"], body["type"])
        db.session.commit()
        return svc.get(actor, node_id)


@blp.route("/nodes/<int:node_id>")
class Node(MethodView):
    @blp.arguments(NodeRenameSchema)
    @blp.response(200, NodeSchema)
    @blp.alt_response(404, schema=ProblemSchema)
    @blp.alt_response(409, schema=ProblemSchema)
    @blp.alt_response(422, schema=ProblemSchema)
    @blp.doc(responses=_AUTH)
    def patch(self, body: dict[str, Any], node_id: int) -> dict[str, Any]:
        """Rename a node in place."""
        actor = require_actor()
        svc = tree_service()
        svc.rename(actor, node_id, body["name"])
        db.session.commit()
        return svc.get(actor, node_id)

    @blp.response(200, DeletedSchema)
    @blp.alt_response(404, schema=ProblemSchema)
    @blp.doc(responses=_AUTH)
    def delete(self, node_id: int) -> dict[str, Any]:
        """Delete a node and everything under it."""
        actor = require_actor()
        svc = tree_service()
        removed = svc.delete(actor, node_id)
        db.session.commit()
        return {"deleted": removed}


@blp.route("/nodes/<int:node_id>/children")
class Children(MethodView):
    @blp.arguments(PageQuerySchema, location="query")
    @blp.response(200, PageSchema)
    @blp.alt_response(404, schema=ProblemSchema)
    @blp.alt_response(409, schema=ProblemSchema)
    @blp.alt_response(422, schema=ProblemSchema)
    @blp.doc(responses=_AUTH)
    def get(self, query: dict[str, Any], node_id: int) -> dict[str, Any]:
        """One page of a folder's contents. A file is a 409, not an empty page."""
        actor = require_actor()
        svc = tree_service()
        after = cursors.decode(query.get("cursor"))
        limit = query.get("limit")
        return _page(
            svc.list_children(actor, node_id, limit, after)
            if limit
            else svc.list_children(actor, node_id, after=after)
        )


@blp.route("/nodes/<int:node_id>/path")
class Path(MethodView):
    @blp.response(200, PathSchema)
    @blp.alt_response(404, schema=ProblemSchema)
    @blp.doc(responses=_AUTH)
    def get(self, node_id: int) -> dict[str, Any]:
        """Breadcrumbs, root first."""
        return {"path": tree_service().path(require_actor(), node_id)}


@blp.route("/nodes/<int:node_id>/move")
class Move(MethodView):
    @blp.arguments(NodeMoveSchema)
    @blp.response(200, NodeSchema)
    @blp.alt_response(404, schema=ProblemSchema)
    @blp.alt_response(409, schema=ProblemSchema)
    @blp.alt_response(422, schema=ProblemSchema)
    @blp.alt_response(503, schema=ProblemSchema)
    @blp.doc(responses=_AUTH)
    def post(self, body: dict[str, Any], node_id: int) -> dict[str, Any]:
        """Re-parent a node. `new_parent_id` of null moves it to the root.

        A POST rather than a PATCH on the node: moving is not a field
        assignment, it is an operation with its own failure modes -- a cycle, a
        depth cap, a file as the destination -- and giving it its own endpoint
        keeps those out of the rename path.
        """
        actor = require_actor()
        svc = tree_service()
        svc.move(actor, node_id, body["new_parent_id"])
        db.session.commit()
        return svc.get(actor, node_id)
