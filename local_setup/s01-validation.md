# S01 repository integration validation

Recorded 2026-11-11T05:16:26+00:00. Base: `308e07adf0fea0463d17df7513cb016024f7e08c`.
Source: final task snapshot `3a8fc969-2895-4be7-a793-ed94cb458fad_9_a_all_code.tar.gz`.

## Scope

Imported the local WAV service, tests, A/B recording fixtures, example, setup and verification scripts, two dependency-lock substitutions and task documentation. Kept the original package name, author metadata, MIT license, full-repository README and existing development gates. Generated media, environments, caches and bundle metadata are excluded from the commit.

Integration adds 23 contract tests, copies fixtures into mutation-test workspaces and runs mutation testing with one worker to avoid timeouts observed with two workers. The service also gives useful detail for empty parser exceptions and uses `wave.open`'s equivalent default read mode. No mutation exclusions or quality thresholds were weakened.

## Environment and results

WSL2 Ubuntu 24.04, Conda Python 3.12.16, uv 0.13.7, Cairo 1.18.0, Pango 1.52.1 and FFmpeg/ffprobe 6.1.1. The full suite used a separate uv-managed development environment. S01 setup created a fresh repository-local `.venv`, using the already installed system Pango headers and existing uv download cache.

| Check | Result |
|---|---|
| `uv sync --dev --frozen`; `uv lock --check` | Passed |
| `uv run ruff format --check .`; `uv run ruff check .` | Passed |
| `uv run ty check src/manim_voiceover`; `uv run mypy` | Passed |
| `uv run pytest --cov=manim_voiceover --cov-fail-under=85` | 237 passed; 97.85% coverage |
| `uv run mutmut run --max-children 1` and `uv run python scripts/check_mutmut_results.py` | All 3,468 killed; zero surviving, timed-out, suspicious or untested mutants |
| `uv run pip-audit` | No known vulnerabilities reported by the configured audit service |
| `uv run slophammer-py dry .` | Passed |
| `uv run slophammer-py check .` and `uv run slophammer-py check . --execute` | Passed |
| `bash scripts/s01.sh doctor`; `bash scripts/s01.sh setup` | Passed; system Pango path; no `.s01-sysroot` needed |
| `bash scripts/s01.sh test` | 158 passed |
| `bash scripts/s01.sh render a`; `bash scripts/s01.sh render b` | Both rendered and passed verification; original fixture hashes unchanged |

| Take | Source seconds | Video seconds | Final voiced-second correlation |
|---|---:|---:|---:|
| a | 5.744036 | 5.933333 | 0.986808 |
| b | 4.751111 | 4.933333 | 0.988085 |

Local logs are in the ignored `media/s01-integration-checks/` folder. Generated videos and verification JSON are under `media/s01/`. GitHub CI independently runs the development gates on the submitted commit; its status is recorded on the pull request rather than predicted here.

## Boundaries of this verification

This reran setup after the final service changes with a fresh `.venv`; it did not use an empty uv cache or reinstall the operating system. It exercised the system Pango headers, not the project-local fallback or its failure branches. It did not add native Windows/macOS or other Python-version validation, large-file/concurrent-render experiments, an independent multi-database security review, or a new human listening review. The other known limitations remain documented in [the S01 guide](../docs/s01.md); the [task record](../docs/s01-task-record.md) preserves the earlier sandbox evidence and its original 135-test count.

## CI runner setup

Two attempts on the inherited `ubuntu-latest` runner (Ubuntu 26.04) could not resolve the Ubuntu package servers and stalled before Python installation or tests. CI now explicitly uses Ubuntu 24.04 to match the tested environment, stops dependency installation after five minutes, and treats incomplete package-index downloads as errors. The job has a 30-minute limit. All existing validation steps remain enabled.
