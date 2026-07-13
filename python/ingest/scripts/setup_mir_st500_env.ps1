param(
    [string]$InstallRoot,
    [string]$PythonExe = "py",
    [string[]]$PythonArgs = @("-3.11"),
    [string]$ExpectedPythonVersion = "3.11",
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

function Remove-GeneratedVenv {
    param(
        [Parameter(Mandatory = $true)][string]$VenvPath,
        [Parameter(Mandatory = $true)][string]$AllowedRoot
    )

    if (-not (Test-Path $VenvPath)) {
        return
    }
    $resolvedVenv = (Resolve-Path $VenvPath).Path
    $resolvedRoot = (Resolve-Path $AllowedRoot).Path
    $comparison = [System.StringComparison]::OrdinalIgnoreCase
    if (-not $resolvedVenv.StartsWith($resolvedRoot, $comparison)) {
        throw "refusing to remove venv outside generated external root: $resolvedVenv"
    }
    Remove-Item -LiteralPath $resolvedVenv -Recurse -Force
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\..\..")).Path
if (-not $InstallRoot) {
    $InstallRoot = Join-Path $repoRoot ".external\mir-st500"
}
$generatedRoot = Join-Path $repoRoot ".external"

$installRootPath = New-Item -ItemType Directory -Force -Path $InstallRoot
$venvDir = Join-Path $installRootPath.FullName ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if ($Recreate) {
    Remove-GeneratedVenv -VenvPath $venvDir -AllowedRoot $generatedRoot
}

if (-not (Test-Path $venvPython)) {
    Invoke-Checked $PythonExe @PythonArgs -m venv $venvDir
}

$actualPythonVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw "failed to inspect MIR-ST500 prep Python: $venvPython"
}
if ($actualPythonVersion.Trim() -ne $ExpectedPythonVersion) {
    throw "MIR-ST500 prep venv uses Python $actualPythonVersion, expected $ExpectedPythonVersion. Rerun with -Recreate or choose matching -PythonArgs/-ExpectedPythonVersion."
}

Invoke-Checked $venvPython -m pip install --upgrade pip setuptools wheel

# The official MIR-ST500 repo pins spleeter==1.5.4, which is not installable on
# the Python versions available here. Spleeter 2.4.2 is usable for local corpus
# reconstruction, but its Windows dependency metadata pins
# tensorflow-io-gcs-filesystem==0.32.0, which has no compatible Windows wheel.
# Install the working TensorFlow stack first, then install Spleeter without that
# broken transitive pin.
Invoke-Checked $venvPython -m pip install `
    "yt-dlp" `
    "youtube_dl==2021.12.17" `
    "ffmpeg-python==0.2.0" `
    "httpx[http2]==0.19.0" `
    "norbert==0.2.1" `
    "numpy==1.24.3" `
    "pandas==1.5.3" `
    "tensorflow==2.12.1" `
    "tensorflow-io-gcs-filesystem==0.31.0" `
    "librosa==0.11.0" `
    "soundfile==0.14.0" `
    "typer==0.3.2"

Invoke-Checked $venvPython -m pip install --no-deps "spleeter==2.4.2"
Invoke-Checked $venvPython -c "import yt_dlp, youtube_dl, tensorflow; from spleeter.separator import Separator"

Write-Host ""
Write-Host "MIR-ST500 reconstruction environment installed."
Write-Host "`$env:AURAL_MIR_ST500_PREP_PYTHON = `"$venvPython`""
Write-Host "Use this Python for get_youtube.py and do_spleeter.py after audio-source review."
