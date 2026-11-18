# Chapter 7 · The silent outage

> Volume II · What Went Wrong
> Written at M0 · last verified 2026-11-13

The demo was broken for roughly seventeen months. Nothing reported it, because the only thing
watching was a route that could not fail.

---

## What was measured

On 13 November 2026, against the deployed service:

| Request | Result |
|---|---|
| `GET /` | **200 OK**, after a 52-second cold start |
| `GET /api/tree` | **500**, 265 bytes, in under half a second |

The frontend loaded, called the API, caught the error, wrote it to the browser console, and
rendered an empty tree. A visitor saw a working application that happened to contain nothing.

## The cause, from the service logs

```
psycopg2.OperationalError: could not translate host name
  "dpg-…" to address: Name or service not known
```

`dpg-` is Render's internal namespace for a managed PostgreSQL instance. *"Name or service not
known"* is a DNS resolution failure — the hostname has no record at all.

That distinction matters. The database was not paused, not out of credit, not refusing the
password. **The instance did not exist.**

> **What remains unverified.** The database being gone is established. *Why* it was deleted is
> not. Render's free PostgreSQL is documented as deleted 30 days after creation plus a 14-day
> grace period, and the timeline fits — created around April 2025, unreachable thereafter. But a
> log cannot distinguish an expiry from a manual deletion, so neither is claimed here.

## Why nothing noticed

```python
@app.route('/')
def home():
    return "✅ Flask backend is running"   # touches nothing
```

Any uptime monitor pointed at that route would have reported green for seventeen months. It
returns a constant. It cannot fail while the process is alive.

That makes it a **liveness** signal — *is the process running* — being read as a **readiness**
signal — *is the service able to do its job*. The two are not the same, and conflating them is
how an outage hides in plain sight.

## What replaces it

```python
@bp.get("/health")
def health():
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        return {"status": "degraded", "db": "unreachable"}, 503
    return {"status": "ok", "db": "ok"}, 200
```

A health check must exercise the dependency it claims is healthy. Anything less is decoration.

Paired with it, `.github/workflows/health-check.yml` probes that endpoint twice a day and
**fails the workflow on any non-200**, which sends an email. It is committed and runs on
schedule, but it exits early with a notice until the `API_URL` repository variable is set —
there is nothing deployed to probe yet. Had both existed in 2025, seventeen months would have
been about twelve hours.

The schedule is deliberately sparse — twice daily, not every ten minutes. An earlier plan pinged
frequently to keep the free instance warm; that was dropped. GitHub's scheduled workflows are
best-effort and are delayed or skipped under load, so a strategy depending on a cron firing
inside every fifteen-minute window all day is built on a guarantee that does not exist. The job
here is monitoring, not warming.

## The cold start, handled honestly

The 52 seconds do not disappear. The free instance spins down after fifteen minutes idle and
takes most of a minute to wake.

Rather than hide that, the interface states it. The rebuilt frontend resolves a session before
rendering anything and has four states rather than one — *waking*, *anonymous*, *signed in*,
*failed* — and the waking copy says exactly what is happening: *"A free instance sleeps after
fifteen idle minutes and can take up to a minute to come back."*

The failed state matters more. It distinguishes **503 with a reachable API** — *"the API is up
but cannot reach its database; this is not a cold start"* — from a request that never arrived.
The April 2025 client had one state, and that is the whole of this chapter in one sentence: it
caught its errors, wrote them to `console.error`, and rendered an empty tree forever.

This reads better than a workaround. It says the infrastructure's constraints are understood and
were handled deliberately. A blank screen for a minute says nothing — or worse, says the
application is broken, which is precisely what happened for seventeen months.

---

## What this cost, and the rule it produced

This did not merely break a demo. It broke a demo **whose link was on a resume**. Everyone who
clicked it between mid-2025 and November 2026 waited about a minute and then saw an empty
application. There is no way to know how many did.

> **The rule:** before depending on any free tier, write down **what expires, when, and what
> happens to the data.** One line — *"Render free PostgreSQL: deleted 30 days after creation
> plus 14 days' grace"* — is the difference between a working demo and seventeen months of
> silence.

That line now sits in the project's record of verified facts, alongside the equivalent for every
other service this project depends on, with a date against each and a standing instruction to
re-check quarterly.

---

## Closed, on 18 November 2026

A chapter about a silent outage should say when it stopped being possible, and it can.

**The API is deployed.** `/api/v1/health` executes `SELECT 1` before it answers, so a `200`
means the database replied rather than that the process is alive. The original route returned a
constant string; it could not fail while the process ran, which is the entire mechanism of this
chapter.

**A GitHub Actions workflow probes it twice a day**, at 05:30 and 09:00 UTC — the hours somebody
is around to act on a failure — and fails the workflow on anything that is not a `200`, which
sends an email. The first real run returned `{"db":"ok","status":"ok"}`.

Two details from building it are worth more than the fact that it exists:

- **The probe had been green and silent for five days** before it watched anything. It exits
  early when `API_URL` is unset, which is correct behaviour and looks identical to success.
  A check that passes because it did nothing is the same failure as a health route that cannot
  fail, one level up.
- **The probe and the browser are configured in two different places** — a repository variable
  and a committed `vercel.json`. Nothing connected them, so the probe could have been green
  against a service the frontend never calls. It now refuses to run when the two name different
  hosts.

The second one is this chapter's lesson applied to its own fix, which is the only way to tell
whether a lesson was learned.

---

**Related:** [Chapter 17 · Architecture on no budget](README.md) ·
[Chapter 8 · The register](README.md)
