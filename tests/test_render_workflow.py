"""Render workflow: run records, source hashing and renamed names in scripts/render_workflow.sh.

The shared workflow is used by ``scripts/dev.sh``; these tests drive it through
``scripts/dev.sh cue-bindings``. Regression tests for two bugs fixed in the former cue-binding
render script (now this workflow):

- Source hashing passed ``find`` output through plain ``xargs``, which splits names at
  blanks and treats quotes and backslashes specially. A source file, or a checkout
  path, containing such characters made the render fail before rendering and left a
  half-written run record behind.
- The next run number was the COUNT of folders in ``runs/`` plus one. After an earlier
  record was deleted, that number could already be in use: the render then failed, or
  a second record with the same number was created for the other take. ``verify``
  without a run also chose the last folder name in text order, so ``99-b`` came after
  ``100-a`` and any other folder could be chosen.

It also checks that environment variables and output folders renamed with the
feature-based names are handled safely: an old variable is rejected, and an old output folder is
renamed in place once, so its voiceover cache keeps its identity.

Most tests run the real scripts in a temporary checkout in which the project
interpreter is replaced by a stand-in. The stand-in runs the script's inline manifest
step with the real Python and replaces the timeline, render and verifier steps, so no
video is rendered. The test marked ``integration`` renders and verifies for real, from
a checkout path that contains spaces.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_SCRIPTS = ("dev.sh", "render_workflow.sh")
ASSETS = Path("examples") / "rl_lessons" / "assets"
pytestmark = pytest.mark.skipif(
    os.name == "nt" or not (ROOT / "scripts" / "dev.sh").is_file(),
    reason="requires the Linux Bash workflow and POSIX filenames",
)

STAND_IN_INTERPRETER = r"""#!/usr/bin/env bash
# Stand-in for the project interpreter. The inline manifest step runs with the real Python;
# the timeline, render and verifier steps are replaced, because these tests check the records.
set -euo pipefail
case "$1" in
  -) exec "$REAL_PYTHON" "$@" ;;
  scripts/expected_reward_timeline.py) echo '{}' ;;
  scripts/verify_cue_bindings_render.py)
    if [[ "$2" == snapshot-cache ]]; then echo '{}'; else printf '%s\n' "$3" >>"$VERIFIED_RECORDS"; fi
    ;;
  scripts/verify_local_wav_render.py) ;;
  -m) ;;
  *) echo "unexpected interpreter call: $*" >&2; exit 97 ;;
