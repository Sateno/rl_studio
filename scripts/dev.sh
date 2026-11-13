#!/usr/bin/env bash
# RL Studio developer commands for Linux and WSL. From Windows, use local-wsl.ps1, which runs them in WSL.
#
#   bash scripts/dev.sh doctor              # prerequisites
#   bash scripts/dev.sh setup               # project environment from uv.lock, plus the pinned test runner
#   bash scripts/dev.sh test                # focused offline test suite (FOCUSED_TESTS below)
#   bash scripts/dev.sh demo local-wav      # Local WAV Narration: render and verify takes A and B
#   bash scripts/dev.sh demo cue-bindings   # Cue Bindings: A -> B -> B with one shared cache, verified
#   bash scripts/dev.sh demo bookmarks      # Manual Narration Bookmarks: animations within one frame, verified
#
# Individual steps:
#   bash scripts/dev.sh local-wav render|verify a|b
#   bash scripts/dev.sh cue-bindings render a|b
#   bash scripts/dev.sh cue-bindings verify [RUN]                  # default: the highest-numbered run
#   bash scripts/dev.sh cue-bindings check-against RUN OTHER [CUE]  # must fail specifically on CUE
#   bash scripts/dev.sh bookmarks render|verify
#
# The project environment is $UV_PROJECT_ENVIRONMENT if set (scripts/rl_studio.sh sets it for the
# Windows launcher), otherwise .venv in the checkout. A test file can also be run on its own:
#   UV_NO_DEV=1 uv run --frozen pytest -v tests/test_render_workflow.py
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/render_workflow.sh
source "$ROOT/scripts/render_workflow.sh"
reject_renamed_variables

SETUP_PYTHON="${RL_STUDIO_PYTHON:-python3.12}"
PANGO_SYSROOT_SCRIPT="$ROOT/scripts/pango_sysroot.sh"
MIN_PANGOCAIRO=1.30
# Core tests plus every feature's tests; none needs a cloud SDK, a microphone or the network.
FOCUSED_TESTS=(
  tests/test_install.py tests/test_cache_models.py tests/test_core_behavior.py tests/test_more_behavior.py
  tests/test_mutation_contracts.py tests/test_mutmut_result_checker.py
  tests/test_render_workflow.py tests/test_render_verification.py
  tests/test_local_wav_service.py tests/test_local_wav_contracts.py tests/test_local_wav_bookmarks.py
  tests/test_cue_bindings.py tests/test_cue_bindings_verifier.py tests/test_expected_reward_lesson.py
)

