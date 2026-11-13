# S01 record: local WAV speech service

> Historical task-sandbox report imported from snapshot 9. Its commands and counts refer to the task subset. For the full repository workflow, see [S01 guide](s01.md).

Final state of the S01 task: what changed, how to run it, where the outputs are, and what is still open.
Environment: Ubuntu 24.04 x86_64 (Taiga image), Python 3.12.3, uv 0.9.10, no administrator access.

## Final results

| Check | Result |
|---|---|
| `bash scripts/s01.sh test` | **135 passed** (48 baseline + 87 speech-service tests; 11 of them are real Manim renders, marked `integration`), 0 skipped |
| `render a` | passed: source 5.744 s, video 5.933 s, final-second correlation 0.9868 |
| `render b` | passed: source 4.751 s, video 4.933 s, final-second correlation 0.9881 |
| Fixture hashes | unchanged (match `fixtures/info.json`) |
| Output videos | byte-identical across all renders since the first session: `baseline-a.mp4` SHA-256 `b000704e4d534d887dd329f07f37241cd329ef1a4a0d01790c1690d87f6fd80e`, `baseline-b.mp4` `e3be867d7328e568ecd3d06914728857ef34479abf616025a757719da41e642f`; played on Windows by the project owner |
| Setup from scratch | passed in a fresh copy with a new `.venv` and an empty uv cache (see Limitations, item 1) |
| Static checks | ruff lint and format clean; mypy strict clean for `local_wav.py` (3 older errors in `openai.py`/`gemini.py` are unchanged); shellcheck clean for both scripts |

## Changed files (compared with the uploaded bundle)

| File | Change |
|---|---|
| `src/manim_voiceover/services/local_wav.py` | **New.** `LocalWavService`: plays one local 16-bit PCM WAV (mono or stereo) at normal speed, using the recording's duration. Never modifies the source. Rejects missing, empty, corrupt, truncated, non-16-bit, incomplete-final-frame and more-than-two-channel files. The cache key is a SHA-256 of the content, so replacing the audio at the same path re-renders and unchanged audio reuses its copy. Raises `AudioDecoderDependencyError` when a layout needs FFmpeg and it is missing. |
| `tests/test_local_wav_service.py` | **New.** 87 tests: service integration, invalid inputs, source preservation, duration, replacement, cache reuse (no re-copy), incomplete frames, FFmpeg-only layouts, the mono/stereo channel limit and real renders. |
| `examples/s01_baseline.py` | Uses `LocalWavService` and `self.voiceover(...)` instead of `add_sound`. Keeps the 0.25 s hold and explains why it is required. File and class names are kept, because the scripts use them. |
| `uv.lock` | Two unavailable pins replaced with verified versions: `certifi` 2026.10.23 → 2026.10.18 and `soupsieve` 4.1 → 4.0.1 (14 lines, CRLF kept). |
| `scripts/s01.sh` | `setup` prepares Pango headers first. `doctor` reports compiler, Python headers, Cairo, Pango, `ffmpeg` and `ffprobe`. |
| `scripts/s01_pango_sysroot.sh` | **New.** Without root, downloads the Ubuntu Pango `-dev` packages that match the installed runtime libraries into `.s01-sysroot/`. |
| `.gitignore` | Ignores `/.s01-sysroot/`. |
| `README.md` | Service description, setup prerequisites, lock note, WAV layouts that need FFmpeg, test notes, fresh-copy procedure, known limitations, memory usage, why the 0.25 s hold is required. |
| `S01_RECORD.md` | **New.** This record. |

## Commands

Run inside the project directory:

```bash
bash scripts/s01.sh doctor     # prerequisites, including Pango headers and ffmpeg/ffprobe
bash scripts/s01.sh setup      # .venv from uv.lock; creates .s01-sysroot/ only if Pango headers are missing
bash scripts/s01.sh test       # all tests (126)
bash scripts/s01.sh render a   # copies fixtures/take_a.wav to media/s01/input.wav, renders and verifies
bash scripts/s01.sh render b   # same path, take b: exercises replacement with the same transcript
bash scripts/s01.sh verify a   # re-verify without rendering
bash scripts/s01.sh verify b
```

Other useful commands:

```bash
.venv/bin/python -m pytest -q -m integration tests        # only the real-render tests (10)
bash scripts/s01_pango_sysroot.sh                         # prepare/reuse .s01-sysroot (setup runs this when needed)
bash scripts/s01_pango_sysroot.sh --pkg-config-path       # print the pkg-config path it provides
uvx ruff@0.11.13 check --no-cache src tests
uvx ruff@0.11.13 format --no-cache --check src tests
uvx mypy@1.13.0 --python-executable .venv/bin/python --cache-dir=/dev/null
uvx --from shellcheck-py shellcheck scripts/s01.sh scripts/s01_pango_sysroot.sh
```

