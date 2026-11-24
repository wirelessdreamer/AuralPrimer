# Classical standards library

This expansion contains **45 distinct works in 60 playable tracks**: Bach's
complete *Toccata and Fugue in D minor*, all three movements of each of
Vivaldi's four *Four Seasons* concertos, and **40 additional standards**.
The existing ten-piece piano-midi.de collection is not counted or replaced.

`catalog.json` records every title, composer, work grouping, download URL,
archive member, SHA-256, source score, typesetter/arranger, license, and dated
Mutopia revision. `midi/` contains the unchanged original MIDI files;
`sources/` contains the corresponding LilyPond source and upstream RDF rights
metadata. The small checked-in sources make this collection reproducible
without depending on a future website download.

## Install or verify

Use the ingest environment, which already contains mido, NumPy, soundfile,
PyYAML and jsonschema:

```powershell
python/ingest/.venv/Scripts/python.exe scripts/import_classical_library.py
python/ingest/.venv/Scripts/python.exe scripts/import_classical_library.py --verify-only
python/ingest/.venv/Scripts/python.exe -m unittest discover -s scripts/tests -p test_import_classical_library.py
```

The default destination is `AuralPrimerPortable/data/songs`. Pass `--out` to
install into a different songs folder. Installation only adds this catalog's
`classical__*.feedpak` directories. Existing entries are verified and reused;
the script refuses to overwrite a mismatching entry. Packs are validated in
staging and moved into the watched library only when complete. Audio, staging
and cache files remain in ignored application/scratch folders.

An offline renderer is required for a fresh installation. The version used
here is **FluidSynth 2.6.1** with **GeneralUser GS 2.0.3**. Official archive
URLs and SHA-256 hashes are in `render-tools.json`; the soundfont is pinned to
commit `684543d5e5efaef08d02be50dcda8d552478fa60`. Download and extract those
archives, then pass the executable and soundfont explicitly:

```powershell
python/ingest/.venv/Scripts/python.exe scripts/import_classical_library.py `
  --fluidsynth "C:/tools/fluidsynth/bin/fluidsynth.exe" `
  --soundfont "C:/tools/GeneralUser-GS/GeneralUser-GS.sf2"
```

On this checkout the script also discovers those tools under
`.external/classical-render-tools/`. They are build tools; their binaries and
the soundfont are not bundled in the game or added as runtime dependencies.

## Playback and score fidelity

Each pack contains audible stereo FLAC rendered directly from its **original
MIDI**, using the source's instrument programs, channels, controllers, tempo
changes and meter. There is no transcription or DAW bounce. The audio uses
General MIDI sounds; these are synthesized score performances rather than
recordings by an orchestra or pianist.

The game receives a single **Full score (keyboard practice)** arrangement.
It includes every pitched source note with its original velocity, note-on
tick, note-off tick and PPQ. Overlapping unison pitches use separate spare
channels inside one `Keys` track so the game parser preserves their written
lengths. General MIDI percussion, where present, stays in the original MIDI
and audio and is excluded from the keyboard chart. Original bytes are also
retained in every pack at `source/original.mid`.

Tempo and time signatures are copied into `song_timeline.json`. Beat positions
are calculated on the original tick grid through tempo changes; a tempo
change between beats does not restart the beat grid. Compound meters and
mid-piece meter changes are respected. The notation renderer has a limited
note-value vocabulary, so spelling of dotted notes, tuplets, ornaments and
articulations is approximate; gameplay MIDI timing remains exact. Source MIDI
may use mechanical tempos or explicit repeats, which are retained.

`validation.json` records the completed installation audit: every MIDI parses,
original and gameplay notes/timing match, title/composer/genre are present,
source bytes and rights evidence match their hashes, manifest/timeline/notation
pass the repository's feedpak schemas, audio covers the complete score, and
each render has nonzero RMS without clipping. The game-side parser is also
checked separately during integration. No application rebuild is needed to
discover added song data.

## Works

The requested works are *Toccata and Fugue*, BWV 565, and *Spring*, RV 269;
*Summer*, RV 315; *Autumn*, RV 293; and *Winter*, RV 297. Each season has all
three movements. Additional standards:

| Composer | Added works |
|---|---|
| J. S. Bach | Sheep May Safely Graze; Air on the G String (flute/guitar); Goldberg Aria; Inventions Nos. 1 and 8; Wachet auf, BWV 645 |
| Beethoven | Moonlight Sonata (three movements); Pathétique Sonata (three movements); Symphony No. 5 (first movement); Rage Over a Lost Penny |
| Brahms | Lullaby, Op. 49 No. 4; Waltz, Op. 39 No. 15 |
| Chopin | Nocturnes, Op. 9 Nos. 1 and 2; Revolutionary, Black Keys and Waterfall Études; Minute Waltz; Fantaisie-Impromptu; Preludes, Op. 28 Nos. 4 and 20 |
| Debussy | Arabesques Nos. 1 and 2 |
| Grieg | In the Hall of the Mountain King (piano); March of the Dwarfs |
| Liszt | Consolation No. 3 |
| Mozart | Symphony No. 25 (first movement); Eine kleine Nachtmusik (four movements); Fantasia in D minor, K. 397 |
| Pachelbel | Canon in D (three violins and cello) |
| Rachmaninoff | Preludes, Op. 3 No. 2 and Op. 23 No. 5 |
| Satie | Gymnopédie No. 1; Gnossienne No. 1 |
| Schubert | Ave Maria; Erlkönig |
| Joplin | The Entertainer; Maple Leaf Rag |
| Mendelssohn | Wedding March (organ arrangement) |
| Mussorgsky | Night on Bald Mountain (piano arrangement) |

## Rights and credits

All MIDI and score editions are from the [Mutopia Project](https://www.mutopiaproject.org/).
The [Mutopia licensing statement](https://www.mutopiaproject.org/legal.html)
explicitly permits copying, distribution, modification, performance and
recording. Its public-domain category is the **contributor's dedication of
their edition**, not an assumption that a public-domain composer makes every
third-party MIDI free to redistribute.

Individual editions use public-domain dedication, CC BY 3.0/4.0, or CC BY-SA
2.5/3.0/4.0. Their own license and attribution travel with each original and
derived chart/audio in `attribution.json`, `LICENSE.txt`, `licenses/` and the
source evidence. Adapted ShareAlike assets remain under the corresponding
ShareAlike license, independently of AuralPrimer's GPL software license.
Contributor names and arrangement credits are preserved per entry.

Rendered sounds use **GeneralUser GS** by **S. Christian Collins**, whose
[license](https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/684543d5e5efaef08d02be50dcda8d552478fa60/documentation/LICENSE.txt)
permits private/commercial music creation and software use. Its full notice is
included in `licenses/GENERALUSER-GS-LICENSE.txt` and copied into every pack.
[FluidSynth](https://www.fluidsynth.org/) is an LGPL-2.1-or-later build tool.
