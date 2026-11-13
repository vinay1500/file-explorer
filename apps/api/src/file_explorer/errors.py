"""Domain errors.

Each carries the machine-readable `code` the API surfaces and the status it maps
to. Keeping the mapping here rather than in the blueprints means the service
layer never has to know it is being called over HTTP.

`NodeNotFound` is deliberately the answer for both "no such node" and "not
yours". A 403 confirms the row exists, and node ids are sequential, so the two
cases must be indistinguishable -- same status, same body.
"""

from __future__ import annotations


class TreeError(Exception):
    code = "TREE_ERROR"
    status = 400
    title = "Tree error"


class NodeNotFound(TreeError):
    code = "NODE_NOT_FOUND"
    status = 404
    title = "Node not found"


class CycleDetected(TreeError):
    code = "CYCLE_DETECTED"
    status = 409
    title = "Cycle detected"


class FileAsParent(TreeError):
    code = "FILE_AS_PARENT"
    status = 409
    title = "A file cannot contain anything"


class DuplicateName(TreeError):
    code = "DUPLICATE_NAME"
    status = 409
    title = "Name already used in this folder"


class DepthExceeded(TreeError):
    code = "DEPTH_EXCEEDED"
    status = 409
    title = "Maximum tree depth exceeded"
