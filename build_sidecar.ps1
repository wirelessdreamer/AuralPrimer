[CmdletBinding()]
param(
  [string]$RepoRoot = "",
  [string]$OutDir = "dist/sidecar",
  [string]$SourceExePath = "",
  [switch]$SkipBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
  if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
    $RepoRoot = $PSScriptRoot
  } elseif (-not [string]::IsNullOrWhiteSpace($PSCommandPath)) {
    $RepoRoot = Split-Path -Parent $PSCommandPath
  } else {
    $RepoRoot = $PWD.Path
  }
}

function Resolve-AbsolutePath([string]$BasePath, [string]$PathValue) {
  if ([string]::IsNullOrWhiteSpace($PathValue)) {
    return ""
  }
  if ([System.IO.Path]::IsPathRooted($PathValue)) {
    return [System.IO.Path]::GetFullPath($PathValue)
  }
  return [System.IO.Path]::GetFullPath((Join-Path $BasePath $PathValue))
}

function Get-Sha256Lower([string]$PathValue) {
  return (Get-FileHash -Path $PathValue -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-NotBlank([string]$Value, [string]$Label) {
  if ([string]::IsNullOrWhiteSpace($Value)) {
    throw "$Label resolved to an empty path"
  }
}

$repoRootAbs = Resolve-AbsolutePath $PWD.Path $RepoRoot
Assert-NotBlank $repoRootAbs "RepoRoot"
if (-not (Test-Path -LiteralPath $repoRootAbs -PathType Container)) {
  throw "RepoRoot does not exist: $repoRootAbs"
}

$outDirAbs = Resolve-AbsolutePath $repoRootAbs $OutDir
Assert-NotBlank $outDirAbs "OutDir"
New-Item -ItemType Directory -Path $outDirAbs -Force | Out-Null

$sourceAbs = Resolve-AbsolutePath $repoRootAbs $SourceExePath
if ($sourceAbs -and -not (Test-Path -LiteralPath $sourceAbs -PathType Leaf)) {
  throw "SourceExePath does not exist: $sourceAbs"
}

if (-not $sourceAbs) {
  $defaultBuiltSidecar = Join-Path $repoRootAbs "python/ingest/dist/aural_ingest.exe"
  if ($SkipBuild) {
    if (-not (Test-Path -LiteralPath $defaultBuiltSidecar -PathType Leaf)) {
      throw "SkipBuild requested but built sidecar not found: $defaultBuiltSidecar"
    }
    $sourceAbs = [System.IO.Path]::GetFullPath($defaultBuiltSidecar)
  } else {
    $ingestRoot = Join-Path $repoRootAbs "python/ingest"
    $entryPath = Join-Path $ingestRoot "src/aural_ingest/cli.py"
    if (-not (Test-Path -LiteralPath $entryPath -PathType Leaf)) {
      throw "Missing ingest entrypoint: $entryPath"
    }

    Push-Location $ingestRoot
    try {
      & python -m PyInstaller --noconfirm --clean --onefile --name aural_ingest --paths (Join-Path $ingestRoot "src") $entryPath
      if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE"
      }
    } finally {
      Pop-Location
    }

    $builtSidecar = Join-Path $ingestRoot "dist/aural_ingest.exe"
    if (-not (Test-Path -LiteralPath $builtSidecar -PathType Leaf)) {
      throw "Expected built sidecar missing: $builtSidecar"
    }
    $sourceAbs = [System.IO.Path]::GetFullPath($builtSidecar)
  }
}

if (-not $sourceAbs) {
  throw "No sidecar executable available. Provide -SourceExePath, or run without -SkipBuild."
}

$packagedSidecar = Join-Path $outDirAbs "aural_ingest.exe"
Copy-Item -LiteralPath $sourceAbs -Destination $packagedSidecar -Force

$sourceItem = Get-Item -LiteralPath $sourceAbs
$packagedItem = Get-Item -LiteralPath $packagedSidecar
$sha = Get-Sha256Lower $packagedSidecar
$manifestPath = Join-Path $outDirAbs "build_manifest.json"

$manifest = [ordered]@{
  schema_version = 1
  sidecar_name = "aural_ingest.exe"
  built_at_utc = [DateTime]::UtcNow.ToString("o")
  source_path = $sourceItem.FullName
  source_size_bytes = [int64]$sourceItem.Length
  source_last_write_utc = $sourceItem.LastWriteTimeUtc.ToString("o")
  packaged_path = $packagedItem.FullName
  packaged_size_bytes = [int64]$packagedItem.Length
  packaged_last_write_utc = $packagedItem.LastWriteTimeUtc.ToString("o")
  sha256 = $sha
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Path $manifestPath -Encoding UTF8

$result = [pscustomobject]$manifest
$result | Add-Member -NotePropertyName manifest_path -NotePropertyValue $manifestPath
$result | Write-Output
