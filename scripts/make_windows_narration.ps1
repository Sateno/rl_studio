param(
    [string]$Voice = 'Microsoft Zira Desktop',
    [ValidateRange(-10, 10)][int]$Rate = -1
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$manifest = Get-Content -LiteralPath (Join-Path $repo 'examples\rl_lessons\assets\narration.json') -Raw | ConvertFrom-Json
$output = Join-Path $repo 'media\local_validation\windows-voice'
New-Item -ItemType Directory -Path $output -Force | Out-Null
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $synth.SelectVoice($Voice)
    $synth.Rate = $Rate
    $synth.Volume = 100
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(22050, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    for ($index = 0; $index -lt $manifest.sections.Count; $index++) {
        $path = Join-Path $output ('section_' + $index + '.wav')
        $synth.SetOutputToWaveFile($path, $format)
        $synth.Speak([string]$manifest.sections[$index])
        $synth.SetOutputToNull()
    }
    @{ voice = $Voice; rate = $Rate; sample_rate = 22050; sections = $manifest.sections } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $output 'voice.json') -Encoding UTF8
    Write-Output ('Generated four offline sections with ' + $Voice + ', rate ' + $Rate)
} finally {
    $synth.Dispose()
}
