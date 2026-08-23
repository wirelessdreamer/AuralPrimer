# `packages/` — shared libraries (Apache-2.0)

Everything under `packages/` is licensed **Apache-2.0** ([LICENSE](LICENSE)),
unlike the applications in `apps/` and `visualizers/`, which are
GPL-3.0-or-later.

## Why the split

The repository's policy is **libraries permissive, applications copyleft**:

| Tree | Licence | Reasoning |
| --- | --- | --- |
| `packages/` | Apache-2.0 | Shared logic that more than one client must consume |
| `apps/`, `visualizers/` | GPL-3.0-or-later | The applications themselves |
| `UnityClient/` | Apache-2.0 | Cannot be GPL: the Unity runtime is proprietary |

The forcing constraint is the mixed-reality client. The Unity runtime cannot be
sublicensed under the GPL, so that client is Apache-2.0 — and **licence
compatibility only runs one way**: Apache-2.0 code may be used by a GPL work,
but GPL code may not be pulled into an Apache one.

If the shared logic stayed GPL, every function the MR client needed would raise
a relicensing question on the way across. Putting it here answers that once.
Nothing about the desktop client's licence changes: a GPL application consuming
Apache-2.0 libraries is exactly the direction that works.

## What belongs here

Pure, reusable logic with no application coupling — the kind of code where two
clients silently disagreeing would be an invisible and maddening bug:

- music theory: keyboard layout maths, scale degrees, key inference, note names
- timing: tempo maps, tick→seconds conversion, A/V offset maths
- input models: MIDI binding match/capture, note grouping
- container types and schemas

Some of this still lives inside `apps/game` and `visualizers/viz-tab` today. It
moves here when the second client needs it — at which point it is born Apache,
and no relicensing question arises at all.

## What does not

Anything that touches the DOM, Tauri, Unity, or an app's own state. Those stay
in their application and stay GPL.

## Exception

`feedpak/schemas/` carries `SPDX-License-Identifier: MIT` per-file, set
deliberately: a container format specification should be as unencumbered as
possible for anyone writing an interoperating tool. MIT is compatible with both
licences above, so it is left as-is.
