[CmdletBinding()]
param(
  [string]$RepoRoot = "",
  [string]$PortableRoot = "D:/AuralPrimer/AuralPrimerPortable",
  [string]$Demucs6ModelPackZipPath = "",
  [string]$FfmpegExePath = "",
  [string]$GameExePath = "",
  # Legacy alias for GameExePath.
  [string]$DesktopExePath = "",
  [string]$StudioExePath = "",
  [string]$SidecarSourceExePath = "",
  [switch]$SkipGameBuild,
  [switch]$SkipStudioBuild,
  # Legacy flag: skip both game and studio builds.
  [switch]$SkipDesktopBuild,
  [switch]$SkipSidecarBuild,
  [switch]$ZipOutput
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

function Resolve-DefaultDemucs6ModelPackZipPath([string]$BasePath) {
  $candidates = @(
    "dist/modelpacks/demucs_6.zip",
    "assets/modelpacks/demucs_6.zip",
    "modelpacks/demucs_6.zip",
    "demucs_6.zip"
  )
  foreach ($candidate in $candidates) {
    $abs = Resolve-AbsolutePath $BasePath $candidate
    if (Test-Path -LiteralPath $abs -PathType Leaf) {
      return $abs
    }
  }
  return ""
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

function Read-ZipJsonEntry([string]$ZipPath, [string]$EntryName) {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
  try {
    $entry = $zip.GetEntry($EntryName)
    if ($null -eq $entry) {
      throw "zip missing ${EntryName}: $ZipPath"
    }
    $stream = $entry.Open()
    $reader = New-Object System.IO.StreamReader($stream)
    try {
      $raw = $reader.ReadToEnd()
    } finally {
      $reader.Dispose()
      $stream.Dispose()
    }
    try {
      return ($raw | ConvertFrom-Json)
    } catch {
      throw "invalid $EntryName in zip '$ZipPath': $($_.Exception.Message)"
    }
  } finally {
    $zip.Dispose()
  }
}

function Normalize-StemRole([string]$Role) {
  $r = $Role.Trim().ToLowerInvariant()
  switch -Regex ($r) {
    "^drum(s)?$" { return "drums" }
    "^vocal(s)?$" { return "vocals" }
    "^key(s|board|boards)?$" { return "keys" }
    default { return $r }
  }
}

function Add-StemRole([System.Collections.Generic.HashSet[string]]$Roles, [object]$Value) {
  if ($null -eq $Value) {
    return
  }

  $parts = @(([string]$Value) -split "[,;|]")
  foreach ($part in $parts) {
    $trimmed = $part.Trim()
    if (-not [string]::IsNullOrWhiteSpace($trimmed)) {
      [void]$Roles.Add((Normalize-StemRole $trimmed))
    }
  }
}

function Add-StemRolesFromNode([System.Collections.Generic.HashSet[string]]$Roles, [object]$Node) {
  if ($null -eq $Node) {
    return
  }

  if ($Node -is [string]) {
    Add-StemRole $Roles $Node
    return
  }

  if ($Node -is [System.Collections.IDictionary]) {
    foreach ($k in @("role", "id", "name", "stem", "target", "instrument")) {
      if ($Node.Contains($k)) {
        Add-StemRole $Roles $Node[$k]
      }
    }
    foreach ($k in @("stems", "roles", "targets", "outputs", "splits", "tracks", "sources")) {
      if ($Node.Contains($k)) {
        Add-StemRolesFromNode $Roles $Node[$k]
      }
    }
    return
  }

  if ($Node -is [pscustomobject]) {
    foreach ($k in @("role", "id", "name", "stem", "target", "instrument")) {
      $v = Get-PropertyValue $Node $k
      if ($null -ne $v) {
        Add-StemRole $Roles $v
      }
    }
    foreach ($k in @("stems", "roles", "targets", "outputs", "splits", "tracks", "sources")) {
      $v = Get-PropertyValue $Node $k
      if ($null -ne $v) {
        Add-StemRolesFromNode $Roles $v
      }
    }
    return
  }

  if (($Node -is [System.Collections.IEnumerable]) -and -not ($Node -is [string])) {
    foreach ($item in $Node) {
      Add-StemRolesFromNode $Roles $item
    }
  }
}

function Get-DeclaredStemRoles([object]$ManifestObj) {
  $roles = New-Object "System.Collections.Generic.HashSet[string]" ([System.StringComparer]::OrdinalIgnoreCase)
  foreach ($k in @("stems", "outputs", "splits", "targets", "tracks", "sources")) {
    $v = Get-PropertyValue $ManifestObj $k
    if ($null -ne $v) {
      Add-StemRolesFromNode $roles $v
    }
  }
  return @($roles | Sort-Object)
}

function Invoke-ScriptFile([string]$ScriptPath, [hashtable]$ScriptArgs) {
  $scriptText = Get-Content -LiteralPath $ScriptPath -Raw
  $sb = [ScriptBlock]::Create($scriptText)
  return & $sb @ScriptArgs
}

$repoRootAbs = Resolve-AbsolutePath $PWD.Path $RepoRoot
Assert-NotBlank $repoRootAbs "RepoRoot"
if (-not (Test-Path -LiteralPath $repoRootAbs -PathType Container)) {
  throw "RepoRoot does not exist: $repoRootAbs"
}

if ([string]::IsNullOrWhiteSpace($GameExePath) -and -not [string]::IsNullOrWhiteSpace($DesktopExePath)) {
  $GameExePath = $DesktopExePath
}
if (-not $GameExePath) {
  $GameExePath = "apps/game/src-tauri/target/release/auralprimer_game_tauri.exe"
}
if (-not $StudioExePath) {
  $StudioExePath = "apps/desktop/src-tauri/target/release/auralprimer_desktop_tauri.exe"
}

$gameExeAbs = Resolve-AbsolutePath $repoRootAbs $GameExePath
$studioExeInput = if (-not [string]::IsNullOrWhiteSpace($StudioExePath)) { $StudioExePath } else { $GameExePath }
$studioExeAbs = Resolve-AbsolutePath $repoRootAbs $studioExeInput
$portableRootAbs = Resolve-AbsolutePath $repoRootAbs $PortableRoot

$demucs6ZipAbs = ""
if ([string]::IsNullOrWhiteSpace($Demucs6ModelPackZipPath)) {
  $demucs6ZipAbs = Resolve-DefaultDemucs6ModelPackZipPath $repoRootAbs
} else {
  $demucs6ZipAbs = Resolve-AbsolutePath $repoRootAbs $Demucs6ModelPackZipPath
}

Assert-NotBlank $gameExeAbs "GameExePath"
Assert-NotBlank $studioExeAbs "StudioExePath"
Assert-NotBlank $portableRootAbs "PortableRoot"
Assert-NotBlank $demucs6ZipAbs "Demucs6ModelPackZipPath"

if (-not (Test-Path -LiteralPath $demucs6ZipAbs -PathType Leaf)) {
  throw "demucs_6 modelpack zip not found: $demucs6ZipAbs (set -Demucs6ModelPackZipPath if needed)"
}

$demucs6ModelPackManifest = Read-ZipJsonEntry -ZipPath $demucs6ZipAbs -EntryName "modelpack.json"
$demucs6ModelPackId = [string](Get-PropertyValue $demucs6ModelPackManifest "id")
if ($demucs6ModelPackId -ne "demucs_6") {
  throw "modelpack.json id must be 'demucs_6' (got '$demucs6ModelPackId' from $demucs6ZipAbs)"
}

$demucs6ModelPackVersion = [string](Get-PropertyValue $demucs6ModelPackManifest "version")
if ([string]::IsNullOrWhiteSpace($demucs6ModelPackVersion)) {
  throw "modelpack.json missing version in $demucs6ZipAbs"
}

$requiredStemRoles = @("keys", "drums", "guitar", "bass", "vocals")
$declaredStemRoles = Get-DeclaredStemRoles $demucs6ModelPackManifest
if ($declaredStemRoles.Count -eq 0) {
  throw "modelpack.json in $demucs6ZipAbs does not declare any stem roles (expected keys/drums/guitar/bass/vocals)"
}

$missingStemRoles = @($requiredStemRoles | Where-Object { $declaredStemRoles -notcontains $_ })
if ($missingStemRoles.Count -gt 0) {
  throw "demucs_6 modelpack missing required stem roles: $($missingStemRoles -join ', ') (declared: $($declaredStemRoles -join ', '))"
}

if ($SkipDesktopBuild) {
  $SkipGameBuild = $true
  $SkipStudioBuild = $true
}

if (-not $SkipGameBuild) {
  Push-Location $repoRootAbs
  try {
    # `--no-bundle` skips MSI/NSIS installer generation (which can fail on
    # WiX/light.exe environments). The portable build only needs the raw
    # executable from target/release/, so installer bundles are not needed.
    & npm run game:build -- --no-bundle
    if ($LASTEXITCODE -ne 0) {
      throw "game:build failed with exit code $LASTEXITCODE"
    }
  } finally {
    Pop-Location
  }
}

if (-not $SkipStudioBuild) {
  Push-Location $repoRootAbs
  try {
    & npm run studio:build -- --no-bundle
    if ($LASTEXITCODE -ne 0) {
      throw "studio:build failed with exit code $LASTEXITCODE"
    }
  } finally {
    Pop-Location
  }
}

if (-not (Test-Path -LiteralPath $gameExeAbs -PathType Leaf)) {
  throw "Game executable not found: $gameExeAbs"
}
if (-not (Test-Path -LiteralPath $studioExeAbs -PathType Leaf)) {
  throw "Studio executable not found: $studioExeAbs"
}

$buildSidecarScript = Join-Path $repoRootAbs "build_sidecar.ps1"
if (-not (Test-Path -LiteralPath $buildSidecarScript -PathType Leaf)) {
  throw "Missing script: $buildSidecarScript"
}

$sidecarArgs = @{
  RepoRoot = $repoRootAbs
  OutDir = "dist/sidecar"
}
if ($SidecarSourceExePath) {
  $sidecarArgs.SourceExePath = $SidecarSourceExePath
}
if ($SkipSidecarBuild) {
  $sidecarArgs.SkipBuild = $true
}

$sidecarInfo = Invoke-ScriptFile -ScriptPath $buildSidecarScript -ScriptArgs $sidecarArgs | Select-Object -Last 1
if (-not $sidecarInfo) {
  throw "build_sidecar.ps1 did not return sidecar metadata"
}

$sidecarBuiltExe = [string]$sidecarInfo.packaged_path
$sidecarBuiltManifest = [string]$sidecarInfo.manifest_path
$sidecarBuiltHash = [string]$sidecarInfo.sha256
Assert-NotBlank $sidecarBuiltExe "build_sidecar packaged_path"
Assert-NotBlank $sidecarBuiltManifest "build_sidecar manifest_path"
Assert-NotBlank $sidecarBuiltHash "build_sidecar sha256"
if (-not (Test-Path -LiteralPath $sidecarBuiltExe -PathType Leaf)) {
  throw "Built sidecar not found: $sidecarBuiltExe"
}
if (-not (Test-Path -LiteralPath $sidecarBuiltManifest -PathType Leaf)) {
  throw "Built sidecar manifest not found: $sidecarBuiltManifest"
}

$portableSidecarDir = Join-Path $portableRootAbs "sidecar"
$portableModelPacksDir = Join-Path $portableRootAbs "modelpacks"
$portableDataDir = Join-Path $portableRootAbs "data"
$portableAssetsDir = Join-Path $portableDataDir "assets"
$portableAssetsModelsDir = Join-Path $portableAssetsDir "models"
$portableConfigDir = Join-Path $portableDataDir "config"
$portableSongsDir = Join-Path $portableDataDir "songs"
$portableVisualizersDir = Join-Path $portableDataDir "visualizers"
$portableWebviewDir = Join-Path $portableDataDir "webview"
# Non-destructive portable updates:
# keep existing content (especially songs/config) and overwrite only build artifacts.
# WebView cache is intentionally reset because stale browser state can pin an old app shell.
New-Item -ItemType Directory -Path $portableRootAbs -Force | Out-Null
New-Item -ItemType Directory -Path $portableSidecarDir -Force | Out-Null
New-Item -ItemType Directory -Path $portableModelPacksDir -Force | Out-Null
New-Item -ItemType Directory -Path $portableDataDir -Force | Out-Null
New-Item -ItemType Directory -Path $portableAssetsDir -Force | Out-Null
New-Item -ItemType Directory -Path $portableAssetsModelsDir -Force | Out-Null
New-Item -ItemType Directory -Path $portableConfigDir -Force | Out-Null
New-Item -ItemType Directory -Path $portableSongsDir -Force | Out-Null
New-Item -ItemType Directory -Path $portableVisualizersDir -Force | Out-Null
if (Test-Path -LiteralPath $portableWebviewDir) {
  Remove-Item -LiteralPath $portableWebviewDir -Recurse -Force
}

$portableGameExe = Join-Path $portableRootAbs "AuralPrimer.exe"
$portableStudioExe = Join-Path $portableRootAbs "AuralStudio.exe"
$portableRootSidecarExe = Join-Path $portableRootAbs "aural_ingest.exe"
$portableSidecarExe = Join-Path $portableSidecarDir "aural_ingest.exe"
$portableSidecarManifest = Join-Path $portableSidecarDir "build_manifest.json"
$portableDemucs6ModelPackZip = Join-Path $portableModelPacksDir "demucs_6.zip"

Copy-Item -LiteralPath $gameExeAbs -Destination $portableGameExe -Force
Copy-Item -LiteralPath $studioExeAbs -Destination $portableStudioExe -Force
Copy-Item -LiteralPath $sidecarBuiltExe -Destination $portableRootSidecarExe -Force
Copy-Item -LiteralPath $sidecarBuiltExe -Destination $portableSidecarExe -Force
Copy-Item -LiteralPath $sidecarBuiltManifest -Destination $portableSidecarManifest -Force
Copy-Item -LiteralPath $demucs6ZipAbs -Destination $portableDemucs6ModelPackZip -Force

# Piano transcription checkpoints (piano_pti = Edwards-robust Kong, piano_d3rm
# placeholder for ICASSP 2025 model). Flat directory copies — no modelpack.json
# wrapper. Auto-discovered at runtime by aural_ingest.transcription helpers via
# resolve_piano_pti_checkpoint_path / resolve_piano_d3rm_checkpoint_path.
$pianoModelDirs = @("piano_pti", "piano_d3rm")
$portablePianoModelpacks = @()
foreach ($pianoDirName in $pianoModelDirs) {
  $sourcePianoDir = Join-Path $repoRootAbs ("assets/models/" + $pianoDirName)
  if (-not (Test-Path -LiteralPath $sourcePianoDir -PathType Container)) {
    continue
  }

  $portablePianoDir = Join-Path $portableAssetsModelsDir $pianoDirName
  if (Test-Path -LiteralPath $portablePianoDir) {
    Remove-Item -LiteralPath $portablePianoDir -Recurse -Force
  }
  Copy-Item -LiteralPath $sourcePianoDir -Destination $portableAssetsModelsDir -Recurse -Force

  $pianoCheckpoints = @()
  $missingPianoCheckpoints = @()
  foreach ($file in (Get-ChildItem -LiteralPath $portablePianoDir -File -ErrorAction SilentlyContinue | Sort-Object Name)) {
    if ($file.Extension -notin @(".pth", ".ckpt", ".pt", ".bin", ".safetensors")) {
      continue
    }
    $sha = Get-Sha256Lower $file.FullName
    $pianoCheckpoints += [ordered]@{
      filename = $file.Name
      portable_path = $file.FullName
      size_bytes = $file.Length
      sha256 = $sha
    }
  }

  if ($pianoCheckpoints.Count -eq 0) {
    Write-Host "Skipping piano model dir '$pianoDirName': no checkpoint files (.pth/.ckpt) under $sourcePianoDir."
    if (Test-Path -LiteralPath $portablePianoDir) {
      Remove-Item -LiteralPath $portablePianoDir -Recurse -Force
    }
    continue
  }

  $portablePianoModelpacks += [ordered]@{
    id = $pianoDirName
    source_path = $sourcePianoDir
    portable_path = $portablePianoDir
    checkpoints = $pianoCheckpoints
  }
}

$mt3ModelpackIds = @("mr_mt3", "yourmt3")
$portableMt3Modelpacks = @()
foreach ($modelpackId in $mt3ModelpackIds) {
  $sourceModelpackDir = Join-Path $repoRootAbs ("assets/models/" + $modelpackId)
  if (-not (Test-Path -LiteralPath $sourceModelpackDir -PathType Container)) {
    continue
  }

  $portableModelpackDir = Join-Path $portableAssetsModelsDir $modelpackId
  if (Test-Path -LiteralPath $portableModelpackDir) {
    Remove-Item -LiteralPath $portableModelpackDir -Recurse -Force
  }
  Copy-Item -LiteralPath $sourceModelpackDir -Destination $portableAssetsModelsDir -Recurse -Force

  $versionDirs = @(
    Get-ChildItem -LiteralPath $portableModelpackDir -Directory -ErrorAction SilentlyContinue |
      Sort-Object Name
  )
  $versionManifests = @()
  foreach ($versionDir in $versionDirs) {
    $manifestPath = Join-Path $versionDir.FullName "modelpack.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
      continue
    }

    $manifestObj = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $manifestId = [string](Get-PropertyValue $manifestObj "id")
    if ($manifestId -ne $modelpackId) {
      throw "modelpack.json id '$manifestId' does not match expected '$modelpackId' at $manifestPath"
    }
    $manifestVersion = [string](Get-PropertyValue $manifestObj "version")
    if ([string]::IsNullOrWhiteSpace($manifestVersion)) {
      throw "modelpack.json missing version at $manifestPath"
    }
    if ($manifestVersion -ne $versionDir.Name) {
      throw "modelpack.json version '$manifestVersion' does not match directory name '$($versionDir.Name)' at $manifestPath"
    }

    $manifestSha256 = Get-Sha256Lower $manifestPath
    $checkpointEntries = @()
    $missingCheckpoints = @()
    $declaredCheckpoints = @(Get-PropertyValue $manifestObj "checkpoints")
    foreach ($cp in $declaredCheckpoints) {
      $cpRel = [string](Get-PropertyValue $cp "path")
      if ([string]::IsNullOrWhiteSpace($cpRel)) {
        throw "checkpoint entry missing 'path' field in $manifestPath"
      }
      $cpExpectedSha = [string](Get-PropertyValue $cp "sha256")
      $cpAbs = Join-Path $versionDir.FullName ($cpRel -replace '/', '\')
      if (-not (Test-Path -LiteralPath $cpAbs -PathType Leaf)) {
        $missingCheckpoints += $cpRel
        continue
      }
      $cpActualSha = Get-Sha256Lower $cpAbs
      if (-not [string]::IsNullOrWhiteSpace($cpExpectedSha)) {
        $cpExpectedShaLower = $cpExpectedSha.ToLowerInvariant()
        if ($cpExpectedShaLower -ne $cpActualSha) {
          throw "checkpoint hash mismatch for '$cpAbs': expected $cpExpectedShaLower got $cpActualSha"
        }
      }
      $cpSize = (Get-Item -LiteralPath $cpAbs).Length
      $checkpointEntries += [ordered]@{
        model = [string](Get-PropertyValue $cp "model")
        path = $cpRel
        portable_path = $cpAbs
        format = [string](Get-PropertyValue $cp "format")
        size_bytes = $cpSize
        sha256 = $cpActualSha
      }
    }

    $isComplete = ($missingCheckpoints.Count -eq 0 -and $checkpointEntries.Count -gt 0)
    $versionManifests += [ordered]@{
      version = $manifestVersion
      manifest_path = $manifestPath
      manifest_sha256 = $manifestSha256
      provider = [string](Get-PropertyValue $manifestObj "provider")
      engine = [string](Get-PropertyValue $manifestObj "engine")
      checkpoints = $checkpointEntries
      missing_checkpoints = $missingCheckpoints
      complete = $isComplete
    }
  }

  if ($versionManifests.Count -eq 0) {
    Write-Host "Skipping modelpack '$modelpackId': no readable modelpack.json under any version subdirectory."
    if (Test-Path -LiteralPath $portableModelpackDir) {
      Remove-Item -LiteralPath $portableModelpackDir -Recurse -Force
    }
    continue
  }

  $incompleteVersions = @($versionManifests | Where-Object { -not $_.complete })
  $allComplete = ($incompleteVersions.Count -eq 0)
  $latestVersion = [string]$versionManifests[-1].version

  $portableMt3Modelpacks += [ordered]@{
    id = $modelpackId
    version = $latestVersion
    complete = $allComplete
    source_path = $sourceModelpackDir
    portable_path = $portableModelpackDir
    versions = $versionManifests
  }
}

# Freshness guard: copied portable sidecar must exactly match freshly built sidecar.
$portableRootSidecarHash = Get-Sha256Lower $portableRootSidecarExe
$portableRootSidecarItem = Get-Item -LiteralPath $portableRootSidecarExe
$portableSidecarHash = Get-Sha256Lower $portableSidecarExe
if ($portableRootSidecarHash -ne $sidecarBuiltHash) {
  throw "Portable root sidecar hash mismatch: expected $sidecarBuiltHash got $portableRootSidecarHash"
}
$portableSidecarHash = Get-Sha256Lower $portableSidecarExe
if ($portableSidecarHash -ne $sidecarBuiltHash) {
  throw "Portable sidecar hash mismatch: expected $sidecarBuiltHash got $portableSidecarHash"
}

$portableSidecarManifestObj = Get-Content -LiteralPath $portableSidecarManifest -Raw | ConvertFrom-Json
if ([string]$portableSidecarManifestObj.sha256 -ne $portableSidecarHash) {
  throw "Portable sidecar manifest hash mismatch: manifest=$($portableSidecarManifestObj.sha256) file=$portableSidecarHash"
}
$portableSidecarManifestHash = Get-Sha256Lower $portableSidecarManifest

$sourceLastWriteUtc = [DateTime]::Parse([string]$sidecarInfo.source_last_write_utc).ToUniversalTime()
$portableRootLastWriteUtc = $portableRootSidecarItem.LastWriteTimeUtc
$portableLastWriteUtc = (Get-Item -LiteralPath $portableSidecarExe).LastWriteTimeUtc
if ($portableRootLastWriteUtc -lt $sourceLastWriteUtc) {
  throw "Portable root sidecar timestamp is older than source sidecar (source=$sourceLastWriteUtc portable=$portableRootLastWriteUtc)"
}
if ($portableLastWriteUtc -lt $sourceLastWriteUtc) {
  throw "Portable sidecar timestamp is older than source sidecar (source=$sourceLastWriteUtc portable=$portableLastWriteUtc)"
}

$demucs6SourceSha256 = Get-Sha256Lower $demucs6ZipAbs
$demucs6PortableSha256 = Get-Sha256Lower $portableDemucs6ModelPackZip
if ($demucs6SourceSha256 -ne $demucs6PortableSha256) {
  throw "Portable demucs_6 modelpack hash mismatch: expected $demucs6SourceSha256 got $demucs6PortableSha256"
}

# --- ffmpeg bundling ---
# Resolve ffmpeg.exe: explicit path -> local candidates -> system PATH -> download
$ffmpegSourceAbs = ""
if (-not [string]::IsNullOrWhiteSpace($FfmpegExePath)) {
  $ffmpegSourceAbs = Resolve-AbsolutePath $repoRootAbs $FfmpegExePath
  if (-not (Test-Path -LiteralPath $ffmpegSourceAbs -PathType Leaf)) {
    throw "Explicit FfmpegExePath not found: $ffmpegSourceAbs"
  }
}

if (-not $ffmpegSourceAbs) {
  $ffmpegCandidates = @(
    "dist/ffmpeg/ffmpeg.exe",
    "external/ffmpeg/ffmpeg.exe",
    "ffmpeg.exe"
  )
  foreach ($candidate in $ffmpegCandidates) {
    $abs = Resolve-AbsolutePath $repoRootAbs $candidate
    if (Test-Path -LiteralPath $abs -PathType Leaf) {
      $ffmpegSourceAbs = $abs
      break
    }
  }
}

if (-not $ffmpegSourceAbs) {
  $systemFfmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
  if ($systemFfmpeg) {
    $ffmpegSourceAbs = $systemFfmpeg.Source
  }
}

if (-not $ffmpegSourceAbs) {
  Write-Host "ffmpeg not found locally or on PATH; downloading ffmpeg-release-essentials..."
  $ffmpegDownloadDir = Join-Path $repoRootAbs "dist/ffmpeg"
  New-Item -ItemType Directory -Path $ffmpegDownloadDir -Force | Out-Null
  $ffmpegZipPath = Join-Path $ffmpegDownloadDir "ffmpeg-release-essentials.zip"
  $ffmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
  [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
  Invoke-WebRequest -Uri $ffmpegUrl -OutFile $ffmpegZipPath -UseBasicParsing
  if (-not (Test-Path -LiteralPath $ffmpegZipPath -PathType Leaf)) {
    throw "Failed to download ffmpeg from $ffmpegUrl"
  }
  # Extract only ffmpeg.exe from the zip (it's inside a versioned subdirectory)
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $ffmpegZip = [System.IO.Compression.ZipFile]::OpenRead($ffmpegZipPath)
  try {
    $ffmpegEntry = $ffmpegZip.Entries | Where-Object { $_.Name -eq "ffmpeg.exe" -and $_.FullName -match "bin/ffmpeg\.exe$" } | Select-Object -First 1
    if ($null -eq $ffmpegEntry) {
      throw "ffmpeg.exe not found inside downloaded archive $ffmpegZipPath"
    }
    $ffmpegExtractedPath = Join-Path $ffmpegDownloadDir "ffmpeg.exe"
    $stream = $ffmpegEntry.Open()
    try {
      $outStream = [System.IO.File]::Create($ffmpegExtractedPath)
      try {
        $stream.CopyTo($outStream)
      } finally {
        $outStream.Dispose()
      }
    } finally {
      $stream.Dispose()
    }
  } finally {
    $ffmpegZip.Dispose()
  }
  if (-not (Test-Path -LiteralPath $ffmpegExtractedPath -PathType Leaf)) {
    throw "Failed to extract ffmpeg.exe from $ffmpegZipPath"
  }
  $ffmpegSourceAbs = $ffmpegExtractedPath
  Write-Host "Downloaded ffmpeg to: $ffmpegSourceAbs"
}

Assert-NotBlank $ffmpegSourceAbs "FfmpegExePath"
$portableRootFfmpegExe = Join-Path $portableRootAbs "ffmpeg.exe"
$portableFfmpegExe = Join-Path $portableSidecarDir "ffmpeg.exe"
Copy-Item -LiteralPath $ffmpegSourceAbs -Destination $portableRootFfmpegExe -Force
Copy-Item -LiteralPath $ffmpegSourceAbs -Destination $portableFfmpegExe -Force
$ffmpegSourceSha256 = Get-Sha256Lower $ffmpegSourceAbs
$ffmpegPortableRootSha256 = Get-Sha256Lower $portableRootFfmpegExe
$ffmpegPortableSha256 = Get-Sha256Lower $portableFfmpegExe
if ($ffmpegSourceSha256 -ne $ffmpegPortableRootSha256) {
  throw "Portable root ffmpeg hash mismatch: expected $ffmpegSourceSha256 got $ffmpegPortableRootSha256"
}
if ($ffmpegSourceSha256 -ne $ffmpegPortableSha256) {
  throw "Portable ffmpeg hash mismatch: expected $ffmpegSourceSha256 got $ffmpegPortableSha256"
}

$portableManifestPath = Join-Path $portableRootAbs "portable_manifest.json"
$portableManifest = [ordered]@{
  schema_version = 1
  built_at_utc = [DateTime]::UtcNow.ToString("o")
  game = @{
    source_path = $gameExeAbs
    portable_path = $portableGameExe
    sha256 = (Get-Sha256Lower $portableGameExe)
  }
  studio = @{
    source_path = $studioExeAbs
    portable_path = $portableStudioExe
    sha256 = (Get-Sha256Lower $portableStudioExe)
  }
  portable_data = @{
    root = $portableDataDir
    config_dir = $portableConfigDir
    songs_dir = $portableSongsDir
    visualizers_dir = $portableVisualizersDir
  }
  sidecar = @{
    source_path = [string]$sidecarInfo.source_path
    source_last_write_utc = [string]$sidecarInfo.source_last_write_utc
    source_sha256 = $sidecarBuiltHash
    build_manifest_path = $portableSidecarManifest
    build_manifest_sha256 = $portableSidecarManifestHash
    tauri_target_triple = [string]$portableSidecarManifestObj.tauri_target_triple
    tauri_external_bin = [string]$portableSidecarManifestObj.tauri_external_bin
    tauri_runtime_path = $portableRootSidecarExe
    tauri_runtime_sha256 = $portableRootSidecarHash
    tauri_runtime_last_write_utc = $portableRootLastWriteUtc.ToString("o")
    portable_path = $portableSidecarExe
    portable_last_write_utc = $portableLastWriteUtc.ToString("o")
    portable_sha256 = $portableSidecarHash
    model_assets = $portableSidecarManifestObj.model_assets
    freshness_guard = @{
      hash_match = $true
      timestamp_check = "portable_last_write_utc>=source_last_write_utc and tauri_runtime_last_write_utc>=source_last_write_utc"
    }
  }
  ffmpeg = @{
    source_path = $ffmpegSourceAbs
    source_sha256 = $ffmpegSourceSha256
    tauri_runtime_path = $portableRootFfmpegExe
    tauri_runtime_sha256 = $ffmpegPortableRootSha256
    portable_path = $portableFfmpegExe
    portable_sha256 = $ffmpegPortableSha256
  }
  modelpacks = @(
    [ordered]@{
      id = "demucs_6"
      version = $demucs6ModelPackVersion
      required_stems = $requiredStemRoles
      declared_stems = $declaredStemRoles
      source_path = $demucs6ZipAbs
      source_sha256 = $demucs6SourceSha256
      portable_path = $portableDemucs6ModelPackZip
      portable_sha256 = $demucs6PortableSha256
    }
  ) + $portableMt3Modelpacks + $portablePianoModelpacks
}
$portableManifest | ConvertTo-Json -Depth 8 | Set-Content -Path $portableManifestPath -Encoding UTF8

$zipPath = ""
if ($ZipOutput) {
  $zipPath = "$portableRootAbs.zip"
  if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
  }
  Compress-Archive -Path (Join-Path $portableRootAbs "*") -DestinationPath $zipPath
}

[pscustomobject]@{
  portable_root = $portableRootAbs
  game_exe = $portableGameExe
  studio_exe = $portableStudioExe
  portable_manifest = $portableManifestPath
  sidecar_hash = $portableSidecarHash
  demucs6_modelpack_zip = $portableDemucs6ModelPackZip
  ffmpeg_exe = $portableFfmpegExe
  zip_path = $zipPath
} | Write-Output
