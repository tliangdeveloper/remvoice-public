# RemVoice public build script
# Usage: powershell -ExecutionPolicy Bypass -File .\frontend_build\build_exe.ps1
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here

$candidates = @()
if ($env:REMVOICE_A_GEN) {
    $candidates += (Join-Path $env:REMVOICE_A_GEN "venv\Scripts\python.exe")
}
$candidates += (Join-Path $root "a-gen\venv\Scripts\python.exe")
$candidates += (Join-Path $here "a-gen\venv\Scripts\python.exe")
$python = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) {
    throw "A Python environment was not found. Set REMVOICE_A_GEN or place a-gen next to this project."
}

$env:PYINSTALLER_CONFIG_DIR = Join-Path $here ".pyinstaller_cache"
$dist = Join-Path $here "dist"
$build = Join-Path $here "build"
$spec = Join-Path $here "build"
$runtime = Join-Path $here "runtime"
$exe = Join-Path $root "RemVoice.exe"
$icon = Join-Path $here "remvoice_icon.ico"
$assets = Join-Path $here "assets"

New-Item -ItemType Directory -Force -Path $runtime, $dist, $build | Out-Null
Get-ChildItem -LiteralPath $runtime -Directory -Filter "_MEI*" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$argsList = @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onefile", "--windowed", "--noupx",
    "--name", "RemVoice",
    "--paths", $here
)
if (Test-Path -LiteralPath $icon) {
    $argsList += @("--icon", $icon)
}
$argsList += @(
    "--version-file", (Join-Path $here "version_info.txt"),
    "--runtime-tmpdir", $runtime,
    "--add-data", "$here\asr_worker.py;."
)
if (Test-Path -LiteralPath $assets) {
    $argsList += @("--add-data", "$assets;assets")
}
$argsList += @(
    "--distpath", $dist, "--workpath", $build, "--specpath", $spec,
    (Join-Path $here "rem_gui.py")
)

Push-Location $here
try {
    & $python @argsList
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$built = Join-Path $dist "RemVoice.exe"
if (-not (Test-Path -LiteralPath $built)) {
    throw "Build output not found: $built"
}
Copy-Item -LiteralPath $built -Destination $exe -Force
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash
Write-Host ""
Write-Host "Built: $exe"
Write-Host "SHA256: $hash"
