param(
    [ValidateSet('sync', 'test', 'test-all', 'render', 's01', 's02', 'bookmarks', 'shell', 'status')]
    [string]$Action = 'status',
    [string]$CueManifest
)

$launcherArgs = @($Action)
if ($CueManifest) { $launcherArgs += @('-CueManifest', $CueManifest) }
& (Join-Path $PSScriptRoot 'local-windows.ps1') @launcherArgs