usage() {
  sed -n '2,21p' "$ROOT/scripts/dev.sh" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

# manimpango 0.6.1 has no Linux wheel and is compiled during setup, which needs the pangocairo
# development files. Use the system ones if present; otherwise prepare the project-local copy in
# .pango-sysroot (no administrator access, see the script header).
ensure_pango_headers() {
  if pkg-config --exists "pangocairo >= $MIN_PANGOCAIRO" 2>/dev/null; then
    echo "Pango headers: system (pangocairo $(pkg-config --modversion pangocairo))"
    return
  fi
  bash "$PANGO_SYSROOT_SCRIPT"
  local sysroot_pkg_config_path
  sysroot_pkg_config_path="$(bash "$PANGO_SYSROOT_SCRIPT" --pkg-config-path)"
  export PKG_CONFIG_PATH="$sysroot_pkg_config_path${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
}

check_bundled_source() {
  "$PYTHON" -c 'import pathlib, manim_voiceover; expected=pathlib.Path("src").resolve(); actual=pathlib.Path(manim_voiceover.__file__).resolve(); assert actual.is_relative_to(expected), actual; print("Using bundled source:", actual)'
}

doctor() {
  "$SETUP_PYTHON" --version
  uv --version
  command -v cc >/dev/null 2>&1 && echo "C compiler: $(cc --version | head -n 1)" ||
    echo 'C compiler is unavailable; manimpango and pycairo cannot be built.'
  "$SETUP_PYTHON" -c 'import pathlib, sysconfig; h = pathlib.Path(sysconfig.get_paths()["include"], "Python.h"); print("Python headers:", h if h.exists() else "MISSING (" + str(h) + ")")'
  if command -v pkg-config >/dev/null 2>&1; then
    pkg-config --exists cairo && echo "Cairo headers: system (cairo $(pkg-config --modversion cairo))" ||
      echo 'Cairo development metadata is unavailable; pycairo cannot be built.'
    if pkg-config --exists "pangocairo >= $MIN_PANGOCAIRO"; then
      echo "Pango headers: system (pangocairo $(pkg-config --modversion pangocairo))"
    elif PKG_CONFIG_PATH="$(bash "$PANGO_SYSROOT_SCRIPT" --pkg-config-path)" pkg-config --exists "pangocairo >= $MIN_PANGOCAIRO"; then
      echo 'Pango headers: project-local (.pango-sysroot)'
    else
      echo 'Pango headers: not installed; setup will download matching packages into .pango-sysroot (scripts/pango_sysroot.sh).'
    fi
  else
    echo 'pkg-config is unavailable; native source builds will fail.'
  fi
  # Only needed to render WAV layouts that pydub's built-in reader cannot read (see docs/local_wav.md).
  for tool in ffmpeg ffprobe; do
    if command -v "$tool" >/dev/null 2>&1; then
      echo "$tool: $(command -v "$tool") ($("$tool" -version 2>/dev/null | head -n 1 | cut -d' ' -f1-3))"
    else
      echo "$tool: not found; plain WAV files still render, but WAV layouts that need FFmpeg will raise AudioDecoderDependencyError (see docs/local_wav.md)."
    fi
  done
  echo "Project environment: $UV_PROJECT_ENVIRONMENT"
}

setup() {
  ensure_pango_headers
  uv sync --frozen --no-dev --extra translate --python "$SETUP_PYTHON"
  uv pip install --python "$PYTHON" -r requirements-test.txt
  check_bundled_source
}

run_tests() {
  check_bundled_source >/dev/null
  if "$PYTHON" -c 'import pytest' 2>/dev/null; then
    "$PYTHON" -m pytest -q "${FOCUSED_TESTS[@]}" "$@"
  else  # an environment without the test runner (the Windows launcher's): add the pinned runner for this run only
    uv run --frozen --no-dev --extra translate --with-requirements requirements-test.txt python -m pytest -q "${FOCUSED_TESTS[@]}" "$@"
  fi
}

# ---------------------------------------------------------------------------
# Local WAV Narration: takes a and b are copied to the SAME working path and rendered with one cache
# ---------------------------------------------------------------------------

LOCAL_WAV_MEDIA="$ROOT/media/local_wav"

local_wav() {
  local action="${1:-}" take="${2:-a}"
  case "$action" in render | verify) ;; *) usage ;; esac
  case "$take" in a | b) ;; *) echo 'Use take a or b.' >&2; exit 2 ;; esac
  migrate_output_folder media/s01 media/local_wav
  mkdir -p "$LOCAL_WAV_MEDIA"
  export LOCAL_WAV_AUDIO="$LOCAL_WAV_MEDIA/input.wav"
  if [[ "$action" == render ]]; then
    cp -- "fixtures/take_${take}.wav" "$LOCAL_WAV_AUDIO"
    render_scene "$LOCAL_WAV_MEDIA" "baseline-${take}" examples/local_wav_baseline.py BaselineRecording
  fi
  "$PYTHON" scripts/verify_local_wav_render.py --take "$take"
}

