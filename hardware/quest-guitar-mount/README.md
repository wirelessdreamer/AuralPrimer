# Quest controller mount for a headless guitar

A 3D-printable mount that puts a **Meta Quest 3/3S Touch Plus** controller on
the head end of a headless guitar, so the headset always knows where the neck
is and the mixed-reality neck highway can sit on the real frets. The defaults
target a **Jamstik Studio** (headless, 24 frets, 25.5" scale, 40 mm at the nut).

![The mount on the head end, seen from the front](preview.png)

![What the headset sees: the controller's top surface (cyan) angled toward the player](preview-eyes.png)

Three printed pieces:

| Part | What it does |
|---|---|
| `head_clamp` | An end cap that slides over the head end until it touches the tip, so it lands in the same place every time. Two lips rest on the outer edges of the head's top face and a screw pushes a pad up from below. It never touches the nut, its height screws, or the top face between the lips. |
| `cradle` | Bolts to the cap with two M4 screws. A half-pipe holds the controller's grip with two straps, with the controller's head pointing away from the guitar like a headstock and its face turned toward you. Reprint it to change the angle without reprinting the clamp. |
| `pressure_pad` | The disc the clamp screw pushes against the back of the head. |

## 1. Measure first

Every size is a parameter in `build_mount.py`, and **the defaults are guesses.**
Neither the Jamstik's head end nor the Touch Plus grip has published
dimensions. Measure with calipers, write your numbers into a copy of
`my_measurements.example.json`, and print the clamp alone as a test fit
before printing the rest.

**Guitar, at the head end:**

| Parameter | What to measure |
|---|---|
| `head_width` | Side to side across the head, where the clamp will sit (the last 20 mm before the tip). |
| `head_thickness` | Top face to back, same place. |
| `head_top_z` | How far the head's top face sits above (+) or below (−) the fretboard surface at the nut. |
| `nut_to_tip` | From the fretboard edge of the nut to the very end of the head. |
| `string_spread_at_clamp` | From the outer edge of the low-E string to the outer edge of the high-e, where the clamp sits. |
| `clamp_len` | How much of the head the clamp can cover from the tip without reaching the nut's height screws. |
| `lip` | How far each lip reaches over the top edge. It must stay clear of the outer strings and of any string-anchor hardware. The build warns when the lips come within 1 mm of the strings. |

**Controller, where the cradle holds the grip** (straps removed):

| Parameter | What to measure |
|---|---|
| `grip_width` | Grip side to side. |
| `grip_depth` | Grip from the palm side to the trigger side. |
| `grip_len` | The length of grip below the controller's head that the cradle can hold. |

## 2. Tape-test the angle

The default angle follows how the controller was held for the first test: past
the head end, rising up from the line of the neck like a raised headstock, with
its top control surface angled partly toward the player so the headset keeps
seeing its tracking LEDs.

| Parameter | Default | What it does |
|---|---|---|
| `grip_rise` | 30° | Lifts the controller from the neck line toward the low-E edge (up, when playing). This is the angle a front-on photo shows. |
| `grip_tilt` | 30° | Tips it back toward the player. Raise it if tracking drops. |
| `face_roll` | −45° | Turns the cradle about the neck. At the default, the grip's palm side rests in the cradle facing the player, so the top surface leans toward them. |
| `face_cant` | 50° | How far a Touch Plus top surface leans from its grip. This is an estimate, used only for the tracking check. |

Every build prints how far the top surface points from the player's eyes, and
warns past 70°; the defaults give about 57°. The eye position is in
`eye_toward_body`, `eye_up` and `eye_back` (mm from the nut). `preview_eyes.png`
shows what the headset sees.

Before printing the cradle, tape the controller to the head end at that angle
and play for a minute with the headset on. When tracking stays steady, set the
angles to match.

## 3. Build the STLs

Needs Blender 4.2 or newer (4.5+ uses the faster Manifold booleans):

```bash
blender --background --factory-startup --python build_mount.py -- --params my_measurements.json
```

This writes `out/`:

- `head_clamp.stl`, `cradle.stl` and `pressure_pad.stl`, each already turned for printing;
- `assembly.stl`, everything in place for a visual check;
- three preview renders;
- `mount_offset.json`.

The build also reports each part's volume, and it warns if a part is not
watertight or has come apart into separate pieces. `out/` is git-ignored.

## 4. Print

- **PETG.** PLA creeps under steady clamp pressure and softens in a warm car.
- 0.2 mm layers, 4 walls, 40% gyroid infill.
- **No supports.**
  - Print the clamp open side up.
  - Print the cradle on the face that meets the clamp; it stands upright.
  - Print the pad flat.

## 5. Hardware

| Qty | Part |
|---|---|
| 2 | M4×8 socket-head screws + 2 M4 nuts (cradle to clamp) |
| 1 | M4×12 screw (a thumb screw is nicest) + 1 M4 nut (clamp pressure) |
| 2 | 20 mm hook-and-loop strap, about 35 cm each |
| — | 1.5 mm self-adhesive felt or EVA foam |

## 6. Assemble

1. Press the nuts into their hex pockets: one from below the clamp, two in the clamp's end face.
2. Line the clamp's inner side walls, end wall and the undersides of both lips with felt, and put felt on the pad.
3. Bolt the cradle to the clamp.
4. Slide the clamp onto the head end until it touches the tip, set the pad under the head, and turn the screw finger-tight. It holds the head between the lips and the pad, so it only needs to be snug.
5. Seat the controller's grip in the cradle with its head pointing away from the guitar, the palm side of the grip against the cradle (it faces you), and the top control surface leaning toward you. Run the straps through both slots and over the grip; they carry the controller, since the cradle opens away from you.
6. In the headset, run the guitar setup's alignment check once. Remount the same way every time (cap against the tip, controller seated to the end of the cradle) and the saved alignment stays valid.

## `mount_offset.json`

Where the neck sits relative to the controller's tracked grip pose, in the
headset's axes (metres + quaternion). The MR client uses it as its first
guess before the alignment check.

It is approximate by construction. It assumes:
- the grip pose's origin is the palm side of the grip, at mid-grip;
- the grip pose's forward runs up the grip toward the controller's head.

The check replaces it with the real numbers. The mount's job is to land in
the same place every time, which the end cap and the cradle's end stop make
possible.

## Not yet verified

- **Fit:** nothing has been printed or tried on a Jamstik yet, and the defaults are unmeasured guesses.
- **Tracking:** a controller that isn't held may lose tracking when its face turns away. Horizon OS may also switch to hand tracking when it sees the controller as set down.

The MR client's guitar setup is where both get handled. See the guitar section of
`docs/unity-mr-client-plan-2026-08-18.md`.
