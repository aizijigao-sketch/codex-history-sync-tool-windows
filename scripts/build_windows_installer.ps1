param(
  [switch]$SkipPackageBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$isccCandidates = @(
  (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
  (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
  (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
)
$iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $iscc) {
  $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
  if ($command) {
    $iscc = $command.Source
  }
}
if (-not $iscc) {
  throw 'Inno Setup compiler ISCC.exe was not found. Install Inno Setup 6 and run this script again.'
}

if (-not $SkipPackageBuild) {
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\build_windows_pyinstaller.ps1') -Clean
}

$exe = Join-Path $root 'dist\CodexHistorySyncTool\CodexHistorySyncTool.exe'
if (-not (Test-Path -LiteralPath $exe)) {
  throw "Packaged EXE was not found: $exe"
}

$cliExe = Join-Path $root 'dist\CodexHistorySyncTool\CodexHistorySyncToolCli.exe'
if (-not (Test-Path -LiteralPath $cliExe)) {
  throw "Packaged CLI EXE was not found: $cliExe"
}

$release = Join-Path $root 'release'
New-Item -ItemType Directory -Force -Path $release | Out-Null

& $iscc (Join-Path $root 'installer\CodexHistorySyncTool.iss')

$setup = Get-ChildItem -LiteralPath $release -Filter '*Setup.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $setup) {
  throw "Installer build finished but no Setup.exe was found in: $release"
}

Write-Output "Built installer: $($setup.FullName)"
