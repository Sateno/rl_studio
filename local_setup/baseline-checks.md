# Initial baseline checks

The published baseline preserves Manim Voiceover v0.4.0 package code and its lockfile. These checks were run locally in WSL2/Ubuntu 24.04 using a separate `rl_studio_ci` uv environment with the full frozen development dependencies. The smaller daily-work environment remains separate.

## Passing checks

- Ruff formatting and lint: passed.
- `ty check src/manim_voiceover`: passed.
- Strict mypy: passed for 23 source files.
- Full pytest suite: **127 passed**, **97.70% coverage**, exceeding the 85% requirement.
- Slophammer duplication check: zero candidates.
- Slophammer structural check: no findings.
- Local mathematical video with Windows Zira narration: rendered and verified; details in `LOCAL_SETUP.md`.

## Unresolved checks

1. **Dependency audit:** `pip-audit` reports 181 advisory rows across 15 inherited dependency packages. Rows include duplicate advisory identifiers; this is not a claim of 181 distinct exploitable issues. See `dependency-audit.txt` for the observed versions and reported fixes. The lockfile has not been silently upgraded and the audit gate remains enabled.
2. **Mutation gate:** the complete four-worker run tested 3,207 mutations: 3,198 killed and 9 timed out. A one-worker retry killed the nine timed-out cases, but that targeted invocation regenerated the result set and marked the other 3,198 cases unchecked. A fresh complete run with controlled concurrency is needed before declaring this gate passing. No package source or tests were changed to suppress findings.
3. **Slophammer execution:** `slophammer-py@0.3.0 check . --execute` failed because its launched type checker could not resolve the `pip` import. Direct ty and mypy both passed in the project CI environment. The checker environment needs investigation; no type-check rule was weakened.

These are recorded starting conditions, not a fully passing CI claim. Prioritize dependency and validation-runner remediation before S01 feature work. Public repository access does not itself establish Alignerr task eligibility or platform compatibility.

## Reproduction of the broader checks

Install Ubuntu's `portaudio19-dev` in addition to the native dependencies in `LOCAL_SETUP.md`, then use a separate environment:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.virtualenvs/rl_studio_ci"
export UV_LINK_MODE=copy
uv sync --dev --frozen --python "$HOME/miniforge3/envs/rl_studio_python/bin/python"
export PATH="$UV_PROJECT_ENVIRONMENT/bin:$PATH"
uv run ruff format --check .
uv run ruff check .
uv run ty check src/manim_voiceover
uv run mypy
uv run pytest --cov=manim_voiceover --cov-fail-under=85
uvx slophammer-py@0.3.0 dry .
uv run mutmut run --max-children 1
uv run python scripts/check_mutmut_results.py
uv run pip-audit
uvx slophammer-py@0.3.0 check .
uvx slophammer-py@0.3.0 check . --execute
```
