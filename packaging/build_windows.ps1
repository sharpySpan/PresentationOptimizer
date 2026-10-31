# Builds dist\Showrunner\ and the installer dist\Showrunner-Setup.exe. Run on Windows.
# Needs Python 3.10+ and Inno Setup 6 (https://jrsoftware.org/isinfo.php) for the installer step.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
& $python -m venv .venv-build
& .\.venv-build\Scripts\python.exe -m pip install --upgrade pip
& .\.venv-build\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist) { Remove-Item -Recurse -Force dist }
& .\.venv-build\Scripts\pyinstaller.exe --noconfirm --clean packaging\showrunner.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$version = & .\.venv-build\Scripts\python.exe -c "import showrunner; print(showrunner.__version__)"
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { $iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source }
if (-not $iscc) { Write-Warning "Inno Setup not found: skipping the installer. The app is in dist\Showrunner\"; exit 0 }
& $iscc "/DAppVersion=$version" packaging\showrunner.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Write-Host "Built: dist\Showrunner-Setup.exe"
