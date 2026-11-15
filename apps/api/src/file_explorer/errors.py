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


class QuotaExceeded(TreeError):
    """The owner is already holding as many nodes as they are allowed.

    409 rather than 507 or 429, to stay inside the taxonomy the rest of these
    use: the same family as `DuplicateName` and `DepthExceeded`, which are also
    "the state of your tree conflicts with this request, and you can resolve
    it". A rate limit would be 429; this is not about how fast they asked.
    """

    code = "QUOTA_EXCEEDED"
    status = 409
    title = "Node limit reached"


class InvalidName(TreeError):
    """The name is wrong on its own terms, whatever is in the database.

    422 rather than 409: a duplicate could succeed tomorrow, an empty name
    could not.
    """

    code = "INVALID_NAME"
    status = 422
    title = "Invalid name"


class InvalidCursor(TreeError):
    """The pagination cursor did not decode.

    422 rather than 400: the request is syntactically fine, the value in it is
    not. A cursor is a token this service issued, so a bad one means either a
    client built its own or one was truncated in transit.
    """

    code = "INVALID_CURSOR"
    status = 422
    title = "Invalid pagination cursor"


class InvalidType(TreeError):
    """`type` is neither "file" nor "folder".

    Checked in the service rather than left to the enum. A bad value reaches
    PostgreSQL as `invalid input value for enum node_type`, which is a
    `DataError` rather than an `IntegrityError` -- so it would miss `_translate`
    entirely and escape as a driver exception carrying the statement.
    """

    code = "INVALID_TYPE"
    status = 422
    title = "Invalid node type"


class OperationTimedOut(TreeError):
    """The statement exceeded `statement_timeout`.

    503 rather than 500 — the request was valid and may succeed later, so the
    caller is being told to come back, not that it did something wrong.
    """

    code = "OPERATION_TIMED_OUT"
    status = 503
    title = "Operation took too long"


class TreeTooDeep(TreeError):
    """A recursive walk hit its depth bound, so its answer is incomplete.

    Not the same thing as `DepthExceeded`, which rejects an operation that
    *would* break I7. This says I7 is **already** broken in stored data: the
    walk was cut short and returned a partial chain. 500, not 409 -- the caller
    did nothing wrong and retrying will not help.

    It exists because the alternative is silence. The CTEs are bounded to stop
    a cycle exhausting memory, and an unchecked bound means a truncated
    ancestry is indistinguishable from a complete one -- which would make the
    cycle check fail open on exactly the data it guards against.
    """

    code = "TREE_TOO_DEEP"
    status = 500
    title = "Stored tree exceeds the depth bound"


class TreeBusy(TreeError):
    """The tree was restructured underneath this request faster than it could finish.

    503, like `OperationTimedOut`, and for the same reason: nothing about the
    request is wrong and it may well succeed on the next attempt. It is a
    separate class because the cause is different -- a timeout means the work
    took too long, this means someone else's work kept invalidating ours.
    """

    code = "TREE_BUSY"
    status = 503
    title = "Tree is being restructured"


class StorageError(TreeError):
    """A database error with no specific mapping.

    Exists so an unmapped constraint cannot escape as a driver exception.
    `str(IntegrityError)` embeds the statement, the bound parameters and the
    constraint name; the detail is logged, never returned.
    """

    code = "STORAGE_ERROR"
    status = 500
    title = "Storage error"
