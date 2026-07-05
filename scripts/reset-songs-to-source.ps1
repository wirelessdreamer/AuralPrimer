<#
.SYNOPSIS
  Reset the AuralPrimer songs library back to source, then re-import everything.

  TESTING TOOL — intentionally NOT wired into the app (no in-game button).
  Run it, or ask Claude to run it, whenever the import/transcription pipeline
  changes and you want every pack rebuilt from the original source folders.

.DESCRIPTION
  Steps (all are logged; nothing is silent):
    1. BACK UP the current library to  data/songs_reset_backup_<timestamp>
       (skip with -NoBackup). Cheap insurance so a bad re-import is undoable
       within a test session.
    2. WIPE every built pack (*.feedpak / *.auralsong) from the songs dir,
       except the names in $KeepNames (the demo, which also self-regenerates
       on app launch).
    3. RE-IMPORT from source:
         a. Piano Psalms corpus  -> import_piano_psalms.py (batch; maps Suno
            stems by role and feeds them in directly, no Demucs).
         b. Standalone Suno stem folders -> `aural_ingest import-dir`, one each.
    4. Print a SUMMARY: reset / re-imported / skipped-missing.

  Missing sources are SKIPPED and logged, never fatal — on this quality-test
  machine, losing a local song whose source is gone is acceptable.

  This is a living testing tool: edit the CONFIG block below as your source
  set moves around. It is safe to re-run.

.PARAMETER DryRun
  Show exactly what would be backed up / deleted / imported, and change nothing.

.PARAMETER NoBackup
  Skip the pre-wipe backup (faster; only if you truly don't care about undo).

.PARAMETER SkipReimport
  Reset only (back up + wipe). Leaves the library empty (plus $KeepNames) so
  you can re-import by hand or from the Studio.

.EXAMPLE
  pwsh scripts/reset-songs-to-source.ps1 -DryRun      # preview
  pwsh scripts/reset-songs-to-source.ps1              # full reset + re-import
  pwsh scripts/reset-songs-to-source.ps1 -SkipReimport
#>
[CmdletBinding()]
param(
  [switch]$DryRun,
  [switch]$NoBackup,
  [switch]$SkipReimport
)

$ErrorActionPreference = 'Stop'

# ============================ CONFIG ============================
# Edit these to match where source data lives on THIS machine.
$RepoRoot   = Split-Path -Parent $PSScriptRoot
$SongsDir   = Join-Path $RepoRoot 'AuralPrimerPortable\data\songs'
# Use the project's own CUDA venv so re-imports run the current pipeline:
$VenvPython = Join-Path $RepoRoot 'python\ingest\.venv\Scripts\python.exe'
$IngestSrc  = Join-Path $RepoRoot 'python\ingest\src'
$PsalmImporter = Join-Path $RepoRoot 'python\ingest\scripts\import_piano_psalms.py'

# Piano Psalms batch corpus — the folder whose "*Piano*Stems*" subfolders are
# imported by import_piano_psalms.py. Leave $null to skip the psalm batch.
$PianoPsalmsCorpus = 'C:\Psalms\Piano Psalms'

# Standalone Suno stem folders, imported individually via `import-dir`.
#   name  = output pack folder name  (produces <name>.feedpak)
#   path  = source folder (a Suno export dir with stems + MIDI)
#   title / artist = metadata overrides
# Add/remove entries as your test set changes. Missing paths are skipped.
$SunoStemFolders = @(
  @{ name = 'ingest_psalm_88_darkness'; path = 'C:\Psalms\Psalm 88 - Darkness Stems'; title = 'Psalm 88 - Darkness'; artist = 'Suno' }
  # @{ name = 'ingest_michael_jackson_beat_it'; path = 'C:\Music\Beat It Stems'; title = 'Beat It'; artist = 'Michael Jackson' }
)

# Melodic transcription method for the standalone imports (matches the psalm
# batch default; change per experiment):
$MelodicMethod = 'piano_chord_supplement'

# Packs preserved through the wipe (the demo also regenerates on app launch):
$KeepNames = @('demo_sine_440hz')
# ================================================================

function Info($m) { Write-Host "[reset] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[reset] $m" -ForegroundColor Yellow }
function Ok($m)   { Write-Host "[reset] $m" -ForegroundColor Green }

Info "repo root : $RepoRoot"
Info "songs dir : $SongsDir"
if ($DryRun) { Warn 'DRY RUN — no files will change.' }

if (-not (Test-Path $SongsDir)) { throw "songs dir not found: $SongsDir" }
if (-not (Test-Path $VenvPython)) { Warn "venv python missing: $VenvPython (re-import will fail)" }

# --- 1. Backup -------------------------------------------------------------
if (-not $NoBackup) {
  $stamp  = Get-Date -Format 'yyyyMMdd_HHmmss'
  $backup = Join-Path (Split-Path $SongsDir -Parent) "songs_reset_backup_$stamp"
  Info "backup -> $backup"
  if (-not $DryRun) { Copy-Item -Recurse -Force $SongsDir $backup }
} else {
  Warn 'skipping backup (-NoBackup)'
}

# --- 2. Wipe built packs ---------------------------------------------------
$built = Get-ChildItem -LiteralPath $SongsDir -Directory -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match '\.(feedpak|auralsong)$' } |
  Where-Object { $KeepNames -notcontains ($_.Name -replace '\.(feedpak|auralsong)$','') }

