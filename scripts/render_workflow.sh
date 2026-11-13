# shellcheck shell=bash
# Shared render workflow for scripts/dev.sh. Sourced, not run; expects ROOT (the checkout) to be set
# and to be the working directory.
#
# Provides the project interpreter, the guard against renamed environment variables, the one-time
# rename of old output folders, working copies, NUL-safe source hashing, numbered run records and
# the standard Manim render command used by every feature demo.

unset PYTHONPATH
export PYTHONNOUSERSITE=1
# scripts/rl_studio.sh (the Linux side of the Windows launcher) sets UV_PROJECT_ENVIRONMENT to its
# environment in Linux storage; otherwise the checkout's .venv is used.
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$ROOT/.venv}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
export UV_PYTHON_DOWNLOADS=never
# The project environment omits the development dependency group. This is a shell setting only:
# the dependency defaults in pyproject.toml and uv.lock are unchanged.
export UV_NO_DEV=1
PYTHON="$UV_PROJECT_ENVIRONMENT/bin/python"

# Environment variables renamed to the feature-based names. An old name is rejected: ignoring it
# would silently render the default input instead of the one the caller asked for.
RENAMED_VARIABLES=("S01_AUDIO:LOCAL_WAV_AUDIO" "S02_CUE_MANIFEST:CUE_BINDINGS_MANIFEST" "S01_PYTHON:RL_STUDIO_PYTHON")

reject_renamed_variables() {
  local pair old new
  for pair in "${RENAMED_VARIABLES[@]}"; do
    old="${pair%%:*}"
    new="${pair#*:}"
    if [[ -n "${!old+set}" ]]; then
      echo "$old was renamed to $new. Unset $old and set $new instead." >&2
      exit 2
    fi
  done
}

# Output folders were renamed with the features (media/s01 -> media/local_wav, media/s02 ->
# media/cue_bindings). Move the old folder only when the new path is absent.
# When both exist, the current workflow uses the new path and leaves earlier evidence in place.
migrate_output_folder() {
  local old="$1" new="$2"
  [[ -e "$old" ]] || return 0
  [[ ! -e "$new" ]] || return 0
  mv -- "$old" "$new"
  echo "Renamed the output folder $old to $new (cache and records kept)."
}

# Copy $1 to $2 unless $2 already holds the same bytes (unchanged copies keep their identity).
place() {
  if ! cmp -s -- "$1" "$2"; then
    cp -- "$1" "$2"
    echo "Placed $2 (from $1)"
  fi
}

# SHA-256 of every WAV and JSON file up to two levels inside the given folders, in byte order.
# Names are passed NUL-separated, so names and the checkout path may contain blanks, quotes,
# backslashes or newlines. (sha256sum escapes names containing a backslash or newline.)
hash_sources() {
  find "$@" -maxdepth 2 -type f \( -name '*.wav' -o -name '*.json' \) -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum --
}

# "NUMBER NAME" for every run record in folder $1 (a folder named NN-a or NN-b), in numeric order.
# Other entries are ignored. Numbers are decimal: 08 and 09 are not read as octal.
run_records() {
  local path name
  for path in "$1"/*; do
    name="${path##*/}"
    if [[ -d "$path" && "$name" =~ ^([0-9]+)-[ab]$ ]]; then
      printf '%d %s\n' "$((10#${BASH_REMATCH[1]}))" "$name"
    fi
  done | LC_ALL=C sort -n -k1,1
}

# One more than the highest record number in $1 (not the number of records), so a deleted
# earlier record never makes a render reuse a number.
next_run_number() {
  local highest
  highest="$(run_records "$1" | tail -n 1 | cut -d ' ' -f 1)"
  echo $((${highest:-0} + 1))
}

# The highest-numbered record in $1, or nothing.
latest_run() {
  run_records "$1" | tail -n 1 | cut -d ' ' -f 2
}

# Render SCENE from FILE into MEDIA as OUTPUT with the settings every demo uses: Cairo, 480p15,
# Manim's own animation cache disabled (the voiceover cache is separate and always used).
render_scene() {
  local media="$1" output="$2" file="$3" scene="$4"
  "$PYTHON" -m manim --renderer cairo -ql --disable_caching --config_file local-manim.cfg \
    --media_dir "$media" -o "$output" "$file" "$scene"
}
