param(
    [string]$InstallRoot,
    [string]$PythonExe = "py",
    [string[]]$PythonArgs = @("-3.10"),
    [string]$AdtofCommit = "b3968fb332f69b65ee07c089fc62f436503755db",
    [string]$MadmomCommit = "27f032e8947204902c675e5e341a3faf5dc86dae",
    [string]$TapcorrectCommit = "4f2d21e73fb0137119a4136513c42936b322fc0b"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..\..")
if (-not $InstallRoot) {
    $InstallRoot = Join-Path $repoRoot ".external\adtof"
}

$installRootPath = New-Item -ItemType Directory -Force -Path $InstallRoot
$adtofRepo = Join-Path $installRootPath.FullName "ADTOF"
$venvDir = Join-Path $installRootPath.FullName ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    & $PythonExe @PythonArgs -m venv $venvDir
}

if (-not (Test-Path $adtofRepo)) {
    git clone https://github.com/MZehren/ADTOF.git $adtofRepo
}

git -C $adtofRepo fetch --tags --prune
git -C $adtofRepo checkout $AdtofCommit

& $venvPython -m pip install --upgrade pip setuptools wheel
& $venvPython -m pip install `
    "librosa>=0.8.0" `
    "Cython" `
    "tensorflow>=2.13.0,<2.16" `
    "numpy>=1.23.5" `
    "matplotlib>=3.8.1" `
    "pandas>=1.2.4" `
    "mir_eval>=0.6" `
    "jellyfish" `
    "pyunpack>=0.2.2" `
    "ffmpeg-python" `
    "pretty_midi>=0.2.9" `
    "beautifulsoup4" `
    "scikit-learn>=1.3.2" `
    "tapcorrect @ git+https://github.com/MZehren/tapcorrect@$TapcorrectCommit#subdirectory=python&egg=tapcorrect" `
    "madmom @ git+https://github.com/CPJKU/madmom@$MadmomCommit"
& $venvPython -m pip install --no-deps -e $adtofRepo

Write-Host ""
Write-Host "ADTOF runtime installed."
Write-Host "`$env:AURAL_ADTOF_PYTHON = `"$venvPython`""
Write-Host "`$env:AURAL_ADTOF_REPO = `"$adtofRepo`""
