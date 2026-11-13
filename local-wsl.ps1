# RL Studio launcher for Windows. Runs scripts/rl_studio.sh in WSL with the native setup
# (Conda Python and the uv environment in Linux storage).
#
#   .\local-wsl.ps1 setup                 # environment from uv.lock (same as sync)
#   .\local-wsl.ps1 test                  # focused offline test suite
#   .\local-wsl.ps1 demo local-wav        # Local WAV Narration: takes A and B, verified
#   .\local-wsl.ps1 demo cue-bindings     # Cue Bindings: A -> B -> B with one shared cache, verified
#   .\local-wsl.ps1 demo bookmarks        # Manual Narration Bookmarks: animations within one frame, verified
#   .\local-wsl.ps1 status | sync | render | shell
param(
    [ValidateSet('status', 'setup', 'sync', 'test', 'demo', 'render', 'shell')][string]$Action = 'status',
    [ValidateSet('local-wav', 'cue-bindings', 'bookmarks')][string]$Target
)
$ErrorActionPreference = 'Stop'
if (($Action -eq 'demo') -ne [bool]$Target) {
    throw 'Use: .\local-wsl.ps1 demo local-wav|cue-bindings|bookmarks (a target is only used with demo)'
}
$linuxRepo = '/mnt/' + $PSScriptRoot.Substring(0, 1).ToLowerInvariant() + $PSScriptRoot.Substring(2).Replace('\', '/')
$distro = if ($env:RL_STUDIO_WSL_DISTRO) { $env:RL_STUDIO_WSL_DISTRO } else { 'Ubuntu-24.04' }
$arguments = @("$linuxRepo/scripts/rl_studio.sh", $Action)
if ($Target) { $arguments += $Target }
& wsl.exe --distribution $distro --exec bash @arguments
exit $LASTEXITCODE
