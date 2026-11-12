param(
    [ValidateSet('sync', 'test', 'render', 's01', 's02', 'shell', 'status')]
    [string]$Action = 'status',
    [string]$CueManifest
)
$ErrorActionPreference = 'Stop'
$condaEnv = 'rl_studio_win'

if ($env:CONDA_DEFAULT_ENV -ne $condaEnv) {
    $conda = Get-Command conda.exe -ErrorAction SilentlyContinue
    $condaExe = if ($conda) { $conda.Source } else { Join-Path $env:USERPROFILE 'miniforge3\Scripts\conda.exe' }
    if (-not (Test-Path -LiteralPath $condaExe)) {
        throw 'Open an Anaconda or Miniforge Prompt and run this launcher again.'
    }
    $launcherArgs = @($Action)
    if ($CueManifest) { $launcherArgs += @('-CueManifest', $CueManifest) }
    & $condaExe run --no-capture-output -n $condaEnv powershell.exe -NoProfile -File $PSCommandPath @launcherArgs
    exit $LASTEXITCODE
}

$venv = Join-Path $PSScriptRoot '.venv-windows'
$python = Join-Path $venv 'Scripts\python.exe'
$env:UV_PROJECT_ENVIRONMENT = $venv
$env:PYTHONNOUSERSITE = '1'

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable exited with code $LASTEXITCODE" }
}

Push-Location -LiteralPath $PSScriptRoot
try {
    if ($Action -ne 'sync' -and -not (Test-Path -LiteralPath $python)) {
        throw 'Run .\local.ps1 sync first.'
    }
    switch ($Action) {
        'sync' {
            Invoke-Checked uv @('sync', '--frozen', '--no-dev', '--extra', 'translate',
                '--python', (Join-Path $env:CONDA_PREFIX 'python.exe'))
            Invoke-Checked uv @('pip', 'install', '--python', $python, '-r', 'requirements-test.txt')
        }
        'status' {
            Invoke-Checked uv @('--version')
            Invoke-Checked $python @('-c', "import sys, manim, manim_voiceover; print(sys.executable); print(sys.version); print('Manim', manim.__version__, 'Voiceover', manim_voiceover.__version__); print(manim_voiceover.__file__)")
        }
        'test' {
            Invoke-Checked $python @('-m', 'pytest', '-q',
                'tests/test_install.py', 'tests/test_cache_models.py', 'tests/test_core_behavior.py',
                'tests/test_more_behavior.py', 'tests/test_mutation_contracts.py', 'tests/test_mutmut_result_checker.py',
                'tests/test_local_wav_service.py', 'tests/test_local_wav_contracts.py',
                'tests/test_s02_cue_bindings.py', 'tests/test_s02_expected_reward.py', 'tests/test_s02_verifier.py')
        }
        'render' {
            Invoke-Checked $python @('scripts/prepare_local_audio.py')
            Invoke-Checked $python @('-m', 'manim', '--renderer', 'cairo', '-ql', '--disable_caching',
                '--config_file', 'local-manim.cfg', 'examples/rl_lessons/local_smoke.py', 'LocalSmoke')
            Invoke-Checked $python @('scripts/verify_local_render.py')
        }
        's01' {
            $media = Join-Path $PSScriptRoot 'media\s01'
            New-Item -ItemType Directory -Path $media -Force | Out-Null
            $previousAudio = $env:S01_AUDIO
            try {
                $env:S01_AUDIO = Join-Path $media 'input.wav'
                foreach ($take in 'a', 'b') {
                    Copy-Item -LiteralPath "fixtures/take_$take.wav" -Destination $env:S01_AUDIO
                    Invoke-Checked $python @('-m', 'manim', '--renderer', 'cairo', '-ql', '--disable_caching',
                        '--config_file', 'local-manim.cfg', '--media_dir', $media, '-o', "baseline-$take",
                        'examples/s01_baseline.py', 'BaselineRecording')
                    Invoke-Checked $python @('scripts/verify_s01_render.py', '--take', $take)
                }
            } finally {
                $env:S01_AUDIO = $previousAudio
            }
        }
        's02' {
            $manifest = if ($CueManifest) { $CueManifest } else { 'examples/rl_lessons/assets/expected_reward_cues.json' }
            $manifest = (Resolve-Path -LiteralPath $manifest).ProviderPath
            $previousManifest = $env:S02_CUE_MANIFEST
            try {
                $env:S02_CUE_MANIFEST = $manifest
                $output = [IO.Path]::GetFileNameWithoutExtension($manifest)
                Invoke-Checked $python @('-m', 'manim', '--renderer', 'cairo', '-ql', '--disable_caching',
                    '--config_file', 'local-manim.cfg', '--media_dir', 'media/s02-windows', '-o', $output,
                    'examples/rl_lessons/expected_reward_cues.py', 'ExpectedRewardCues')
            } finally {
                $env:S02_CUE_MANIFEST = $previousManifest
            }
        }
        'shell' {
            $env:VIRTUAL_ENV = $venv
            $env:Path = "$(Join-Path $venv 'Scripts');$env:Path"
            Invoke-Checked powershell.exe @('-NoLogo', '-NoProfile', '-NoExit')
        }
    }
} finally {
    Pop-Location
}
