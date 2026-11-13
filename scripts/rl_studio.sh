#!/usr/bin/env bash
# Linux side of the Windows launcher (local.ps1). Uses the native setup: Conda Python
# (rl_studio_python) and the uv environment in Linux storage ($HOME/.virtualenvs/rl_studio).
#
#   bash scripts/rl_studio.sh setup        # same as sync: the environment from uv.lock (--no-dev --extra translate)
#   bash scripts/rl_studio.sh test         # focused offline test suite (scripts/dev.sh test), offline
#   bash scripts/rl_studio.sh demo TARGET  # scripts/dev.sh demo TARGET: local-wav, cue-bindings or bookmarks
#   bash scripts/rl_studio.sh render       # the local smoke lesson (examples/rl_lessons/local_smoke.py)
#   bash scripts/rl_studio.sh shell|status
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="${RL_STUDIO_CONDA_PREFIX:-$HOME/miniforge3/envs/rl_studio_python}/bin:$PATH"
export UV_PROJECT_ENVIRONMENT="${RL_STUDIO_ENV:-$HOME/.virtualenvs/rl_studio}"
export UV_LINK_MODE=copy
export PYTHONNOUSERSITE=1
export UV_NO_DEV=1  # shell-only: dependency defaults and uv.lock are unchanged
args=(--frozen --no-dev --extra translate)
case "${1:-status}" in
  sync | setup)
    uv sync "${args[@]}" --python "${RL_STUDIO_CONDA_PREFIX:-$HOME/miniforge3/envs/rl_studio_python}/bin/python"
    # Caches the pinned test runner for the offline `test`; it is not installed into the environment.
    uv run "${args[@]}" --with-requirements requirements-test.txt python -m pytest --version
    ;;
  test)
    mkdir -p media/local_validation
    UV_OFFLINE=1 bash scripts/dev.sh test 2>&1 | tee media/local_validation/pytest.log
    ;;
  demo)
    bash scripts/dev.sh demo "${2:-}"
    ;;
  render)
    mkdir -p media/local_validation
    uv run --offline "${args[@]}" python scripts/prepare_local_audio.py
    uv run --offline "${args[@]}" python -m manim --renderer cairo -ql --disable_caching \
      --config_file local-manim.cfg \
      examples/rl_lessons/local_smoke.py LocalSmoke 2>&1 | tee media/local_validation/render.log
    uv run --offline "${args[@]}" python scripts/verify_local_render.py
    ;;
  shell)
    source "$UV_PROJECT_ENVIRONMENT/bin/activate"
    exec bash --noprofile --norc -i
    ;;
  status)
    uv --version
    uv run --offline "${args[@]}" python -c 'import sys, manim, manim_voiceover; print(sys.executable); print(sys.version); print("Manim", manim.__version__, "Voiceover", manim_voiceover.__version__); print(manim_voiceover.__file__)'
    ;;
  *) echo 'Usage: scripts/rl_studio.sh {setup|sync|test|demo TARGET|render|shell|status}' >&2; exit 2 ;;
esac