esac
"""


def make_checkout(root: Path) -> Path:
    """A minimal checkout for the cue-binding render workflow, with the stand-in interpreter."""
    (root / "scripts").mkdir(parents=True)
    for script in WORKFLOW_SCRIPTS:
        shutil.copy2(ROOT / "scripts" / script, root / "scripts" / script)
    assets = root / ASSETS
    assets.mkdir(parents=True)
    for name in ("section_0.wav", "section_2.wav", "section_3.wav"):
        (assets / name).write_bytes(b"placeholder recording " + name.encode())
    shutil.copy2(ROOT / ASSETS / "expected_reward_cues.json", assets / "expected_reward_cues.json")
    (root / "fixtures" / "cue_bindings").mkdir(parents=True)
    (root / "fixtures" / "cue_bindings" / "calculation_alt.wav").write_bytes(b"placeholder alternate take")
    for take in ("a", "b"):
        (root / "fixtures" / f"take_{take}.wav").write_bytes(b"placeholder take " + take.encode())
    interpreter = root / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text(STAND_IN_INTERPRETER, encoding="utf-8")
    interpreter.chmod(0o755)
    return root


@pytest.fixture
def checkout(tmp_path):
    return make_checkout(tmp_path / "checkout")


RENAMED_VARIABLES = {
    "S01_AUDIO": "LOCAL_WAV_AUDIO",
    "S02_CUE_MANIFEST": "CUE_BINDINGS_MANIFEST",
    "S01_PYTHON": "RL_STUDIO_PYTHON",
}


def runs_dir(root: Path) -> Path:
    return root / "media" / "cue_bindings" / "runs"


def workflow_env(root: Path, **extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in RENAMED_VARIABLES}
    env.update(UV_PROJECT_ENVIRONMENT=str(root / ".venv"), REAL_PYTHON=sys.executable)
    env.update(VERIFIED_RECORDS=str(root.parent / "verified-records.txt"), **extra)
    return env


def run_dev(root: Path, *args: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "scripts" / "dev.sh"), *args],
        env=workflow_env(root, **extra_env),
        capture_output=True,
        text=True,
        timeout=60,
    )


def run_workflow(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_dev(root, "cue-bindings", *args)


def verified_records(root: Path) -> list[str]:
    log = root.parent / "verified-records.txt"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def make_records(root: Path, names: list[str]) -> None:
    """Existing run records, each with a file that must survive later renders unchanged."""
    for name in names:
        (runs_dir(root) / name).mkdir(parents=True)
        (runs_dir(root) / name / "expected.json").write_text(f'{{"record": "{name}"}}\n', encoding="utf-8")


def rendered_run(result: subprocess.CompletedProcess[str]) -> str:
    match = re.search(r"^Run (\S+): ", result.stdout, re.MULTILINE)
    assert match, result.stdout + result.stderr
    return match.group(1)


_ESCAPES = {"\\": "\\", "n": "\n", "r": "\r"}


def read_sha256_listing(path: Path) -> dict[str, str]:
    """Parse sha256sum output, including its escaped form for names with a backslash or newline."""
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="surrogateescape").split("\n")[:-1]:
        escaped = line.startswith("\\")
        if escaped:
            line = line[1:]
        digest, name = line[:64], line[66:]
        if escaped:
            name = re.sub(r"\\(.)", lambda match: _ESCAPES[match.group(1)], name)
        assert name not in entries, f"{name!r} is listed twice"
        entries[name] = digest
    return entries


def expected_sources(root: Path) -> dict[str, str]:
    """Every WAV and JSON file the script must hash, found independently of the script."""
    work = root / "media" / "cue_bindings" / "cues"  # the script names working copies by absolute path
    starts = [(ASSETS.as_posix(), root / ASSETS), ("fixtures", root / "fixtures"), (str(work), work)]
    sources: dict[str, str] = {}
    for label, directory in starts:
        for path in directory.rglob("*"):
            depth = len(path.relative_to(directory).parts)
            if path.is_file() and depth <= 2 and path.suffix in (".wav", ".json"):
                sources[f"{label}/{path.relative_to(directory).as_posix()}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return sources


# ---------------------------------------------------------------------------
# Source hashing: names are passed NUL-separated, never split or unquoted
# ---------------------------------------------------------------------------

AWKWARD_NAMES = {
    "space": "my take.wav",
    "apostrophe": "Bob's take.wav",
    "double_quote": 'say "three".wav',
    "tab": "tab\tname.wav",
    "newline": "new\nline.wav",
    "backslash": "back\\slash.wav",
}


@pytest.mark.parametrize("name", AWKWARD_NAMES.values(), ids=AWKWARD_NAMES.keys())
def test_source_hashes_list_every_file_once_when_its_name_needs_quoting(checkout, name):
    (checkout / "fixtures" / name).write_bytes(b"a source whose name needs quoting")

    result = run_workflow(checkout, "render", "a")

    assert result.returncode == 0, result.stderr
    record = runs_dir(checkout) / rendered_run(result)
    before = read_sha256_listing(record / "sources-before.sha256")
    assert before == expected_sources(checkout)
    assert f"fixtures/{name}" in before
    assert (record / "sources-after.sha256").read_bytes() == (record / "sources-before.sha256").read_bytes()


def test_source_hashes_work_when_the_checkout_path_contains_spaces(tmp_path):
    root = make_checkout(tmp_path / "check out" / "rl studio")

    result = run_workflow(root, "render", "b")

    assert result.returncode == 0, result.stderr
    before = read_sha256_listing(runs_dir(root) / rendered_run(result) / "sources-before.sha256")
    assert before == expected_sources(root)
    assert str(root / "media" / "cue_bindings" / "cues" / "calculation_alt.wav") in before


# ---------------------------------------------------------------------------
# Run numbers: one more than the highest existing record, never the count
# ---------------------------------------------------------------------------

NUMBERING_CASES = {
    "first_run": ([], "a", "01-a"),
    "first_deleted_same_take": (["02-b", "03-b"], "b", "04-b"),
    "middle_deleted_other_take": (["01-a", "03-b"], "a", "04-a"),
    "only_a_later_run_left": (["05-a"], "b", "06-b"),
    "decimal_08_09": (["08-a", "09-b"], "a", "10-a"),
    "past_99": (["98-a", "99-b"], "a", "100-a"),
}


@pytest.mark.parametrize("existing, take, expected", NUMBERING_CASES.values(), ids=NUMBERING_CASES.keys())
def test_next_run_number_follows_the_highest_existing_record(checkout, existing, take, expected):
    make_records(checkout, existing)

    result = run_workflow(checkout, "render", take)

    assert result.returncode == 0, result.stderr
    assert rendered_run(result) == expected
    assert sorted(path.name for path in runs_dir(checkout).iterdir()) == sorted([*existing, expected])
    for name in existing:  # earlier records are left exactly as they were
        assert (runs_dir(checkout) / name / "expected.json").read_text(encoding="utf-8") == f'{{"record": "{name}"}}\n'
    assert verified_records(checkout) == [str(runs_dir(checkout) / expected)]


def test_entries_that_are_not_run_records_do_not_change_the_number(checkout):
    make_records(checkout, ["01-a"])
    for name in ("scratch", "zz-notes", "7-x", "03-b.bak"):
        (runs_dir(checkout) / name).mkdir()
    (runs_dir(checkout) / "05-a").write_text("a file, not a record", encoding="utf-8")

    result = run_workflow(checkout, "render", "b")

    assert result.returncode == 0, result.stderr
    assert rendered_run(result) == "02-b"


# ---------------------------------------------------------------------------
# verify without a run: the highest-numbered record
# ---------------------------------------------------------------------------

LATEST_CASES = {
    "two_digit": (["09-b", "10-a"], "10-a"),
    "past_99": (["99-b", "100-a"], "100-a"),
    "other_folders_ignored": (["01-a", "02-b", "zz-notes"], "02-b"),
}


@pytest.mark.parametrize("existing, expected", LATEST_CASES.values(), ids=LATEST_CASES.keys())
def test_verify_without_a_run_checks_the_highest_numbered_record(checkout, existing, expected):
    make_records(checkout, existing)

    result = run_workflow(checkout, "verify")

    assert result.returncode == 0, result.stderr
    assert verified_records(checkout) == [str(runs_dir(checkout) / expected)]


def test_verify_without_any_run_record_reports_that_none_was_found(checkout):
    (runs_dir(checkout) / "scratch").mkdir(parents=True)

    result = run_workflow(checkout, "verify")

    assert result.returncode == 2
    assert "No render record found" in result.stderr
    assert verified_records(checkout) == []


def test_verify_of_a_named_run_is_unchanged(checkout):
    make_records(checkout, ["01-a", "02-b"])

    result = run_workflow(checkout, "verify", "01-a")

    assert result.returncode == 0, result.stderr
    assert verified_records(checkout) == [str(runs_dir(checkout) / "01-a")]


# ---------------------------------------------------------------------------
# A real render and verification from a checkout path with spaces
# ---------------------------------------------------------------------------

REAL_CHECKOUT_FILES = (
    "local-manim.cfg",
    "scripts/dev.sh",
    "scripts/render_workflow.sh",
    "scripts/render_verification.py",
    "scripts/expected_reward_timeline.py",
    "scripts/verify_cue_bindings_render.py",
    "examples/rl_lessons/expected_reward_cues.py",
)
# The environment running these tests: the Windows launcher's (UV_PROJECT_ENVIRONMENT) or .venv.
PROJECT_ENVIRONMENT = Path(os.environ.get("UV_PROJECT_ENVIRONMENT", ROOT / ".venv"))


@pytest.mark.integration
@pytest.mark.skipif(not (PROJECT_ENVIRONMENT / "bin" / "python").is_file(), reason="needs the project environment (setup)")
def test_real_render_from_a_checkout_path_with_spaces_is_recorded_and_verified(tmp_path):
    root = tmp_path / "check out" / "rl studio"
    for relative in REAL_CHECKOUT_FILES:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, root / relative)
    shutil.copytree(ROOT / ASSETS, root / ASSETS)
    shutil.copytree(ROOT / "fixtures", root / "fixtures")
    shutil.copy2(ROOT / "fixtures" / "take_a.wav", root / "fixtures" / "my take.wav")
    env = {key: value for key, value in os.environ.items() if key not in RENAMED_VARIABLES}
    env["UV_PROJECT_ENVIRONMENT"] = str(PROJECT_ENVIRONMENT)

    result = subprocess.run(
        ["bash", str(root / "scripts" / "dev.sh"), "cue-bindings", "render", "a"], env=env, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-4000:]
    record = runs_dir(root) / "01-a"
    assert '"passed": true' in (record / "verification.json").read_text(encoding="utf-8")
    assert "fixtures/my take.wav" in read_sha256_listing(record / "sources-before.sha256")


# ---------------------------------------------------------------------------
# Feature-based names: renamed environment variables and output folders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("old", RENAMED_VARIABLES)
def test_renamed_environment_variable_is_rejected_with_its_new_name(checkout, old):
    result = run_dev(checkout, "cue-bindings", "render", "a", **{old: "/somewhere/else"})

    assert result.returncode == 2
    assert f"{old} was renamed to {RENAMED_VARIABLES[old]}" in result.stderr
    assert not (checkout / "media").exists()  # nothing was prepared or rendered


OUTPUT_FOLDERS = {
    "local_wav": ("s01", "local_wav", ("local-wav", "verify", "a")),
    "cue_bindings": ("s02", "cue_bindings", ("cue-bindings", "verify", "01-a")),
}


@pytest.mark.parametrize("old, new, command", OUTPUT_FOLDERS.values(), ids=OUTPUT_FOLDERS.keys())
def test_old_output_folder_is_renamed_in_place_with_its_cache(checkout, old, new, command):
    cached = checkout / "media" / old / "voiceovers" / "cached-copy.wav"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"cached copy")
    (checkout / "media" / old / "runs" / "01-a").mkdir(parents=True)
    identity = os.stat(cached)

    result = run_dev(checkout, *command)

    assert result.returncode == 0, result.stderr
    moved = checkout / "media" / new / "voiceovers" / "cached-copy.wav"
    assert not (checkout / "media" / old).exists()
    after = os.stat(moved)
    same_identity = (after.st_ino, after.st_mtime_ns, after.st_ctime_ns) == (
        identity.st_ino,
        identity.st_mtime_ns,
        identity.st_ctime_ns,
    )
    assert same_identity, "the cached copy was copied instead of renamed"
    assert f"Renamed the output folder media/{old} to media/{new}" in result.stdout


def test_render_uses_new_output_folder_and_preserves_legacy_folder(checkout):
    make_records(checkout, ["01-a"])
    old_record = checkout / "media" / "s02" / "runs" / "01-a"
    old_record.mkdir(parents=True)
    evidence = old_record / "verification.json"
    evidence.write_text('{"passed": true}', encoding="utf-8")

    result = run_workflow(checkout, "render", "b")

    assert result.returncode == 0, result.stderr
    assert rendered_run(result) == "02-b"
    assert sorted(path.name for path in runs_dir(checkout).iterdir()) == ["01-a", "02-b"]
    assert evidence.read_text(encoding="utf-8") == '{"passed": true}'
