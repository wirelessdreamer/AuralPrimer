# Implementation plans — model upgrades T1–T3 (2026-07-07)

Execution plans for the nine items in
[research-model-usage-reassessment-2026-07-07.md](research-model-usage-reassessment-2026-07-07.md),
grounded file-by-file (6-agent code sweep, run `wf_4dab9c4c-a0b`). Written to
be executed item-by-item by an implementation session. Each plan states its
goal, steps with anchors, verification, and its ship gate.

## Shared conventions (read first, apply to every item)

- **Worktree trap:** the shared venv's editable install points at MAIN's
  `src/`. Prefix any direct CLI/script run with
  `PYTHONPATH=<worktree>/python/ingest/src`. `pytest` is immune. Frozen
  builds from the worktree are safe (`aural_ingest.spec` puts `SPECPATH/src`
  in pathex) but need the venv junctioned in first:
  `New-Item -ItemType Junction python\ingest\.venv -Target D:\AuralPrimer\python\ingest\.venv`.
- **MT3-family resolver caveat:** unlike basic_pitch/piano resolvers, MT3
  search roots do NOT ascend past worktrees (transcription.py:1314-1328
  helper unused there). From a worktree, set `MT3_CHECKPOINT_DIR` or
  `AURALPRIMER_YOURMT3_CHECKPOINT_PATH`, or run with cwd = `D:/AuralPrimer`.
- **Deps:** any new dependency ⇒ update README attribution (both tables,
  upstream link, license) in the same commit, and add the package to
  `aural_ingest.spec` COLLECT_PACKAGES (line 16) / SUBMODULE_PACKAGES (line
  46) if the frozen sidecar needs it. Sidecar change ⇒ rebuild sidecar +
  repack portable before user verification.
- **NC weights handling:** CC BY-NC-SA modelpacks get a `LICENSE` file inside
  the modelpack dir and a `license` field in `modelpack.json`. ShareAlike:
  fine-tunes/conversions of NC-SA weights stay NC-SA. Pin exact upstream
  revisions; mirrors relabel licenses (observed twice).
- **Benchmark methodology (drums):** calibrate on
  `stratified_sample_validation_100.json`, report on
  `stratified_sample_test_30.json`, through the real
  `gt-benchmark --algorithm <id>` path. Fine-taxonomy aggregate carries the
  canonical-note confound for 5-class engines (run4 report:109-111): engines
  emitting one note per class (drum_crnn, ADTOF) lose paired FP+FN against
  ref ride/tom1/tom3; MT3-family engines emitting fine taxonomy don't.
  **Compare 5-class per-class buckets as the primary metric across engine
  families; use the aggregate only within the same family.**
- **Promotion gate (unchanged):** no `gameplay_default` flip on corpus F1
  alone — gameplay-metric regression check + human in-game listening review.

Suggested execution order: **T1.2 → T1.1 → T2.6a → T1.3 → T2.4 → T2.5 →
T3.8a → T3.7 → rest**, because T1.2 needs zero new deps (weights already on
disk), T1.1 proves the madmom install that T1.3 and T3.8 also depend on, and
T2.6a (SDR harness) should exist before any separator swaps.

---

## T1.1 — Beats: Beat This! with madmom DBN post-processing

**Goal:** flip `File2Beats(dbn=False)` → configurable, default True, in the
production meter path. Improves every chart's beat grid.

**Why it was off:** meter_tracker.py:13-14 records "the `--dbn` path pulls
madmom, whose model files are CC BY-NC-SA and non-shippable." Two-layer
stale: (a) NC is now acceptable; (b) research-meter-tracking-2026-07-05.md:57
already noted the DBN decoder (`DBNDownBeatTrackingProcessor`) is **BSD code
that loads no NC model at all** — the concern never applied. The real
historical blocker was install health: madmom's last PyPI release (0.16.1,
2018) breaks on numpy>=1.24; our venv is **py3.13 + numpy 2.4.3**.

