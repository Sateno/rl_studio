# RL Studio local setup

The local baseline is working under **WSL2 / Ubuntu 24.04**, using Conda Python 3.12.16 and uv 0.13.7. The checkout is Manim Voiceover v0.4.0 at `51d788b768d04676b4596c42c5dce2cda9740e11`.

Upstream package files, names and license are retained; RL Studio maintains the dependency lockfile. RL Studio adds the lesson fixtures and local setup helpers; feature implementation follows on separate branches.

## Daily commands

From PowerShell:

```powershell
cd C:\rl_projects\src\repos\rl_studio
.\local.ps1 status
.\local.ps1 test
.\local.ps1 render
.\local.ps1 shell
```

From the Windows Anaconda/Miniforge Prompt, use:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File C:\rl_projects\src\repos\rl_studio\local.ps1 shell
```

The shell opens in this checkout with the Linux project environment active. Type `exit` to return to Windows. Do not install the Linux project packages into your existing Windows environments.

## Environment layout

- Source: `C:\rl_projects\src\repos\rl_studio`, exposed in WSL as `/mnt/c/rl_projects/src/repos/rl_studio`.
- Linux Conda installation: `$HOME/miniforge3`, matching the Conda/Miniforge workflow already used on Windows.
- Conda environment `rl_studio_python`: provides Python 3.12.16 and uv 0.13.7.
- uv environment: `$HOME/.virtualenvs/rl_studio`. Keeping it in Linux storage avoids putting environment files on the mounted Windows drive.
- Project packages: the maintained `uv.lock`, installed with `--frozen --no-dev --extra translate`. The small translation extra is needed for an existing mocked core test; no translation service is called.
- Test runner: pytest 9.0.3, supplied by uv's `--with` environment. `sync` prepares its cache; `test` and `render` then use `--offline`.

Conda supplies the interpreter; uv owns the project packages. The daily environment omits the full development dependencies and transcription models. A separate `rl_studio_ci` environment holds the frozen development dependencies for the complete checks, including Slophammer.

## Reproduction

On Ubuntu 24.04 with a Linux Conda installation, install the native tools as root (or with sudo):

```bash
apt-get update
apt-get install -y --no-install-recommends build-essential pkg-config libcairo2-dev libpango1.0-dev ffmpeg sox espeak-ng fonts-dejavu-core curl ca-certificates git texlive-latex-base texlive-latex-extra texlive-fonts-recommended dvisvgm
```

From the checkout, as the regular Linux user:

```bash
conda create -n rl_studio_python --file local_setup/conda-linux-64.explicit.txt
bash scripts/rl_studio.sh sync
bash scripts/rl_studio.sh test
bash scripts/rl_studio.sh render
```

The explicit Conda specification is for Linux x86_64. `local_setup/native-packages.txt` records the tested Ubuntu package versions. If your Conda installation is elsewhere, set `RL_STUDIO_CONDA_PREFIX` to the full path of its `rl_studio_python` environment. Set `RL_STUDIO_ENV` to override the uv environment path.

## Verified results

- 49 focused existing tests passed, including cache models, core service behavior, audio helpers and narration tracking.
- The same 49 tests passed in a fresh temporary uv environment built from the cached locked packages.
- Cairo rendered `MathTex` and local narration into `media/videos/local_smoke/480p15/LocalSmoke.mp4`: 43.467 seconds, 854 by 480 pixels, 15 fps.
- The original eSpeak baseline also passed rendering and media verification inside a Linux network namespace with networking disabled. The current Windows-voice render uses the same offline rendering pipeline.
- The 42.721-second source narration fits within the video. The final voiced second has 0.9939 normalized correlation with the decoded MP4 audio; the check requires more than 0.97. This verifies preservation of the ending through AAC encoding, rather than a human listening review.
- Four video frames were visually inspected for legibility and clipping. The displayed calculation is `0.25 * 0 + 0.75 * 4 = 3`, compared with a certain reward of 2.
- The new Python helper files and example pass Ruff checks. The broader pre-publication suite was subsequently run; see `local_setup/baseline-checks.md` for the full passing checks. Alignerr environment verification is still separate.

Reports are in `media/local_validation/`, which is already ignored by Git. Benign upstream pydub deprecation warnings and a Manim muxer unused-option warning were observed; stream/duration/audio checks passed.

## Fixtures and scope

`examples/rl_lessons/local_smoke.py` uses the existing `VoiceoverScene`, `Scene.add_sound`, animation and timing APIs. It does not implement S01 or a new speech service.

`examples/rl_lessons/assets/` contains offline Windows narration generated with **Microsoft Zira Desktop**, rate -1, at 22050 Hz / 16-bit / mono. The wording is unchanged. The two short A/B recording fixtures retain their original eSpeak NG 1.51 audio. Existing files are retained when rendering again.

The original pilot, assets and MP4 are backed up in `media/local_validation/espeak-original/`.

To regenerate the Windows narration, run `scripts/make_windows_narration.ps1` with Windows PowerShell 5.1; it accepts `-Voice` and `-Rate`. Then open `local.ps1 shell` and run:

```bash
uv run --offline --frozen --no-dev --extra translate python scripts/assemble_windows_audio.py
bash scripts/rl_studio.sh render
```

Windows is required only to synthesize new Zira recordings. The supplied WAVs render in Linux without Windows speech support.

S01 remains a later task: selecting an external WAV through the Voiceover service API, preserving it, and handling content-sensitive cache replacement. Platform instructions, eligibility and the separate initial prompt still require verification later. Begin feature work from the validated baseline on a new branch.
