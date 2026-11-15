"""The HTTP contract.

These assert what a client can rely on, which is a different question from
whether the tree logic is right — that is covered by the service tests against
real PostgreSQL. What matters here is that every failure looks the same, that
nothing leaks, and that the generated document describes the API that exists.

The sharpest one is `test_move_accepts_a_null_destination_in_the_document`.
A `new_parent_id` typed as a plain integer generates a TypeScript client that
cannot express move-to-root, so the frontend grows a workaround and the feature
quietly stops working. That is F-06, and it is invisible in Python.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import pytest
from flask import Flask, request
from flask.testing import FlaskClient
from sqlalchemy import text

from file_explorer.app import create_app
from file_explorer.config import Config


@pytest.fixture
def client(app: Flask, alice: int, session: object) -> Iterator[FlaskClient]:
    """A test client acting as alice.

    `DEV_ACTOR_ID` is set here rather than in the environment so the default --
    unset, and therefore 401 -- stays the default everywhere else.
    """
    previous = app.config.get("DEV_ACTOR_ID")
    app.config["DEV_ACTOR_ID"] = alice
    try:
        with app.test_client() as test_client:
            yield test_client
    finally:
        app.config["DEV_ACTOR_ID"] = previous


def _problem(response: object) -> dict[str, object]:
    body = json.loads(response.data)  # type: ignore[attr-defined]
    assert isinstance(body, dict)
    return body


# ----------------------------------------------------------- the document


@pytest.mark.invariant
def test_the_openapi_document_generates(client: FlaskClient) -> None:
    spec = _problem(client.get("/api/openapi.json"))
    assert spec["openapi"].startswith("3.")  # type: ignore[union-attr]
    paths = spec["paths"]
    assert isinstance(paths, dict)
    for route in (
        "/api/v1/roots",
        "/api/v1/nodes",
        "/api/v1/nodes/{node_id}",
        "/api/v1/nodes/{node_id}/children",
        "/api/v1/nodes/{node_id}/path",
        "/api/v1/nodes/{node_id}/move",
    ):
        assert route in paths, f"{route} missing from the document: {sorted(paths)}"


@pytest.mark.invariant
def test_move_accepts_a_null_destination_in_the_document(client: FlaskClient) -> None:
    """C-03. `new_parent_id` must be nullable in the generated schema.

    Typed as a plain integer, the generated TypeScript is `new_parent_id:
    number` and move-to-root becomes unrepresentable in the client — so the
    frontend works around it and the feature silently stops existing. That is
    F-06, and nothing in the Python would ever show it.
    """
    spec = _problem(client.get("/api/openapi.json"))
    schemas = spec["components"]["schemas"]  # type: ignore[index]
    move = next(s for name, s in schemas.items() if name.startswith("NodeMove"))
    field = move["properties"]["new_parent_id"]
    assert field.get("nullable") is True or "null" in str(field.get("type")), (
        f"new_parent_id is not nullable in the document: {field}. "
        "A generated client then cannot express move-to-root."
    )


@pytest.mark.invariant
def test_every_route_documents_401(client: FlaskClient) -> None:
    """Documented from day one, so M5 is not a breaking change for clients."""
    spec = _problem(client.get("/api/openapi.json"))
    verbs = {"get", "post", "put", "patch", "delete", "head", "options"}
    missing = [
        f"{method.upper()} {path}"
        for path, methods in spec["paths"].items()  # type: ignore[union-attr]
        for method, operation in methods.items()
        # A path item also carries non-operation keys such as `parameters`,
        # whose value is a list rather than an operation object.
        if method in verbs
        and path.startswith("/api/v1/")
        and "401" not in operation.get("responses", {})
    ]
    assert not missing, f"routes with no documented 401: {missing}"


# ------------------------------------------------- every failure is a problem


@pytest.mark.invariant
def test_an_unrouted_path_is_a_problem_document_not_html(client: FlaskClient) -> None:
    """The handler most APIs forget. Without it this is a Werkzeug HTML page."""
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.content_type.startswith("application/problem+json")
    body = _problem(response)
    assert body["status"] == 404
    assert body["code"] == "NOT_FOUND"
    assert body["instance"] == "/api/v1/does-not-exist"


@pytest.mark.invariant
def test_the_wrong_method_is_a_problem_document(client: FlaskClient) -> None:
    response = client.delete("/api/v1/roots")
    assert response.status_code == 405
    assert response.content_type.startswith("application/problem+json")
    assert _problem(response)["code"] == "METHOD_NOT_ALLOWED"


@pytest.mark.invariant
def test_an_unknown_field_is_422_not_silently_ignored(client: FlaskClient) -> None:
    """A dropped field is how a client ships a typo and never finds out."""
    response = client.post(
        "/api/v1/nodes",
        json={"name": "home", "type": "folder", "parentId": 1},  # camelCase typo
    )
    assert response.status_code == 422
    assert response.content_type.startswith("application/problem+json")
    body = _problem(response)
    assert body["code"] == "VALIDATION_FAILED"
    # Asserted against the whole document rather than one key: what matters to
    # a client is that the offending field is named somewhere it can find it.
    assert "parentId" in json.dumps(body), f"the 422 does not say which field: {body}"


@pytest.mark.invariant
def test_a_domain_error_carries_its_code_and_status(client: FlaskClient) -> None:
    client.post("/api/v1/nodes", json={"name": "home", "type": "folder", "parent_id": None})
    response = client.post(
        "/api/v1/nodes", json={"name": "home", "type": "folder", "parent_id": None}
    )
    assert response.status_code == 409
    body = _problem(response)
    assert body["code"] == "DUPLICATE_NAME"
    assert body["type"].endswith("#duplicate_name")  # type: ignore[union-attr]


@pytest.mark.invariant
def test_no_error_body_carries_sql_or_parameters(client: FlaskClient) -> None:
    """The other half of A-1, asserted at the boundary a caller actually sees."""
    responses = [
        client.get("/api/v1/nodes/999999/children"),
        client.get("/api/v1/nodes/999999/path"),
        client.post("/api/v1/nodes/999999/move", json={"new_parent_id": None}),
        client.post("/api/v1/nodes", json={"name": "x", "type": "nonsense"}),
        client.get("/api/v1/roots?cursor=not-a-cursor"),
    ]
    for response in responses:
        rendered = response.data.decode()
        assert "[SQL:" not in rendered, f"statement leaked: {rendered[:200]}"
        assert "parameters" not in rendered, f"parameters leaked: {rendered[:200]}"
        assert "Traceback" not in rendered


@pytest.mark.invariant
def test_a_duplicate_name_response_does_not_echo_the_name(client: FlaskClient) -> None:
    """SECURITY §3. The response says what is already in a folder, otherwise."""
    secret = "quarterly-layoffs"
    client.post("/api/v1/nodes", json={"name": secret, "type": "folder", "parent_id": None})
    response = client.post(
        "/api/v1/nodes", json={"name": secret, "type": "folder", "parent_id": None}
    )
    assert response.status_code == 409
    assert secret not in response.data.decode()


# ------------------------------------------------------------- the headers


@pytest.mark.invariant
def test_nothing_is_cacheable(client: FlaskClient) -> None:
    """Every body is one owner's tree, and identity will be a cookie at M5."""
    for response in (
        client.get("/api/v1/roots"),
        client.get("/api/v1/does-not-exist"),
        client.post("/api/v1/nodes", json={"name": "a", "type": "folder", "parent_id": None}),
    ):
        assert response.headers.get("Cache-Control") == "no-store", (
            f"{response.status_code} is cacheable: {response.headers.get('Cache-Control')}"
        )


