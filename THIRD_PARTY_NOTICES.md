# Third-Party Notices

This file records notices for third-party runtime artifacts bundled with
AuralPrimer portable builds.

## FFmpeg

AuralPrimer portable builds include `ffmpeg.exe` for ingest-side audio
conversion. FFmpeg is developed by the FFmpeg project:

https://ffmpeg.org/

FFmpeg is licensed under LGPL-2.1-or-later or GPL, depending on the exact build
configuration and linked components. Before distributing a portable build,
verify the selected binary's license terms against its source package. The
portable build records the selected `ffmpeg.exe` source path and SHA-256 in
`portable_manifest.json`.

Upstream FFmpeg license information:

https://ffmpeg.org/legal.html

## Classical MIDI music assets

The optional classical standards collection contains MIDI and LilyPond editions
from the [Mutopia Project](https://www.mutopiaproject.org/). These assets have
their own public-domain, CC BY, or CC BY-SA terms, independently of the GPL
application. Individual source, contributor, arranger, license and modification
notices are in `assets/classical/catalog.json` and accompany installed
packs in `attribution.json`, `LICENSE.txt`, `source/` and `licenses/`.

Offline audio renders use [GeneralUser GS](https://www.schristiancollins.com/generaluser.php)
2.0.3 by S. Christian Collins, under its included music-creation/software-use
license. The complete notice is in
`assets/classical/licenses/GENERALUSER-GS-LICENSE.txt` and each pack.
[FluidSynth](https://www.fluidsynth.org/) 2.6.1 (LGPL-2.1-or-later) is used only
as an offline build tool; its executable and the soundfont are not bundled with
the game. See `assets/classical/README.md` for reproduction and rights.
