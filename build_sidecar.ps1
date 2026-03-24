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

function Resolve-PythonInvocation([string]$RepoRootPath) {
  $venvCandidates = @(
    (Resolve-AbsolutePath $RepoRootPath "python/ingest/.venv/Scripts/python.exe"),
    (Resolve-AbsolutePath $RepoRootPath ".venv/Scripts/python.exe")
  )

  foreach ($candidate in $venvCandidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
      return [pscustomobject]@{
        command = $candidate
        args = @()
      }
    }
  }

  $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    return [pscustomobject]@{
      command = $pyLauncher.Source
      args = @("-3")
    }
  }

  $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($pythonCommand) {
    return [pscustomobject]@{
      command = $pythonCommand.Source
      args = @()
    }
  }

  throw "No usable Python interpreter found. Expected python/ingest/.venv/Scripts/python.exe, .venv/Scripts/python.exe, py, or python."
}

function Resolve-DefaultDemucsModelPackZipPath([string]$RepoRootPath) {
  $candidates = @(
    "dist/modelpacks/demucs_6.zip",
    "assets/modelpacks/demucs_6.zip",
    "modelpacks/demucs_6.zip",
    "demucs_6.zip"
  )

  foreach ($candidate in $candidates) {
    $abs = Resolve-AbsolutePath $RepoRootPath $candidate
    if ($abs -and (Test-Path -LiteralPath $abs -PathType Leaf)) {
      return $abs
    }
  }

  throw "Unable to resolve demucs_6.zip under $RepoRootPath"
}

function Invoke-CheckedProcess([string]$Command, [object[]]$Arguments, [string]$FailureMessage) {
  & $Command @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "$FailureMessage (exit code $LASTEXITCODE)"
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

$demucsModelpackAbs = Resolve-DefaultDemucsModelPackZipPath $repoRootAbs

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
    $specPath = Join-Path $ingestRoot "aural_ingest.spec"
    $runtimeRequirementsPath = Join-Path $ingestRoot "requirements-runtime.txt"
    $buildRequirementsPath = Join-Path $ingestRoot "requirements-build.txt"
    if (-not (Test-Path -LiteralPath $specPath -PathType Leaf)) {
      throw "Missing PyInstaller spec: $specPath"
    }
    if (-not (Test-Path -LiteralPath $runtimeRequirementsPath -PathType Leaf)) {
      throw "Missing runtime requirements file: $runtimeRequirementsPath"
    }
    if (-not (Test-Path -LiteralPath $buildRequirementsPath -PathType Leaf)) {
      throw "Missing build requirements file: $buildRequirementsPath"
    }
    $pythonInvocation = Resolve-PythonInvocation $repoRootAbs

    Push-Location $ingestRoot
    try {
      Invoke-CheckedProcess `
        -Command $pythonInvocation.command `
        -Arguments (@($pythonInvocation.args) + @(
          "-m", "pip", "install", "--upgrade",
          "--requirement", $buildRequirementsPath,
          "--requirement", $runtimeRequirementsPath
        )) `
        -FailureMessage "Failed to install sidecar build/runtime dependencies"

      Invoke-CheckedProcess `
        -Command $pythonInvocation.command `
        -Arguments (@($pythonInvocation.args) + @(
          "-m", "PyInstaller", "--noconfirm", "--clean", $specPath
        )) `
        -FailureMessage "PyInstaller build failed"
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

$runtimeCheckRaw = ""
$runtimeCheckOutput = & $sourceAbs "runtime-check" "--json" "--demucs-modelpack-zip-path" $demucsModelpackAbs 2>&1
$runtimeCheckExitCode = $LASTEXITCODE
$runtimeCheckRaw = ($runtimeCheckOutput | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($runtimeCheckRaw)) {
  throw "Sidecar runtime-check produced no output"
}

try {
  $runtimeCheck = $runtimeCheckRaw | ConvertFrom-Json
} catch {
  throw "Sidecar runtime-check did not emit valid JSON: $runtimeCheckRaw"
}

if ($runtimeCheckExitCode -ne 0 -or -not $runtimeCheck.ok) {
  $runtimeCheckError = ""
  if ($runtimeCheck -and $runtimeCheck.PSObject.Properties.Name -contains "error") {
    $runtimeCheckError = [string]$runtimeCheck.error
  }
  if ([string]::IsNullOrWhiteSpace($runtimeCheckError)) {
    $runtimeCheckError = $runtimeCheckRaw
  }
  throw "Sidecar runtime-check failed: $runtimeCheckError"
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
  runtime_check = $runtimeCheck
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Path $manifestPath -Encoding UTF8

$result = [pscustomobject]$manifest
$result | Add-Member -NotePropertyName manifest_path -NotePropertyValue $manifestPath
$result | Write-Output