@pytest.mark.invariant
def test_an_oversized_body_is_rejected_before_it_is_read(client: FlaskClient) -> None:
    response = client.post(
        "/api/v1/nodes",
        data=b"x" * 32768,
        content_type="application/json",
    )
    assert response.status_code == 413
    assert _problem(response)["code"] == "PAYLOAD_TOO_LARGE"


# ------------------------------------------------------------ the happy path


@pytest.mark.invariant
def test_a_full_round_trip(client: FlaskClient) -> None:
    home = _problem(
        client.post("/api/v1/nodes", json={"name": "home", "type": "folder", "parent_id": None})
    )
    assert home["parent_id"] is None

    doc = _problem(
        client.post(
            "/api/v1/nodes", json={"name": "notes.txt", "type": "file", "parent_id": home["id"]}
        )
    )
    assert doc["parent_id"] == home["id"]

    children = _problem(client.get(f"/api/v1/nodes/{home['id']}/children"))
    assert [c["name"] for c in children["items"]] == ["notes.txt"]  # type: ignore[union-attr]

    renamed = _problem(client.patch(f"/api/v1/nodes/{doc['id']}", json={"name": "renamed.txt"}))
    assert renamed["name"] == "renamed.txt"

    path = _problem(client.get(f"/api/v1/nodes/{doc['id']}/path"))
    assert [p["name"] for p in path["path"]] == ["home", "renamed.txt"]  # type: ignore[union-attr]

    moved = _problem(client.post(f"/api/v1/nodes/{doc['id']}/move", json={"new_parent_id": None}))
    assert moved["parent_id"] is None, "move-to-root did not reach the root"

    deleted = _problem(client.delete(f"/api/v1/nodes/{home['id']}"))
    assert deleted["deleted"] == 1


