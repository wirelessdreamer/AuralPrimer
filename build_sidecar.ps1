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

function Assert-NotBlank([string]$Value, [string]$Label) {
  if ([string]::IsNullOrWhiteSpace($Value)) {
    throw "$Label resolved to an empty path"
  }
}

function Get-Sha256Lower([string]$PathValue) {
  return (Get-FileHash -Path $PathValue -Algorithm SHA256).Hash.ToLowerInvariant()
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

  throw "Could not find a Python interpreter. Expected python/ingest/.venv/Scripts/python.exe, `py -3`, or `python`."
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
    Invoke-Checked $pythonCommand @("-m", "pip", "install", "-e", ".") "install ingest sidecar deps" $ingestRoot
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
if (-not $runtimeCheck.ok) {
  throw "Packaged sidecar runtime-check failed: $($runtimeCheck.output)"
}

$runtimePayload = $null
if (-not [string]::IsNullOrWhiteSpace($runtimeCheck.output)) {
  try {
    $runtimePayload = $runtimeCheck.output | ConvertFrom-Json
  } catch {
    throw "runtime-check did not emit valid JSON: $($runtimeCheck.output)"
  }
}

$packagedSidecar = Join-Path $outDirAbs "aural_ingest.exe"
Copy-Item -LiteralPath $sourceAbs -Destination $packagedSidecar -Force

$sourceItem = Get-Item -LiteralPath $sourceAbs
$packagedItem = Get-Item -LiteralPath $packagedSidecar
$sha = Get-Sha256Lower $packagedSidecar
$manifestPath = Join-Path $outDirAbs "build_manifest.json"

$manifest = [ordered]@{
  schema_version = 2
  sidecar_name = "aural_ingest.exe"
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
  runtime_check = $runtimePayload
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -Path $manifestPath -Encoding UTF8

$result = [pscustomobject]$manifest
$result | Add-Member -NotePropertyName manifest_path -NotePropertyValue $manifestPath
$result | Write-Output
