#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
unset PYTHONPATH
export PYTHONNOUSERSITE=1
export UV_PROJECT_ENVIRONMENT="$ROOT/.venv"
export UV_LINK_MODE=copy
export UV_PYTHON_DOWNLOADS=never
PYTHON="$ROOT/.venv/bin/python"
PANGO_SYSROOT_SCRIPT="$ROOT/scripts/s01_pango_sysroot.sh"
MIN_PANGOCAIRO=1.30

# manimpango 0.6.1 has no Linux wheel and is compiled during setup, which needs the
# pangocairo development files. Use the system ones if present; otherwise prepare the
# project-local copy in .s01-sysroot (no administrator access, see the script header).
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

action="${1:-doctor}"
case "$action" in
  doctor)
    "${S01_PYTHON:-python3.12}" --version
    uv --version
    command -v cc >/dev/null 2>&1 && echo "C compiler: $(cc --version | head -n 1)" ||
      echo 'C compiler is unavailable; manimpango and pycairo cannot be built.'
    "${S01_PYTHON:-python3.12}" -c 'import pathlib, sysconfig; h = pathlib.Path(sysconfig.get_paths()["include"], "Python.h"); print("Python headers:", h if h.exists() else "MISSING (" + str(h) + ")")'
    if command -v pkg-config >/dev/null 2>&1; then
      pkg-config --exists cairo && echo "Cairo headers: system (cairo $(pkg-config --modversion cairo))" ||
        echo 'Cairo development metadata is unavailable; pycairo cannot be built.'
      if pkg-config --exists "pangocairo >= $MIN_PANGOCAIRO"; then
        echo "Pango headers: system (pangocairo $(pkg-config --modversion pangocairo))"
      elif PKG_CONFIG_PATH="$(bash "$PANGO_SYSROOT_SCRIPT" --pkg-config-path)" pkg-config --exists "pangocairo >= $MIN_PANGOCAIRO"; then
        echo 'Pango headers: project-local (.s01-sysroot)'
      else
        echo 'Pango headers: not installed; setup will download matching packages into .s01-sysroot (scripts/s01_pango_sysroot.sh).'
      fi
    else
      echo 'pkg-config is unavailable; native source builds will fail.'
    fi
    # Only needed to render WAV layouts that pydub's built-in reader cannot read (see README).
    for tool in ffmpeg ffprobe; do
      if command -v "$tool" >/dev/null 2>&1; then
        echo "$tool: $(command -v "$tool") ($("$tool" -version 2>/dev/null | head -n 1 | cut -d' ' -f1-3))"
      else
        echo "$tool: not found; plain WAV files still render, but WAV layouts that need FFmpeg will raise AudioDecoderDependencyError (see README)."
      fi
    done
    ;;
  setup)
    ensure_pango_headers
    uv sync --frozen --no-dev --extra translate --python "${S01_PYTHON:-python3.12}"
    uv pip install --python "$PYTHON" -r requirements-test.txt
    "$PYTHON" -c 'import pathlib, manim_voiceover; expected=pathlib.Path("src").resolve(); actual=pathlib.Path(manim_voiceover.__file__).resolve(); assert actual.is_relative_to(expected), actual; print("Using bundled source:", actual)'
    ;;
  test)
    "$PYTHON" -c 'import pathlib, manim_voiceover; assert pathlib.Path(manim_voiceover.__file__).resolve().is_relative_to(pathlib.Path("src").resolve())'
    # This checkout contains extra provider tests requiring the full dev environment.
    # Keep this minimal workflow scoped to the task's original core tests and S01 tests.
    "$PYTHON" -m pytest -q \
      tests/test_install.py tests/test_cache_models.py tests/test_core_behavior.py \
      tests/test_more_behavior.py tests/test_mutation_contracts.py tests/test_local_wav*.py
    ;;
  render|verify)
    take="${2:-a}"
    case "$take" in a|b) ;; *) echo 'Use take a or b.' >&2; exit 2 ;; esac
    mkdir -p media/s01
    export S01_AUDIO="$ROOT/media/s01/input.wav"
    if [[ "$action" == render ]]; then
      cp -- "fixtures/take_${take}.wav" "$S01_AUDIO"
      "$PYTHON" -m manim --renderer cairo -ql --disable_caching --config_file local-manim.cfg \
        --media_dir "$ROOT/media/s01" -o "baseline-${take}" examples/s01_baseline.py BaselineRecording
    fi
    "$PYTHON" scripts/verify_s01_render.py --take "$take"
    ;;
  *) echo 'Usage: bash scripts/s01.sh {doctor|setup|test|render [a|b]|verify [a|b]}' >&2; exit 2 ;;
esac