# ---------------------------------------------------------------------------
# Cue Bindings: the expected-reward lesson from a working folder, with one shared cache.
#
# The original recordings are only copied (into media/cue_bindings/cues); media/cue_bindings and its
# voiceover cache are never cleared, so successive renders (A, B, B again) share one cache. Each
# render gets a numbered record in media/cue_bindings/runs/ (NN-take, one more than the highest
# existing number): the expected timeline and source hashes are written BEFORE rendering; the
# source hashes are checked again afterwards, the cached copies are recorded, and
# scripts/verify_cue_bindings_render.py compares the render with the pre-render expectations.
# ---------------------------------------------------------------------------

CUE_ASSETS="examples/rl_lessons/assets"
CUE_ALTERNATE="fixtures/cue_bindings/calculation_alt.wav"
CUE_MEDIA="$ROOT/media/cue_bindings"
CUE_WORK="$CUE_MEDIA/cues"
CUE_RUNS="$CUE_MEDIA/runs"
CUE_CACHE="$CUE_MEDIA/voiceovers"

cue_prepare_a() {
  mkdir -p "$CUE_WORK" "$CUE_RUNS"
  for section in section_0 section_2 section_3; do
    place "$CUE_ASSETS/$section.wav" "$CUE_WORK/$section.wav"
  done
  place "$CUE_ASSETS/expected_reward_cues.json" "$CUE_WORK/manifest-a.json"
}

# Manifest B is manifest A with only the calculation cue rebound to the alternate take.
cue_prepare_b() {
  cue_prepare_a
  place "$CUE_ALTERNATE" "$CUE_WORK/calculation_alt.wav"
  "$PYTHON" - "$CUE_WORK/manifest-a.json" "$CUE_WORK/manifest-b.json.new" <<'EOF'
import json, sys
a = json.loads(open(sys.argv[1], encoding="utf-8-sig").read())
b = {"version": a["version"], "cues": [{**e, "wav": "calculation_alt.wav"} if e["id"] == "calculation" else e for e in a["cues"]]}
changed = [(x, y) for x, y in zip(a["cues"], b["cues"]) if x != y]
assert [x["id"] for x, _ in changed] == ["calculation"], changed
open(sys.argv[2], "w", encoding="utf-8").write(json.dumps(b, indent=2) + "\n")
EOF
  place "$CUE_WORK/manifest-b.json.new" "$CUE_WORK/manifest-b.json"
  rm -f -- "$CUE_WORK/manifest-b.json.new"
}

cue_hash_sources() {
  hash_sources "$CUE_ASSETS" fixtures "$CUE_WORK"
}

cue_snapshot_cache() {
  "$PYTHON" scripts/verify_cue_bindings_render.py snapshot-cache "$CUE_CACHE"
}

cue_render() {
  local take="$1" run record manifest
  case "$take" in
    a) cue_prepare_a ;;
    b)
      if [[ ! -f "$CUE_ALTERNATE" ]]; then
        echo "B needs the alternate calculation recording at $CUE_ALTERNATE. It is missing, so B is not rendered." >&2
        exit 3
      fi
      cue_prepare_b
      ;;
    *) echo 'Use: bash scripts/dev.sh cue-bindings render {a|b}' >&2; exit 2 ;;
  esac
  run="$(printf '%02d' "$(next_run_number "$CUE_RUNS")")-$take"
  record="$CUE_RUNS/$run"
  mkdir "$record"
  manifest="$CUE_WORK/manifest-$take.json"
  # Predeclared before rendering: the expected timeline (with its tolerance) and the source hashes.
  "$PYTHON" scripts/expected_reward_timeline.py expect "$manifest" >"$record/expected.json"
  cue_hash_sources >"$record/sources-before.sha256"
  cue_snapshot_cache >"$record/cache-before.json"
  CUE_BINDINGS_MANIFEST="$manifest" render_scene "$CUE_MEDIA" "run-$run" \
    examples/rl_lessons/expected_reward_cues.py ExpectedRewardCues
  cue_hash_sources >"$record/sources-after.sha256"
  cue_snapshot_cache >"$record/cache-after.json"
  if ! cmp -s "$record/sources-before.sha256" "$record/sources-after.sha256"; then
    diff "$record/sources-before.sha256" "$record/sources-after.sha256" >&2 || true
    echo "An audio source or manifest changed during the render." >&2
    exit 1
  fi
  echo "Run $run: video media/cue_bindings/videos/expected_reward_cues/480p15/run-$run.mp4; record $record"
  echo "Source hashes unchanged ($(wc -l <"$record/sources-after.sha256") files)."
  "$PYTHON" scripts/verify_cue_bindings_render.py verify "$record" >/dev/null
}

