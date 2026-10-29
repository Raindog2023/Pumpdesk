$root = Split-Path -Parent $PSScriptRoot
$ws = New-Object -ComObject WScript.Shell
$dirs = @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))
foreach ($dir in $dirs) {
  $lnk = $ws.CreateShortcut((Join-Path $dir 'PumpDesk.lnk'))
  $lnk.TargetPath = Join-Path $root 'PumpDesk.bat'
  $lnk.WorkingDirectory = $root
  $lnk.WindowStyle = 7
  $lnk.IconLocation = (Join-Path $root 'windows\pumpdesk.ico')
  $lnk.Description = 'PumpDesk - pump.fun paper-trading scanner'
  $lnk.Save()
  Write-Host "Shortcut created in $dir"
}