**Step 0 (gate — do before any code change):** prove
`pip install git+https://github.com/CPJKU/madmom` builds and imports in the
ingest venv (Cython build; numpy-2.x compat unverified). Then prove
`File2Beats(checkpoint_path=..., device="cpu", dbn=True)` runs on one psalm
WAV. **If this fails, STOP and record the failure** — do not chase a WSL
workaround (the DBN runs in-process; unlike D3RM's subprocess, it's not
isolatable cheaply).

**Steps:**
1. Add `madmom @ git+https://github.com/CPJKU/madmom@<pinned-sha>` to
   pyproject.toml deps; README attribution rows (BSD code; note the DBN path
   loads no NC model); `aural_ingest.spec` COLLECT_PACKAGES.
2. meter_tracker.py:224 — `dbn=True` by default, overridable via
   `AURALPRIMER_METER_DBN=0` env and `config["meter_dbn"]`. Wrap so a madmom
   ImportError degrades to `dbn=False` (NOT to librosa — the model still
   works without DBN). Rewrite docstring lines 13-14.
3. Add `postprocessor: "dbn"|"minimal"` to the returned `meta` (persisted in
   manifests via cli.py:3170-3180 — makes A/B provenance visible per pack).
4. Tests (new coverage, nothing existing pins this —
   test_meter_tracker.py never invokes track_meter with a model): monkeypatch
   `beat_this.inference.File2Beats` to capture the `dbn` kwarg; assert
   env/config override; assert madmom-missing degrades dbn→False without
   losing the beat_this path.
5. `available()` (meter_tracker.py:88-96) stays madmom-agnostic (dbn is an
   enhancement, not a requirement).

