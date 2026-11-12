# RL Studio on Windows

Use a native Windows Conda installation (Anaconda or Miniforge). Conda supplies Python and FFmpeg; uv manages the project packages in the checkout's ignored `.venv-windows` directory.

From an Anaconda or Miniforge Prompt, create the environment once:

```bat
conda create -n rl_studio_win --override-channels -c conda-forge python=3.12 uv=0.13.7 ffmpeg=9.0.2
```

From PowerShell in the checkout:

```powershell
.\local.ps1 sync
.\local.ps1 status
.\local.ps1 test
.\local.ps1 s01
.\local.ps1 s02
.\local.ps1 render
```

- `sync` installs the locked runtime packages and focused test requirements.
- `test` runs the focused suite, including the S01 and S02 tests.
- `s01` renders and verifies both supplied recordings. Videos and verification JSON are under `media/s01/`.
- `s02` renders the expected reward cue lesson to `media/s02-windows/`. Pass `-CueManifest` with a path relative to the checkout or an absolute path to select a different manifest. The [S02 guide](docs/s02.md) gives the personal take example and the Linux A → B → B verifier commands.
- `render` renders the original math lesson using the existing narration fixtures. This command also needs a working LaTeX installation with `latex` and `dvisvgm` on PATH; the S01 example does not need LaTeX.
- `shell` opens PowerShell with the project Python and Conda tools available. Use `exit` to return.

The launcher finds Conda on PATH or in the default per-user Miniforge installation. If it cannot find Conda, open PowerShell from an Anaconda or Miniforge Prompt.

Use `main` as the Windows trunk. Branch ordinary Windows work from `main` and merge it back after validation. For Alignerr, branch each SXX task from `dev`, use the Linux setup in that branch's [S01 guide](https://github.com/Sateno/rl_studio/blob/dev/docs/s01.md), and merge the task branch back into `dev` first. Then bring the reviewed source and test changes onto an integration branch from `main`, validate on Windows, and merge into `main`. The Bash setup and render scripts are also available on `main`. Use the Linux Python and uv installation in WSL or Taiga, and the Conda environment above for native Windows. The Linux CI workflow covers the full development checks, including mutation testing.