@pytest.mark.invariant
def test_pagination_round_trips_through_an_opaque_cursor(
    client: FlaskClient, make_node: Callable[..., int], alice: int, session: object
) -> None:
    home = _problem(
        client.post("/api/v1/nodes", json={"name": "home", "type": "folder", "parent_id": None})
    )
    for n in range(5):
        client.post(
            "/api/v1/nodes",
            json={"name": f"f{n}", "type": "file", "parent_id": home["id"]},
        )

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        url = f"/api/v1/nodes/{home['id']}/children?limit=2"
        if cursor:
            url += f"&cursor={cursor}"
        page = _problem(client.get(url))
        seen.extend(str(item["name"]) for item in page["items"])  # type: ignore[union-attr]
        cursor = page["next_cursor"]  # type: ignore[assignment]
        if cursor is None:
            break

    assert seen == ["f0", "f1", "f2", "f3", "f4"]
    assert len(seen) == len(set(seen))


@pytest.mark.invariant
def test_a_malformed_cursor_is_422(client: FlaskClient) -> None:
    response = client.get("/api/v1/roots?cursor=%%%not-base64%%%")
    assert response.status_code == 422
    assert _problem(response)["code"] in {"INVALID_CURSOR", "VALIDATION_FAILED"}


# ------------------------------------------------------------- the auth seam


@pytest.mark.invariant
def test_no_configured_actor_is_401_not_an_open_api(app: Flask) -> None:
    """Fails closed. A deployment that forgets `DEV_ACTOR_ID` serves nobody."""
    previous = app.config.get("DEV_ACTOR_ID")
    app.config["DEV_ACTOR_ID"] = None
    try:
        with app.test_client() as anonymous:
            response = anonymous.get("/api/v1/roots")
        assert response.status_code == 401
        assert response.content_type.startswith("application/problem+json")
        assert _problem(response)["code"] == "UNAUTHENTICATED"
    finally:
        app.config["DEV_ACTOR_ID"] = previous