**Verify:** ruff + pytest; then A/B on 2-3 psalms via `cmd_refresh_meter`
(cli.py:4129-4226) comparing beats.json/downbeats before/after (no in-house
beat benchmark exists — Beat This!'s ISMIR results say DBN helps downbeats;
our check is bar-line sanity on real songs + the user's ear in Studio).
Sidecar rebuild + portable before user verification.

**Effort:** small (½ day) *if* step 0 passes. **Risk:** madmom build on
py3.13/numpy2.4 — genuinely unknown.

---

## T1.2 — Drums: benchmark (and likely promote) YourMT3+ drums

**Goal:** head-to-head YourMT3+ vs drum_crnn run-4 (0.576 overall / 0.706
macro-5) and mr_mt3_drums on test-30 + psalm listening; if it wins, make it
the drums lead in `gameplay_default` (human-review gated).

**Already true (no work):** weights installed at
`D:/AuralPrimer/assets/models/yourmt3/hf-main-20260325/` (536 MB, sha256
recorded, source HF mimbres/YourMT3 — license Apache-2.0 verified
2026-07-07); engine registered (transcription.py:40-43, engine table
:294-308); mt3_infer 0.1.3 supports the exact checkpoint variant
(adapters/yourmt3.py CHECKPOINT_CONFIGS); `fidelity_midi` profile already
leads with it (transcription.py:163-170); `create_portable.ps1:523` already
stages the pack when present. Prior local evidence: trusted-synthetic run
`20260325_121140` — yourmt3_drums led all 20 engines (mean F1 0.399 vs
mr_mt3 0.292).

**The one missing piece:** `gt-benchmark --algorithm yourmt3_drums` fails —
`get_drum_algorithm` imports `aural_ingest.algorithms.<id>` and no
`algorithms/yourmt3_drums.py` exists (ground_truth_benchmark.py:79-100).

**Steps:**
1. Thin shims `algorithms/yourmt3_drums.py` + `algorithms/mr_mt3_drums.py`:
   `transcribe(stem_path)` delegating to
   `transcription._transcribe_drums_mt3_events(stem_path, "<id>")[0]`.
   Import-safe without the checkpoint (lazy import inside transcribe; raise
   RuntimeError when modelpack absent, matching drum_crnn's contract so
   gt-benchmark records a case error rather than silently passing).
   Unit tests mirroring test_drum_crnn_adapter.py's import-safety tests.
2. Run test-30 for yourmt3_drums AND mr_mt3_drums (mr_mt3 has never been on
   this case set either):
   `MT3_CHECKPOINT_DIR=D:/AuralPrimer/assets/models ... gt-benchmark
   --dataset egmd --algorithm yourmt3_drums --case-id-file
   benchmarks/drums/gt_runs/stratified_sample_test_30.json --split test
   --tolerance-ms 50 --pitch-tolerance-semitones 0`.
   Record `mean_runtime_sec` — no measured runtime exists anywhere (declared
   15x realtime; VRAM unmeasured; capture GPU peak via
   `torch.cuda.max_memory_allocated` in the shim's meta if cheap).
3. Compare per-class 5-class buckets vs run-4 (primary) + aggregate
   (MT3 emits fine taxonomy — no confound; expect its aggregate advantage to
   be partly taxonomic; the run-4 report's confound note explains).
4. Reimport 1-2 drum-heavy psalms with `--drum-filter yourmt3_drums` for the
   listening review (same reimport flow as 2026-07-07, engine flag swapped).
5. If promoted: reorder `TRANSCRIPTION_PROFILES["gameplay_default"]["drum_engines"]`
   to lead with yourmt3_drums (transcription.py:105-118) — **only after the
   user's in-game review**. Add YourMT3 rows to README attribution (it ships
   already via create_portable when the pack is present). Fill
   `apps/desktop/src/models/preferredModelPacks.ts:27-31` url/sha256 if/when
   hosted distribution is wanted (separate decision — 536 MB).

**Effort:** small-medium (1 day incl. benchmark wall-clock). **Risk:** speed
(15x realtime declared → a 4-min song ≈ 16 s GPU, fine; CPU another story —
capture both if feasible).

---

## T1.3 — Drums: evaluate ADTOF (real-music SOTA, CC BY-NC-SA)

**Goal:** measure the model the field says is best on real music (5-class F
0.85-0.89 on MDB/ADTOF-YT; 0.78-0.83 ENST/RGW) against our stack, without
committing to its TF runtime in the sidecar.

**Constraints:** official MZehren/ADTOF only (CC BY-NC-SA covers code +
weights + dataset). xavriley/ADTOF-pytorch is **unlicensed code — not a
legal channel**. Runtime: TF>=2.13 + madmom-from-git + tapcorrect. TF does
NOT go into the frozen sidecar (huge, and only this engine needs it).

**Design: subprocess engine, dedicated venv** — the exact pattern
magenta_egmd_drums uses for its TF1 venv, combined with drums_oaf's
registration template:
1. `python/ingest/scripts/SETUP-ADTOF.md` + a setup script creating a
   dedicated venv (py3.10/3.11) with TF 2.13, madmom (pin the same sha as
   T1.1), tapcorrect, and the official ADTOF repo; document
   `AURAL_ADTOF_PYTHON` (venv python) + `AURAL_ADTOF_REPO` env vars.
2. `algorithms/adtof_drums.py` modeled on drums_oaf.py:187-229's fail-safe
   contract (return `[]` when venv/env absent — never raise) BUT registered
   like the other externals in `KNOWN_NEURAL_DRUM_ENGINES`
   (transcription.py:50-52) + guarded wrapper in
   `build_default_drum_algorithm_registry` (:1297-1309). The adapter shells
   out: writes a temp JSON contract {wav_path, out_json}, invokes the venv
   python running a small runner script inside the ADTOF repo, parses
   events back, maps ADTOF's 5 classes (KD/SD/TT/HH/CY+RD) to canonical
   notes via `_pitch_to_canonical_note` equivalents — note ADTOF's CY class
   *includes ride*, same 5-class emission confound as drum_crnn; score on
   5-class buckets.
3. gt-benchmark on validation-100 (threshold sanity if the runner exposes
   any) + test-30; psalm listening import via `--drum-filter adtof_drums`.
4. Ship decision LATER and only if it beats yourmt3 + run-4 on 5-class
   buckets AND survives listening: shipping = documenting the setup script
   (weights fetched from upstream by the user's machine, modelpack-style
   local layout with LICENSE file). It stays out of every profile's
   `drum_engines` list until then (transcription.py:45-49 comment is the
   rule).

**Effort:** medium (1-2 days; venv wrangling dominates). **Risks:** madmom
(shared with T1.1 — prove there first), TF-on-Windows CPU-only (fine — ~5 MB
model, 10x realtime CPU per paper), subprocess latency per song (~seconds,
amortized).

---

## T2.4 — Drums: crash/ride + toms split and real velocity (7/8-class)

**Goal:** stop collapsing cymbals→crash-49 and toms→47. The entire frontend
already renders 8 lanes — drum_crnn just never lights ride/tom1/tom3.

**Found plumbing (all already in place):**
- Lanes: `_GM_DRUM_LANES` distinguishes crash/ride/tom_low/mid/high
  (feedpak_writer.py:80-103); game maps them to distinct lanes
  (drumTabChart.ts:43-55, chartLoader.ts:67-77 8-lane classify,
  viz-drum-highway 8 lanes). **Zero frontend changes for classes.**
- Scoring: aggregate confound disappears automatically for engines emitting
  49 AND 51 (+48/47/41) — zero scorer changes; optionally extend the
  per-class diagnostic beyond FIVE_CLASS_DRUM_LABELS for crash-vs-ride
  visibility (ground_truth_benchmark.py:54-76).
- Velocity: already flows sidecar→drum_tab.json `hits.v`
  (feedpak_writer.py:398-402) but the game DROPS it:
  chartLoader.ts:39-46 `DrumEvent` lacks velocity and
  vizSongContext.ts:40 hardcodes 100. Fix = add the field + thread it
  (small, isolated frontend change; viz-drum-highway already renders
  velocity-sensitively at :332/:356).

**Two routes (do A; B is the fallback):**
- **Route A — per-stem onset decode (in-house, no new transcriber):** run
  DrumSep MDX23C (6-stem: kick/snare/toms/hh/crash/ride — the
  `aufr33-jarredou_DrumSep_model_mdx23c_ep_141_sdr_10.8059.ckpt`, CC BY-NC-SA
  confirmed via Wayback of the deleted author repo; ~438 MB) on the drums
  stem, then per-stem onset detection (librosa onset or our CRNN's kick/snare
  heads as gates) — class = stem identity, velocity = local stem peak RMS
  (real dynamics, not confidence). New engine id `drum_stemsep` following the
  drums_oaf modelpack/env gating; MSST inference code via ZFTurbo's MIT repo
  (new dep) or a minimal vendored MDX23C forward. **Avoid the xavriley
  pipeline's essentia dep** (AGPL + Windows-painful).
- **Route B — 8-class CRNN retrain:** targets.py:28-56 is the fold point;
  dataset_adapters/egmd.py:225 already reaches fine labels. Bump
  `ModelConfig.num_classes`, new CLASSES tuple + fallback-MIDI map
  (mirror `DRUM_5CLASS_TO_MIDI` in algorithms/_common.py). Cheap to train
  (91 min/run-4) but E-GMD cymbal recall is already the weak spot; expect
  the split to make cymbals harder, not easier (run-4 report :129-130
  proposed the *merge* direction for a reason).

**Verify:** test-30 aggregate (now confound-free for the new engine) +
5-class buckets + a crash/ride-visible diagnostic; psalm listening focused
on the ride lane; velocity visually checked in viz-drum-highway.

**Effort:** medium-large (2-4 days Route A). Depends on: T2.6a for SDR
sanity of the DrumSep checkpoint is optional but nice.

---

## T2.5 — Vocals: pitch lane (RMVPE) + note MIDI

**Goal:** transcribe vocals at all. Today: stem exists, zero notes, zero
overlay (cli.py:3934-3939 hard-excludes vocals).

**License:** RMVPE MIT (code+weights). SOME/ROSVOT weights likely CC
BY-NC-SA (acceptable, verify at implementation) — but start with **heuristic
note segmentation on RMVPE F0**, exactly the torchcrepe precedent
(melodic_torchcrepe.py:49-71 onset-based same-pitch splitting), and add a
neural segmenter later only if the heuristic disappoints.

**Steps (the vocals-lane checklist — every anchor verified):**
1. Adapter `algorithms/melodic_rmvpe.py` shaped like melodic_torchcrepe.py
   (:301-342): `transcribe(stem_path, instrument=...)` → `list[MelodicNote]`;
   graceful `[]` when weights absent; env `AURAL_RMVPE_DEVICE`; freq range
   from INSTRUMENT_FREQ_RANGES. Weights via convention-A resolver
   (`resolve_rmvpe_checkpoint_path`, transcription.py:1464-1511 pattern) +
   `scripts/download_rmvpe_checkpoint.py` (template:
   download_piano_pti_checkpoint.py) → `assets/models/rmvpe/`.
2. Registry + method: add `"melodic_rmvpe"` to KNOWN_MELODIC_METHODS
   (transcription.py:59-94); closure in
   build_default_melodic_algorithm_registry (:1514-1916).
3. New role: `"vocals"` in INSTRUMENT_ROLES (:246-251) — check every
   consumer; `"vocals"` branch in melodic_fallback_chain (:2086-2166) =
   `[melodic_rmvpe, torchcrepe, pyin]`; `"vocals"` in INSTRUMENT_FREQ_RANGES
   (:255-262, ~80–1100 Hz); `"vocals"` key in each profile's
   melodic_methods_by_instrument; vocals stem added to instrument_stems
   collection (cli.py:3521-3533).
4. notes.mid channel: `MIDI_CHANNEL_VOCALS = 5` (cli.py:1481-1487,
   _INSTRUMENT_MIDI_CHANNELS :1519-1525 — otherwise vocals collides with
   legacy melodic on ch4); mirror in chartLoader.ts InstrumentRole +
   CHANNEL_TO_ROLE + name regex (:474-549) and arrangement_prep CONTRACT C3
   (:80-91). playersPanel.ts:153-154's vocals→melodic placeholder becomes
   `vocals`.
5. Free wins to claim: feedpak_writer auto-emits
   `arrangements/notation_vocals.json` once a Vocals track exists in
   notes.mid (writer needs zero changes, :519-545); write raw F0 to the
   spec-reserved `vocal_pitch_contour` manifest field (+`pitch_extraction`
   provenance); un-exclude vocals from spectrogram generation — the Studio's
   lyricTimingWorkspace already probes `spectrogram/vocals/` first
   (lyricTimingWorkspace.ts:783) and lights up immediately.
6. Eval: no in-house vocal GT. Add a MIR-ST500 dataset adapter (NC — now
   usable) with a small stratified sample; plus psalm listening (these are
   vocal-heavy worship covers — the real target).

**Effort:** medium (2-3 days). **Risk:** singing→notes is genuinely hard
(vibrato/portamento); keep expectations at "karaoke-useful," not
"score-perfect." The pitch *contour* lane alone is already user-visible
value.

---

## T2.6 — Stem separation: measure first, then upgrade

**Goal:** in-house SDR numbers (currently impossible — the standing
`museval OK=False`), then evaluate htdemucs_ft (drums) and a RoFormer
provider against htdemucs_6s.

**a) SDR harness (do first — it gates every separation claim):**
1. Add `museval` dep (+ `musdb` if convenient) → README attribution.
2. Set `AURAL_MUSDB18_HQ_ROOT` (env-only by design,
   quality_benchmark.py:59-72); download MUSDB18-HQ (NC — now fine,
   ship_policy stays "internal benchmarking only").
3. Write the missing runner: iterate track dirs (mixture.wav + 4 ref stems),
   run the configured separation provider, map our 6 roles → MUSDB's 4
   (guitar+keys+other→other; keys alias note cli.py:96), call the
   already-implemented-but-never-called `evaluate_museval_separation`
   (quality_benchmark.py:280-395). Wire as a `benchmark-quality` mode or a
   standalone script in benchmarks/quality/.
4. Baseline htdemucs_6s SDR on a ~10-track sample → the reference row.

**b) htdemucs_ft for the drums stem (cheap, same package):**
`_prepare_demucs_weight_file` uses `weights[0]` only (cli.py:1081-1117) and
`_load_demucs_model` loads a single `.th` (:1120-1140) — but htdemucs_ft
upstream is a **BagOfModels of 4 per-source models**. Two options: (i)
drums-only: package just the drums fine-tuned `.th` as a `demucs_ft_drums`
modelpack used by a drums-refinement pass (separate provider or a
post-pass that re-separates only drums); (ii) full bag support: extend
modelpack schema to multi-weight + apply BagOfModels. Start with (i);
measure with (a) before wiring anything into production. New modelpack id ⇒
new candidates in `_default_demucs_modelpack_candidates` (cli.py:928-976)
and create_portable validation (create_portable.ps1:285-319).

