param(
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$AllPassed = $true

function Report-Check([string]$Name, [bool]$Passed, [string]$Detail) {
    if ($Passed) {
        Write-Host "PASS [$Name] $Detail" -ForegroundColor Green
    } else {
        Write-Host "FAIL [$Name] $Detail" -ForegroundColor Red
        $script:AllPassed = $false
    }
}

function Find-Python {
    if (Test-Path -LiteralPath $VenvPython) {
        return $VenvPython
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        try {
            & $py.Source -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)"
            if ($LASTEXITCODE -eq 0) { return "$($py.Source)|-3.12" }
        } catch {}
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $python) { return $python.Source }
    return $null
}

function Invoke-Python([string]$PythonSpec, [string[]]$Arguments) {
    if ($PythonSpec -like "*|-3.12") {
        $parts = $PythonSpec.Split('|', 2)
        & $parts[0] -3.12 @Arguments | Out-Host
    } else {
        & $PythonSpec @Arguments | Out-Host
    }
    return [int]$LASTEXITCODE
}

Set-Location -LiteralPath $RepoRoot
$PythonSpec = Find-Python
if ($null -eq $PythonSpec) {
    Report-Check "Python" $false "Python 3.12 or newer was not found."
    exit 1
}
$versionExit = Invoke-Python $PythonSpec @("-c", "import sys; print(sys.version.split()[0]); raise SystemExit(0 if sys.version_info >= (3,12) else 1)")
Report-Check "Python" ($versionExit -eq 0) "Python 3.12+"
if ($versionExit -ne 0) { exit 1 }

if (-not $CheckOnly) {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Host "Creating .venv..."
        $venvExit = Invoke-Python $PythonSpec @("-m", "venv", (Join-Path $RepoRoot ".venv"))
        if ($venvExit -ne 0) { throw "Failed to create .venv" }
    }
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip" }
    & $VenvPython -m pip install -e $RepoRoot
    if ($LASTEXITCODE -ne 0) { throw "Failed to install project dependencies" }
    $PythonSpec = $VenvPython
}

$tkExit = Invoke-Python $PythonSpec @("-c", "import tkinter; print(tkinter.TkVersion)")
Report-Check "Tkinter" ($tkExit -eq 0) "Tkinter import"
$win32Exit = Invoke-Python $PythonSpec @("-c", "import win32com.client; print('pywin32')")
Report-Check "pywin32" ($win32Exit -eq 0) "win32com import"

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
if ((($null -eq $ffmpeg) -or ($null -eq $ffprobe)) -and (-not $CheckOnly)) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($null -eq $winget) {
        Report-Check "FFmpeg install" $false "winget is unavailable; install FFmpeg and add it to PATH."
    } else {
        Write-Host "Installing FFmpeg with winget..."
        & $winget.Source install --id Gyan.FFmpeg --exact --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { Report-Check "FFmpeg install" $false "winget failed" }
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        $ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
        $ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
    }
}
Report-Check "FFmpeg" ($null -ne $ffmpeg) "ffmpeg on PATH"
Report-Check "ffprobe" ($null -ne $ffprobe) "ffprobe on PATH"

if ($win32Exit -eq 0) {
    $powerPointCode = "import win32com.client; app=win32com.client.DispatchEx('PowerPoint.Application'); app.Quit(); print('PowerPoint COM')"
    $powerPointExit = Invoke-Python $PythonSpec @("-c", $powerPointCode)
    Report-Check "PowerPoint" ($powerPointExit -eq 0) "PowerPoint COM instance"
} else {
    Report-Check "PowerPoint" $false "pywin32 is unavailable"
}

if (-not $AllPassed) {
    Write-Host "Setup checks failed. Resolve the FAIL items above and run setup.bat again." -ForegroundColor Yellow
    exit 1
}

if ($CheckOnly) {
    Write-Host "All dependency checks passed. No changes were made." -ForegroundColor Green
} else {
    Write-Host "Setup complete. Run run-ui.bat to open the queue." -ForegroundColor Green
}
