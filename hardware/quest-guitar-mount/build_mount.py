"""Parametric, 3D-printable mount for a Meta Quest 3/3S Touch Plus controller
on the head end of a headless guitar (defaults: Jamstik Studio).

Two printed parts plus a small pressure pad:

* ``head_clamp``   -- an end cap that slides over the guitar's head end. It
  butts against the tip (so it lands in the same place every time) and is
  clamped between two top lips and a screw-driven pad from below. It never
  touches the top face between the lips, the nut or its adjustment screws.
* ``cradle``       -- bolts to the cap's end wall with 2x M4; a strapped
  half-pipe holds the controller's grip, angled toward the player's eyes so
  the controller's tracking LEDs face the headset. Reprint it to change the
  angle without reprinting the clamp.
* ``pressure_pad`` -- a disc the clamp screw pushes up against the back of
  the head. Line it (and the lips) with felt.

Run (Blender 4.2+; Manifold booleans need 4.5+, EXACT is the fallback)::

    blender --background --factory-startup --python build_mount.py -- \
        [--params my_measurements.json] [--out out] [--no-render]

Writes ``<out>/head_clamp.stl``, ``cradle.stl`` (already turned so its
mating face is down on the bed), ``pressure_pad.stl``, ``assembly.stl``
(everything in place, for checking), ``preview_*.png`` and
``mount_offset.json`` (where the neck sits relative to the controller's
grip pose, for the headset's first guess before its alignment check).

Units are millimetres and degrees. The CAD frame is the guitar's neck frame:
origin at the nut, centred across the fretboard, on the fretboard surface;
+X along the neck toward the body; +Z out of the fretboard face; low-E side
at -Y on a right-handed guitar.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

# Every size here is a measurement or a print choice; override any of them
# with --params. The guitar and controller values are first guesses until
# measured (see README.md); print a test fit before trusting them.
DEFAULTS: dict[str, float] = {
    # --- Guitar head end (measure with calipers) ---------------------------
    "head_width": 46.0,  # side to side, where the clamp sits
    "head_thickness": 22.0,  # top face to back, where the clamp sits
    "head_top_z": 0.0,  # head top face height vs the fretboard at the nut (+ = higher)
    "nut_to_tip": 40.0,  # nut (fretboard side) to the very end of the head
    "string_spread_at_clamp": 36.0,  # outer edge of low-E to outer edge of high-e, at the clamp
    "clamp_len": 20.0,  # how much of the head the clamp covers, from the tip
    "lip": 3.0,  # how far each top lip reaches over the head's top edge
    "pad": 1.5,  # felt/EVA lining on every inner face
    # --- Controller grip (Touch Plus; measure where the cradle holds it) -----
    "grip_width": 34.0,  # side to side
    "grip_depth": 40.0,  # palm side to trigger side
    "grip_len": 84.0,  # length of grip the cradle supports
    "grip_clear": 0.8,  # extra room around the grip
    # --- Layout -----------------------------------------------------------
    "arm_len": 26.0,  # end wall to the start of the cradle, along the neck
    "grip_rise": 30.0,  # controller raised from the neck line toward the low-E edge (up, when playing), like a raised headstock
    "grip_tilt": 30.0,  # ...and tipped back toward the player
    "face_roll": -45.0,  # cradle opening, rotated about the neck axis from +Z toward the low-E side; the default
    # opens away from the player, so the grip's palm side rests in the cradle facing the player
    "face_cant": 50.0,  # how far the controller's top control surface leans from its grip axis toward the palm side (estimate)
    # --- Where the player's eyes are, from the nut (only for the tracking check) ---
    "eye_toward_body": 350.0,
    "eye_up": 300.0,  # toward the low-E edge
    "eye_back": 250.0,  # behind the fretboard face, toward the player
    # --- Print / hardware -------------------------------------------------
    "wall": 4.0,
    "end_wall": 6.0,
    "base": 6.0,
    "lip_thk": 2.6,
    "pad_gap": 4.5,  # room under the head for the pressure pad + its felt
    "plate": 8.0,
    "saddle_wall": 3.4,
    "strap_w": 21.0,  # 20 mm hook-and-loop strap
    "strap_t": 3.6,
    "m4_clear": 4.5,
    "m4_nut_af": 7.0,
    "m4_nut_t": 3.2,
    "m4_head_d": 7.8,
    "m4_head_h": 4.4,
    "bolt_spacing": 24.0,
}

SEGMENTS = 96


# ---------------------------------------------------------------------------
# Mesh helpers: every object keeps an identity transform; geometry is baked.
# ---------------------------------------------------------------------------


def _link(name: str, bm: bmesh.types.BMesh) -> bpy.types.Object:
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def box(name: str, lo, hi, m: Matrix | None = None) -> bpy.types.Object:
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    size = [b - a for a, b in zip(lo, hi)]
    centre = [(a + b) / 2 for a, b in zip(lo, hi)]
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    bmesh.ops.translate(bm, vec=centre, verts=bm.verts)
    if m is not None:
        bmesh.ops.transform(bm, matrix=m, verts=bm.verts)
    return _link(name, bm)


def prism(name: str, rx: float, ry: float, depth: float, m: Matrix, segments: int = SEGMENTS):
    """Elliptic (or, with segments=6, hex) prism along local Z, centred, then placed by m."""
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=segments, radius1=1.0, radius2=1.0, depth=depth
    )
    bmesh.ops.scale(bm, vec=(rx, ry, 1.0), verts=bm.verts)
    bmesh.ops.transform(bm, matrix=m, verts=bm.verts)
    return _link(name, bm)


def boolean(target: bpy.types.Object, cutter: bpy.types.Object, op: str) -> None:
    """Apply a boolean (UNION / DIFFERENCE) and delete the cutter."""
    mod = target.modifiers.new("bool", "BOOLEAN")
    mod.operation = op
    mod.object = cutter
    try:
        mod.solver = "MANIFOLD"
    except TypeError:
        mod.solver = "EXACT"
    deps = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(target.evaluated_get(deps))
    target.modifiers.remove(mod)
    old = target.data
    target.data = mesh
    bpy.data.meshes.remove(old)
    bpy.data.objects.remove(cutter)


def union(target, *others) -> None:
    for o in others:
        boolean(target, o, "UNION")


def cut(target, *others) -> None:
    for o in others:
        boolean(target, o, "DIFFERENCE")


def at(pos: Vector, x: Vector, y: Vector, z: Vector) -> Matrix:
    """Placement matrix: local X/Y/Z axes -> x/y/z, origin -> pos."""
    m = Matrix.Identity(4)
    for i, axis in enumerate((x, y, z)):
        for r in range(3):
            m[r][i] = axis[r]
    m.translation = pos
    return m


def hex_nut_pocket(name: str, p: dict, pos: Vector, axis: Vector, depth: float):
    """Hex pocket for an M4 nut; axis points into the part."""
    across_corners = (p["m4_nut_af"] + 0.3) / math.cos(math.radians(30))
    z = axis.normalized()
    x = z.orthogonal().normalized()
    y = z.cross(x)
    return prism(name, across_corners / 2, across_corners / 2, depth * 2, at(pos, x, y, z), segments=6)


# ---------------------------------------------------------------------------
# Geometry.
# ---------------------------------------------------------------------------


def frame(p: dict) -> dict:
    """Derived positions shared by the parts and by mount_offset.json."""
    tip = -p["nut_to_tip"]
    half_in = p["head_width"] / 2 + p["pad"]
    top = p["head_top_z"]
    bottom_in = top - p["head_thickness"] - p["pad"] - p["pad_gap"]
    f = {
        "tip": tip,
        "half_in": half_in,
        "half_out": half_in + p["wall"],
        "top": top,
        "bottom_in": bottom_in,
        "bottom_out": bottom_in - p["base"],
        "lip_top": top + p["lip_thk"],
        "x_open": tip + p["clamp_len"],
        "x_wall_out": tip - p["end_wall"],
    }
    f["z_mid"] = (f["bottom_out"] + f["lip_top"]) / 2
    f["x_plate_out"] = f["x_wall_out"] - p["plate"]
    # Cradle orientation.
    rise = math.radians(p["grip_rise"])
    tilt = math.radians(p["grip_tilt"])
    roll = math.radians(p["face_roll"])
    # Away from the guitar, raised toward the low-E edge, tipped back toward the player.
    axis = Vector((-math.cos(rise) * math.cos(tilt), -math.sin(rise) * math.cos(tilt), -math.sin(tilt)))
    opening = Vector((0.0, -math.sin(roll), math.cos(roll)))  # about the neck axis, +Z toward low-E
    opening = (opening - axis * opening.dot(axis)).normalized()
    f["axis"], f["opening"] = axis, opening
    f["side"] = opening.cross(axis).normalized()
    f["rx"] = p["grip_width"] / 2 + p["grip_clear"]  # across the opening
    f["ry"] = p["grip_depth"] / 2 + p["grip_clear"]  # along the opening
    near_end = Vector((f["x_plate_out"] - p["arm_len"], 0.0, f["z_mid"]))
    half_len = p["grip_len"] / 2
    f["saddle_centre"] = near_end + axis * half_len + opening * (f["ry"] + p["saddle_wall"] - 2.0)
    # The arm meets the saddle's back near its guitar-side end; the straps sit
    # clear of it: one just past the arm, one near the far end.
    f["s_arm"] = -half_len + 9.0
    f["s_straps"] = (-half_len + 19.0 + p["strap_w"] / 2, half_len - p["strap_w"] / 2 - 1.0)
    # The controller's top control surface: up the grip, leaning toward the palm
    # side, which rests in the cradle (so, away from the opening). Its tracking
    # LEDs need to see the headset, so the build reports how far it points from
    # the player's eyes.
    cant = math.radians(p["face_cant"])
    f["face"] = (axis * math.cos(cant) - opening * math.sin(cant)).normalized()
    f["head_centre"] = f["saddle_centre"] + axis * (half_len + 20)
    eye = Vector((p["eye_toward_body"], -p["eye_up"], -p["eye_back"]))
    f["face_to_eyes_deg"] = math.degrees(f["face"].angle(eye - f["head_centre"]))
    return f


def build_head_clamp(p: dict, f: dict) -> bpy.types.Object:
    body = box("head_clamp", (f["x_wall_out"], -f["half_out"], f["bottom_out"]), (f["x_open"], f["half_out"], f["lip_top"]))
    cavity = box("cavity", (f["tip"], -f["half_in"], f["bottom_in"]), (f["x_open"] + 1, f["half_in"], f["top"]))
    opening = box("opening", (f["tip"], -(f["half_in"] - p["lip"]), f["top"] - 1), (f["x_open"] + 1, f["half_in"] - p["lip"], f["lip_top"] + 1))
    cut(body, cavity, opening)
    # Pressure screw: M4 up through the base, nut trapped from below.
    sx = (f["tip"] + f["x_open"]) / 2
    up = Vector((0, 0, 1))
    hole = prism("screw", p["m4_clear"] / 2, p["m4_clear"] / 2, p["base"] * 3, at(Vector((sx, 0, f["bottom_out"])), Vector((1, 0, 0)), Vector((0, 1, 0)), up))
    nut = hex_nut_pocket("nut", p, Vector((sx, 0, f["bottom_out"])), up, p["m4_nut_t"] + 0.3)
    cut(body, hole, nut)
    # Cradle bolts: blind holes into the end wall from outside, nuts trapped at the face.
    out = Vector((1, 0, 0))
    for i, y in enumerate((-p["bolt_spacing"] / 2, p["bolt_spacing"] / 2)):
        pos = Vector((f["x_wall_out"], y, f["z_mid"]))
        bolt = prism(f"bolt{i}", p["m4_clear"] / 2, p["m4_clear"] / 2, (p["end_wall"] - 0.8) * 2, at(pos, Vector((0, 1, 0)), Vector((0, 0, 1)), out))
        pocket = hex_nut_pocket(f"bnut{i}", p, pos, out, p["m4_nut_t"] + 0.3)
        cut(body, bolt, pocket)
    return body


def build_cradle(p: dict, f: dict) -> bpy.types.Object:
    plate = box("cradle", (f["x_plate_out"], -f["half_out"], f["bottom_out"]), (f["x_wall_out"], f["half_out"], f["lip_top"]))
    # Arm: plate centre to the back of the saddle, near its guitar-side end.
    back = f["saddle_centre"] + f["axis"] * f["s_arm"] - f["opening"] * (f["ry"] + p["saddle_wall"] - 1.0)
    start = Vector((f["x_plate_out"] + 1.0, 0.0, f["z_mid"]))
    run = back - start
    zx = run.normalized()
    ref = Vector((0, 0, 1)) if abs(zx.z) < 0.9 else Vector((0, 1, 0))
    yx = ref.cross(zx).normalized()
    xx = yx.cross(zx)
    arm = box("arm", (-9, -8, 0), (9, 8, run.length), at(start, xx, yx, zx))
    union(plate, arm)
    # Saddle: an elliptic half-pipe open toward the player.
    m = at(f["saddle_centre"], f["side"], f["opening"], f["axis"])
    outer = prism("outer", f["rx"] + p["saddle_wall"], f["ry"] + p["saddle_wall"], p["grip_len"], m)
    inner = prism("inner", f["rx"], f["ry"], p["grip_len"] + 2, m)
    half = box("half", (-200, 0, -200), (200, 200, 200), m)
    cut(outer, inner, half)
    union(plate, outer)
    # Strap slots through both saddle walls, below the rim so a bridge of wall
    # stays above each slot.
    for i, s in enumerate(f["s_straps"]):
        c = f["saddle_centre"] + f["axis"] * s - f["opening"] * (p["strap_t"] / 2 + 3.0)
        slot = box(
            f"slot{i}",
            (-(f["rx"] + p["saddle_wall"] + 5), -p["strap_t"] / 2, -p["strap_w"] / 2),
            (f["rx"] + p["saddle_wall"] + 5, p["strap_t"] / 2, p["strap_w"] / 2),
            at(c, f["side"], f["opening"], f["axis"]),
        )
        keep = box(f"keep{i}", (-(f["rx"] - 0.5), -50, -50), (f["rx"] - 0.5, 50, 50), at(c, f["side"], f["opening"], f["axis"]))
        cut(slot, keep)  # only through the walls, not the floor
        cut(plate, slot)
    # Bolt holes with counterbores on the outer face.
    for i, y in enumerate((-p["bolt_spacing"] / 2, p["bolt_spacing"] / 2)):
        pos = Vector((f["x_plate_out"], y, f["z_mid"]))
        ax = at(pos, Vector((0, 1, 0)), Vector((0, 0, 1)), Vector((1, 0, 0)))
        hole = prism(f"h{i}", p["m4_clear"] / 2, p["m4_clear"] / 2, p["plate"] * 3, ax)
        bore = prism(f"cb{i}", p["m4_head_d"] / 2 + 0.3, p["m4_head_d"] / 2 + 0.3, p["m4_head_h"] * 2, ax)
        cut(plate, hole, bore)
    # Keep the grip's pocket clean of anything the arm union pushed into it.
    cut(plate, prism("pocket", f["rx"], f["ry"], p["grip_len"] + 2, m))
    return plate


def build_pad(p: dict, f: dict) -> bpy.types.Object:
    m = at(Vector((0, 0, 1.6)), Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
    pad = prism("pressure_pad", 8.0, 8.0, 3.2, m)
    seat = prism("seat", p["m4_clear"] / 2, p["m4_clear"] / 2, 3.0, at(Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))))
    cut(pad, seat)
    return pad


# ---------------------------------------------------------------------------
# Checks, offset, export, preview.
# ---------------------------------------------------------------------------


def check(p: dict, parts: list[bpy.types.Object], f: dict | None = None) -> list[str]:
    notes = []
    if f is not None:
        print(f"[mount] top control surface points {f['face_to_eyes_deg']:.0f} deg from the player's eyes")
        if f["face_to_eyes_deg"] > 70:
            notes.append(f"the controller's top surface points {f['face_to_eyes_deg']:.0f} deg away from the eyes: tracking may drop")
    clearance = (p["head_width"] - p["string_spread_at_clamp"]) / 2 - p["lip"]
    if clearance < 1.0:
        notes.append(f"lips reach within {clearance:.1f} mm of the outer strings: lower 'lip' or measure again")
    for ob in parts:
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bad = sum(1 for e in bm.edges if not e.is_manifold)
        volume = bm.calc_volume()
        pieces = _islands(bm)
        bm.free()
        if bad:
            notes.append(f"{ob.name}: {bad} non-manifold edges")
        if pieces != 1:
            notes.append(f"{ob.name}: {pieces} separate pieces (something no longer touches)")
        print(
            f"[mount] {ob.name}: {volume / 1000:.1f} cm3, {len(ob.data.vertices)} verts, "
            f"non-manifold edges: {bad}, pieces: {pieces}"
        )
    return notes


def _islands(bm: bmesh.types.BMesh) -> int:
    parent = list(range(len(bm.verts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for e in bm.edges:
        a, b = find(e.verts[0].index), find(e.verts[1].index)
        if a != b:
            parent[a] = b
    return len({find(i) for i in range(len(bm.verts))})


def _to_unity(v: Vector) -> Vector:
    """CAD (right-handed, Z out of the fretboard) -> Unity neck frame (X, Y, Z) = (x, z, y)."""
    return Vector((v.x, v.z, v.y))


def mount_offset(p: dict, f: dict) -> dict:
    """The neck frame in the controller's grip-pose frame (Unity axes, metres).

    Assumes the grip pose origin is the palm side of the grip at mid-grip, its
    forward (+Z) runs up the grip toward the controller's head, and its +Y
    points out of the palm side through the trigger side (out of the cradle
    opening). Approximate by construction: the headset's alignment check is
    what makes it exact; the mount only has to land in the same place.
    """
    origin = f["saddle_centre"] - f["opening"] * f["ry"]
    gz, gy = _to_unity(f["axis"]), _to_unity(f["opening"])
    gx = gy.cross(gz)  # Unity is left-handed: right = Cross(up, forward) numerically
    rot = Matrix((gx, gy, gz)).transposed()  # columns = grip axes in neck coordinates
    local = rot.transposed() @ (_to_unity(Vector((0, 0, 0)) - origin))
    q = rot.transposed().to_quaternion()
    return {
        "controller": "meta-quest-touch-plus",
        "pose": "openxr-grip",
        "neck_origin_in_grip_m": [round(c / 1000, 4) for c in local],
        "neck_rotation_in_grip_quat": [round(q.x, 5), round(q.y, 5), round(q.z, 5), round(q.w, 5)],
        "note": "First guess from the CAD (approximate grip-pose model); the headset's alignment check replaces it.",
        "face_to_eyes_deg": round(f["face_to_eyes_deg"], 1),
        "params": {k: p[k] for k in ("nut_to_tip", "head_top_z", "arm_len", "grip_rise", "grip_tilt", "face_roll", "face_cant", "grip_len")},
    }


def export_stl(ob: bpy.types.Object, path: Path, m: Matrix | None = None) -> None:
    for o in bpy.context.scene.objects:
        o.select_set(False)
    if m is not None:
        ob.data.transform(m)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.wm.stl_export(filepath=str(path), export_selected_objects=True, apply_modifiers=True, global_scale=1.0)
    if m is not None:
        ob.data.transform(m.inverted())
    ob.select_set(False)


def ghosts(p: dict, f: dict) -> list[bpy.types.Object]:
    """Stand-ins for the guitar neck and the controller, for the previews only."""
    neck = box("ghost_neck", (f["tip"], -p["head_width"] / 2, p["head_top_z"] - p["head_thickness"]), (260, p["head_width"] / 2, p["head_top_z"]))
    m = at(f["saddle_centre"], f["side"], f["opening"], f["axis"])
    grip = prism("ghost_grip", p["grip_width"] / 2, p["grip_depth"] / 2, p["grip_len"] + 10, m)
    head = prism("ghost_head", 32, 30, 34, at(f["head_centre"], f["side"], f["opening"], f["axis"]))
    fz = f["face"]
    fx = fz.orthogonal().normalized()
    face = prism("ghost_face", 24, 24, 3, at(f["head_centre"] + fz * 18, fx, fz.cross(fx), fz))
    strings = []
    for i in range(6):
        y = -p["string_spread_at_clamp"] / 2 + i * p["string_spread_at_clamp"] / 5
        strings.append(box(f"ghost_string{i}", (f["tip"] + 6, y - 0.4, p["head_top_z"] + 0.5), (260, y + 0.4, p["head_top_z"] + 1.3)))
    return [neck, grip, head, face, *strings]


def render(objects: dict[str, tuple[bpy.types.Object, tuple]], out: Path, f: dict, p: dict) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "OBJECT"
    scene.display.shading.show_cavity = True
    scene.display.shading.show_shadows = True
    scene.render.resolution_x, scene.render.resolution_y = 960, 680
    scene.render.film_transparent = False
    world = bpy.data.worlds.new("w")
    scene.world = world
    world.color = (0.035, 0.04, 0.055)
    scene.display.shading.background_type = "WORLD"
    for ob, colour in objects.values():
        ob.color = colour
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 50
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    target = Vector((f["tip"] - 40, 0, -10))

    def look(name: str, position: Vector, aim: Vector, up: Vector) -> None:
        cam.location = position
        fwd = (aim - position).normalized()
        right = fwd.cross(up).normalized()
        cam.rotation_euler = Matrix((right, right.cross(fwd), -fwd)).transposed().to_euler()
        scene.render.filepath = str(out / f"preview_{name}.png")
        bpy.ops.render.render(write_still=True)

    up_when_playing = Vector((0, -1, 0))  # the low-E edge points at the ceiling
    # What the headset sees: from the eye position the tracking check uses, so the
    # controller's top surface (cyan) should face the camera.
    look("eyes", Vector((p["eye_toward_body"], -p["eye_up"], -p["eye_back"])), f["head_centre"], up_when_playing)
    # From the audience, as a webcam on the desk sees it: fretboard face on, head end to the right.
    look("front", target + Vector((60, 0, 520)), target, up_when_playing)
    look("below", target + Vector((-60, 160, -260)), target, Vector((0, 0, 1)))
    look("side", target + Vector((-90, -380, -20)), target, Vector((0, 0, 1)))


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="build_mount")
    ap.add_argument("--params", type=Path, default=None, help="JSON of DEFAULTS keys to override")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "out")
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args(argv)

    p = dict(DEFAULTS)
    if args.params:
        overrides = json.loads(args.params.read_text(encoding="utf-8"))
        unknown = sorted(set(overrides) - set(DEFAULTS))
        if unknown:
            raise SystemExit(f"unknown parameter(s): {', '.join(unknown)}")
        p.update({k: float(v) for k, v in overrides.items()})
    args.out.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    f = frame(p)
    clamp = build_head_clamp(p, f)
    cradle = build_cradle(p, f)
    pad = build_pad(p, f)
    notes = check(p, [clamp, cradle, pad], f)

    export_stl(clamp, args.out / "head_clamp.stl")
    # Print the cradle on its mating face: +X (toward the clamp) -> down.
    export_stl(cradle, args.out / "cradle.stl", Matrix.Rotation(math.radians(90), 4, "Y"))
    export_stl(pad, args.out / "pressure_pad.stl")
    pad_place = Matrix.Translation(((f["tip"] + f["x_open"]) / 2, 0, f["bottom_in"] + 0.2))
    pad.data.transform(pad_place)
    for o in bpy.context.scene.objects:
        o.select_set(o in (clamp, cradle, pad))
    bpy.ops.wm.stl_export(filepath=str(args.out / "assembly.stl"), export_selected_objects=True, global_scale=1.0)

    offset = mount_offset(p, f)
    (args.out / "mount_offset.json").write_text(json.dumps(offset, indent=2) + "\n", encoding="utf-8")
    print("[mount] mount_offset:", json.dumps(offset))

    if not args.no_render:
        g = ghosts(p, f)
        colours = {
            "clamp": (clamp, (0.13, 0.83, 0.93, 1)),
            "cradle": (cradle, (0.65, 0.55, 0.98, 1)),
            "pad": (pad, (1.0, 0.71, 0.28, 1)),
            "neck": (g[0], (0.36, 0.25, 0.18, 1)),
            "grip": (g[1], (0.85, 0.86, 0.88, 1)),
            "head": (g[2], (0.85, 0.86, 0.88, 1)),
            "face": (g[3], (0.13, 0.83, 0.93, 1)),
        }
        for i, s in enumerate(g[4:]):
            colours[f"string{i}"] = (s, (0.9, 0.9, 0.85, 1))
        render(colours, args.out, f, p)

    for n in notes:
        print(f"[mount] WARNING: {n}")
    print(f"[mount] wrote {args.out}")


if __name__ == "__main__":
    main()
