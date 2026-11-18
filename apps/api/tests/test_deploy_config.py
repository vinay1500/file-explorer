"""The deployment is a file, and the file has to agree with the code.

F-23 was that the running system existed only as dashboard settings: no
`Procfile`, no `render.yaml`, no `Dockerfile`, nothing. Committing the files
fixes the "cannot be recreated" half. This is the other half -- a committed
file that drifts from the code is a worse artefact than no file, because it
reads as the truth.

What these check is narrow and deliberately so: that every setting the
application **refuses to start without** is declared, and that no secret is
declared with a value. Parsing is by regular expression rather than with a YAML
library, because the alternative is adding a dependency to the API in order to
test a file the API never reads.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RENDER = ROOT / "render.yaml"
VERCEL = ROOT / "vercel.json"
CONFIG = Path(__file__).resolve().parents[1] / "src" / "file_explorer" / "config.py"

#: `- key: NAME` in the envVars block.
ENV_KEY = re.compile(r"^\s*-\s*key:\s*([A-Z_][A-Z0-9_]*)\s*$", re.M)

#: `os.environ["NAME"]` -- a read with no default, so a missing value is a
#: start-up failure rather than a quiet fallback.
REQUIRED = re.compile(r"os\.environ\[\"([A-Z_][A-Z0-9_]*)\"\]")


def _declared() -> set[str]:
    return set(ENV_KEY.findall(RENDER.read_text(encoding="utf-8")))


@pytest.mark.invariant
def test_the_deploy_files_exist_and_say_something() -> None:
    """Both of them, because each is half of one deployment.

    `vercel.json` is not decoration here: its rewrite is what makes the session
    cookie same-site, and without it `create_app` refuses to start in the
    configuration `render.yaml` describes.
    """
    assert RENDER.is_file(), f"{RENDER} is missing"
    assert VERCEL.is_file(), f"{VERCEL} is missing"
    assert "healthCheckPath" in RENDER.read_text(encoding="utf-8")
    assert "/api/" in VERCEL.read_text(encoding="utf-8")
    assert len(_declared()) >= 5, "the envVars block parsed to almost nothing"


@pytest.mark.invariant
def test_every_setting_with_no_default_is_declared_for_the_deployment() -> None:
    """A setting the app refuses to start without, that the deploy never sets.

    `config.py` reads some values with `os.environ[...]` and no fallback, on
    purpose: a deployment that forgets one should fail loudly rather than serve
    something unexpected. That is only an improvement if the deployment
    actually declares them, and nothing checked that until now.
    """
    required = set(REQUIRED.findall(CONFIG.read_text(encoding="utf-8")))
    assert required, "no required settings found; the pattern is wrong"

    missing = sorted(required - _declared())
    assert not missing, (
        f"config.py refuses to start without {missing}, and render.yaml does not declare them. "
        "The deployment would fail on boot with a KeyError"
    )


@pytest.mark.invariant
def test_declaring_a_proxy_also_declares_the_hop_count() -> None:
    """The pair that `create_app` refuses to start without.

    `BEHIND_PROXY=1` with no `TRUST_PROXY_HOPS` means `remote_addr` is an edge
    address for every caller on earth, so the login rate limit becomes one
    shared bucket and a single script locks everybody out. The application
    refuses that combination; this makes sure the deployment file cannot
    describe it either.
    """
    text = RENDER.read_text(encoding="utf-8")
    declared = _declared()
    if re.search(r"key:\s*BEHIND_PROXY\s*\n\s*value:\s*\"?1\"?", text):
        assert "TRUST_PROXY_HOPS" in declared, (
            "render.yaml sets BEHIND_PROXY=1 and never mentions TRUST_PROXY_HOPS, so the service "
            "would refuse to boot"
        )


@pytest.mark.invariant
def test_the_hop_count_can_actually_be_measured() -> None:
    """The setting is unguessable by design, so the stack has to show its work.

    `TRUST_PROXY_HOPS` must be read off a real request through the deployed
    stack -- one hop too many and a caller sets their own `X-Forwarded-For` and
    picks their own rate-limit bucket. Render only exposes HTTP request logs on
    a paid workspace, so on a free instance the only place that header can
    appear is the process's own access log. Without this the task is blocked on
    something no free plan can supply, which is how a measurement quietly
    becomes a guess.
    """
    text = RENDER.read_text(encoding="utf-8")
    if not re.search(r"key:\s*BEHIND_PROXY\s*\n\s*value:\s*\"?1\"?", text):
        pytest.skip("no proxy declared, so there is no hop count to measure")
    start = re.search(r"startCommand: >-\n((?:\s{6}.*\n)+)", text)
    assert start, "render.yaml has no folded startCommand block"
    command = " ".join(line.strip() for line in start.group(1).splitlines())
    assert "--access-logfile" in command, (
        "gunicorn is not writing an access log, so no request is recorded at all"
    )
    assert "x-forwarded-for" in command.lower(), (
        "the access log format never prints X-Forwarded-For, so TRUST_PROXY_HOPS could only be "
        "guessed -- and guessing high is the failure the setting exists to prevent"
    )


#: The destination host in `vercel.json`'s rewrite.
REWRITE_HOST = re.compile(r'"destination"\s*:\s*"https://([^/"]+)')


@pytest.mark.invariant
def test_the_rewrite_points_at_the_service_this_repository_declares() -> None:
    """Two files, one deployment, and nothing was checking they agreed.

    `vercel.json` rewrote `/api/*` to `file-explorer-backend.onrender.com` --
    the name of the *old* service, the one whose database had been gone for
    seventeen months (F-24). `render.yaml` declares `file-explorer-api`. Both
    files were committed, both read as the truth, and the browser would have
    reached a host this repository never deploys to.

    This is F-23's other half: committing the deployment is only worth
    something if the committed files cannot quietly disagree.

    **The first version of this test was wrong, and wrong in the worst
    direction.** It asserted the literal `<name>.onrender.com` appeared in
    `vercel.json`, because that is the hostname Render gives a service whose
    subdomain is free. `file-explorer-api` was taken, so the real service is
    `file-explorer-api-xgxr.onrender.com` -- and the test therefore *passed* on
    the stale host and would have *failed* on the correct one. A guard that
    enforces the wrong value is worse than no guard: it is an argument against
    fixing the bug.

    So the name is matched as a **prefix**, which is what Render actually
    guarantees: your service name, optionally a hyphen and a random suffix,
    then `.onrender.com`. That still rejects the old service and any unrelated
    host, which is the property worth having.
    """
    text = RENDER.read_text(encoding="utf-8")
    service = re.search(r"^\s*-\s*type:\s*web\s*$\n\s*name:\s*(\S+)", text, re.M)
    assert service, "render.yaml declares no web service name"
    name = service.group(1)
    rewrite = VERCEL.read_text(encoding="utf-8")
    host = REWRITE_HOST.search(rewrite)
    assert host, "vercel.json has no https rewrite destination"
    pattern = rf"^{re.escape(name)}(-[a-z0-9]+)?\.onrender\.com$"
    assert re.match(pattern, host.group(1)), (
        f"render.yaml deploys {name!r} and vercel.json rewrites to {host.group(1)!r}, so the "
        "frontend would call a host nothing in this repository deploys to"
    )


@pytest.mark.invariant
def test_the_rewrite_keeps_the_path_and_stays_on_https() -> None:
    """The rewrite is what makes the cookie same-site, so its shape matters.

    Dropping `/api/:path*` from the destination sends every call to the service
    root, which answers 404 for all of them. Downgrading to `http` makes a
    `Secure` cookie unsendable. Neither failure produces an error anywhere in
    this repository -- they produce a demo that does not work.
    """
    rewrite = VERCEL.read_text(encoding="utf-8")
    assert '"source": "/api/:path*"' in rewrite, (
        "vercel.json no longer rewrites /api/:path*, so the browser would reach Vercel's own "
        "static hosting for every API call"
    )
    assert re.search(r'"destination"\s*:\s*"https://', rewrite), (
        "the rewrite destination is not https, and a Secure cookie cannot travel over http"
    )
    assert rewrite.count("/api/:path*") == 2, (
        "the destination dropped the :path* suffix, so every API call would land on the service "
        "root and answer 404"
    )


@pytest.mark.invariant
def test_the_frontend_build_is_a_file_not_a_dashboard_setting() -> None:
    """F-23's other half, found when the frontend would not deploy.

    `render.yaml` exists because the API's deployment lived only as dashboard
    settings: nobody could recreate it and nobody could see what it had been.
    The frontend had exactly the same problem and nobody noticed, because the
    frontend had never been deployed at all -- its build command, output
    directory and framework existed only in a web form, and the first evidence
    of that was a build failing against an archived repository.

    Vercel resolves these from `vercel.json` in preference to the dashboard, so
    putting them here makes the file authoritative rather than decorative.

    `npm ci` and not `npm install`: there is a committed lockfile, and `ci`
    fails when it disagrees with `package.json` instead of quietly resolving
    something else -- the same reasoning as the CI job.
    """
    config = json.loads(VERCEL.read_text(encoding="utf-8"))

    assert config.get("framework") == "vite", (
        "vercel.json does not declare the framework, so the preset falls back to whatever the "
        "dashboard happens to say"
    )
    build = config.get("buildCommand", "")
    assert "apps/web" in build, (
        f"vercel.json's buildCommand does not enter apps/web: {build!r}. The repository root has "
        "no package.json, so a build run there produces nothing and the last good deployment is "
        "served instead -- silently"
    )
    assert "npm ci" in build, (
        f"vercel.json's buildCommand does not use `npm ci`: {build!r}. There is a committed "
        "lockfile and `npm install` would ignore a disagreement with package.json"
    )
    assert config.get("outputDirectory") == "apps/web/dist", (
        "vercel.json does not declare where the build output lands, so Vercel looks in the "
        "repository root and finds nothing"
    )


@pytest.mark.invariant
def test_no_secret_is_committed_with_a_value() -> None:
    """A connection string in a public repository is a published credential.

    `sync: false` means the dashboard asks once and remembers; `generateValue`
    means nobody has to choose. Either is fine. A literal is not.
    """
    text = RENDER.read_text(encoding="utf-8")
    secrets = ("DATABASE_URL", "SECRET_KEY", "TRUST_PROXY_HOPS")
    offences = []
    for name in secrets:
        block = re.search(rf"key:\s*{name}\s*\n((?:\s+\w+:.*\n)+)", text)
        if block and not re.search(r"(sync:\s*false|generateValue:\s*true)", block.group(1)):
            offences.append(name)
    assert not offences, f"these are declared with a literal value in a public file: {offences}"


@pytest.mark.invariant
def test_a_free_instance_does_not_declare_a_paid_only_field() -> None:
    """Render refuses the whole Blueprint, so this is not a runtime surprise.

    `preDeployCommand` is available only to paid web services, private services
    and background workers. A Blueprint that declares it on a free instance type
    is rejected outright with *"pre-deploy command is not supported for free
    tier services"* -- so the first person to apply it gets an error instead of
    a deployment.

    This file carried exactly that for a day. It was written from memory of how
    Render ought to work rather than from the documentation, and nothing here
    could have caught it: the field is valid YAML, the file parses, and no test
    in this repository talks to Render.

    What a test *can* do is refuse the combination, which is what this is.
    """
    text = RENDER.read_text(encoding="utf-8")
    plans = re.findall(r"^\s*plan:\s*(\w+)", text, re.M)
    assert plans, "no plan declared; the pattern is wrong"

    if "free" in plans:
        offenders = [f for f in ("preDeployCommand",) if re.search(rf"^\s*{f}:", text, re.M)]
        assert not offenders, (
            f"{offenders} is paid-only and this Blueprint declares plan: free. Render rejects "
            "the Blueprint rather than the field, so nothing deploys at all. Migrations belong "
            "in buildCommand on a free instance"
        )


@pytest.mark.invariant
def test_the_deployed_python_version_is_pinned_and_matches_ci() -> None:
    """Two mechanisms, because one failing silently puts prod on another interpreter.

    Render's native Python runtime reads `PYTHON_VERSION` (full
    major.minor.patch) and falls back to `.python-version`. `runtimeVersion`,
    which this file used at first, is not how Python is pinned there at all.
    """
    pinned = Path(__file__).resolve().parents[1] / ".python-version"
    assert pinned.is_file(), "apps/api/.python-version is missing"
    version = pinned.read_text(encoding="utf-8").strip()

    text = RENDER.read_text(encoding="utf-8")
    declared = re.search(r"key:\s*PYTHON_VERSION\s*\n\s*value:\s*([\d.]+)", text)
    assert declared, "render.yaml does not declare PYTHON_VERSION"
    assert declared.group(1) == version, (
        f"render.yaml says {declared.group(1)} and .python-version says {version}"
    )

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    major_minor = ".".join(version.split(".")[:2])
    assert f'python-version: "{major_minor}"' in ci, (
        f"CI does not pin {major_minor}, so the deployment and the tests would differ"
    )
    # As a *field*, not as a word: the file explains in prose why that field is
    # the wrong mechanism, and a substring check would fail on its own comment.
    assert not re.search(r"^\s*runtimeVersion:", text, re.M), (
        "runtimeVersion is not how Render pins Python"
    )


@pytest.mark.invariant
def test_the_editor_config_points_at_paths_that_exist() -> None:
    """`pyrightconfig.json` silences a diagnostic, so it must not silence more.

    It turns off `reportMissingImports`, which is correct here and only here:
    D-13 means no package is installed on the developer's machine, so the
    editor reports every third-party import as unresolved and is right to. mypy
    in CI, where the packages exist, is what type-checks this code.

    The risk in a file like that is that it quietly stops applying -- a path
    that no longer exists, and the editor silently checks nothing. So the paths
    are asserted, and so is the thing it must never turn off.
    """
    import json

    config = json.loads((ROOT / "pyrightconfig.json").read_text(encoding="utf-8"))

    for key in ("include", "extraPaths"):
        for entry in config[key]:
            assert (ROOT / entry).exists(), f"pyrightconfig {key} points at {entry}, which is gone"

    assert config["reportUndefinedVariable"] == "error", (
        "the editor config stopped flagging undefined names, which is the one diagnostic that "
        "catches a typo nothing else sees until CI"
    )
    assert "apps/api/src" in config["extraPaths"], (
        "without this the editor cannot resolve file_explorer.* either, and a first-party import "
        "typo is a real bug"
    )
