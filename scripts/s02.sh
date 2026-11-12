#!/usr/bin/env bash
# S02: render the expected-reward cue lesson from a working folder, with one shared audio cache.
#
#   bash scripts/s02.sh render a       # manifest A: sections 0, 2 and 3; verified after rendering
#   bash scripts/s02.sh render b       # manifest B: calculation rebound to fixtures/s02/calculation_alt.wav
#   bash scripts/s02.sh verify [RUN]   # verify a recorded render again (default: the latest run), e.g. 01-a
#   bash scripts/s02.sh check-against RUN OTHER [CUE]
#                                      # RUN checked against OTHER's expectations must fail specifically on CUE
#                                      # (default: calculation), e.g. check-against 02-b 01-a
#
# Uses the .venv created by `bash scripts/s01.sh setup`. The original recordings are only
# copied (into media/s02/cues); media/s02 and its voiceover cache are never cleared, so
# successive renders (A, B, B again) share one cache. Each render gets a numbered record in
# media/s02/runs/: the expected timeline and source hashes are written BEFORE rendering; the
# source hashes are checked again afterwards, the cached copies are recorded, and
# scripts/verify_s02_render.py compares the render with the pre-render expectations.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
unset PYTHONPATH
export PYTHONNOUSERSITE=1
PYTHON="$ROOT/.venv/bin/python"
ASSETS="examples/rl_lessons/assets"
ALTERNATE="fixtures/s02/calculation_alt.wav"
MEDIA="$ROOT/media/s02"
WORK="$MEDIA/cues"
RUNS="$MEDIA/runs"
CACHE="$MEDIA/voiceovers"

# Every audio source and manifest whose bytes must not change.
list_sources() {
  find "$ASSETS" fixtures "$WORK" -maxdepth 2 -type f \( -name '*.wav' -o -name '*.json' \) | LC_ALL=C sort
}

# Copy $1 to $2 unless $2 already holds the same bytes (unchanged copies keep their identity).
place() {
  if ! cmp -s -- "$1" "$2"; then
    cp -- "$1" "$2"
    echo "Placed $2 (from $1)"
  fi
}

prepare_a() {
  mkdir -p "$WORK" "$RUNS"
  for section in section_0 section_2 section_3; do
    place "$ASSETS/$section.wav" "$WORK/$section.wav"
  done
  place "$ASSETS/expected_reward_cues.json" "$WORK/manifest-a.json"
}

# Manifest B is manifest A with only the calculation cue rebound to the alternate take.
prepare_b() {
  prepare_a
  place "$ALTERNATE" "$WORK/calculation_alt.wav"
  "$PYTHON" - "$WORK/manifest-a.json" "$WORK/manifest-b.json.new" <<'EOF'
import json, sys
a = json.loads(open(sys.argv[1], encoding="utf-8-sig").read())
b = {"version": a["version"], "cues": [{**e, "wav": "calculation_alt.wav"} if e["id"] == "calculation" else e for e in a["cues"]]}
changed = [(x, y) for x, y in zip(a["cues"], b["cues"]) if x != y]
assert [x["id"] for x, _ in changed] == ["calculation"], changed
open(sys.argv[2], "w", encoding="utf-8").write(json.dumps(b, indent=2) + "\n")
EOF
  place "$WORK/manifest-b.json.new" "$WORK/manifest-b.json"
  rm -f -- "$WORK/manifest-b.json.new"
}

snapshot_cache() {
  "$PYTHON" scripts/verify_s02_render.py snapshot-cache "$CACHE"
}

verify_run() {
  "$PYTHON" scripts/verify_s02_render.py verify "$1" >/dev/null
}

action="${1:-}"
take="${2:-}"
case "$action" in
  render)
    case "$take" in
      a) prepare_a ;;
      b)
        if [[ ! -f "$ALTERNATE" ]]; then
          echo "B needs the alternate calculation recording at $ALTERNATE. It is missing, so B is not rendered." >&2
          exit 3
        fi
        prepare_b
        ;;
      *) echo 'Use: bash scripts/s02.sh render {a|b}' >&2; exit 2 ;;
    esac
    count=$(find "$RUNS" -mindepth 1 -maxdepth 1 -type d | wc -l)
    run="$(printf '%02d' $((count + 1)))-$take"
    record="$RUNS/$run"
    mkdir "$record"
    manifest="$WORK/manifest-$take.json"
    # Predeclared before rendering: the expected timeline (with its tolerance) and the source hashes.
    "$PYTHON" scripts/s02_timeline.py expect "$manifest" >"$record/expected.json"
    list_sources | xargs sha256sum >"$record/sources-before.sha256"
    snapshot_cache >"$record/cache-before.json"
    S02_CUE_MANIFEST="$manifest" "$PYTHON" -m manim --renderer cairo -ql --disable_caching \
      --config_file local-manim.cfg --media_dir "$MEDIA" -o "run-$run" \
      examples/rl_lessons/expected_reward_cues.py ExpectedRewardCues
    list_sources | xargs sha256sum >"$record/sources-after.sha256"
    snapshot_cache >"$record/cache-after.json"
    if ! cmp -s "$record/sources-before.sha256" "$record/sources-after.sha256"; then
      diff "$record/sources-before.sha256" "$record/sources-after.sha256" >&2 || true
      echo "An audio source or manifest changed during the render." >&2
      exit 1
    fi
    echo "Run $run: video media/s02/videos/expected_reward_cues/480p15/run-$run.mp4; record $record"
    echo "Source hashes unchanged ($(wc -l <"$record/sources-after.sha256") files)."
    verify_run "$record"
    ;;
  verify)
    if [[ -n "$take" ]]; then
      record="$RUNS/$take"
    else
      record="$(find "$RUNS" -mindepth 1 -maxdepth 1 -type d | LC_ALL=C sort | tail -n 1)"
    fi
    if [[ -z "$record" || ! -d "$record" ]]; then
      echo "No render record ${take:-found} in $RUNS" >&2
      exit 2
    fi
    verify_run "$record"
    ;;
  check-against)
    other="${3:-}"
    cue="${4:-calculation}"
    if [[ ! -d "$RUNS/$take" || ! -f "$RUNS/$other/expected.json" ]]; then
      echo 'Use: bash scripts/s02.sh check-against RUN OTHER [CUE], e.g. check-against 02-b 01-a' >&2
      exit 2
    fi
    output="$MEDIA/checks/$take-against-$other.json"
    if [[ -e "$output" ]]; then
      output="${output%.json}-$(date -u +%Y%m%dT%H%M%SZ).json"
    fi
    "$PYTHON" scripts/verify_s02_render.py verify "$RUNS/$take" --expected "$RUNS/$other/expected.json" \
      --expect-mismatch "$cue" --output "$output" >/dev/null
    echo "Check: $output"
    ;;
  *) echo 'Usage: bash scripts/s02.sh {render {a|b}|verify [RUN]|check-against RUN OTHER [CUE]}' >&2; exit 2 ;;
esac
