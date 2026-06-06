param(
  [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root '.venv-build'
$python = Join-Path $venv 'Scripts\python.exe'
$dist = Join-Path $root 'dist'
$build = Join-Path $root 'build'
$guiSpec = Join-Path $root 'CodexHistorySyncTool.spec'
$cliSpec = Join-Path $root 'CodexHistorySyncToolCli.spec'

if ($Clean) {
  Remove-Item -LiteralPath $dist -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $build -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $guiSpec -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $cliSpec -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath $python)) {
  py -3 -m venv $venv
}

& $python -m pip install --upgrade pip
& $python -m pip install pyinstaller

& $python -m PyInstaller `
  --noconfirm `
  --onedir `
  --windowed `
  --name CodexHistorySyncTool `
  --distpath $dist `
  --workpath $build `
  --specpath $root `
  --collect-submodules scripts `
  (Join-Path $root 'windows_app.py')

$exe = Join-Path $dist 'CodexHistorySyncTool\CodexHistorySyncTool.exe'
if (-not (Test-Path -LiteralPath $exe)) {
  throw "Build finished but executable was not found: $exe"
}

& $python -m PyInstaller `
  --noconfirm `
  --onedir `
  --console `
  --name CodexHistorySyncToolCli `
  --distpath $dist `
  --workpath $build `
  --specpath $root `
  --collect-submodules scripts `
  (Join-Path $root 'windows_app.py')

$cliExe = Join-Path $dist 'CodexHistorySyncToolCli\CodexHistorySyncToolCli.exe'
if (-not (Test-Path -LiteralPath $cliExe)) {
  throw "Build finished but CLI executable was not found: $cliExe"
}

Copy-Item -LiteralPath $cliExe -Destination (Join-Path $dist 'CodexHistorySyncTool\CodexHistorySyncToolCli.exe') -Force

Write-Output "Built: $exe"
Write-Output "Built: $cliExe"