@pytest.mark.invariant
def test_the_documented_error_shape_is_the_one_actually_sent(client: FlaskClient) -> None:
    """The document must not promise an error response the handlers do not send.

    Two axes, and the first version of this test only checked one.

    **Schema.** flask-smorest documents its own `Error` -- `code` as an
    integer, plus `message` -- while the handlers send RFC 9457, where `code`
    is a string alongside `type`, `title` and `status`.

    **Media type.** `ERROR_SCHEMA` fixes the schema and leaves the media type
    at smorest's `application/json`, while the live response is
    `application/problem+json`. That is the one property the whole module
    exists to give a client: tell a problem from a body by the media type
    rather than by guessing from the shape.

    The first version reached the schema through
    `default["content"]["application/json"]["schema"]` -- a literal index. It
    could not see a media-type mismatch at all; it *presumed* the answer. Worse,
    fixing the document would have made it raise `KeyError`, which reads as
    "the fix broke a test" rather than "the test encoded the bug".
    """
    spec = _problem(client.get("/api/openapi.json"))
    schemas = spec["components"]["schemas"]  # type: ignore[index]
    default = spec["components"]["responses"]["DEFAULT_ERROR"]  # type: ignore[index]

    # Derived, not asserted by indexing.
    documented_types = sorted(default["content"])
    assert documented_types == ["application/problem+json"], (
        f"the document declares {documented_types} for errors; the service sends "
        "application/problem+json, so a generated client keying off the "
        "documented type matches nothing this service returns"
    )

    ref = default["content"]["application/problem+json"]["schema"]["$ref"]
    properties = schemas[ref.rsplit("/", 1)[-1]]["properties"]
    assert properties["code"]["type"] == "string", (
        f"the documented error has code as {properties['code']['type']}; the "
        "handlers send a string machine-readable code"
    )
    for required in ("type", "title", "status"):
        assert required in properties, (
            f"the documented error has no {required!r}: {sorted(properties)}"
        )

    # And the live response agrees with the document on both axes.
    live = client.get("/api/v1/does-not-exist")
    assert live.content_type.split(";")[0] == documented_types[0], (
        f"live {live.content_type!r} vs documented {documented_types[0]!r}"
    )
    body = _problem(live)
    assert set(body) >= {"type", "title", "status", "code"}
    assert isinstance(body["code"], str)


@pytest.mark.invariant
def test_every_documented_error_uses_the_problem_media_type(client: FlaskClient) -> None:
    """Not just the default component -- every operation's own error responses."""
    spec = _problem(client.get("/api/openapi.json"))
    wrong = [
        f"{method.upper()} {path} {status}: {sorted(response['content'])}"
        for path, methods in spec["paths"].items()  # type: ignore[union-attr]
        for method, operation in methods.items()
        if method in {"get", "post", "patch", "delete"}
        for status, response in operation.get("responses", {}).items()
        if status.startswith(("4", "5"))
        and "content" in response
        and sorted(response["content"]) != ["application/problem+json"]
    ]
    assert not wrong, f"error responses documented with the wrong media type: {wrong}"


@pytest.mark.invariant
def test_a_failure_after_the_handler_is_still_a_problem_document() -> None:
    """The last-resort guarantee, which a config setting was quietly removing.

    `problems.py`'s `Exception` handler covers anything raised *inside*
    dispatch. It does not cover an `after_request` function, or response
    processing -- those escape to `Flask.handle_exception`, and
    `PROPAGATE_EXCEPTIONS = True` makes Flask re-raise them to the WSGI server
    instead of running them through the handler.

    Under gunicorn that is a bare HTML 500 with a traceback, which is precisely
    what the module was written to make impossible. The comment justifying the
    setting described something the setting does not do.
    """
    built = create_app(Config)
    built.config["DEV_ACTOR_ID"] = 1

    @built.after_request
    def _explode(response: object) -> object:
        if request.path == "/api/v1/roots":
            raise RuntimeError("after_request failed with /srv/secret/path in the message")
        return response

    with built.test_client() as broken:
        response = broken.get("/api/v1/roots")

    assert response.status_code == 500
    assert response.content_type.startswith("application/problem+json"), (
        f"a failure after the handler escaped as {response.content_type!r}"
    )
    rendered = response.data.decode()
    assert "Traceback" not in rendered
    assert "/srv/secret/path" not in rendered, "the exception message leaked"


# ------------------------------------------------------------- isolation