**c) RoFormer provider (greenfield):**
Provider = one function registered in
`build_default_stem_separation_provider_registry` (cli.py:1016-1017) **plus
the name added to the known-names set at cli.py:1904** (else it's treated as
a module:function import path). Contract precisely: `fn(mix_wav, stems_dir,
*, mix_sha256, shifts, config, protected_roles)` returning
`{'ok', 'status', 'stem_paths': {role: 'audio/stems/<role>.wav'}, ...}`
(demucs return :1462-1475; external-provider example
test_import_pipeline.py:1452-1470; graceful-fail dict, never raise).
Runtime via ZFTurbo MSST (MIT) as a dep; checkpoint per-license verified and
packaged as its own modelpack. **Interplay warning:** piano_denoise.py (main
checkout WIP) targets *demucs* artifact profiles — a separator change
changes that profile; coordinate with the piano WIP before flipping any
default. Also respect `protected_roles` (user-supplied stems are never
overwritten, cli.py:1308-1342).

**Verify:** SDR vs the (a) baseline per stem; then one full import A/B on a
psalm + downstream drum/melodic F1 spot-check (separation quality bounds
every transcriber — that's the point).

**Effort:** (a) 1 day, (b) 1-2 days, (c) 2-4 days. Order strictly a → b → c.

---

## T3.7 — Guitar: fix the broken eval, then evaluate the QMUL model + tab

**Step 0 — the 24-bit WAV bug (do immediately, tiny):** every
guitar_techs directinput case scores 0.000 for ALL algorithms because
`algorithms/_common.py` `_lin2lin` handles sampwidth 1/2/4 only; 24-bit
WAVs → swallowed exception → silent empty audio
(benchmarks/guitar/phase1_baselines_report.md:30-119). Fix + regression test
+ re-run the 104-case guitar_techs baseline (it has never produced a real
number). Check the user's in-flight main-checkout WIP (transcription.py /
piano work) hasn't already fixed it before touching.

