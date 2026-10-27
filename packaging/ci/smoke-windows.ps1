# Install the built Lura-Setup.exe like a user would, run it, and take screenshots.
#   pwsh packaging/ci/smoke-windows.ps1 <version>
param([string]$Version)
$ErrorActionPreference = "Stop"
$shots = Join-Path $PWD "screenshots"
New-Item -ItemType Directory -Force $shots | Out-Null

function Shoot([string]$name) {
  Add-Type -AssemblyName System.Windows.Forms, System.Drawing
  $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
  $bmp.Save((Join-Path $shots "$name.png"), [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
  Write-Host "screenshot: $name.png ($($b.Width)x$($b.Height))"
}

Write-Host "== install (silent, per user)"
$setup = Get-Item "dist\Lura-Setup-$Version.exe"
Start-Process $setup.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER" -Wait
$app = Join-Path $env:LOCALAPPDATA "Programs\Lura"
if (-not (Test-Path "$app\LuraApp.exe")) { throw "LuraApp.exe not installed in $app" }
& "$app\lura.exe" --version
Write-Host "Start menu:"; Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Lura" | ForEach-Object { "  " + $_.Name }
$run = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name Lura -ErrorAction SilentlyContinue
Write-Host "start at login: $($run.Lura)"

Write-Host "== run"
$proc = Start-Process "$app\LuraApp.exe" -ArgumentList "run", "--no-browser" -PassThru
$urlFile = Join-Path $env:APPDATA "Lura\dashboard.url"
for ($i = 0; $i -lt 60 -and -not (Test-Path $urlFile); $i++) { Start-Sleep 1 }
if (-not (Test-Path $urlFile)) { throw "dashboard never came up" }
Start-Sleep 20   # let the face animate and the connectors start (npx downloads them)
if ($proc.HasExited) { throw "LuraApp exited with $($proc.ExitCode)" }

$url = (Get-Content $urlFile).Trim()
$token = $url.Split("#t=")[1]
$status = Invoke-RestMethod -Uri ($url.Split("#")[0] + "api/status") -Headers @{ "X-Lura-Token" = $token }
Write-Host "status: $($status.status)  can_talk: $($status.capabilities.can_talk)"
foreach ($c in $status.connectors) { Write-Host "  connector $($c.name): $($c.status) $($c.tools.Count) tools $($c.error)" }

Shoot "windows-desktop-face"
# The settings window, the way the Start-menu shortcut opens it: a native
# window (WebView2), not a browser tab.
$win = Start-Process "$app\LuraApp.exe" -ArgumentList "dashboard" -PassThru
Start-Sleep 12
if ($win.HasExited) { Write-Host "dashboard window exited with $($win.ExitCode)" }
$title = (Get-Process -Id $win.Id -ErrorAction SilentlyContinue).MainWindowTitle
Write-Host "dashboard window title: '$title'"
Shoot "windows-desktop-dashboard"

Write-Host "== dashboard screenshots"
Push-Location $env:RUNNER_TEMP
npm init -y | Out-Null
npm install --silent playwright@1.56.1 | Out-Null
npx --yes playwright@1.56.1 install chromium | Out-Null
Copy-Item (Join-Path $PSScriptRoot "shoot_dashboard.mjs") .
node shoot_dashboard.mjs $urlFile $shots
Pop-Location

Write-Host "== log"
Get-Content (Join-Path $env:LOCALAPPDATA "Lura\lura.log")
Stop-Process -Id $proc.Id -Force
