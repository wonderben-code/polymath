# ruff: noqa: E501  (the desk stand-in below is a shell script kept on single lines)
"""scripts/connect-meta.sh, run end to end against stand-ins for the server.

The helper only orchestrates the operator desk's own reviewed steps, so the
stand-ins record each step and answer like the desk does. The files it writes
are then checked with the server's real validation: the readiness proposal and
its quote, the provider attestation, the review and the public limits.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymath import meta_readiness_contracts as readiness
from polymath.meta_probe_contracts import ProviderAccountAttestation
from polymath.meta_research_contracts import NativeResearchReview
from polymath.public_research_contracts import PublicLimits

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "connect-meta.sh"
KEY = "sk-test-1234567890abcdef"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None, reason="needs bash and jq"
)

DESK = r"""#!/usr/bin/env bash
mkdir -p "$POLYMATH_DESK_STATE"
echo "desk $1" >> "$POLYMATH_META_STATE/steps.log"
case "$1" in
  proposal) echo '== quote'; echo '{}'; echo '== saved'; echo '{"id":"rid-1"}';;
  probe) echo job-7 > "$POLYMATH_DESK_STATE/last-probe-job"; echo '== armed'; echo '{}';;
  probe-run) echo ran;;
  get) case "$2" in
    budget/verification/jobs/*) echo "{\"native_status\":\"$PROBE_RESULT\",\"receipt\":{\"id\":\"rcpt-9\"}}";;
    budget/research/public/current) echo '{"activation":{"id":"act-1","manifest":{"valid_until":"2099-01-01T00:00:00Z"}}}';;
  esac;;
  public) echo "public $2 $3 $7" >> "$POLYMATH_META_STATE/steps.log"; echo '== activated'; echo '{}';;
  revoke-public) echo '{}';;
esac
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def sandbox(tmp_path: Path):
    opt = tmp_path / "opt"
    (opt / "deploy").mkdir(parents=True)
    (opt / "scripts").mkdir()
    env = (ROOT / "deploy" / "production.env.example").read_text()
    env = env.replace(
        "POLYMATH_DOMAIN=polymath.example.org", "POLYMATH_DOMAIN=research.example.org"
    )
    env = env.replace("OPERATOR_BOOTSTRAP_TOKEN=\n", "OPERATOR_BOOTSTRAP_TOKEN=abc123\n")
    (opt / "deploy" / "production.env").write_text(env)
    shutil.copy(SCRIPT, opt / "scripts" / "connect-meta.sh")
    _executable(opt / "scripts" / "operator-desk.sh", DESK)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(
        bin_dir / "docker",
        '#!/usr/bin/env bash\necho "docker $*" >> "$POLYMATH_META_STATE/steps.log"\n',
    )
    _executable(
        bin_dir / "curl",
        '#!/usr/bin/env bash\nfor a in "$@"; do last="$a"; done\necho "page $last"\n',
    )
    state = tmp_path / "state"
    return {
        "opt": opt,
        "state": state,
        "cron": tmp_path / "cron",
        "env": {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "POLYMATH_DIR": str(opt),
            "POLYMATH_META_STATE": str(state),
            "POLYMATH_CRON_FILE": str(tmp_path / "cron"),
            "PROBE_RESULT": "passed",
        },
    }


def _preflight(sandbox, failed: int) -> None:
    (sandbox["opt"] / "scripts" / "meta-preflight.py").write_text(
        f'print("Summary: 3 passed, 0 warnings, {failed} failed.")\n'
    )


def _run(sandbox, *args, answers: str = "", **extra_env):
    return subprocess.run(
        ["bash", str(sandbox["opt"] / "scripts" / "connect-meta.sh"), *args],
        input=answers,
        text=True,
        capture_output=True,
        env={**sandbox["env"], **extra_env},
        start_new_session=True,  # no controlling terminal, so answers come from stdin
        timeout=60,
    )


def _settings(sandbox) -> dict[str, str]:
    lines = (sandbox["opt"] / "deploy" / "production.env").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))


# key, model, prices ok, billing id, Meta limit, available, total, daily,
# per-visitor daily, investigations, concept art, auto renew, go ahead
ANSWERS = f"{KEY}\n\n\nacct.123:abc\n100\n\n\n\n\n\n\n\n\n"


def test_setup_switches_research_on_with_valid_reviewed_files(sandbox):
    _preflight(sandbox, 0)
    result = _run(sandbox, answers=ANSWERS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Research is on at https://research.example.org" in result.stdout
    assert KEY not in result.stdout + result.stderr

    settings = _settings(sandbox)
    assert settings["MODEL_API_KEY"] == KEY
    assert settings["META_MODEL"] == "muse-spark-1.3"
    assert settings["PAID_META_PROBE_ENABLED"] == "false"
    assert settings["PAID_META_RESEARCH_ENABLED"] == "true"
    assert settings["PUBLIC_META_RESEARCH_ENABLED"] == "true"
    assert settings["PAID_META_IMAGE_ENABLED"] == "true"
    assert settings["BUDGET_MAX_HARD_CAP_MICRODOLLARS"] == "50000000"
    assert settings["PUBLIC_MAX_DELEGATION_SECONDS"] == str(14 * 86400)
    mode = (sandbox["opt"] / "deploy" / "production.env").stat().st_mode & 0o777
    assert mode == 0o600

    steps = (sandbox["state"] / "steps.log").read_text().splitlines()
    desk = [line for line in steps if line.startswith("desk")]
    assert desk[:5] == ["desk proposal", "desk probe", "desk probe-run", "desk get", "desk public"]
    assert "public rid-1 rcpt-9" in "\n".join(steps)
    assert any("--profile research" in line for line in steps)
    assert "renew-if-due" in sandbox["cron"].read_text()
    for name in ("proposal.json", "attestation.json", "review.json", "limits.json"):
        assert KEY not in (sandbox["state"] / name).read_text()

    # The server's own validation accepts every reviewed file, as the desk sends it.
    now = datetime.now(UTC)
    observed = (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    load = lambda name: json.loads((sandbox["state"] / name).read_text())  # noqa: E731
    proposal = readiness.MetaReadinessProposal.model_validate(load("proposal.json"))
    readiness.validate_proposal_time(proposal, now)
    quote = readiness.quote_proposal(proposal)
    assert quote.per_call_reservation_microdollars == 1_380_352
    attestation = ProviderAccountAttestation.model_validate(
        {**load("attestation.json"), "observed_at": observed}
    )
    review = load("review.json")
    NativeResearchReview.model_validate({**review, "observed_at": observed})
    limits = PublicLimits.model_validate(load("limits.json"))
    assert attestation.provider_hard_cap_microdollars >= proposal.account.hard_cap_microdollars
    assert proposal.account.run_cap_microdollars >= quote.per_call_reservation_microdollars
    assert limits.public_daily_cap_microdollars <= proposal.account.daily_cap_microdollars
    assert limits.session_daily_cap_microdollars <= limits.public_daily_cap_microdollars


def test_rejected_key_spends_nothing_and_leaves_research_off(sandbox):
    _preflight(sandbox, 2)
    result = _run(sandbox, answers=ANSWERS)
    assert result.returncode != 0
    assert "Nothing was spent" in result.stderr
    assert "Research stays off" in result.stderr
    settings = _settings(sandbox)
    assert settings["PAID_META_PROBE_ENABLED"] == "false"
    assert settings["PUBLIC_META_RESEARCH_ENABLED"] == "false"
    steps = (sandbox["state"] / "steps.log").read_text()
    assert "desk probe" not in steps and "desk public" not in steps


def test_failed_check_call_leaves_research_off(sandbox):
    _preflight(sandbox, 0)
    result = _run(sandbox, answers=ANSWERS, PROBE_RESULT="failed")
    assert result.returncode != 0
    assert "did not pass" in result.stderr
    settings = _settings(sandbox)
    assert settings["PAID_META_PROBE_ENABLED"] == "false"
    assert settings["PAID_META_RESEARCH_ENABLED"] == "false"
    assert "desk public" not in (sandbox["state"] / "steps.log").read_text()


def test_renew_stop_and_status(sandbox):
    _preflight(sandbox, 0)
    assert _run(sandbox, answers=ANSWERS).returncode == 0

    due = _run(sandbox, "renew-if-due")
    assert due.returncode == 0 and "nothing to do" in due.stdout

    stopped = _run(sandbox, "stop")
    assert stopped.returncode == 0 and "spending is off" in stopped.stdout
    assert _settings(sandbox)["PUBLIC_META_RESEARCH_ENABLED"] == "false"
    assert not sandbox["cron"].exists()

    renewed = _run(sandbox, "renew")
    assert renewed.returncode == 0 and "research stays on" in renewed.stdout
    settings = _settings(sandbox)
    assert settings["PUBLIC_META_RESEARCH_ENABLED"] == "true"
    assert settings["PAID_META_IMAGE_ENABLED"] == "true"

    status = _run(sandbox, "status")
    assert status.returncode == 0 and "Open until" in status.stdout
