# Stop the realtime 8D engine by creating its stop flag.
# The engine's watchdog checks for this file twice a second and exits within ~1 s.
$flag = Join-Path $PSScriptRoot "stop.flag"
New-Item -ItemType File -Path $flag -Force | Out-Null
Write-Host "stop flag created - engine will exit within ~1s"