@pytest.mark.invariant
def test_no_route_reaches_another_owners_node(
    client: FlaskClient, session: object, bob: int, make_node: Callable[..., int]
) -> None:
    """The HTTP layer's whole isolation story, which nothing else tested.

    Review was blunt about why: `alice` is always id 1 under `RESTART
    IDENTITY`, so every other assertion in this file still holds if
    `require_actor()` were replaced with `return 1`, or if a route passed a
    literal `1` to the service instead of `actor`.

    This builds a node that belongs to **bob** and drives every route as
    alice. 404 on all of them -- never 403, which would confirm the row exists
    and make sequential ids enumerable, and never 200.
    """
    theirs = make_node(bob, "bob-private")
    make_node(bob, "bob-child", parent_id=theirs)
    session.commit()  # type: ignore[attr-defined]

    attempts = {
        "GET children": client.get(f"/api/v1/nodes/{theirs}/children"),
        "GET path": client.get(f"/api/v1/nodes/{theirs}/path"),
        "PATCH rename": client.patch(f"/api/v1/nodes/{theirs}", json={"name": "mine-now"}),
        "POST move": client.post(f"/api/v1/nodes/{theirs}/move", json={"new_parent_id": None}),
        "DELETE": client.delete(f"/api/v1/nodes/{theirs}"),
        "POST create under": client.post(
            "/api/v1/nodes", json={"name": "smuggled", "type": "folder", "parent_id": theirs}
        ),
    }

    wrong = {
        label: response.status_code
        for label, response in attempts.items()
        if response.status_code != 404
    }
    assert not wrong, f"routes that did not answer 404 for another owner's node: {wrong}"

    for label, response in attempts.items():
        assert "bob-private" not in response.data.decode(), f"{label} echoed the name"

    session.rollback()  # type: ignore[attr-defined]
    remaining = session.execute(  # type: ignore[attr-defined]
        text("SELECT count(*) FROM nodes WHERE owner_id = :o"), {"o": bob}
    ).scalar_one()
    assert remaining == 2, f"bob's subtree was modified: {remaining} of 2 rows left"


@pytest.mark.invariant
def test_a_listing_shows_only_the_caller(
    client: FlaskClient, session: object, bob: int, make_node: Callable[..., int]
) -> None:
    make_node(bob, "theirs")
    session.commit()  # type: ignore[attr-defined]
    client.post("/api/v1/nodes", json={"name": "mine", "type": "folder", "parent_id": None})

    page = _problem(client.get("/api/v1/roots"))
    assert [item["name"] for item in page["items"]] == ["mine"]  # type: ignore[union-attr]


@pytest.mark.invariant
def test_an_unknown_query_parameter_is_rejected(client: FlaskClient) -> None:
    """webargs overrides the schema's own `unknown`, and its query default is EXCLUDE.

    So the strict base class did nothing here and `?cursr=` was silently
    dropped — which is not cosmetic. A dropped cursor restarts the listing at
    page one, so a client paging through loops forever while every single
    request succeeds.
    """
    response = client.get("/api/v1/roots?cursr=abc")
    assert response.status_code == 422, (
        f"an unknown query parameter was accepted ({response.status_code}); "
        "a dropped cursor is an infinite pagination loop"
    )
    assert "cursr" in response.data.decode()


@pytest.mark.invariant
def test_a_405_carries_allow_and_a_401_carries_www_authenticate(
    client: FlaskClient, app: Flask
) -> None:
    """Both are RFC 9110 MUSTs, and building a fresh response drops them."""
    wrong_method = client.delete("/api/v1/roots")
    assert wrong_method.status_code == 405
    assert wrong_method.headers.get("Allow"), "405 with no Allow header"

    previous = app.config.get("DEV_ACTOR_ID")
    app.config["DEV_ACTOR_ID"] = None
    try:
        with app.test_client() as anonymous:
            unauthenticated = anonymous.get("/api/v1/roots")
    finally:
        app.config["DEV_ACTOR_ID"] = previous

    assert unauthenticated.status_code == 401
    assert unauthenticated.headers.get("WWW-Authenticate"), "401 with no WWW-Authenticate"
    assert "DEV_ACTOR_ID" not in unauthenticated.data.decode(), (
        "the 401 told an unauthenticated caller how this service is configured"
    )