cue_verify() {
  local name="${1:-}" record latest
  if [[ -n "$name" ]]; then
    record="$CUE_RUNS/$name"
  else
    latest="$(latest_run "$CUE_RUNS")"
    record="${latest:+$CUE_RUNS/$latest}"
  fi
  if [[ -z "$record" || ! -d "$record" ]]; then
    echo "No render record ${name:-found} in $CUE_RUNS" >&2
    exit 2
  fi
  "$PYTHON" scripts/verify_cue_bindings_render.py verify "$record" >/dev/null
}

# Sets CUE_CHECK to the saved result.
cue_check_against() {
  local run="${1:-}" other="${2:-}" cue="${3:-calculation}"
  if [[ -z "$run" || -z "$other" || ! -d "$CUE_RUNS/$run" || ! -f "$CUE_RUNS/$other/expected.json" ]]; then
    echo 'Use: bash scripts/dev.sh cue-bindings check-against RUN OTHER [CUE], e.g. check-against 02-b 01-a' >&2
    exit 2
  fi
  CUE_CHECK="$CUE_MEDIA/checks/$run-against-$other.json"
  if [[ -e "$CUE_CHECK" ]]; then
    CUE_CHECK="${CUE_CHECK%.json}-$(date -u +%Y%m%dT%H%M%SZ).json"
  fi
  echo "Checking $run against the expectations of $other: the failed checks listed next are expected;" \
    "the check passes only if they are specific to the $cue recording." >&2
  "$PYTHON" scripts/verify_cue_bindings_render.py verify "$CUE_RUNS/$run" --expected "$CUE_RUNS/$other/expected.json" \
    --expect-mismatch "$cue" --output "$CUE_CHECK" >/dev/null
  echo "Check: $CUE_CHECK"
}

cue_bindings() {
  migrate_output_folder media/s02 media/cue_bindings
  local action="${1:-}"
  shift || true
  case "$action" in
    render) cue_render "${1:-}" ;;
    verify) cue_verify "${1:-}" ;;
    check-against) cue_check_against "$@" ;;
    *) usage ;;
  esac
}

# ---------------------------------------------------------------------------
# Manual Narration Bookmarks: the expected-reward calculation revealed at bookmarks of its recording.
#
# The expectations (each bookmark's frame) and the source hashes are written to media/bookmarks
# BEFORE rendering; the render is then verified into media/bookmarks/verification.json. The
# voiceover cache media/bookmarks/voiceovers is kept between renders.
# ---------------------------------------------------------------------------

BOOKMARKS_MEDIA="$ROOT/media/bookmarks"
BOOKMARKS_ASSETS="examples/rl_lessons/assets"
BOOKMARKS_RECORDING="$BOOKMARKS_ASSETS/section_2.wav"
BOOKMARKS_FILE="$BOOKMARKS_ASSETS/expected_reward_bookmarks.json"
BOOKMARKS_VERIFIER="scripts/verify_local_wav_bookmarks_render.py"

