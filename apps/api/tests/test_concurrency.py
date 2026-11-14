"""Two real connections, deliberately interleaved.

These are the tests that cannot be faked. A mocked session cannot block on
another session's row lock, so the property under test — that the second
transaction *waits* and then re-reads a tree the first one changed — only exists
against a real database.

The interleaving is driven explicitly rather than by sleeping and hoping: one
side holds a transaction open until the test lets it go.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from file_explorer.errors import CycleDetected
from file_explorer.services.tree import TreeService

MAX_DEPTH = 64
BLOCK_CHECK_SECONDS = 0.6


def _acyclic(s: Session, owner_id: int) -> bool:
    reachable = int(
        s.execute(
            text("""
            WITH RECURSIVE down AS (
                SELECT id, 0 AS d FROM nodes WHERE owner_id = :o AND parent_id IS NULL
                UNION ALL
                SELECT n.id, down.d + 1 FROM nodes n JOIN down ON n.parent_id = down.id
                 WHERE n.owner_id = :o AND down.d < :m
            )
            SELECT count(DISTINCT id) FROM down
            """),
            {"o": owner_id, "m": MAX_DEPTH},
        ).scalar_one()
    )
    total = int(
        s.execute(
            text("SELECT count(*) FROM nodes WHERE owner_id = :o"), {"o": owner_id}
        ).scalar_one()
    )
    return reachable == total


@pytest.mark.concurrency
def test_opposing_moves_cannot_both_commit_two_cycle(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[[], Session],
) -> None:
    """Two siblings, each moved into the other.

    Worth having, but **this is not the discriminating test.** It passes even
    against an implementation that reuses the rows returned by the locking
    select, because the moved node is in both lock sets by construction. The
    test that actually bites is the four-cycle below.
    """
    a = make_node(alice, "alpha")
    b = make_node(alice, "beta")
    session.commit()

    s1, s2 = new_session(), new_session()
    svc1, svc2 = TreeService(s1, MAX_DEPTH), TreeService(s2, MAX_DEPTH)

    svc1.move(alice, a, b)
    s1.commit()

    with pytest.raises(CycleDetected):
        svc2.move(alice, b, a)
    s2.rollback()

    assert _acyclic(session, alice)


@pytest.mark.concurrency
def test_concurrent_move_four_cycle(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[[], Session],
) -> None:
    """G-06 and G-07. The test that distinguishes a correct implementation.

    Two separate trees::

        alpha            gamma
          └── beta         └── delta

    T1 moves `alpha` under `delta`. T2 moves `gamma` under `beta`. Each is
    legal on its own: `delta`'s ancestry is {delta, gamma} and contains no
    alpha; `beta`'s is {beta, alpha} and contains no gamma.

    Together they close a four-node cycle — alpha → delta → gamma → beta →
    alpha — and nothing in the schema can stop it, because
    `ck_nodes_not_own_parent` only covers a cycle of length one.

    What makes it safe is that T1 locks `alpha` + ancestry(delta) = {alpha,
    gamma, delta} while T2 locks `gamma` + ancestry(beta) = {alpha, beta,
    gamma}. The sets intersect on alpha and gamma, so T2 waits — and when it
    wakes it must re-read, because `beta`'s ancestry now runs
    beta → alpha → delta → gamma and *does* contain gamma.

    An implementation that reuses the rows from its locking select sees the
    stale {beta, alpha}, finds no gamma, and commits the cycle. Row locks do
    not refresh a recursive CTE: `LockRows` sits above the plan and EvalPlanQual
    re-checks only the outer qual.
    """
    alpha = make_node(alice, "alpha")
    beta = make_node(alice, "beta", parent_id=alpha)
    gamma = make_node(alice, "gamma")
    delta = make_node(alice, "delta", parent_id=gamma)
    session.commit()

    s1, s2 = new_session(), new_session()
    svc2 = TreeService(s2, MAX_DEPTH)

    locked_by_t1 = threading.Event()
    release_t1 = threading.Event()
    t2_result: dict[str, object] = {}

    def hold_t1() -> None:
        """Take T1's locks, then wait, then finish the move.

        Driven through the repository rather than the service so the test owns
        the moment of commit. The service's own ordering is what the other
        tests cover; here the interleaving is the subject.
        """
        # Reaching into the service's repository on purpose: the interleaving
        # is the subject here, so the test needs to own the moment of commit.
        repo = TreeService(s1, MAX_DEPTH)._repo
        repo.lock_move_participants(alice, alpha, delta)
        locked_by_t1.set()
        release_t1.wait(timeout=10)
        repo.set_parent(alice, alpha, delta)
        s1.commit()

    def run_t2() -> None:
        try:
            svc2.move(alice, gamma, beta)
            s2.commit()
            t2_result["outcome"] = "committed"
        except CycleDetected:
            s2.rollback()
            t2_result["outcome"] = "rejected"
        # Broad on purpose -- any unexpected failure is recorded and asserted
        # on below, rather than vanishing into a dead thread.
        except Exception as exc:
            s2.rollback()
            t2_result["outcome"] = f"error: {type(exc).__name__}"

    t1 = threading.Thread(target=hold_t1, daemon=True)
    t1.start()
    assert locked_by_t1.wait(timeout=5), "T1 never acquired its locks"

    t2 = threading.Thread(target=run_t2, daemon=True)
    t2.start()

    # T2 must be waiting on T1's locks. If it has already finished, the lock
    # sets did not overlap and the whole argument is wrong.
    t2.join(timeout=BLOCK_CHECK_SECONDS)
    assert t2.is_alive(), "T2 did not block on T1's locks"

    release_t1.set()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t2.is_alive(), "T2 never finished"

    assert t2_result["outcome"] == "rejected", (
        f"T2 should have been rejected after re-reading; got {t2_result['outcome']}"
    )

    assert _acyclic(session, alice), "a cycle was committed"
    rows = dict(
        session.execute(
            text("SELECT id, parent_id FROM nodes WHERE owner_id = :o"), {"o": alice}
        ).all()
    )
    assert rows[alpha] == delta, "T1's move did not land"
    assert rows[gamma] is None, "T2's move landed and should not have"
    assert rows[beta] == alpha
    assert rows[delta] == gamma


@pytest.mark.concurrency
def test_move_does_not_block_unrelated_insert(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[[], Session],
) -> None:
    """G-12. `FOR NO KEY UPDATE`, not `FOR UPDATE`.

    `FOR UPDATE` conflicts with `FOR KEY SHARE` — the lock an INSERT takes on
    its parent row for the foreign key check — so a held ancestry chain would
    block anyone creating a node underneath it. `FOR NO KEY UPDATE` still
    excludes other moves, which is all the cycle argument needs.
    """
    home = make_node(alice, "home")
    sub = make_node(alice, "sub", parent_id=home)
    elsewhere = make_node(alice, "elsewhere")
    session.commit()

    s1, s2 = new_session(), new_session()
    repo1 = TreeService(s1, MAX_DEPTH)._repo

    # Hold the chain {elsewhere's node, home, sub}.
    repo1.lock_move_participants(alice, elsewhere, sub)

    # Creating a child under a locked parent must not wait. The session's
    # lock_timeout is 4s, so a conflict would raise rather than hang.
    s2.execute(
        text(
            "INSERT INTO nodes (owner_id, parent_id, name, type) "
            "VALUES (:o, :p, 'new-child', 'folder')"
        ),
        {"o": alice, "p": home},
    )
    s2.commit()

    s1.rollback()

    assert (
        int(
            session.execute(
                text("SELECT count(*) FROM nodes WHERE owner_id = :o"), {"o": alice}
            ).scalar_one()
        )
        == 4
    )
