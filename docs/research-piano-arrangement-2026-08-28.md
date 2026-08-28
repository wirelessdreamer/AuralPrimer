# Research — Piano arrangement: PiCoGen rejected, classical reduction shipped (2026-08-28)

Two arrangement tools were evaluated: **PiCoGen** (neural piano-cover
generation) and **automatic piano reduction**. One shipped. This document
records why the other did not, with the evidence, so the question does not get
re-opened from scratch.

## Why arrangement at all

Transcription mislabels instruments in dense mixes. On *Jireh*, whole-mix
MuScriptor labelled **5271 of ~6500 notes** `acoustic_piano` and found **zero
guitar** in a minute of music that plainly has guitar.

That reads as a catastrophic failure and mostly is not one. In a big mix the
piano and the guitar are frequently playing the *same notes*, voiced
differently. The engine got the harmony right and the voicing wrong.

So "which instrument played this note" is a question the audio cannot answer
reliably, while **"what would a pianist play here"** is one the note data can.
The second question is better posed, and it is the one an arrangement tool
asks. The instrument labels stop having to be correct and become what they
actually are: evidence about which notes several parts agree on.

---

## PiCoGen — REJECTED. Three independent blockers.

Upstream: [tanchihpin0517/PiCoGen](https://github.com/tanchihpin0517/PiCoGen)
(papers [arXiv:2407.20883](https://arxiv.org/html/2407.20883v1),
[arXiv:2408.01551](https://arxiv.org/pdf/2408.01551)). Code lives on branches
`v1` / `v2`; `main` holds only a README. Not archived — last `main` commit
2025-05-31, maintainer active on issues as recently as 2025-12-04.

### Blocker 1 (fatal): a required *code* dependency is non-commercial

This is the one that decides it, and it is **not** a weights problem, so the
project's "NC weights are acceptable" allowance does not rescue it.

`PiCoGen-sheetsage/requirements.txt` requires a Jukebox fork
(`git+https://github.com/tanchihpin0517/mirtoolkit_jukebox.git`), whose LICENSE
is OpenAI's verbatim:

> **Noncommercial Use License** — No portion of the Software, nor any content
> created with the Software, may be used for commercial purposes.

It is not optional at runtime: `picogen2/mirtoolkit/sheetsage.py` defaults
`use_jukebox=True` and `picogen2/infer.py` never overrides it.
`docker/Dockerfile-full` bakes Jukebox into the image.

GPL-3.0 §7 forbids imposing further restrictions on downstream recipients. A
field-of-use ("no commercial purposes") clause on a **linked code dependency**
cannot be combined with GPL-3.0-or-later code. Worse, the clause reaches
"content created with the Software" — it purports to restrict the user's own
generated piano covers.

The only theoretical escape is `use_jukebox=False` (SheetSage ships a
handcrafted mel-spectrogram variant). **Unverified**, and unlikely: PiCoGen2's
decoder was fine-tuned on Jukebox-variant hidden states, so a dimensional or
distributional mismatch is expected.

### Blocker 2: the encoder weights are not obtainable

The SheetSage checkpoint URLs in the asset manifest (identical in the PiCoGen
fork and upstream `chrisdonahue/sheetsage@main`) point at
`https://sheetsage.s3.amazonaws.com/...`, which returns **HTTP 403
`AccessDenied`** to both `HEAD` and ranged `GET`. `chrisdonahue/sheetsage-data`
mirrors only the Hooktheory dataset and test fixtures — no model weights. A
clean install cannot run today. The sole surviving route is a 20.46 GB Docker
image (`tanchihpin0517/picogen2:latest-full`, last updated 2024-08-13) with the
checkpoints baked in.

### Blocker 3: the environment is unreachable

`requires-python = ">=3.8,<3.11"`; the official env is `python=3.10`.
Transitive pins from the Jukebox fork are `numba==0.48.0`, `librosa==0.7.2`,
`soundfile==0.10.3.post1`; the SheetSage fork pins `numpy<2`. Our sidecar is
**Python 3.13 / torch 2.11 / numpy ≥2 on Windows**. `numba==0.48.0` (Jan 2020)
has no 3.13 wheels and will not build against the 3.13 C API; the Jukebox
fork's `setup.py` imports the removed `pkg_resources`. Reaching our env means
porting a 2020-era codebase, not adjusting a pin.

Independently: the v2 README requires *"Linux with GPU memory larger than
16GB"* and states *"we still haven't implemented CPU version of PiCoGen2.
Currently we don't have a plan for that."* Weight fetching shells out to
`wget`, which is Windows-hostile.

### The weights situation, for the record

Weights are on Zenodo, not HuggingFace, and the picture is **better than the
repo's own LICENSE claims** — which is itself a problem, because the two
contradict each other:

| Artifact | Host | Stated license | Gate |
|---|---|---|---|
| PiCoGen2 decoder `model_ft_00070000` (467.7 MB) | [Zenodo 13380452](https://zenodo.org/records/13380452) | **CC BY 4.0** (API: `"license": {"id": "cc-by-4.0"}`) | passes |
| PiCoGen v1 `model_00075000` (328.5 MB) | [Zenodo 11649613](https://zenodo.org/records/11649613) | **none** (API: `"license": null`) | **fails — no stated license** |
| SheetSage melody/harmony encoder | S3 (403) | CC BY-NC-SA 3.0 | passes (NC weights allowed) but unobtainable |
| Jukebox 5B VQ-VAE + prior | `openaipublic.azureedge.net` | not separately stated; code is NC | fails |

The repo LICENSE says trained models are CC BY-NC-SA 3.0 — but that header is
copied **verbatim** from `chrisdonahue/sheetsage`'s LICENSE, HookTheory
provenance claim and all. It appears to be inherited boilerplate rather than a
considered statement about PiCoGen's own decoder, and it contradicts Zenodo.

**Conclusion.** Even ignoring the broken downloads and the Python 3.13 wall,
PiCoGen requires linking NC-licensed *code*, which cannot coexist with
GPL-3.0-or-later. Nothing was added to `pyproject.toml`, `requirements-runtime.txt`,
or the README attribution tables — attribution lists what we actually bundle,
and we bundle none of this. If piano-cover generation returns to the roadmap it
needs a model whose **encoder** stack is permissively licensed; the CC BY 4.0
decoder is the one clean piece and it is useless on its own.

---

## Piano reduction — SHIPPED, classical

`python/ingest/src/aural_ingest/algorithms/piano_reduction.py`, exposed as
`aural_ingest piano-reduce <pack>`.

### Why classical rather than neural

The neural framing considered was BERT + semi-supervised reduction
([arXiv:2512.21324](https://arxiv.org/pdf/2512.21324)). Classical won on the
merits, not merely as a fallback:

1. **No weights, so no licence gate to clear.** The permanent lesson of the
   PiCoGen review above.
2. **Zero new dependencies.** Nothing added to `pyproject.toml` or
   `requirements-runtime.txt`; nothing to mirror into the frozen sidecar;
   nothing new in the README attribution tables. It runs offline, on CPU, on
   Windows, inside the existing sidecar.
3. **The hand model already existed.** `algorithms/piano_playability.py`
   already owns span, finger count, salience ranking, motif protection and the
   overtone cull — for a *single* part. The genuinely missing capability was
   never the reduction cut; it was turning *many* parts into the one part that
   module already knows how to handle.

### What it actually does — simplification, then harmonisation

* **Simplify.** `merge_parts` collapses cross-part unisons (one key, one
  finger), unioning the time span and taking the loudest velocity;
  `collapse_octave_doublings` thins a pitch class sounding in too many octaves
  at one attack, keeping the outer voices (bass anchor, melody) and cutting the
  middle; then `make_playable` does the hand-model cut, unchanged.
* **Harmonise.** `assign_hands` deals the survivors into two non-crossing hands
  using `piano_playability.hand_split`'s own split point, so the hand
  assignment agrees by construction with the feasibility test the notes were
  cut to satisfy. Output that is only a note list has skipped this step:
  "playable" is a claim about hands, so the hands belong in the output.

### The one thing a single-part pass cannot know

How many parts played a note. A pitch that guitar and keys both attack at the
same instant is one note to a pianist and **two independent votes** that the
note is real. `merge_parts` records the contributing roles as *provenance* —
which is exactly the corroboration input `piano_playability`'s salience model
already accepts for agreement between transcription sources. The doubling that
made the transcription look wrong is what makes the reduction confident.

Role priors set which part wins a unison's label and which is treated as the
melody: vocals > lead guitar > melodic > keys > bass > rhythm guitar. Rhythm
guitar sits last because it is overwhelmingly strummed doubling of harmony some
other part also states — the part most likely to *be* the duplicate.

### Verified

On a synthetic four-part pack shaped like the *Jireh* case (guitar and keys
stating the same harmony an octave apart, bass, vocal tune): **8 of 8 attack
groups unplayable before → 0 after**, 64 notes in → 32 out, and **8 of 8 melody
notes kept** with `motif_intact: true`. 48 unit tests; the source `notes.mid` is
byte-identical after a run.

### Deliberately not done

Default import behaviour is unchanged — this is analysis, invoked explicitly.
No note is ever invented: everything in the output was played by some part in
the source. There is no "fill the left hand with a root-fifth" step, because
fabricating harmony to pad a thin hand is a claim the note data does not
support.
