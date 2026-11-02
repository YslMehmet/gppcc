# Build dist\gppcc.exe (single-file Windows app).
#   .\build_windows.ps1
# If scripts are blocked: powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not $IsWindows -and $env:OS -ne "Windows_NT") {
    Write-Error "build_windows.ps1 must run on Windows."
}

$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "could not create virtualenv (is Python 3.9+ on PATH?)" }
}
& $py -m pip install --quiet -r requirements.txt pyinstaller pillow
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "-> icon"
New-Item -ItemType Directory -Force build | Out-Null
& $py packaging\make_icon.py build\icon-1024.png
if ($LASTEXITCODE -ne 0) { throw "make_icon.py failed" }
& $py -c "from PIL import Image; Image.open(r'build\icon-1024.png').save(r'build\gppcc.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
if ($LASTEXITCODE -ne 0) { throw "icon conversion failed" }

Write-Host "-> pyinstaller"
& $py -m PyInstaller --noconfirm --clean --log-level WARN `
    --distpath dist --workpath build\pyinstaller packaging\gppcc.spec
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

Write-Host "built dist\gppcc.exe"
