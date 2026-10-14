# OhioXR 2026: data inventory for the talk

What the benchmark, shootout and process evidence can support on stage, ranked
by how well it backs the theme: **measurement is what makes AI-built software
trustworthy and parallelisable.** Every number was checked against the file
named beside it (paths relative to the repo root unless noted). The Slide
column names slides by title, since slide numbers shift as the deck changes.

## On slides now

| # | Data point | Source | Slide |
|---|---|---|---|
| 1 | Shipped piano chooser **0.671** vs Transkun **0.991** on MAESTRO (10 pieces). Transkun matches its published number, which proves the harness is sound. It isn't in the shipped chain. | `benchmarks/piano/runs/20260822_2026-08-22_piano-shootout/report.md` | The benchmark is not the product |
| 2 | The same engines on Psalms keyboard stems (10, Suno stems + paired MIDI): Transkun **0.667**, PTI **0.587**, shipped **0.584**, Basic Pitch **0.522**. 24 engines × 24 items = 576 runs. | same | The benchmark is not the product |
| 3 | Shipped drums **0.284** vs YourMT3 **0.590** (E-GMD stratified test-30, 3,904 events). The shipped default scores **0.000 on toms and cymbals**. YourMT3 still waits for a listening review. | `benchmarks/drums/phase1_baseline_report.md` | Best measured vs shipped |
| 4 | Keys cleanup climb **0.706 → 0.928** on synthetic audio (4 cases, 117 notes). On real audio no step beat the baseline: MAESTRO **0.979 → 0.835**, Psalms **0.587 → 0.532**. | `docs/research-piano-cleanup-deep-dive-2026-06-20.md` + shootout | A climb that didn't transfer |
| 5 | `thresholds.yml`: **12 decisions, 81 machine checks**. **4** pin a "not promoted" result. **156** benchmark JSONs are checked in. | `benchmarks/thresholds.yml` | The harness |
| 6 | Receipts. Silent zero: 104 Guitar-TECHS cases "ok" with 0 notes, from a 24-bit WAV bug. MR-MT3 promoted from a remembered number, measured 0.234 < 0.284, demoted. A zero rationalised as "correctly tuned". Test-set leakage: 0.547 was honestly 0.534 < 0.535. YourMT3 mis-gated. A hand model hidden on an unmerged branch. | agent report §3; commits `62e1888`, `4815469`, `ae48023` (main checkout) | What the harness caught |
| 8 | **100** research claims put to 3-vote refutation panels: **89 confirmed, 11 refuted**. Runs used up to 105 agents. YourMT3 guitar: paper 88.92 → ours 0.688 (GuitarSet-40) → 0.287 vs DSP 0.347 (Guitar-TECHS-104). | `docs/research-guitar-amptone-2026-06-27.md`, `research-transcription-novel-approaches-2026-07-02.md`, `thresholds.yml` | Agents checking agents |
| 9 | **383** non-merge commits on main, all agent-written: **339 Claude Code** (co-author trailer, first 2026-05-21) and **44 Codex** (no trailer; attributed by the author). **48** merged PRs, 45 from agent branches. **14** worktrees. **2,542** tests (1,057 Py · 1,214 TS · 271 Rust). | `git rev-list`, `gh pr list`, `git worktree list` | The workflow, in numbers |
| 10 | Ableton Full Control MCP: **245** tools, **740** tests, 60 of 68 commits Claude co-authored. Every Ableton render and test in AuralPrimer went through it. (The ~5 s leading silence on the classical renders was a render bug, trimmed at import; it's not a talking point.) | `D:/Code/AbletonFullControlMCP`; `docs/midi-studio-import.md` | Giving the agent a studio · The description is not the tool |
| 11 | Jireh: **5,271** of ~6,500 notes labelled piano, **0** guitar. The reduction takes unplayable chords **8 → 0** (synthetic 4-part pack). | `docs/research-piano-arrangement-2026-08-28.md` | Ask a better question |
| 12 | Four guitar views (string lanes, neck highway, tab scroll, neck coach) mocked as one running page at **11:24**; all four built into the game by **15:48** the same day (2026-10-09). | `git log` on branch `claude/guitar-mockups-test-tabs-19fca8`: `63c137c` → `cc550ef`; page `docs/guitar-prototype/guitar-surfaces.html` | Mock it first · Four guitar views |
| 13 | Nashville fretboard mock-up: **3 versions in 7 minutes** (17:17 → 17:24), one sentence of feedback each ("bleed out of the fixed position", "colour by octave"). | same branch: `019953a` → `634bb37` → `6477ffe`; page `docs/guitar-prototype/nashville-fretboard.html` | Mock it first · Feedback in plain words |
| 14 | Our importer kept **52 of 1,330** notes of a commercial lead tab (Enter Sandman, feedBack pack): it read the sparse top-level summary, not each phrase's hardest level. Found while building the mock-up on real tabs; fixed before any game code. | commit `7950215` | Mock it first |
| 15 | Controller mount: parametric Blender script, every part checked watertight and one piece, warns if it would touch the strings. **Not printed yet.** | `hardware/quest-guitar-mount/` (`cfb81af`) | Hardware gets a mock-up too |

The live mock-ups on slides 22-26 are local copies in `mockups/` (from `docs/guitar-prototype/`, embed mode `#embed-<view>`). Re-copy them if the mock-ups change. They need no network.

## Available, not yet on a slide

- **Separation SDR** (MUSDB18-HQ, 10 tracks): RoFormer **9.027** vs Demucs **7.324**. The intuitive `demucs_ft_drums` lost even on drums (8.143 vs 8.959) and wasn't promoted. Source: `benchmarks/quality/musdb_separation_eval.md`.
- **Sampling bias**: "20 cases" turned out to be one performance on 20 kits. Stratifying reversed the drum ranking (`combined_filter` went from top to last, 0.123). Source: `benchmarks/drums/phase1_baseline_report.md`.
- **The full drum ladder** on E-GMD test-30: YourMT3 0.590 · drum-CRNN run-4 0.576 · Magenta 0.535 · ADTOF 0.422 · shipped 0.284 · MR-MT3 0.234. In-house run-4 is the only weights we control end to end.
- **Silent zeros, all three**: 24-bit WAV, Demucs float32 output (`piano_polyphonic` 0 → 16,039 notes after a PCM_16 conversion), and RMVPE returning `[]` in ~0.1 s. Gates now require a non-zero note count.
- **The stale-binary lesson (March 2026)**: an old sidecar exe in the portable collapsed drums to 3 notes. It became a CI recovery guard and the "always rebuild the portable" rule. Source: `TRANSCRIPTION_REGRESSION_HISTORY.md`.

## Don't use on stage

- **0.928 as an accuracy claim.** It's 4 synthetic cases, tuned on the same set, with a lenient tolerance.
- **Relative-consensus scores and the agreement percentages** (11.5%, 27.7%). They measure agreement, not correctness.
- **Funk-20 drum numbers, the "+50% drums" win, and the Suno 6-song drum scores.** The references are suspect; some MIDIs were offset by up to −8.92 s.
- **"Psalms gold" as human ground truth.** It's Suno stems with Suno MIDI: exactly aligned, not human-performed. Say "paired".
- **CI status, either way.** CI isn't passing and isn't a focus right now; the CI slide was cut. Thresholds run in warn mode.
- **Paper numbers as ours** (YourMT3 88.92/88.79).
- **Tiny slices** (fast-5, short-20, 2- to 12-case buckets). Signs flipped between 8 and 60 cases.
- **Shipped guitar F1.** There isn't one; the only torchcrepe GuitarSet run errored on 12/12 cases.
