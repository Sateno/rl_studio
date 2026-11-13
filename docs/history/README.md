# Historical records

These files record earlier validation and export work. They were moved here **unchanged** (byte for
byte) when the project switched from numbered task names to feature-based names, so they still use
the old names and paths.

| File | What it records |
|---|---|
| `s01-task-record.md` | The Local WAV Narration task-sandbox record |
| `s01-validation.md` | The Local WAV Narration repository integration validation |
| `baseline-checks.md` | The baseline validation before feature work |
| `TAIGA_EXPORT_PROVENANCE.json` | Provenance of the exported repository bundle |
| `TAIGA_EXPORT_SHA256SUMS.txt` | Checksums of that bundle, with its original paths (renamed files no longer match) |

## Old and new names

| Old name | Current name |
|---|---|
| S01 | Local WAV Narration: Python/file names `local_wav`, command target `local-wav` |
| S02 | Cue Bindings: Python/file names `cue_bindings`, command target `cue-bindings` |
| `scripts/s01.sh` (`doctor`, `setup`, `test`, `render`, `verify`) | `scripts/dev.sh` with `doctor`, `setup`, `test`, `local-wav render a` (or `verify`, take `b`) and `demo local-wav` |
| `scripts/s02.sh` (`render`, `verify`, `check-against`) | `scripts/dev.sh` with `cue-bindings render`, `cue-bindings verify`, `cue-bindings check-against` and `demo cue-bindings` |
| `scripts/s01_pango_sysroot.sh`, `.s01-sysroot/` | `scripts/pango_sysroot.sh`, `.pango-sysroot/` |
| `scripts/s02_timeline.py` | `scripts/expected_reward_timeline.py` (frame rules in `scripts/render_verification.py`) |
| `scripts/verify_s01_render.py` | `scripts/verify_local_wav_render.py` |
| `scripts/verify_s02_render.py` | `scripts/verify_cue_bindings_render.py` |
| `examples/s01_baseline.py` | `examples/local_wav_baseline.py` |
| `fixtures/s02/` | `fixtures/cue_bindings/` |
| `tests/test_s02_cue_bindings.py` | `tests/test_cue_bindings.py` |
| `tests/test_s02_expected_reward.py` | `tests/test_expected_reward_lesson.py` |
| `tests/test_s02_verifier.py` | `tests/test_cue_bindings_verifier.py` |
| `docs/s01.md`, `docs/s02.md` | `docs/local_wav.md`, `docs/cue_bindings.md` |
| `media/s01/`, `media/s02/` | `media/local_wav/`, `media/cue_bindings/` (the old folder is moved only if the new folder is absent; when both exist the old evidence stays in place) |
| `S01_AUDIO`, `S02_CUE_MANIFEST`, `S01_PYTHON` | `LOCAL_WAV_AUDIO`, `CUE_BINDINGS_MANIFEST`, `RL_STUDIO_PYTHON` (the old names are rejected) |