**Then:**
1. **YourMT3 guitar pass** (cheap — T1.2 ships the runtime): shim
   `algorithms/` adapter for guitar roles or run via the melodic path;
   benchmark on the GuitarSet mic 40-case locked baseline (F1 0.222) and
   guitar_techs (post-fix). Paper claims 88.92 onset on GuitarSet; our mix
   reality will be lower — measure it.
2. **QMUL High-Res Guitar Transcription** (arXiv 2402.15258, code public,
   NC-expected — verify license at implementation): evaluate as an external
   engine (subprocess or venv pattern per T1.3 if its deps demand);
   GuitarSet + guitar_techs benchmarks.
3. **Tab lanes (string/fret):** the wire format ALREADY exists —
   arrangement.schema.json notes require `{t, s, f}` (+bends/slides/chords
   with roman-numeral functions); arrangement_prep currently flattens s/f to
   MIDI (:99-113) and feedpak_writer never emits s/f. Plan: Fretting-
   Transformer (arXiv 2506.14223) as MIDI→tab post-pass; emit s/f
   arrangement JSONs alongside notation; extend viz-tab's MelodicNote with
   optional s/f and bypass its greedy `pitchToFret` (:160-172) when present.
   viz-fretboard (pure placeholder today, :24-40) becomes the real fretboard
   lane.
