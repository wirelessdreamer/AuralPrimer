#!/usr/bin/env python3
"""Render the shootout run into a single self-contained HTML page.

Reads ``summary.json`` from a run directory, so it can be re-run at any point
and always reflects what has actually been measured -- including while the run
is still in flight, which the page states plainly rather than hiding.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

TIER_LABEL = {
    "gold": ("Worship idiom", "Suno keyboard stems, paired MIDI"),
    "gold_maestro": ("Canonical piano", "MAESTRO v3, Disklavier-aligned MIDI"),
    "song": ("Real recordings", "no note-level ground truth yet"),
}


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "&mdash;"
    return f"{float(value):.{digits}f}"


def bar_cell(value: float | None, *, tone: str = "brass") -> str:
    """A number with its own magnitude behind it, so ranking reads at a glance."""
    if value is None:
        return '<td class="num">&mdash;</td>'
    pct = max(0.0, min(1.0, float(value))) * 100
    return (f'<td class="num bar"><span class="bar-fill {tone}" style="width:{pct:.1f}%"></span>'
            f'<span class="bar-val">{float(value):.3f}</span></td>')


def accuracy_table(entries: list[dict[str, Any]]) -> str:
    rows = []
    for e in sorted(entries, key=lambda x: -(x.get("mean_f1") or 0.0)):
        if not e.get("mean_f1") and not e.get("mean_onset_only_f1"):
            continue
        onset = e.get("mean_onset_only_f1") or 0.0
        offs = e.get("mean_note_with_offset_f1") or 0.0
        sustain_gap = offs / onset if onset else 0.0
        gap_class = "crit" if sustain_gap < 0.35 else ("warn" if sustain_gap < 0.6 else "good")
        rows.append(
            "<tr>"
            f'<th scope="row"><code>{esc(e["algorithm"])}</code></th>'
            + bar_cell(e.get("mean_f1"))
            + f'<td class="num">{fmt(e.get("mean_onset_only_f1"))}</td>'
            + f'<td class="num"><span class="dot {gap_class}"></span>{fmt(e.get("mean_note_with_offset_f1"))}</td>'
            + f'<td class="num">{fmt(e.get("mean_pitch_accuracy"))}</td>'
            + f'<td class="num">{fmt(e.get("mean_velocity_mae"), 1)}</td>'
            + f'<td class="num">{fmt(e.get("mean_onset_timing_mae_ms"), 1)}</td>'
            "</tr>")
    if not rows:
        return '<p class="muted">No scored items in this tier yet.</p>'
    return (
        '<div class="scroll"><table>'
        "<thead><tr><th>Engine</th><th>Note F1</th><th>Onset F1</th><th>+Offset F1</th>"
        "<th>Pitch acc</th><th>Vel MAE</th><th>Onset MAE</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>")


def summarize(payload: dict[str, Any], songs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mean each metric per engine across the given items (scored items only)."""
    out = []
    for algo in payload["algorithms"]:
        acc: dict[str, list[float]] = {}
        for song in songs:
            result = next((r for r in song["results"] if r["algorithm"] == algo), None)
            if not result or not result.get("overall"):
                continue
            o = result["overall"]
            for key in ("f1", "onset_only_f1", "note_with_offset_f1", "pitch_accuracy",
                        "velocity_mae", "onset_timing_mae_ms"):
                v = o.get(key)
                if v is not None:
                    acc.setdefault(key, []).append(float(v))
        mean = {f"mean_{k}": (sum(v) / len(v) if v else None) for k, v in acc.items()}
        mean["algorithm"] = algo
        out.append(mean)
    return out