bookmarks_render() {
  mkdir -p "$BOOKMARKS_MEDIA"
  # Predeclared before rendering: the expected timeline, the source hashes and the cache.
  "$PYTHON" "$BOOKMARKS_VERIFIER" expect "$BOOKMARKS_RECORDING" "$BOOKMARKS_FILE" >"$BOOKMARKS_MEDIA/expected.json"
  hash_sources "$BOOKMARKS_ASSETS" >"$BOOKMARKS_MEDIA/sources-before.sha256"
  "$PYTHON" "$BOOKMARKS_VERIFIER" snapshot-cache "$BOOKMARKS_MEDIA/voiceovers" >"$BOOKMARKS_MEDIA/cache-before.json"
  LOCAL_WAV_BOOKMARKS_AUDIO="$ROOT/$BOOKMARKS_RECORDING" LOCAL_WAV_BOOKMARKS_FILE="$ROOT/$BOOKMARKS_FILE" \
    render_scene "$BOOKMARKS_MEDIA" bookmarks examples/rl_lessons/expected_reward_bookmarks.py ExpectedRewardBookmarks
  hash_sources "$BOOKMARKS_ASSETS" >"$BOOKMARKS_MEDIA/sources-after.sha256"
  "$PYTHON" "$BOOKMARKS_VERIFIER" snapshot-cache "$BOOKMARKS_MEDIA/voiceovers" >"$BOOKMARKS_MEDIA/cache-after.json"
  if ! cmp -s "$BOOKMARKS_MEDIA/sources-before.sha256" "$BOOKMARKS_MEDIA/sources-after.sha256"; then
    diff "$BOOKMARKS_MEDIA/sources-before.sha256" "$BOOKMARKS_MEDIA/sources-after.sha256" >&2 || true
    echo "A recording or bookmark file changed during the render." >&2
    exit 1
  fi
  echo "Video: media/bookmarks/videos/expected_reward_bookmarks/480p15/bookmarks.mp4"
  bookmarks_verify
}

bookmarks_verify() {
  "$PYTHON" "$BOOKMARKS_VERIFIER" verify "$BOOKMARKS_MEDIA" --output "$BOOKMARKS_MEDIA/verification.json" >/dev/null
  echo "Verification: media/bookmarks/verification.json"
}

bookmarks() {
  case "${1:-}" in
    render) bookmarks_render ;;
    verify) bookmarks_verify ;;
    *) usage ;;
  esac
}

# ---------------------------------------------------------------------------
# Demos
# ---------------------------------------------------------------------------

demo_local_wav() {
  local_wav render a
  local_wav render b
  echo "Local WAV A/B verified: media/local_wav/verification-a.json, media/local_wav/verification-b.json"
}

# A -> B -> B with one shared cache; each render is verified against its own pre-render
# expectations, B must fail A's expectations specifically on the calculation (and the reverse),
# and the summary is written to media/cue_bindings/verification.json.
demo_cue_bindings() {
  local runs=() checks=() take
  for take in a b b; do
    cue_bindings render "$take"
    runs+=("$(latest_run "$CUE_RUNS")")
  done
  cue_check_against "${runs[1]}" "${runs[0]}"
  checks+=("$CUE_CHECK")
  cue_check_against "${runs[0]}" "${runs[1]}"
  checks+=("$CUE_CHECK")
  cue_check_against "${runs[2]}" "${runs[0]}"
  checks+=("$CUE_CHECK")
  "$PYTHON" scripts/verify_cue_bindings_render.py summarize-demo "$CUE_MEDIA" "${runs[@]}" --checks "${checks[@]}" \
    --output "$CUE_MEDIA/verification.json"
}

demo() {
  case "${1:-}" in
    local-wav) demo_local_wav ;;
    cue-bindings) demo_cue_bindings ;;
    bookmarks) bookmarks_render ;;
    *) echo 'Use: bash scripts/dev.sh demo {local-wav|cue-bindings|bookmarks}' >&2; exit 2 ;;
  esac
}

action="${1:-}"
shift || true
case "$action" in
  doctor) doctor ;;
  setup) setup ;;
  test) run_tests "$@" ;;
  demo) demo "$@" ;;
  local-wav) local_wav "$@" ;;
  cue-bindings) cue_bindings "$@" ;;
  bookmarks) bookmarks "$@" ;;
  *) usage ;;
esac
