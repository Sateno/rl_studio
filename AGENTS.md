# AGENTS.md

These instructions apply to this repository.

- Keep generated changes small and reviewable by maintainers.
- When applying Slophammer standards, follow the upstream agent entrypoint:
  https://github.com/osolmaz/slophammer/blob/main/docs/AGENT_ENTRYPOINT.md
- Preserve the public package API unless the change is explicitly scoped as breaking.
- Add or update tests when changing behavior.
- Source code lives under `src/manim_voiceover`.
- Full validation uses Python 3.12+ and `uv sync --dev --frozen`; run the pinned Slophammer from that environment with `uv run`.
- Run `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check src/manim_voiceover`, `uv run mypy`, `uv run pytest --cov=manim_voiceover --cov-fail-under=85`, `uv run slophammer-py dry .`, `uv run mutmut run --max-children 2`, `uv run python scripts/check_mutmut_results.py`, `uv run pip-audit`, `uv run slophammer-py check .`, and `uv run slophammer-py check . --execute` before submitting a change.
- Public functions and meaningful helpers must stay annotated and pass Ruff `ANN`, ty, and strict mypy.
- Keep `Any`, casts, and import ignores isolated to external-library boundaries with a clear reason.
- Use the existing project style and avoid unrelated formatting churn.
