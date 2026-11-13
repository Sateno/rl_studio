# Baseline validation

The dependency and validation cleanup preserves the Manim Voiceover v0.4.0 package implementation and API. Its dependencies are now maintained in RL Studio's lockfile. Verification was performed in WSL2/Ubuntu 24.04 with Python 3.12.16 and uv 0.13.7.

## Results

- Lockfile consistency, Ruff formatting and lint: passed.
- ty and strict mypy: passed.
- Full pytest suite: **127 passed**, **97.70% coverage** (85% required).
- Full mutation run with two workers: **3,207 killed**, zero survivors, timeouts or unchecked mutants. The mutation gate passed.
- Dependency audit: **zero reported vulnerabilities**, across 184 dependency records. Audit time: `2026-11-10T14:13:41.188080+00:00`.
- Slophammer duplication, structural and execution checks: passed with no findings.
- The local Windows Zira lesson rendered with networking disabled: 43.467 seconds, with final spoken-audio correlation 0.9939. The existing WAV fixtures and lesson wording were retained.

## Changes that cleared the failures

The audit initially flagged 15 inherited packages. Their locked versions were updated, including related dependencies needed by the resolver. Pylint's old major-version cap was raised to permit a patched release; minimum pip and GitPython versions were also raised. Updating setuptools refreshed some optional transcription/GPU dependency entries in the universal lockfile; those packages and models are not installed by the daily setup.

Slophammer 0.3.0 is now a pinned development dependency and runs through `uv run`, inside the same project environment as ty, mypy and the package under test. This fixes the isolated-tool environment's inability to resolve `pip`. The checker needs Python 3.12+, so its dependency has a Python-version marker; the library retains Python 3.11+ support.

Mutation testing uses two workers, matching GitHub CI. No tests, type-check rules, audit checks, coverage thresholds or mutation gates were weakened. No S01 recording service was implemented.

## Full verification commands

Install `portaudio19-dev` in addition to the native tools in `LOCAL_SETUP.md`. Run the full suite with Linux Python 3.12+:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.virtualenvs/rl_studio_ci"
export UV_LINK_MODE=copy
uv sync --dev --frozen --python "$HOME/miniforge3/envs/rl_studio_python/bin/python"
export PATH="$UV_PROJECT_ENVIRONMENT/bin:$PATH"
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run ty check src/manim_voiceover
uv run mypy
uv run pytest --cov=manim_voiceover --cov-fail-under=85
uv run slophammer-py dry .
uv run mutmut run --max-children 2
uv run python scripts/check_mutmut_results.py
uv run pip-audit
uv run slophammer-py check .
uv run slophammer-py check . --execute
```

The smaller daily environment remains separate. `local.ps1 sync` installs its locked runtime dependencies; `test` and `render` then run offline. Alignerr platform verification and task eligibility remain separate from these repository checks.
