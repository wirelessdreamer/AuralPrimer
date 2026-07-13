param(
    [string]$DownloadRoot = "E:\AudioSourceOfTruthData\raw_datasets\musdb18_hq",
    [string]$ExtractRoot = "E:\AudioSourceOfTruthData\extracted\musdb18_hq",
    [switch]$SkipDownload,
    [switch]$SkipExtract
)

$ErrorActionPreference = "Stop"

$url = "https://zenodo.org/records/3338373/files/musdb18hq.zip?download=1"
$expectedMd5 = "12d4f2ecd55245a4688754dd76363103"
$zipPath = Join-Path $DownloadRoot "musdb18hq.zip"

New-Item -ItemType Directory -Force -Path $DownloadRoot | Out-Null
New-Item -ItemType Directory -Force -Path $ExtractRoot | Out-Null

if (-not $SkipDownload) {
    Write-Host "Downloading MUSDB18-HQ from Zenodo."
    Write-Host "Target: $zipPath"
    & curl.exe --location --fail --continue-at - --output $zipPath $url
    if ($LASTEXITCODE -ne 0) {
        throw "curl failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path -LiteralPath $zipPath)) {
    throw "MUSDB18-HQ archive is missing: $zipPath"
}

Write-Host "Verifying MD5: $zipPath"
$actualMd5 = (Get-FileHash -LiteralPath $zipPath -Algorithm MD5).Hash.ToLowerInvariant()
if ($actualMd5 -ne $expectedMd5) {
    throw "MUSDB18-HQ MD5 mismatch. Expected $expectedMd5, got $actualMd5"
}

if (-not $SkipExtract) {
    Write-Host "Extracting MUSDB18-HQ to $ExtractRoot"
    $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
    if ($tar) {
        & $tar.Source -xf $zipPath -C $ExtractRoot
        if ($LASTEXITCODE -ne 0) {
            throw "tar extraction failed with exit code $LASTEXITCODE"
        }
    } else {
        Expand-Archive -LiteralPath $zipPath -DestinationPath $ExtractRoot -Force
    }
}

$candidateRoots = @(
    (Join-Path $ExtractRoot "musdb18hq"),
    $ExtractRoot
)
$musdbRoot = $null
foreach ($candidate in $candidateRoots) {
    if ((Test-Path -LiteralPath (Join-Path $candidate "test")) -and
        (Test-Path -LiteralPath (Join-Path $candidate "train"))) {
        $musdbRoot = (Resolve-Path -LiteralPath $candidate).Path
        break
    }
}

if (-not $musdbRoot) {
    Write-Warning "Could not find train/test folders yet. If extraction is skipped, rerun without -SkipExtract."
    Write-Host "`$env:AURAL_MUSDB18_HQ_ROOT = `"<extracted-musdb18hq-root>`""
    exit 0
}

$testTracks = Get-ChildItem -LiteralPath (Join-Path $musdbRoot "test") -Directory -ErrorAction SilentlyContinue
$trainTracks = Get-ChildItem -LiteralPath (Join-Path $musdbRoot "train") -Directory -ErrorAction SilentlyContinue

Write-Host "MUSDB18-HQ ready."
Write-Host "Train tracks: $($trainTracks.Count)"
Write-Host "Test tracks: $($testTracks.Count)"
Write-Host "`$env:AURAL_MUSDB18_HQ_ROOT = `"$musdbRoot`""
