Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$release = Join-Path $root 'release'
$setup = Get-ChildItem -LiteralPath $release -Filter '*Setup.exe' |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

if (-not $setup) {
  throw "No Setup.exe found in: $release"
}

$installDir = Join-Path $env:TEMP 'CodexHistorySyncToolInstallTest'
$missingCodexHome = Join-Path $env:TEMP 'missing-codex-home-installer-test'
Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction SilentlyContinue

& $setup.FullName /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="$installDir" /CURRENTUSER
Start-Sleep -Seconds 2

$exe = Join-Path $installDir 'CodexHistorySyncTool.exe'
if (-not (Test-Path -LiteralPath $exe)) {
  throw "Installed EXE was not found: $exe"
}

$cliExe = Join-Path $installDir 'CodexHistorySyncToolCli.exe'
if (-not (Test-Path -LiteralPath $cliExe)) {
  throw "Installed CLI EXE was not found: $cliExe"
}

$tkData = Join-Path $installDir '_internal\_tk_data'
for ($i = 0; $i -lt 20 -and -not (Test-Path -LiteralPath $tkData); $i++) {
  Start-Sleep -Milliseconds 500
}
if (-not (Test-Path -LiteralPath $tkData)) {
  throw "Installed Tk data directory was not found: $tkData"
}

$stdoutPath = Join-Path $env:TEMP 'codex-history-sync-installer-smoke.stdout.txt'
$stderrPath = Join-Path $env:TEMP 'codex-history-sync-installer-smoke.stderr.txt'
Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
$process = Start-Process -FilePath $cliExe `
  -ArgumentList @("--run-backend", "--json", "--codex-home", $missingCodexHome, "status") `
  -NoNewWindow `
  -Wait `
  -PassThru `
  -RedirectStandardOutput $stdoutPath `
  -RedirectStandardError $stderrPath
$backendText = (
  (Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue) +
  (Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue)
).Trim()
if ($backendText -notmatch '"ok": false' -or $backendText -notmatch 'Missing config file') {
  throw "Installed EXE did not return the expected backend JSON error. Output: $backendText"
}

$uninstaller = Join-Path $installDir 'unins000.exe'
if (-not (Test-Path -LiteralPath $uninstaller)) {
  throw "Uninstaller was not found: $uninstaller"
}

& $uninstaller /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
for ($i = 0; $i -lt 30 -and (Test-Path -LiteralPath $installDir); $i++) {
  Start-Sleep -Seconds 1
}

if (Test-Path -LiteralPath $installDir) {
  throw "Install directory still exists after uninstall: $installDir"
}

[pscustomobject]@{
  ok = $true
  setup = $setup.FullName
  install_dir_removed = $true
} | ConvertTo-Json