The fresh-copy setup check (new `.venv`, empty uv cache, working results untouched) is in `README.md` under "Verifying setup in a fresh copy". Keep the image's `SSL_CERT_FILE` set when running it.

## Output paths

| Path | Content |
|---|---|
| `media/s01/videos/s01_baseline/480p15/baseline-a.mp4` | Rendered video, take a |
| `media/s01/videos/s01_baseline/480p15/baseline-b.mp4` | Rendered video, take b |
| `media/s01/videos/s01_baseline/480p15/baseline-{a,b}.srt` / `.wav` | Subtitles and Manim's soundtrack for each take |
| `media/s01/verification-a.json`, `media/s01/verification-b.json` | Verification results (durations, correlation, fixture check) |
| `media/s01/voiceovers/` | Cached recording copies (one per distinct content) and `cache.json` |
| `media/s01/input.wav` | Working copy of the last rendered take |
| `.venv/` | Virtual environment (git-ignored) |
| `.s01-sysroot/` | Project-local Pango headers, only created when the system has none (git-ignored) |

## Limitations and open issues

The complete list is in `README.md` under "Known limitations". For each of 19 limitations it gives the meaning, when it matters and its origin (S01, inherited, environment or out of scope), plus what was tested, what is still unverified, and dependencies with workarounds. The items below are the short version.

1. **Setup proof timing.** The fresh-copy setup proof passed when the suite had 89 tests, before the incomplete-frame, FFmpeg-layout and channel-limit changes. Those later changes did not touch the lock, setup or the sysroot script (only `doctor` output lines in `s01.sh`), but setup was not re-run from scratch afterwards.
2. **Lock substitutions.** The package index reachable here does not serve `certifi` 2026.10.23 (HTTP 403, possibly blocked on purpose) or `soupsieve` 4.1 (uploaded 2026-11-10, possibly not yet mirrored). The security check used the index's vulnerability feed only, which reported none. OSV and GitHub were unreachable, so I could not compare against the newer versions.
3. **Unpinned build tools.** Cython, setuptools, wheel, meson-python, meson, ninja and hatchling resolve at setup time, so future builds may use different versions.
4. **Pango fallback.** Needs the Ubuntu archive to be reachable and the installed runtime library versions to still be listed there. Its failure branches (hash mismatch, missing runtime package) were not exercised. The system `libpango1.0-dev` path was not exercised (no root).
5. **HTTPS trust.** The image's egress proxy re-signs TLS. uv needs the image's `SSL_CERT_FILE`.
6. **FFmpeg-only WAV layouts.** An odd-sized chunk before `data`, or `data` after ten chunks, needs `ffmpeg` and `ffprobe`. The `avconv`/`avprobe` alternatives that pydub also accepts were not tested. Without FFmpeg, 4 real-render tests are skipped. The official renders were not re-run with FFmpeg hidden; the fixtures are plain WAVs, which tests show render without it.
7. **Accepted but unchecked bytes.** Bytes after the `data` chunk and the RIFF size field are not validated. Tests and experiments show they do not change the decoded audio or the duration. A truncated file whose header was rewritten to match is indistinguishable from a shorter recording.
8. **`cache.json` grows.** The base library appends one entry per voiceover, even on cache hits. This predates S01 and was left unchanged.
9. **Out of scope by design.** No transcription, so the transcript is not checked against the audio and bookmarks (`wait_until_bookmark`) do not work. No speed adjustment: `global_speed` must be 1.0. SoX is not installed, so a warning appears at import.
10. **Test notes.** Most service tests use a mock scene (validation only). Rendering is proven by the `integration` tests. The read-only-source test is weaker when run as root. One `audioop` deprecation warning comes from pydub.
11. **Verification scope.** Automated checks are numerical (durations, correlation, byte comparisons). The project owner played the rendered videos on Windows; the current videos are byte-identical to those. Setup, tests and renders were not run on Windows or macOS.
12. **Mono and stereo only (decision).** Recordings with more than two channels are rejected. Manim encodes MP4 audio as stereo, and before the restriction a 5.1 file lost its LFE channel.
13. **Memory (documented).** Validation holds 3.0x the file size on every render (measured at 11, 110 and 220 MiB). A whole render of a 110 MiB recording peaked at 708 MiB. Details are in the README under "Memory usage".
14. **0.25 s hold (kept).** Without it, the video ends 11–18 ms before the recording at 15 fps, and `verify` fails. With it, the video ends 0.18–0.19 s after the recording, inside the 0.4 s limit.
15. **RF64 (documented).** Files over 4 GiB in RF64 format are rejected with the generic "corrupt or not an uncompressed PCM WAV" message.
16. **Outside the project.** Evidence and logs are in `~/s01-evidence`, and the fresh-copy proof is in `~/s01-fresh` (older code). Both can be deleted. uv writes its own empty lock files to `/tmp`. No git commands were run.