4. **New GT unlocked, free:** guitarset.py already parses per-string indices
   but discards them (`notes, _str_idx`, :160) — keep them, and parse the
   JAMS `chord`/`key_mode` namespaces (docstring :9-17) → per-string tab GT
   and chord/key GT for T3.8, from data already on disk. GAPS (14 h) +
   IDMT-SMT-Guitar as additional corpora.

**Effort:** step 0 tiny; 1-2 medium; 3 large (touches schema→ingest→viz).

---

## T3.8 — Chords & key: fill the reserved slots

**Goal:** ingest-side key + chord detection into the feedpak slots that
already exist end-to-end but are never written: `keys.json` + `harmony.json`
(schemas, manifest keys :50-51, TS types manifest.ts:85-86, Rust
feedpak.rs:84-86 — all present; zero readers/writers).

**Phase a (small, immediate visible win):**
1. Key detection in ingest: port the Krumhansl-Schmuckler already shipped in
   viz-tab (index.ts:303-365) to a small Python pass over transcribed notes
   (no new deps, deterministic), OR madmom's key CNN (NC — dep shared with
   T1.1) for audio-based detection. Write `keys.json` + stamp
   `manifest.harmony.key/mode`.
2. The game HUD lights up for free: `extractKeyModeFromManifest`
   (apps/game/src/hud.ts:33-45) already probes `m.harmony.key/mode` and
   currently defaults to C major.
