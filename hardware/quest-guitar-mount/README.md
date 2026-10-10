# Quest controller mount for a headless guitar

A 3D-printable mount that puts a **Meta Quest 3/3S Touch Plus** controller on
the head end of a headless guitar, so the headset always knows where the neck
is and the mixed-reality neck highway can sit on the real frets. The defaults
target a **Jamstik Studio** (headless, 24 frets, 25.5" scale, 40 mm at the nut).

![The mount on the head end, seen from the front](preview.png)

![What the headset sees: the controller's top surface (cyan normal) toward the player](preview-eyes.png)

The controller in these renders is Meta's own Touch Plus model, not a stand-in.

## The parts

| Part | What it does |
|---|---|
| `d_plug` | Replaces the Jamstik's black D-shaped end cover, under the string holes. A copy of the cover's D, with its locating tab, presses into the pocket, and crush ribs give it a tight fit. A block on the end face below the string holes holds two M4 nuts for the cradle. It never touches the strings, the top face, the nut screws or the finish. |
| `cradle` | Bolts to the plug's block with two M4 screws. An arm angles out and back from its plate, and a seat shaped to the real controller holds the grip from below, with the controller's head up and away like a raised headstock. Reprint it to change the angle. |

![The plug, seen from inside the guitar: the D, its tab at the bottom of the curve, and the crush ribs](preview-plug.png)

## How the plug fits

The cover measured 37.00 mm across its flat edge and 12.38 mm to the end of its
tab; the tab stands about 1.9 mm out of the curve and is 2.9 mm wide. The curve
is an arc through the ends of the flat edge and its lowest point, about 21.6 mm
in radius, which is half the head end's width: the cover follows the round back
of the neck.

The plug's D is `d_fit` (0.15 mm) under that size all round, with a 0.5 mm step
at its tip to lead it in. Six crush ribs stand `d_rib` (0.2 mm) proud of the
measured D: four round the curve and two on the flat edge. They shave down as
the plug goes in, which gives a press fit without sanding the whole D. The tab
is only `d_cover_t` deep, as deep as the cover is thick, so it keys into the
rim's notch and the plug goes in only one way round.

With the guitar in playing position, the controller's weight presses the D
against the pocket's walls, so the shape holds it, not friction. Only a pull
straight out of the pocket (the guitar hung head-down, or a knock) relies on
the ribs' grip. Until that has been tried, tie a tether from the cradle to the
neck.

**Print `d_plug_fit_test.stl` first:** just the plug on a 1.2 mm plate. It takes
minutes and shows whether `d_fit` and `d_rib` suit your printer before you print
the whole part.

## How the controller seats

The build reads Meta's Touch Plus model and finds the controller's own frame
from it:

- **Grip axis:** a line through the centres of slices across the lower grip.
- **Top surface:** the face with the thumbstick and buttons. Its normal is
  50° off the grip axis, leaning toward the back of the grip, away from the
  trigger. It matches the model's thumbstick and button axes to about 1°.
- **Buttons, measured up the grip from its end:** the grip button runs 49–69 mm,
  the trigger 81–116 mm and the thumbstick 96–114 mm. The whole controller is
  122 mm along the grip axis.

The seat is a shell minus the convex hull of the grip's lowest 48 mm, grown by
`cradle_clear` (0.6 mm). It holds the grip from below when playing, so the
controller's weight settles it into the seat. The grip's end sits in a cup at
the bottom of the seat, which puts the controller in the same place every time.
The rims stop 2 mm short of the grip's widest line, so the controller lifts
straight out.

**One 20 mm hook-and-loop strap** goes round the outside of the seat and over
the grip, 24–45 mm up from its end. Notches in both rims keep it there, below
the grip button.

**No button can be pressed.** The seat keeps at least `button_clear` (2.5 mm)
plus the clearance away from the trigger (and its travel), the grip button and
the thumbstick. Each build reports the real gaps. At the defaults, with a left
controller, the gaps are:

| Part | Gap to the seat |
|---|---|
| Grip button | 11 mm |
| Trigger | 35 mm |
| Thumbstick | 50 mm |

With a right controller the grip button faces the seat. The seat stops below
it, and the keep-out holds it 3.1 mm away.

**Battery:** the model shows a separate panel on the palm side of the grip that
wraps round its end, which is likely the battery cover. The seat covers its
lower part, so take the strap off and lift the controller out to change the
battery.

## 1. Measure first

Every guitar size is a parameter in `build_mount.py`, and **the guitar defaults
are guesses**. Measure with calipers, write your numbers into a copy of
`my_measurements.example.json`, and print a test fit before trusting them. The
controller is not measured: its shape comes from Meta's model.

**Guitar:**

| Parameter | What to measure |
|---|---|
| `nut_to_tip` | From the fretboard edge of the nut to the end face. |
| `d_top_z` | Height of the D pocket's flat top edge (just under the string holes), relative to the fretboard surface at the nut. Below is negative. The default, -6.6 mm, is read off a photo. It moves only the headset's first guess, not the fit. |
| `d_width` | The cover across its flat edge. Measured 37.00 mm. |
| `d_depth` | The cover from its flat edge to the bottom of its curve, without the tab. 10.4 mm: 12.38 mm measured with the tab, less the tab. |
| `d_tab_w`, `d_tab_len` | The cover's locating tab: its width (2.9 mm) and how far it stands out of the curve (1.9 mm). |
| `d_cover_t` | The cover's thickness, which sets how deep the plug's tab is. **Guessed at 1.5 mm.** |
| `d_plug_len` | How far into the pocket the plug reaches. Stop short of the two Phillips screws inside. **Guessed at 6 mm.** |
| `head_width` | The head end's width at the end face. Measured 43.16 mm; the plug's block is this wide. |
| `head_thickness`, `head_top_z` | The rest of the head end's size. Used for the stand-in and for the check that keeps the controller out of the guitar. |
| `string_spread` | Outer edge of low-E to outer edge of high-e. Used for the stand-in. |

**Fit (print choices):**

| Parameter | Default | What it does |
|---|---|---|
| `d_fit` | 0.15 mm | How far under the measured D the plug is, all round. |
| `d_rib` | 0.2 mm | How far the crush ribs stand proud of the measured D. Raise it if the fit test is loose. |
| `plate_height` | 14 mm | Height of the cradle's plate and the plug's block, from 0.5 mm under the D's flat edge down. |
| `end_wall` | 6 mm | Thickness of the plug's block on the end face, which holds the nuts. |

**Controller:**

| Parameter | What it is |
|---|---|
| `controller_mesh` | Path to Meta's `MetaQuestTouchPlus_Left.fbx` / `_Right.fbx`; `{Hand}` becomes `Left` or `Right`. The default points at the Meta XR SDK in this repo's Unity package cache (`com.meta.xr.sdk.core/Meshes/MetaQuestTouchPlus/`). If the file is missing, the build stops and names this parameter. |
| `controller_hand` | `left` (default) or `right`. |

**Seat:**

| Parameter | Default | What it does |
|---|---|---|
| `cradle_len` | 48 mm | How far up the grip, from its end, the seat reaches. Keep it below the grip button. |
| `cradle_clear` | 0.6 mm | Room between the grip and the seat. |
| `cradle_rim` | 2 mm | How far short of the grip's widest line the rims stop. |
| `button_clear` | 2.5 mm | Extra room kept round the trigger, grip button and thumbstick. |
| `strap_at` | 34.5 mm | Strap centre, measured from the grip's end. |

## 2. Tape-test the angle

The default angle follows how the controller was held for the first test. It
sits past the head end with the grip toward the guitar, rising from the line of
the neck like a raised headstock. Its top control surface is angled toward the
player, so the headset keeps seeing its tracking LEDs.

| Parameter | Default | What it does |
|---|---|---|
| `grip_rise` | 40° | Lifts the controller from the neck line toward the low-E edge (up, when playing). A front-on photo shows this angle. |
| `grip_tilt` | 45° | Tips the controller back toward the player. A front view hides it, and it does the most for tracking. |
| `face_roll` | 60° | Turns the controller about its grip. At 0° the top surface leans toward the player; positive values turn it toward the low-E edge. |
| `arm_back` | 35° | Angles the arm from the neck line toward the back of the neck, so neither the arm nor the seat passes in front of the string holes. |
| `arm_len` | 28 mm | Length of the arm, from the plate to the seat. |

Every build prints how far the controller's top surface (its real normal, from
Meta's model) points from the player's eyes, and warns past 70°. The defaults
give **44°**; the earlier 30°/30° angles give 58° with the real controller. The
eye position is set by `eye_toward_body`, `eye_up` and `eye_back` (mm from the
nut). `preview_eyes.png` shows what the headset sees.

Before printing, tape the controller to the head end at that angle and play for
a minute with the headset on. Once tracking stays steady, set the angles to
match.

## 3. Build the STLs

Needs Blender 4.2 or newer (4.5+ uses the faster Manifold booleans), and Meta's
model where `controller_mesh` points:

```bash
blender --background --factory-startup --python build_mount.py -- --params my_measurements.json
```

This writes `out/`:

- `d_plug.stl` and `cradle.stl`, already turned for printing;
- `d_plug_fit_test.stl`, the plug alone on a thin plate;
- `assembly.stl`, both parts in place in the guitar's frame;
- `mount.blend`, the same with the guitar and Meta's controller in place, in millimetres;
- seven preview renders;
- `mount_offset.json`.

`out/` is git-ignored. The build also checks:

- every part is watertight and in one piece;
- the plug's block leaves at least 1.2 mm round each nut, and stays below the string holes;
- the plug is long enough for its crush ribs behind the tab;
- the top surface's angle to the eyes;
- the gaps to every button;
- the strap stays below the grip button;
- nothing of the cradle is within `string_clear` (100 mm) in front of the string holes;
- a hex key reaches each bolt head straight on;
- the controller stays out of the guitar.

## 4. Print

- **PETG.**
- 0.2 mm layers, 4 walls, 40% gyroid infill.
- Print the fit test first, then the plug on its outside face with the D up (the STL is turned that way), so the D's outline is drawn in single layers. The nut pockets open onto the bed, and their ceilings bridge about 7 mm, so it needs no supports. Press the fit test in; if it is loose, raise `d_rib`, and if it won't go, lower it.
- Print the cradle on the face that bolts to the plug (the STL is turned that way).
  - It stands 79 mm tall on a 36 × 14 mm plate, so use a brim.
  - About 2.5 cm² overhangs past 45°: a patch under the cup next to the arm, and a strip inside the seat under one rim. Add small supports there if your printer needs them.

## 5. Hardware

| Qty | Part |
|---|---|
| 2 | M4 × 8 socket-head screws, heads in the cradle's counterbores |
| 2 | M4 hex nuts, pressed into the plug's block |
| 1 | 20 mm hook-and-loop strap, about 30 cm |

## 6. Assemble

1. Press the two M4 nuts into the hex pockets on the outside of the plug's block.
2. Take the black D cover off the guitar's head end and keep it. Press the plug into the pocket in its place, tab down into the rim's notch, pushing on the block.
3. Screw the cradle to the plug through the two counterbored holes.
4. Set the controller in the seat with its grip's end down in the cup and its head up and away from the guitar. The seat takes it only one way round.
5. Run the strap round the outside of the seat, through both rim notches, and over the grip. It sits below the grip button.
6. In the headset, run the guitar setup's alignment check once. Remount the same way every time (grip's end in the cup) and the saved alignment stays valid.

## `mount_offset.json`

Where the neck sits relative to the controller **model's origin**: the origin and
axes of Meta's `MetaQuestTouchPlus_<Hand>` model as Unity draws it, which is
where the Meta SDK's `OVRControllerPrefab` places it. Axes are the headset's
(Unity), in metres plus a quaternion. `reference_pose` names that pose. The key
names still say `grip` so that the Unity reader (`MountOffset.TryParse`) keeps
working.

The MR client uses the file as its first guess, before the alignment check.
It is exact for the CAD; it is out by as much as the guitar measurements are.
The check replaces it with the real numbers. The mount's job is to land in the
same place every time, which the cup at the grip's end makes possible.

## Meta's controller model: licence

The Touch Plus model is Meta SDK content (Meta XR SDK, `com.meta.xr.sdk.core`).
The build reads it in place, from wherever `controller_mesh` points, every time
it runs. Never commit the FBX, a converted mesh, or any file holding its
geometry. That includes `out/mount.blend`, which contains the controller. The
committed previews show the model only as renders.

## Not yet verified

- **Fit:** nothing has been printed yet.
  - The guitar numbers are guesses, apart from the D cover's size and the head end's width. The cover's thickness (`d_cover_t`) and the room in the pocket before the screws (`d_plug_len`) matter most for the plug.
  - The seat's fit depends on Meta's model matching a real Touch Plus to within the 0.6 mm clearance.
- **Battery cover:** which panel it is, and which way it slides, is read from the model's shape, not checked on a controller.
- **The plug's grip:** how the original cover stays in (a snap lip behind the rim, or friction) is unknown, so the plug relies on crush ribs. A lip would allow a snap-in plug. Until a pull straight out has been tried, keep a tether on.
- **Tracking:** a controller that isn't held may lose tracking when its face turns away. Horizon OS may also switch to hand tracking when it sees the controller as set down.
- **Reference pose:** the client applies the offset to the controller's tracked pose. Whether that pose is exactly where the Meta SDK draws its model has not been checked on a headset. The alignment check absorbs any difference.

The MR client's guitar setup is where both tracking questions get handled. See
the guitar section of `docs/unity-mr-client-plan-2026-08-18.md`.
