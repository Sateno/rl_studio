param([ValidateSet('sync', 'test', 'render', 'shell', 'status')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$linuxRepo = '/mnt/' + $PSScriptRoot.Substring(0, 1).ToLowerInvariant() + $PSScriptRoot.Substring(2).Replace('\', '/')
$distro = if ($env:RL_STUDIO_WSL_DISTRO) { $env:RL_STUDIO_WSL_DISTRO } else { 'Ubuntu-24.04' }
& wsl.exe --distribution $distro --exec bash "$linuxRepo/scripts/rl_studio.sh" $Action
exit $LASTEXITCODE
