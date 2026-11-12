#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="${RL_STUDIO_CONDA_PREFIX:-$HOME/miniforge3/envs/rl_studio_python}/bin:$PATH"
export UV_PROJECT_ENVIRONMENT="${RL_STUDIO_ENV:-$HOME/.virtualenvs/rl_studio}"
export UV_LINK_MODE=copy
export PYTHONNOUSERSITE=1
args=(--frozen --no-dev --extra translate)
case "${1:-status}" in
  sync)
    uv sync "${args[@]}" --python "${RL_STUDIO_CONDA_PREFIX:-$HOME/miniforge3/envs/rl_studio_python}/bin/python"
    uv run "${args[@]}" --with pytest==9.0.3 python -m pytest --version
    ;;
  test)
    mkdir -p media/local_validation
    uv run --offline "${args[@]}" --with pytest==9.0.3 python -m pytest -q \
      tests/test_install.py tests/test_cache_models.py tests/test_core_behavior.py \
      tests/test_more_behavior.py tests/test_mutation_contracts.py tests/test_mutmut_result_checker.py tests/test_local_wav*.py \
      tests/test_s02_cue_bindings.py tests/test_s02_expected_reward.py tests/test_s02_verifier.py \
      2>&1 | tee media/local_validation/pytest.log
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
  *) echo 'Usage: scripts/rl_studio.sh {sync|test|render|shell|status}' >&2; exit 2 ;;
esac