3. loadFeedpak.ts pointer for keys/harmony (:140-158 currently omits them).

**Phase b (chords):** madmom CNNChordFeatureProcessor (NC ok) or BTC
(ISMIR19 — license unclear, verify; skip if unstated) → `harmony.json`
events `{t, root, quality, rn, bass}`. Ground truth for eval: GuitarSet JAMS
chord annotations (T3.7 step 4) + Ableton-authored fixtures (the established
fixture pattern). Surface: start with Studio-side display + the Nashville
plugin (viz-nashville already computes degrees from key; real chord labels
upgrade it); a dedicated chords viz plugin is its own later item
(vizSongContext needs a new field, :34).

**Effort:** (a) 1 day, (b) 2-3 days. Dependency: madmom proof from T1.1 if
the audio-based route is chosen.

---

## T3.9 — Piano: no action

Already strongest (0.928 F1 supplement path). The actual live issue is the
torch-2.11 PTI incompatibility being worked in the main checkout (piano_pti
WIP + D3RM adapters) — **do not touch from this plan**; coordinate before
any dep changes that could affect torch pinning (notably T2.6c's MSST dep).
Watch list: Mobile-AMT (onset F1 96.7 MAESTRO — weights availability
unverified).

---

## Cross-item dependency graph

```
T1.1 (madmom proof) ──┬─→ T1.3 (ADTOF venv uses same madmom pin)
                      └─→ T3.8a (madmom key CNN, optional route)
T1.2 (yourmt3 shims) ───→ T3.7.1 (guitar pass reuses runtime)
T2.6a (SDR harness) ────→ T2.6b/c (any separator claim)  [soft: T2.4 DrumSep sanity]
T3.7.0 (24-bit fix) ────→ T3.7.1/2 (guitar_techs numbers are fiction until then)
```

Every item lands as its own commit(s) with its benchmark artifacts under
`benchmarks/`, in the established report style (honest negative results
included — run-3/tatum precedent).
