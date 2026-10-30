# Turn realtime 8D off: stop the engine, restore your previous default output.
& (Join-Path $PSScriptRoot "stop_8d.ps1")
& (Join-Path $PSScriptRoot "switch_audio.ps1") -To restore
Write-Host "8D off, normal audio restored"
