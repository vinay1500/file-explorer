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