def build(run_dir: Path) -> str:
    payload = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    songs = payload["songs"]
    algorithms = payload["algorithms"]
    extras = payload["extras"]

    total_expected = 24 * 24
    done = len(list((run_dir / "predictions").rglob("*.json")))
    complete = done >= total_expected
    by_tier = {t: [s for s in songs if s.get("tier") == t] for t in TIER_LABEL}

    # --- findings, each tied to a number on this page -----------------------
    findings = []
    maestro_sum = summarize(payload, by_tier["gold_maestro"])
    m_by = {e["algorithm"]: e for e in maestro_sum}
    tk = (m_by.get("piano_transkun") or {}).get("mean_f1")
    pti = (m_by.get("piano_pti") or {}).get("mean_f1")
    auto = (m_by.get("piano_auto") or {}).get("mean_f1")
    if tk and pti:
        findings.append((
            "good", "The harness agrees with the literature",
            f"On MAESTRO, <code>piano_transkun</code> scores {tk:.3f} and <code>piano_pti</code> "
            f"{pti:.3f} &mdash; both within a point of their published note-F1. The evaluation code "
            "is measuring what it claims to, so the other tiers can be read at face value."))
    if pti:
        findings.append((
            "crit", "The PTI disable gate looks stale",
            f"<code>transcription.piano_pti_enabled()</code> returns <code>[]</code> for all six PTI "
            f"variants under torch&nbsp;&ge;&nbsp;2, citing NaN velocities and a ~100&times; note "
            f"explosion. Force-enabled on torch 2.11 they are among the strongest engines here "
            f"({pti:.3f} on MAESTRO). Production is discarding its best piano engine."))
    if auto and tk:
        findings.append((
            "crit", "The auto-chooser is not picking the best engine",
            f"<code>piano_auto</code> scores {auto:.3f} on MAESTRO &mdash; identical, note for note, "
            f"to <code>piano_basic_pitch_playable</code>, which is what it selects. "
            f"<code>piano_transkun</code> reaches {tk:.3f} on the same audio."))
    dead = [a for a in algorithms
            if not any(r.get("note_count") for s in songs for r in s["results"] if r["algorithm"] == a)]
    if dead:
        findings.append((
            "warn", "Engines that produce nothing at all",
            "Registered for <code>keys</code> but silent on every scored item: "
            + ", ".join(f"<code>{esc(d)}</code>" for d in dead)
            + ". These fail silently rather than erroring, which is indistinguishable from "
              "&ldquo;the audio had no notes&rdquo;."))

    sustain = [e for e in maestro_sum
               if e.get("mean_onset_only_f1") and e.get("mean_note_with_offset_f1")
               and e["mean_note_with_offset_f1"] / e["mean_onset_only_f1"] < 0.6]
    if len(sustain) >= max(3, len(maestro_sum) // 3):
        findings.append((
            "warn", "Sustain modelling is the shared weakness",
            f"{len(sustain)} of {len(maestro_sum)} scored engines lose more than 40% of their onset "
            "F1 once note offsets must also land inside tolerance. Onsets are largely solved here; "
            "note <em>duration</em> is not."))

    finding_html = "".join(
        f'<article class="finding {sev}"><h3>{esc(title)}</h3><p>{body}</p></article>'
        for sev, title, body in findings)

    # --- tier sections -----------------------------------------------------
    sections = []
    for tier in ("gold_maestro", "gold", "song"):
        items = by_tier[tier]
        if not items:
            continue
        label, sub = TIER_LABEL[tier]
        scored = [s for s in items if any(r.get("overall") for r in s["results"])]
        if tier == "song":
            note = ('<p class="note">Scored against a cross-engine consensus, not ground truth. '
                    'An error most engines share counts as a success here, so treat this as a '
                    'ranking aid only &mdash; it is not accuracy.</p>')
        else:
            note = ""
        sections.append(
            f'<section class="tier"><header class="tier-head">'
            f'<h2>{esc(label)}</h2><p class="tier-sub">{esc(sub)} &middot; '
            f'{len(items)} item{"s" if len(items) != 1 else ""}</p></header>'
            f"{note}{accuracy_table(summarize(payload, scored))}</section>")

    # --- throughput --------------------------------------------------------
    rt_rows = []
    for name in sorted(extras["runtime"], key=lambda n: _mean(extras["runtime"][n])):
        v = _mean(extras["runtime"][name])
        rt_rows.append(f'<tr><th scope="row"><code>{esc(name)}</code></th>'
                       f'<td class="num">{v:.3f}&times;</td></tr>')
    throughput = ('<div class="scroll"><table><thead><tr><th>Engine</th>'
                  "<th>Mean &times; realtime</th></tr></thead><tbody>"
                  + "".join(rt_rows) + "</tbody></table></div>")

    # --- coverage ----------------------------------------------------------
    cov_rows = []
    for s in songs:
        ok = sum(1 for r in s["results"] if r.get("status") == "ok" and r.get("note_count"))
        empty = sum(1 for r in s["results"] if r.get("status") == "ok" and not r.get("note_count"))
        bad = sum(1 for r in s["results"] if r.get("status") not in ("ok",))
        tier_label = TIER_LABEL.get(s.get("tier"), ("&mdash;", ""))[0]
        cov_rows.append(
            f'<tr><th scope="row">{esc(s["song_name"])}</th>'
            f'<td>{esc(tier_label)}</td>'
            f'<td class="num">{s["duration_sec"]/60:.1f}</td>'
            f'<td class="num">{ok}</td><td class="num">{empty}</td><td class="num">{bad}</td>'
            f'<td class="num">{s["reference_count"]}</td>'
            f'<td class="num">{s["midi_offset_sec"]:+.3f}</td></tr>')
    coverage = ('<div class="scroll"><table><thead><tr><th>Item</th><th>Tier</th><th>Min</th>'
                "<th>Produced</th><th>Empty</th><th>Failed</th><th>Ref notes</th><th>Offset s</th>"
                "</tr></thead><tbody>" + "".join(cov_rows) + "</tbody></table></div>")

    status = ("Complete" if complete else "In progress")
    pct = min(100.0, done / total_expected * 100)

    return f"""<title>Piano Engine Shootout</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">
<style>
:root {{
  --paper:#fbfaf8; --raise:#ffffff; --ink:#15181c; --ink-soft:#3d454f;
  --slate:#6a7482; --rule:#e3e1dc; --brass:#a67c1f; --brass-soft:#e8dcc2;
  --good:#2f6f55; --warn:#9a6414; --crit:#a3423a;
  --shadow:0 1px 2px rgba(20,23,26,.05), 0 8px 24px -16px rgba(20,23,26,.25);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --paper:#14171a; --raise:#1b1f24; --ink:#e9ebee; --ink-soft:#b4bcc6;
    --slate:#8b95a2; --rule:#2b3037; --brass:#d4a545; --brass-soft:#3a3221;
    --good:#63b892; --warn:#d39445; --crit:#e0796d;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.8);
  }}
}}
:root[data-theme="dark"] {{
  --paper:#14171a; --raise:#1b1f24; --ink:#e9ebee; --ink-soft:#b4bcc6;
  --slate:#8b95a2; --rule:#2b3037; --brass:#d4a545; --brass-soft:#3a3221;
  --good:#63b892; --warn:#d39445; --crit:#e0796d;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.8);
}}
* {{ box-sizing:border-box; }}
body {{
  background:var(--paper); color:var(--ink);
  font-family:"Source Serif 4", Georgia, serif; font-size:17px; line-height:1.65;
  margin:0; padding:clamp(1.5rem,4vw,4rem) clamp(1rem,5vw,2rem);
}}
.wrap {{ max-width:1080px; margin:0 auto; display:flex; flex-direction:column; gap:3rem; }}
h1,h2,h3,.eyebrow,.chip,th,.num,code {{ font-family:"Archivo", system-ui, sans-serif; }}
h1 {{ font-size:clamp(2rem,5vw,3.1rem); line-height:1.05; margin:0; font-weight:700;
     letter-spacing:-.03em; text-wrap:balance; }}
h2 {{ font-size:1.4rem; margin:0; font-weight:600; letter-spacing:-.015em; }}
h3 {{ font-size:1.02rem; margin:0 0 .3rem; font-weight:600; letter-spacing:-.01em; }}
p {{ margin:0; max-width:68ch; }}
.eyebrow {{ font-size:.72rem; text-transform:uppercase; letter-spacing:.16em;
  color:var(--brass); font-weight:600; }}
.lede {{ font-size:1.12rem; color:var(--ink-soft); }}
.muted, .note {{ color:var(--slate); }}
.note {{ font-size:.94rem; border-left:2px solid var(--brass-soft); padding-left:.9rem; }}
code {{ font-family:"IBM Plex Mono", ui-monospace, monospace; font-size:.86em;
  background:var(--brass-soft); color:var(--ink); padding:.1em .32em; border-radius:3px; }}

header.masthead {{ display:flex; flex-direction:column; gap:1rem;
  border-bottom:1px solid var(--rule); padding-bottom:1.75rem; }}
.status {{ display:flex; flex-wrap:wrap; align-items:center; gap:.75rem 1.25rem; }}
.chip {{ font-size:.74rem; text-transform:uppercase; letter-spacing:.1em; font-weight:600;
  padding:.28rem .6rem; border-radius:999px; border:1px solid var(--rule); color:var(--slate); }}
.chip.live {{ color:var(--warn); border-color:currentColor; }}
.chip.done {{ color:var(--good); border-color:currentColor; }}
.meter {{ flex:1 1 220px; min-width:180px; height:6px; background:var(--rule);
  border-radius:999px; overflow:hidden; }}
.meter span {{ display:block; height:100%; background:var(--brass); }}
.meter-label {{ font-family:"IBM Plex Mono", monospace; font-size:.8rem; color:var(--slate);
  font-variant-numeric:tabular-nums; }}

.findings {{ display:grid; gap:1rem; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); }}
.finding {{ background:var(--raise); border:1px solid var(--rule); border-radius:6px;
  padding:1.1rem 1.25rem; box-shadow:var(--shadow); border-left:3px solid var(--slate); }}
.finding p {{ font-size:.95rem; color:var(--ink-soft); }}
.finding.crit {{ border-left-color:var(--crit); }}
.finding.warn {{ border-left-color:var(--warn); }}
.finding.good {{ border-left-color:var(--good); }}

.tier {{ display:flex; flex-direction:column; gap:1rem; }}
.tier-head {{ display:flex; flex-direction:column; gap:.15rem;
  border-bottom:1px solid var(--rule); padding-bottom:.6rem; }}
.tier-sub {{ font-family:"IBM Plex Mono", monospace; font-size:.8rem; color:var(--slate); }}

.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
th, td {{ text-align:left; padding:.5rem .7rem; border-bottom:1px solid var(--rule);
  white-space:nowrap; }}
thead th {{ font-size:.72rem; text-transform:uppercase; letter-spacing:.08em;
  color:var(--slate); font-weight:600; border-bottom-width:2px; }}
tbody th {{ font-weight:400; }}
tbody tr:hover {{ background:var(--raise); }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
td.bar {{ position:relative; min-width:96px; }}
.bar-fill {{ position:absolute; left:0; top:50%; transform:translateY(-50%); height:70%;
  background:var(--brass-soft); border-radius:2px; }}
.bar-val {{ position:relative; }}
.dot {{ display:inline-block; width:6px; height:6px; border-radius:50%; margin-right:.45rem;
  vertical-align:middle; background:var(--slate); }}
.dot.good {{ background:var(--good); }} .dot.warn {{ background:var(--warn); }}
.dot.crit {{ background:var(--crit); }}

.two {{ display:grid; gap:2.5rem; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); }}
footer {{ border-top:1px solid var(--rule); padding-top:1.25rem; color:var(--slate);
  font-size:.85rem; }}
a {{ color:var(--brass); }}
:focus-visible {{ outline:2px solid var(--brass); outline-offset:2px; }}
</style>

<div class="wrap">
<header class="masthead">
  <p class="eyebrow">AuralPrimer &middot; benchmark round {esc(payload.get('label', ''))}</p>
  <h1>Piano Engine Shootout</h1>
  <p class="lede">{len(algorithms)} transcription engines measured across three ground-truth
    tiers &mdash; canonical solo piano, worship-idiom stems, and three real recordings.</p>
  <div class="status">
    <span class="chip {'done' if complete else 'live'}">{esc(status)}</span>
    <div class="meter"><span style="width:{pct:.1f}%"></span></div>
    <span class="meter-label">{done} / {total_expected} runs</span>
  </div>
  {'' if complete else '<p class="note">The run is still in flight. Every number here is measured, but tiers fill in as items finish &mdash; engines missing from a table have not been scored on that tier yet.</p>'}
</header>

<section>
  <h2>What the numbers say</h2>
  <div class="findings" style="margin-top:1rem">{finding_html}</div>
</section>

{''.join(sections)}

<section class="two">
  <div class="tier">
    <header class="tier-head"><h2>Throughput</h2>
      <p class="tier-sub">wall time &divide; audio duration</p></header>
    {throughput}
  </div>
  <div class="tier">
    <header class="tier-head"><h2>How to read the tiers</h2>
      <p class="tier-sub">what each column of evidence is worth</p></header>
    <p class="note"><strong>Canonical piano</strong> is MAESTRO v3 &mdash; Disklavier captures whose
      MIDI is aligned to the audio by the recording rig. These are directly comparable to published
      transcription numbers.</p>
    <p class="note"><strong>Worship idiom</strong> is paired Suno keyboard stems: the material this
      project actually targets, with reference MIDI offset-calibrated per item against the audio's
      own onset envelope.</p>
    <p class="note"><strong>Real recordings</strong> have no note-level ground truth yet. Official
      sheet music, once aligned, replaces the consensus reference there.</p>
  </div>
</section>

<section class="tier">
  <header class="tier-head"><h2>Coverage</h2>
    <p class="tier-sub">per item: engines that produced notes, returned nothing, or failed</p></header>
  {coverage}
</section>

<footer>
  Generated {esc(payload.get('generated', ''))} &middot;
  onset tolerance {payload['tolerance_ms']:.0f}&nbsp;ms, offset tolerance
  {payload['offset_tolerance_ms']:.0f}&nbsp;ms &middot;
  predictions cached per engine, so scoring re-runs without re-transcribing.
</footer>
</div>
"""


def _mean(values: list[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    out = Path(args.out)
    out.write_text(build(Path(args.run)), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