Info ("wiping {0} built pack(s); keeping: {1}" -f $built.Count, ($KeepNames -join ', '))
foreach ($d in $built) {
  Info "  rm $($d.Name)"
  if (-not $DryRun) { Remove-Item -Recurse -Force $d.FullName }
}

if ($SkipReimport) { Ok 'reset complete (-SkipReimport); library emptied.'; return }

# --- 3a. Re-import Piano Psalms batch --------------------------------------
$env:PYTHONPATH = $IngestSrc
$reimported = 0; $skipped = @()

if ($PianoPsalmsCorpus -and (Test-Path $PianoPsalmsCorpus)) {
  Info "psalm batch <- $PianoPsalmsCorpus"
  $psalmArgs = @(
    $PsalmImporter,
    '--corpus-root', $PianoPsalmsCorpus,
    '--out-root',    $SongsDir,
    '--venv-python', $VenvPython,
    '--ingest-src',  $IngestSrc,
    '--melodic-method', $MelodicMethod
  )
  if ($DryRun) { Info "  would run: $VenvPython $($psalmArgs -join ' ')" }
  else { & $VenvPython @psalmArgs; if ($LASTEXITCODE -ne 0) { Warn "psalm batch exit $LASTEXITCODE" } }
} else {
  Warn "psalm corpus missing/unset: $PianoPsalmsCorpus"; $skipped += 'piano-psalms-batch'
}

# --- 3b. Re-import standalone Suno stem folders ----------------------------
foreach ($s in $SunoStemFolders) {
  if (-not (Test-Path $s.path)) { Warn "skip (source gone): $($s.name) <- $($s.path)"; $skipped += $s.name; continue }
  $outPack = Join-Path $SongsDir ($s.name + '.feedpak')
  Info "import $($s.name) <- $($s.path)"
  # import-dir with no --config: aural_ingest finds the folder's audio and runs
  # the full current pipeline (separation + transcription). To instead reuse the
  # folder's already-separated stems by role, add a --config JSON with
  # {"input_stem_paths": {"bass": "...", ...}} — see import_piano_psalms.py.
  $impArgs = @(
    '-c', 'from aural_ingest.cli import main; import sys; sys.exit(main())',
    'import-dir', $s.path,
    '--out', $outPack,
    '--title', $s.title,
    '--artist', $s.artist,
    '--melodic-method', $MelodicMethod
  )
  if ($DryRun) { Info "  would run: $VenvPython import-dir $($s.path) --out $outPack" }
  else { & $VenvPython @impArgs; if ($LASTEXITCODE -ne 0) { Warn "  $($s.name) exit $LASTEXITCODE"; $skipped += $s.name } else { $reimported++ } }
}

# --- 4. Summary ------------------------------------------------------------
Ok  "done. re-imported (standalone): $reimported"
if ($skipped.Count) { Warn "skipped: $($skipped -join ', ')" }
Info "library now: $((Get-ChildItem -LiteralPath $SongsDir -Directory | Where-Object { $_.Name -match '\.(feedpak|auralsong)$' }).Count) pack(s)"
Info 'note: rebuild the portable if the game/studio need to see library changes at the exe level (usually not — packs are read from data/songs at runtime).'
