[CmdletBinding()]
param(
  [string]$RepoRoot = "",
  [string]$OutDir = "dist/sidecar",
  [string]$SourceExePath = "",
  [string]$TargetTriple = "",
  [string[]]$TauriAppRoots = @(),
  [switch]$SkipBuild,
  [switch]$SyncTauriBinaries
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

$ExternalBinContract = "binaries/aural_ingest"
$ExternalBinName = "aural_ingest"

function Resolve-AbsolutePath([string]$BasePath, [string]$PathValue) {
  if ([string]::IsNullOrWhiteSpace($PathValue)) {
    return ""
  }
  if ([System.IO.Path]::IsPathRooted($PathValue)) {
    return [System.IO.Path]::GetFullPath($PathValue)
  }
  return [System.IO.Path]::GetFullPath((Join-Path $BasePath $PathValue))
}

function Assert-NotBlank([string]$Value, [string]$Label) {
  if ([string]::IsNullOrWhiteSpace($Value)) {
    throw "$Label resolved to an empty path"
  }
}

function Get-Sha256Lower([string]$PathValue) {
  return (Get-FileHash -Path $PathValue -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-PropertyValue([object]$Obj, [string]$Name) {
  if ($null -eq $Obj) {
    return $null
  }
  $prop = $Obj.PSObject.Properties[$Name]
  if ($null -eq $prop) {
    return $null
  }
  return $prop.Value
}

function Get-PythonCommand([string]$RepoRootAbs) {
  $venvCandidates = @(
    (Join-Path $RepoRootAbs "python/ingest/.venv/Scripts/python.exe"),
    (Join-Path $RepoRootAbs "python/ingest/venv/Scripts/python.exe")
  )
  foreach ($candidate in $venvCandidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      return @($candidate)
    }
  }

  $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    return @($pyLauncher.Source, "-3")
  }

  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($python) {
    return @($python.Source)
  }

  $python3 = Get-Command python3 -ErrorAction SilentlyContinue
  if ($python3) {
    return @($python3.Source)
  }

  throw "Could not find a Python interpreter. Expected python/ingest/.venv/Scripts/python.exe, `py -3`, `python`, or `python3`."
}

function Invoke-Checked([string[]]$CommandPrefix, [string[]]$Arguments, [string]$Label, [string]$Workdir) {
  Push-Location $Workdir
  try {
    & $CommandPrefix[0] @($CommandPrefix | Select-Object -Skip 1) @Arguments
    if ($LASTEXITCODE -ne 0) {
      throw "$Label failed with exit code $LASTEXITCODE"
    }
  } finally {
    Pop-Location
  }
}

function Invoke-CapturedCommand([string]$Executable, [string[]]$Arguments, [string]$Label, [string]$Workdir) {
  Push-Location $Workdir
  try {
    $output = & $Executable @Arguments 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    return [pscustomobject]@{
      exit_code = $exitCode
      output = $output.Trim()
      ok = ($exitCode -eq 0)
    }
  } finally {
    Pop-Location
  }
}

function Get-TargetTriple([string]$RequestedTriple, [string]$RepoRootAbs) {
  if (-not [string]::IsNullOrWhiteSpace($RequestedTriple)) {
    return $RequestedTriple.Trim()
  }

  foreach ($envName in @("TAURI_ENV_TARGET_TRIPLE", "CARGO_BUILD_TARGET", "TARGET")) {
    $value = [string][Environment]::GetEnvironmentVariable($envName)
    if (-not [string]::IsNullOrWhiteSpace($value)) {
      return $value.Trim()
    }
  }

  Push-Location $RepoRootAbs
  try {
    $hostTuple = (& rustc --print host-tuple 2>$null | Out-String).Trim()
    if (-not [string]::IsNullOrWhiteSpace($hostTuple)) {
      return $hostTuple
    }

    $verbose = (& rustc -Vv 2>$null | Out-String)
    foreach ($line in ($verbose -split "`r?`n")) {
      if ($line -match "^host:\s+(.+)$") {
        return $Matches[1].Trim()
      }
    }
  } finally {
    Pop-Location
  }

  throw "Could not determine target triple. Provide -TargetTriple or ensure rustc is installed."
}

function Get-FileNameWithoutExe([string]$Name) {
  if ($Name.EndsWith(".exe", [System.StringComparison]::OrdinalIgnoreCase)) {
    return $Name.Substring(0, $Name.Length - 4)
  }
  return $Name
}

function Get-ExpectedExternalBinaryName([string]$TargetTripleValue, [string]$SourceAbs) {
  $sourceLeaf = Split-Path -Leaf $SourceAbs
  $baseName = Get-FileNameWithoutExe $sourceLeaf
  $ext = [System.IO.Path]::GetExtension($sourceLeaf)
  return "$baseName-$TargetTripleValue$ext"
}

function Get-RuntimeAssetHash([object]$Asset) {
  if ($null -eq $Asset) {
    return ""
  }
  foreach ($name in @("sha256", "sha256_tree")) {
    $prop = $Asset.PSObject.Properties[$name]
    if ($null -ne $prop -and -not [string]::IsNullOrWhiteSpace([string]$prop.Value)) {
      return [string]$prop.Value
    }
  }
  return ""
}

function Assert-RuntimeAsset([object]$Asset, [string]$Label, [bool]$Required) {
  if ($null -eq $Asset) {
    if ($Required) {
      throw "runtime-check missing asset payload for $Label"
    }
    return
  }

  $okProp = $Asset.PSObject.Properties["ok"]
  $hashValue = Get-RuntimeAssetHash $Asset
  if ($Required -and (($null -eq $okProp) -or (-not [bool]$okProp.Value))) {
    $assetError = [string](Get-PropertyValue $Asset "error")
    $assetPath = [string](Get-PropertyValue $Asset "path")
    throw "runtime-check required asset '$Label' is unavailable: $assetError $assetPath".Trim()
  }
  if (($null -ne $okProp) -and [bool]$okProp.Value -and [string]::IsNullOrWhiteSpace($hashValue)) {
    throw "runtime-check asset '$Label' is marked ok but did not include a hash"
  }
}

function Get-ConfiguredExternalBins([string]$AppRootAbs) {
  $tauriConfigPath = Join-Path $AppRootAbs "tauri.conf.json"
  if (-not (Test-Path -LiteralPath $tauriConfigPath -PathType Leaf)) {
    throw "Missing tauri.conf.json: $tauriConfigPath"
  }

  $config = Get-Content -LiteralPath $tauriConfigPath -Raw | ConvertFrom-Json
  $bundle = $config.bundle
  if ($null -eq $bundle) {
    return @()
  }
  $externalBin = $bundle.externalBin
  if ($null -eq $externalBin) {
    return @()
  }
  return @($externalBin | ForEach-Object { [string]$_ })
}

function Sync-TauriExternalBinary(
  [string]$AppRootAbs,
  [string]$PackagedSidecarPath,
  [string]$ExpectedExternalBinaryLeaf,
  [string]$ExpectedHash
) {
  $configuredBins = Get-ConfiguredExternalBins $AppRootAbs
  if ($configuredBins -notcontains $ExternalBinContract) {
    throw "Tauri config missing bundle.externalBin contract '$ExternalBinContract': $AppRootAbs/tauri.conf.json"
  }

  $binariesDir = Join-Path $AppRootAbs "binaries"
  New-Item -ItemType Directory -Path $binariesDir -Force | Out-Null

  $destination = Join-Path $binariesDir $ExpectedExternalBinaryLeaf
  Copy-Item -LiteralPath $PackagedSidecarPath -Destination $destination -Force
  $copiedHash = Get-Sha256Lower $destination
  if ($copiedHash -ne $ExpectedHash) {
    throw "Tauri externalBin copy hash mismatch for ${destination}: expected $ExpectedHash got $copiedHash"
  }

  return [ordered]@{
    app_root = $AppRootAbs
    configured_external_bin = $ExternalBinContract
    destination_path = $destination
    destination_name = $ExpectedExternalBinaryLeaf
    sha256 = $copiedHash
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

$ingestRoot = Join-Path $repoRootAbs "python/ingest"
$specPath = Join-Path $ingestRoot "aural_ingest.spec"
if (-not (Test-Path -LiteralPath $specPath -PathType Leaf)) {
  throw "Missing PyInstaller spec: $specPath"
}

$pythonCommand = Get-PythonCommand $repoRootAbs
$pythonDisplay = ($pythonCommand -join " ")
$resolvedTargetTriple = Get-TargetTriple $TargetTriple $repoRootAbs

$sourceAbs = Resolve-AbsolutePath $repoRootAbs $SourceExePath
if ($sourceAbs -and -not (Test-Path -LiteralPath $sourceAbs -PathType Leaf)) {
  throw "SourceExePath does not exist: $sourceAbs"
}

if (-not $sourceAbs) {
  $defaultBuiltSidecar = Join-Path $ingestRoot "dist/aural_ingest.exe"
  if ($SkipBuild) {
    if (-not (Test-Path -LiteralPath $defaultBuiltSidecar -PathType Leaf)) {
      throw "SkipBuild requested but built sidecar not found: $defaultBuiltSidecar"
    }
    $sourceAbs = [System.IO.Path]::GetFullPath($defaultBuiltSidecar)
  } else {
    Invoke-Checked $pythonCommand @("-m", "pip", "install", "--upgrade", "pip") "pip upgrade" $ingestRoot
    Invoke-Checked $pythonCommand @("-m", "pip", "install", "--upgrade", "pyinstaller") "install pyinstaller" $ingestRoot
    Invoke-Checked $pythonCommand @("-m", "pip", "install", "--no-deps", "-e", ".") "install ingest sidecar package" $ingestRoot
    Invoke-Checked $pythonCommand @("-m", "PyInstaller", "--noconfirm", "--clean", $specPath) "PyInstaller build" $ingestRoot

    if (-not (Test-Path -LiteralPath $defaultBuiltSidecar -PathType Leaf)) {
      throw "Expected built sidecar missing: $defaultBuiltSidecar"
    }
    $sourceAbs = [System.IO.Path]::GetFullPath($defaultBuiltSidecar)
  }
}

if (-not $sourceAbs) {
  throw "No sidecar executable available. Provide -SourceExePath, or run without -SkipBuild."
}

$runtimeCheck = Invoke-CapturedCommand $sourceAbs @("runtime-check") "runtime-check" $repoRootAbs

$runtimePayload = $null
if (-not [string]::IsNullOrWhiteSpace($runtimeCheck.output)) {
  try {
    $runtimePayload = $runtimeCheck.output | ConvertFrom-Json
  } catch {
    throw "runtime-check did not emit valid JSON: $($runtimeCheck.output)"
  }
}

if ($null -eq $runtimePayload) {
  throw "runtime-check returned no JSON payload"
}

$runtimeAssets = Get-PropertyValue $runtimePayload "assets"
if ($null -eq $runtimeAssets) {
  $runtimeAssets = [pscustomobject]@{}
}

$basicPitchAsset = Get-PropertyValue $runtimeAssets "basic_pitch_model"
$demucsModelpackAsset = Get-PropertyValue $runtimeAssets "demucs_modelpack"
$mt3CheckpointAssets = Get-PropertyValue $runtimeAssets "mt3_checkpoints"

Assert-RuntimeAsset $basicPitchAsset "basic_pitch_model" $false
Assert-RuntimeAsset $demucsModelpackAsset "demucs_modelpack" $false
if ($null -ne $mt3CheckpointAssets) {
  foreach ($engineAssetProp in @($mt3CheckpointAssets.PSObject.Properties)) {
    if ($null -ne $engineAssetProp) {
      Assert-RuntimeAsset $engineAssetProp.Value ("mt3_checkpoints/" + $engineAssetProp.Name) $false
    }
  }
}

$packagedSidecar = Join-Path $outDirAbs "aural_ingest.exe"
Copy-Item -LiteralPath $sourceAbs -Destination $packagedSidecar -Force

$sourceItem = Get-Item -LiteralPath $sourceAbs
$packagedItem = Get-Item -LiteralPath $packagedSidecar
$sha = Get-Sha256Lower $packagedSidecar
$manifestPath = Join-Path $outDirAbs "build_manifest.json"
$expectedExternalBinaryLeaf = Get-ExpectedExternalBinaryName $resolvedTargetTriple $packagedSidecar

$resolvedAppRoots = @()
$normalizedAppRoots = @()
foreach ($rawAppRoot in $TauriAppRoots) {
  if ([string]::IsNullOrWhiteSpace($rawAppRoot)) {
    continue
  }

  foreach ($appRootPart in @($rawAppRoot -split "[,;]")) {
    $appRoot = $appRootPart.Trim()
    if (-not [string]::IsNullOrWhiteSpace($appRoot)) {
      $normalizedAppRoots += $appRoot
    }
  }
}

foreach ($appRoot in $normalizedAppRoots) {
  $resolved = Resolve-AbsolutePath $repoRootAbs $appRoot
  if (-not (Test-Path -LiteralPath $resolved -PathType Container)) {
    throw "TauriAppRoot does not exist: $resolved"
  }
  if ($resolvedAppRoots -notcontains $resolved) {
    $resolvedAppRoots += $resolved
  }
}

if ($SyncTauriBinaries -and $resolvedAppRoots.Count -eq 0) {
  $resolvedAppRoots = @(
    (Join-Path $repoRootAbs "apps/desktop/src-tauri"),
    (Join-Path $repoRootAbs "apps/game/src-tauri")
  ) | Where-Object { Test-Path -LiteralPath $_ -PathType Container }
}

$syncedTauriBinaries = @()
foreach ($appRootAbs in $resolvedAppRoots) {
  $syncInfo = Sync-TauriExternalBinary `
    -AppRootAbs $appRootAbs `
    -PackagedSidecarPath $packagedSidecar `
    -ExpectedExternalBinaryLeaf $expectedExternalBinaryLeaf `
    -ExpectedHash $sha
  $syncedTauriBinaries += [pscustomobject]$syncInfo
}

$manifest = [ordered]@{
  schema_version = 3
  sidecar_name = "aural_ingest.exe"
  tauri_sidecar_name = $ExternalBinName
  tauri_external_bin = $ExternalBinContract
  tauri_target_triple = $resolvedTargetTriple
  tauri_external_binary_name = $expectedExternalBinaryLeaf
  built_at_utc = [DateTime]::UtcNow.ToString("o")
  source_path = $sourceItem.FullName
  source_size_bytes = [int64]$sourceItem.Length
  source_last_write_utc = $sourceItem.LastWriteTimeUtc.ToString("o")
  packaged_path = $packagedItem.FullName
  packaged_size_bytes = [int64]$packagedItem.Length
  packaged_last_write_utc = $packagedItem.LastWriteTimeUtc.ToString("o")
  sha256 = $sha
  python_command = $pythonDisplay
  skip_build = [bool]$SkipBuild
  synced_tauri_binaries = $syncedTauriBinaries
  runtime_check = $runtimePayload
  model_assets = [ordered]@{
    basic_pitch_model = $basicPitchAsset
    demucs_modelpack = $demucsModelpackAsset
    mt3_checkpoints = $mt3CheckpointAssets
  }
}
$manifest | ConvertTo-Json -Depth 10 | Set-Content -Path $manifestPath -Encoding UTF8

$result = [pscustomobject]$manifest
$result | Add-Member -NotePropertyName manifest_path -NotePropertyValue $manifestPath
$result | Write-Output
