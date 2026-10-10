"""Parametric, 3D-printable mount for a Meta Quest 3/3S Touch Plus controller
on the head end of a headless guitar (defaults: Jamstik Studio).

The printed part is the ``cradle``:

* a plate that bolts (2x M4) to the guitar's end face, centred on the D-shaped
  pocket under the string holes and kept below them;
* a straight arm out from the plate, along the neck line;
* a seat form-fitted to the controller's real grip: a shell minus the convex
  hull of the grip, grown by a clearance. The grip's end bottoms out in it, so
  the controller lands in the same place every time, and a hook-and-loop strap
  in two rim slots holds it down. The seat stops short of the grip button and
  keeps clear of the trigger and thumbstick; the strap crosses the grip below
  the grip button.

The controller's shape comes from Meta's own Touch Plus model, read at build
time from the Meta XR SDK (``controller_mesh``) -- never copied. Meta's model
is SDK content: never commit it, or any file holding its geometry. Everything
written to ``out/`` (git-ignored) may hold it.

The guitar-side part the plate bolts to is being redesigned as a plug for that
D pocket (it replaces the guitar's black end cover); until it exists the plate
is aimed at the D face (``iface_*`` in frame()). ``build_head_clamp`` and
``build_pad`` are the earlier end-cap clamp, which no longer mates and is not
built.

Run (Blender 4.2+; Manifold booleans need 4.5+, EXACT is the fallback)::

    blender --background --factory-startup --python build_mount.py -- \
        [--params my_measurements.json] [--out out] [--no-render]

Writes ``<out>/cradle.stl`` (already turned so its mating face is down on the
bed), ``assembly.stl`` (the cradle in place, in the CAD frame), ``mount.blend``
(the same, with the guitar and Meta's controller in place, shown in
millimetres), ``preview_*.png`` and ``mount_offset.json`` (where the neck sits
relative to the controller model's origin, for the headset's first guess
before its alignment check).

Units are millimetres and degrees. The CAD frame is the guitar's neck frame:
origin at the nut, centred across the fretboard, on the fretboard surface;
+X along the neck toward the body; +Z out of the fretboard face; low-E side
at -Y on a right-handed guitar.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

# Every size here is a measurement or a print choice; override any of them
# with --params. The guitar values are first guesses until measured (see
# README.md); print a test fit before trusting them. The controller's shape is
# not a parameter: it is read from Meta's model.
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
    # --- The end face the cradle bolts to (the D pocket's cover) ------------
    "d_top_z": -6.0,  # the D's flat top edge, just under the row of string holes
    "d_width": 37.0,  # the D across its flat edge
    "d_depth": 12.38,  # the D from its flat edge to the bottom of its curve
    "string_clear": 100.0,  # how far out from the end face nothing of the cradle may sit in front of the string holes
    # --- Controller seat (the grip itself comes from Meta's model) ----------
    "cradle_len": 48.0,  # how far up the grip, from its end, the seat reaches
    "cradle_clear": 0.6,  # room between the grip and the seat
    "cradle_rim": 2.0,  # rims stop this far short of the grip's widest line
    "button_clear": 2.5,  # extra room kept around the trigger, grip button and thumbstick
    "strap_at": 34.5,  # strap centre, from the grip's end
    # --- Layout -----------------------------------------------------------
    "arm_len": 28.0,  # plate face to the seat, along the arm
    "arm_back": 35.0,  # the arm angles from the neck line toward the back of the neck, away from the string holes
    "grip_rise": 40.0,  # controller raised from the neck line toward the low-E edge (up, when playing), like a raised headstock
    "grip_tilt": 45.0,  # ...and tipped back toward the player
    "face_roll": 60.0,  # turns the controller about its grip: 0 leans its top surface toward the player,
    # positive toward the low-E edge (up, when playing)
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
    "saddle_wall": 3.4,  # the seat's wall
    "strap_w": 21.0,  # 20 mm hook-and-loop strap
    "strap_t": 3.6,  # how deep the strap slots notch the rims
    "m4_clear": 4.5,
    "m4_nut_af": 7.0,
    "m4_nut_t": 3.2,
    "m4_head_d": 7.8,
    "m4_head_h": 4.4,
    "bolt_spacing": 24.0,
}

# Meta's Touch Plus model, read in place (never copied). {Hand} becomes Left or Right.
TEXT_DEFAULTS: dict[str, str] = {
    "controller_mesh": "D:/AuralPrimer/UnityClient/Aural Primer/Library/PackageCache/"
    "com.meta.xr.sdk.core@4ea2676097b9/Meshes/MetaQuestTouchPlus/MetaQuestTouchPlus_{Hand}.fbx",
    "controller_hand": "left",
}

KEY_CLEAR = 25.0  # straight room a hex key needs out from each cradle bolt head

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


def _fbx_numbers(text: str, start: int, key: str) -> list[float]:
    """The numbers of the first ``key: *N { a: ... }`` array after ``start``."""
    i = text.index(f"{key}: *", start)
    a = text.index("a:", i)
    return [float(x) for x in text[a + 2 : text.index("}", a)].split(",")]


def read_touch_plus(p: dict) -> dict:
    """Meta's Touch Plus model, rest pose, in millimetres in the model's own frame.

    The Meta XR SDK ships it as an ASCII FBX 7.5, which Blender's importer
    refuses, so this reads the two arrays it needs: ``Vertices`` (x, y, z
    triples) and ``PolygonVertexIndex`` (a negative index ends a polygon and
    stands for -index - 1) of ``oculus_controller_<l|r>_MeshX``. The skin
    clusters say which vertices move with the trigger, the grip button and the
    thumbstick.

    Model frame (the file's own axes, kept as they are; frame() turns them into
    the CAD frame): right-handed, +Y up out of the controller's top, +Z forward
    (where it points), +X to the left of a controller seen from behind. The
    file is in metres (UnitScaleFactor 100 = 100 cm per unit) and its RootNode
    scales the vertices by 0.01, so a vertex number is centimetres. Unity's
    importer shows the same model with X negated (Unity is left-handed): the
    model as Unity draws it is (-x, y, z).
    """
    hand = p["controller_hand"]
    path = Path(p["controller_mesh"].format(Hand=hand.capitalize()))
    if not path.is_file():
        raise SystemExit(
            f"controller_mesh: no file at {path}. Point the 'controller_mesh' parameter at Meta's "
            "MetaQuestTouchPlus_Left.fbx / _Right.fbx (Meta XR SDK: com.meta.xr.sdk.core/Meshes/"
            "MetaQuestTouchPlus/, e.g. in a Unity project's Library/PackageCache). It is read in place, never copied."
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("; FBX"):
        raise SystemExit(f"controller_mesh: {path} is not an ASCII FBX")
    axes = {k: int(re.search(rf'"{k}", "int", "Integer", "",(-?\d+)', text).group(1)) for k in ("UpAxis", "FrontAxis", "CoordAxis")}
    signs = [int(re.search(rf'"{k}Sign", "int", "Integer", "",(-?\d+)', text).group(1)) for k in ("UpAxis", "FrontAxis", "CoordAxis")]
    if (axes["UpAxis"], axes["FrontAxis"], axes["CoordAxis"]) != (1, 2, 0) or signs != [1, 1, 1]:
        raise SystemExit(f"controller_mesh: {path} is not Y-up / Z-front / X-coord; read_touch_plus assumes it is")
    geometry = re.search(rf';Geometry::, Model::oculus_controller_{hand[0]}_MeshX\s+C: "OO",(\d+),', text)
    if geometry is None:
        raise SystemExit(f"controller_mesh: no oculus_controller_{hand[0]}_MeshX in {path} (wrong hand?)")
    start = text.index(f"\tGeometry: {geometry.group(1)},")
    unit_cm = float(re.search(r'"UnitScaleFactor", "double", "Number", "",([-\d.eE+]+)', text).group(1))
    root = re.search(r'"Model::RootNode", "Null" \{[^}]*?"Lcl Scaling", "Lcl Scaling", "", "A",([-\d.eE+]+)', text)
    mm = 10.0 * unit_cm * (float(root.group(1)) if root else 1.0)
    xyz = _fbx_numbers(text, start, "Vertices")
    verts = [Vector((xyz[i], xyz[i + 1], xyz[i + 2])) * mm for i in range(0, len(xyz), 3)]
    faces, poly = [], []
    for k in _fbx_numbers(text, start, "PolygonVertexIndex"):
        k = int(k)
        poly.append(k if k >= 0 else -k - 1)
        if k < 0:
            faces.append(poly)
            poly = []
    parts = {}
    for bone in ("trigger_front", "trigger_grip", "thumbstick"):
        cluster = re.search(rf';Model::{hand}_b_{bone}, SubDeformer::\s+C: "OO",\d+,(\d+)', text)
        at_cluster = text.index(f"\tDeformer: {cluster.group(1)},")
        parts[bone] = sorted({int(i) for i in _fbx_numbers(text, at_cluster, "Indexes")})
    return {"verts": verts, "faces": faces, "parts": parts, "path": path, "hand": hand}


def controller_frame(ctrl: dict) -> dict:
    """The controller's own frame, found from its mesh (model frame, mm).

    * ``a``: the grip's long axis, toward the head: a line through the centres
      of slices across the lowest 12-62 mm of the grip, refined until it settles.
    * ``top``: the top control surface's normal (the face with the thumbstick
      and buttons): the area-weighted normal of the body's faces near the
      thumbstick, narrowed to the flat face plate. It matches the rig's
      thumbstick and button axes to about a degree.
    * ``b``: which way that surface leans off the grip axis: the back of the
      grip (the palm-heel side, opposite the trigger). ``-b`` is the trigger side.
    * ``c = a x b``: across the grip. The grip button is on +c for a left
      controller and -c for a right one (the models are mirror images).
    * ``end``: where the grip axis leaves the grip's end.
    """
    V = np.array([tuple(v) for v in ctrl["verts"]])
    moving = set().union(*ctrl["parts"].values())
    body = np.array([i for i in range(len(V)) if i not in moving])
    B = V[body]
    centre = B.mean(0)
    a = np.linalg.svd(B - centre)[2][0]
    if (V[ctrl["parts"]["thumbstick"]].mean(0) - centre) @ a < 0:
        a = -a
    for _ in range(10):
        ref = np.array([1.0, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1.0, 0])
        e1 = np.cross(a, ref)
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(a, e1)
        s = B @ a
        centres = []
        for s0 in np.arange(s.min() + 12, s.min() + 62, 4.0):
            P = B[np.abs(s - s0) < 2]
            u, w = P @ e1, P @ e2
            centres.append(a * s0 + e1 * (u.max() + u.min()) / 2 + e2 * (w.max() + w.min()) / 2)
        centres = np.array(centres)
        mid = centres.mean(0)
        line = np.linalg.svd(centres - mid)[2][0]
        a = line if line @ a > 0 else -line
    thumb = V[ctrl["parts"]["thumbstick"]].mean(0)
    near = []
    bodyset = set(body.tolist())
    for f in ctrl["faces"]:
        P = V[f]
        if all(i in bodyset for i in f) and np.linalg.norm(P.mean(0) - thumb) < 30:
            near.append(sum(np.cross(P[i], P[(i + 1) % len(P)]) for i in range(len(P))) / 2)
    normals = np.array(near)
    areas = np.linalg.norm(normals, axis=1)
    keep = areas > 1e-9
    normals, areas = normals[keep], areas[keep]
    top = normals[normals @ a > 0.3 * areas].sum(0)
    for cone in (25, 15, 10, 10):
        top /= np.linalg.norm(top)
        top = normals[normals @ top > math.cos(math.radians(cone)) * areas].sum(0)
    top /= np.linalg.norm(top)
    b = top - a * (top @ a)
    b /= np.linalg.norm(b)
    c = np.cross(a, b)
    end = mid + a * ((B - mid) @ a).min()
    parts = {k: V[v] for k, v in ctrl["parts"].items()}
    if (parts["trigger_front"].mean(0) - end) @ b > 0:
        raise SystemExit("controller_frame: the trigger came out on the palm side; the mesh is not what this expects")
    span = {k: (float(((P - end) @ a).min()), float(((P - end) @ a).max())) for k, P in parts.items()}
    vec = lambda x: Vector(tuple(float(c) for c in x))  # noqa: E731
    return {"a": vec(a), "b": vec(b), "c": vec(c), "top": vec(top), "end": vec(end),
            "span": span, "length": float(((V - end) @ a).max()), "cant": math.degrees(math.acos(float(top @ a)))}


def frame(p: dict, cf: dict) -> dict:
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
    # The face the cradle bolts to, and its axes: re-aim the cradle here. Now the
    # middle of the D pocket in the end face, below the string holes, where the
    # plug that will fill the pocket takes the two M4 screws.
    f["iface_pos"] = Vector((tip, 0.0, p["d_top_z"] - p["d_depth"] / 2))
    f["iface_out"] = Vector((-1.0, 0.0, 0.0))  # away from the guitar
    f["iface_up"] = Vector((0.0, 0.0, 1.0))  # toward the string holes
    f["iface_across"] = f["iface_up"].cross(f["iface_out"])  # (across, up, out) is right-handed
    f["plate_w"] = p["d_width"] - 1.0
    f["plate_h"] = p["d_depth"] - 1.0  # keeps 0.5 mm below the string holes
    # The arm leaves the plate between the bolt heads and angles back, away from the string holes.
    back = math.radians(p["arm_back"])
    f["arm_dir"] = (f["iface_out"] * math.cos(back) - f["iface_up"] * math.sin(back)).normalized()
    # The controller: its grip axis away from the guitar, raised toward the
    # low-E edge, tipped back toward the player; its top surface's lean turned
    # about that axis by face_roll.
    rise, tilt, roll = (math.radians(p[k]) for k in ("grip_rise", "grip_tilt", "face_roll"))
    A = Vector((-math.cos(rise) * math.cos(tilt), -math.sin(rise) * math.cos(tilt), -math.sin(tilt)))
    player = Vector((0.0, 0.0, -1.0))
    lean0 = (player - A * player.dot(A)).normalized()
    turn = A.cross(lean0)
    if turn.y > 0:
        turn = -turn  # positive roll turns the lean toward the low-E edge (-Y)
    L = lean0 * math.cos(roll) + turn * math.sin(roll)
    # R turns the model frame into the CAD frame: grip axis a -> A, lean b -> L.
    R = Matrix((A, L, A.cross(L))).transposed() @ Matrix((cf["a"], cf["b"], cf["c"]))
    f["R"], f["A"], f["L"] = R, A, L
    # The seat holds the grip from below when playing, so the controller's weight
    # sits it in the seat (and its end in the cup); it lifts out upward.
    down = Vector((0.0, 1.0, 0.0))
    f["floor"] = (down - A * down.dot(A)).normalized()
    f["across"] = A.cross(-f["floor"])  # rim to rim
    return f


def place(p: dict, f: dict, cf: dict, ctrl: dict) -> list[Vector]:
    """Put the controller where the arm meets the seat; returns its vertices in CAD (mm).

    The arm runs from the plate's middle (arm_len, along arm_dir) into the cup
    under the grip's end, off-centre toward the floor so all of it lands on the
    seat's side of the rims.
    """
    R, A, floor = f["R"], f["A"], f["floor"]
    rel = [R @ (v - cf["end"]) for v in ctrl["verts"]]
    anchor = f["iface_pos"] + f["iface_out"] * p["plate"] + f["arm_dir"] * p["arm_len"]
    arm_w = p["bolt_spacing"] - (p["m4_head_d"] + 0.6) - 1.6
    arm_up = f["arm_dir"].cross(f["iface_across"])
    off = max(abs((f["iface_across"] * (arm_w / 2) * i + arm_up * (f["plate_h"] / 2) * j).dot(floor)) for i in (-1, 1) for j in (-1, 1))
    f["arm_w"], f["arm_up"], f["anchor"] = arm_w, arm_up, anchor
    f["E"] = anchor + A * (p["cradle_clear"] + p["saddle_wall"]) - floor * (off + p["cradle_rim"] + 1.0)  # the grip's end, in CAD
    f["t"] = f["E"] - R @ cf["end"]
    cad = [f["E"] + r for r in rel]
    thumb = sum((cad[i] for i in ctrl["parts"]["thumbstick"]), Vector()) / len(ctrl["parts"]["thumbstick"])
    f["face"] = (R @ cf["top"]).normalized()
    f["thumb"] = thumb
    eye = Vector((p["eye_toward_body"], -p["eye_up"], -p["eye_back"]))
    f["face_to_eyes_deg"] = math.degrees(f["face"].angle(eye - thumb))
    return cad


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


def _hull_points(points: list[Vector]) -> list[Vector]:
    """The corners of the convex hull of points."""
    bm = bmesh.new()
    vs = [bm.verts.new(q) for q in points]
    res = bmesh.ops.convex_hull(bm, input=vs)
    keep = {g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)}
    out = [v.co.copy() for v in keep]
    bm.free()
    return out


def _ball() -> list[Vector]:
    """Directions of a 162-point geodesic ball, scaled so its faces touch the unit sphere."""
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=3, radius=1.0)
    inner = min(f.calc_center_median().dot(f.normal) for f in bm.faces)
    out = [v.co / inner for v in bm.verts]
    bm.free()
    return out


def hull(name: str, points: list[Vector], grow: float = 0.0, sweep: Vector | None = None) -> bpy.types.Object:
    """Convex hull of points, grown by ``grow`` all round, optionally swept along ``sweep``."""
    pts = _hull_points(points)
    if grow > 0:
        pts = _hull_points([q + d * grow for q in pts for d in _ball()])
    if sweep is not None:
        pts = pts + [q + sweep for q in pts]
    bm = bmesh.new()
    vs = [bm.verts.new(q) for q in pts]
    res = bmesh.ops.convex_hull(bm, input=vs)
    loose = {g for g in res["geom_interior"] + res["geom_unused"] if isinstance(g, bmesh.types.BMVert)}
    bmesh.ops.delete(bm, geom=list(loose), context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _link(name, bm)


def grip_section(cad: list[Vector], faces: list[list[int]], E: Vector, A: Vector, top: float) -> list[Vector]:
    """The controller's surface from its grip end up to ``top`` mm along the grip axis,
    with the cut at ``top`` closed by where the mesh's edges cross it."""
    s = [(q - E).dot(A) for q in cad]
    pts = [q for q, sq in zip(cad, s) if sq <= top]
    for f in faces:
        for i, j in zip(f, f[1:] + f[:1]):
            if (s[i] - top) * (s[j] - top) < 0:
                pts.append(cad[i].lerp(cad[j], (top - s[i]) / (s[j] - s[i])))
    return pts


def build_cradle(p: dict, f: dict, ctrl: dict, cad: list[Vector]) -> bpy.types.Object:
    A, E = f["A"], f["E"]
    u, v = -f["floor"], f["across"]  # u: out of the seat's opening; v: rim to rim
    seat_m = at(E, u, v, A)  # local x = u, y = v, z = up the grip from its end
    clear, wall = p["cradle_clear"], p["saddle_wall"]
    section = grip_section(cad, ctrl["faces"], E, A, p["cradle_len"])
    # Seat: the grip's hull grown by clearance + wall, cut off at cradle_len and
    # below the rims, minus the grip's hull grown by the clearance and swept out
    # of the opening (so nothing overhangs the grip and it lifts straight out).
    seat = hull("cradle", section, grow=clear + wall)
    cut(seat, box("above", (-300, -300, p["cradle_len"]), (300, 300, 300), seat_m))
    cut(seat, box("rims", (-p["cradle_rim"], -300, -300), (300, 300, 300), seat_m))
    cut(seat, hull("pocket", section, grow=clear, sweep=u * 150))
    # Strap slots: the rims notched where the strap crosses, so it stays put below the grip button.
    band = (p["strap_at"] - p["strap_w"] / 2, p["strap_at"] + p["strap_w"] / 2)
    cut(seat, box("slots", (-p["cradle_rim"] - p["strap_t"], -300, band[0]), (300, 300, band[1]), seat_m))
    # Plate on the end face, and the arm out of its middle, between the bolt heads.
    iface = at(f["iface_pos"], f["iface_across"], f["iface_up"], f["iface_out"])
    plate = box("plate", (-f["plate_w"] / 2, -f["plate_h"] / 2, 0), (f["plate_w"] / 2, f["plate_h"] / 2, p["plate"]), iface)
    root = f["iface_pos"] + f["iface_out"] * (p["plate"] / 2)
    arm_m = at(root, f["iface_across"], f["arm_up"], f["arm_dir"])
    reach = (f["anchor"] - root).dot(f["arm_dir"]) + clear + wall + 6
    arm = box("arm", (-f["arm_w"] / 2, -f["plate_h"] / 2, 0), (f["arm_w"] / 2, f["plate_h"] / 2, reach), arm_m)
    union(seat, plate, arm)
    cut(seat, hull("pocket", section, grow=clear, sweep=u * 150))
    # The mating face stays flat, and the plate stays below the string holes.
    cut(seat, box("behind", (-300, -300, -300), (300, 300, 0), iface))
    cut(seat, box("holes", (-300, f["plate_h"] / 2, -300), (300, 300, p["plate"]), iface))
    # Room around everything that moves: the cradle never touches a button.
    for name, idx in ctrl["parts"].items():
        travel = f["L"] * 10 if name == "trigger_front" else None  # a pulled trigger swings back toward the grip
        keep_out = hull(f"keep_{name}", [cad[i] for i in idx], grow=clear + p["button_clear"], sweep=travel)
        cut(seat, keep_out)
    # Bolt holes, counterbored on the outer face for socket heads.
    for i, y in enumerate((-p["bolt_spacing"] / 2, p["bolt_spacing"] / 2)):
        pos = f["iface_pos"] + f["iface_across"] * y + f["iface_out"] * p["plate"]
        ax = at(pos, f["iface_across"], f["iface_up"], f["iface_out"])
        hole = prism(f"h{i}", p["m4_clear"] / 2, p["m4_clear"] / 2, p["plate"] * 3, ax)
        bore = prism(f"cb{i}", p["m4_head_d"] / 2 + 0.3, p["m4_head_d"] / 2 + 0.3, p["m4_head_h"] * 2, ax)
        cut(seat, hole, bore)
    return seat


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


def _shared_volume(target: bpy.types.Object, probe: bpy.types.Object) -> float:
    """How much of ``probe`` the target fills, mm3 (the probe is used up)."""
    copy = bpy.data.objects.new("overlap", target.data.copy())
    bpy.context.scene.collection.objects.link(copy)
    boolean(copy, probe, "INTERSECT")
    bm = bmesh.new()
    bm.from_mesh(copy.data)
    volume = abs(bm.calc_volume())
    bm.free()
    mesh = copy.data
    bpy.data.objects.remove(copy)
    bpy.data.meshes.remove(mesh)
    return volume


def check_cradle(p: dict, f: dict, cf: dict, cradle: bpy.types.Object, ctrl: dict, cad: list[Vector]) -> list[str]:
    """The fit checks: buttons, string holes, bolt access, the guitar, the D."""
    notes = []
    tip = f["tip"]
    # Nothing may touch the controller, and least of all a button.
    bm = bmesh.new()
    bm.from_mesh(cradle.data)
    tree = BVHTree.FromBMesh(bm)
    bm.free()
    def inside(q: Vector) -> bool:  # odd number of crossings along a ray
        ray, crossings = Vector((0.31, 0.52, 0.79)).normalized(), 0
        hit = tree.ray_cast(q, ray)
        while hit[0] is not None:
            crossings += 1
            hit = tree.ray_cast(hit[0] + ray * 1e-3, ray)
        return crossings % 2 == 1

    moving = {i: name for name, idx in ctrl["parts"].items() for i in idx}
    nearest: dict[str, float] = {}
    for i, q in enumerate(cad):
        dist = tree.find_nearest(q)[3]
        signed = -dist if dist < 10 and inside(q) else dist
        group = moving.get(i, "body")
        nearest[group] = min(nearest.get(group, 1e9), signed)
    names = {"body": "body", "trigger_front": "trigger", "trigger_grip": "grip button", "thumbstick": "thumbstick"}
    print("[mount] closest the cradle comes: " + ", ".join(f"{names[k]} {v:.1f} mm" for k, v in sorted(nearest.items())))
    for group, gap in nearest.items():
        if gap < (0.0 if group == "body" else p["button_clear"]):
            notes.append(f"the cradle comes within {gap:.1f} mm of the controller's {names[group]}")
    lo = cf["span"]["trigger_grip"][0]
    band_hi = p["strap_at"] + p["strap_w"] / 2
    print(f"[mount] strap: {band_hi - p['strap_w']:.0f}-{band_hi:.0f} mm up the grip; the grip button starts at {lo:.1f} mm")
    if band_hi > lo - 2.0:
        notes.append(f"the strap slot reaches {band_hi:.1f} mm up the grip, within 2 mm of the grip button ({lo:.1f} mm): lower 'strap_at'")
    # The string holes stay reachable: nothing of the cradle out in front of them.
    half = max(p["head_width"], p["d_width"]) / 2
    corridor = box("strings", (tip - p["string_clear"], -half, p["d_top_z"]), (tip + 0.01, half, p["head_top_z"] + 15))
    blocked = _shared_volume(cradle, corridor)
    lo_x, hi_z = tip - p["string_clear"], p["head_top_z"] + 15
    hidden = sum(1 for q in cad if lo_x < q.x < tip and abs(q.y) < half and p["d_top_z"] < q.z < hi_z)
    print(f"[mount] in front of the string holes: cradle {blocked:.1f} mm3; controller {'yes, unstrap it to change strings' if hidden else 'no'}")
    if blocked > 0.5:
        notes.append(f"the cradle passes in front of the string holes ({blocked:.0f} mm3 within {p['string_clear']:.0f} mm of the end face)")
    # A hex key reaches each bolt head straight on.
    for i, y in enumerate((-p["bolt_spacing"] / 2, p["bolt_spacing"] / 2)):
        start = f["iface_pos"] + f["iface_across"] * y + f["iface_out"] * (p["plate"] + 0.1)
        path = prism(f"key{i}", p["m4_head_d"] / 2 + 0.3, p["m4_head_d"] / 2 + 0.3, KEY_CLEAR,
                     at(start + f["iface_out"] * (KEY_CLEAR / 2), f["iface_across"], f["iface_up"], f["iface_out"]))
        rel = [((q - start).dot(f["iface_out"]), (q - start - f["iface_out"] * (q - start).dot(f["iface_out"])).length) for q in cad]
        in_way = sum(1 for d, r in rel if 0 < d < KEY_CLEAR and r < p["m4_head_d"] / 2 + 0.3)
        blocked = _shared_volume(cradle, path)
        if blocked > 0.5 or in_way:
            notes.append(f"bolt {i}: a hex key's straight path out of the counterbore is blocked ({blocked:.0f} mm3 of cradle, {in_way} controller points)")
    # The controller stays out of the guitar.
    inside = sum(1 for q in cad if q.x > tip - 0.5 and abs(q.y) < p["head_width"] / 2
                 and p["head_top_z"] - p["head_thickness"] < q.z < p["head_top_z"] + 12)
    if inside:
        notes.append(f"the controller reaches into the guitar's head end ({inside} points)")
    # For the D plug: how close each bolt hole comes to the D's curve (D taken as a half-ellipse).
    for name, r in (("M4 hole", p["m4_clear"] / 2), ("M4 nut", p["m4_nut_af"] / 2 + 0.15)):
        y = p["bolt_spacing"] / 2
        curve = p["d_top_z"] - p["d_depth"] * math.sqrt(max(0.0, 1 - (y / (p["d_width"] / 2)) ** 2))
        margin = (f["iface_pos"].z - r) - curve
        print(f"[mount] {name} at {y:.0f} mm across: {margin:+.1f} mm inside the D's curve (half-ellipse D)")
    return notes


def _unity_model(q: Vector) -> Vector:
    """Model frame (the FBX's, right-handed) -> the same model as Unity draws it (X negated)."""
    return Vector((-q.x, q.y, q.z))


def mount_offset(p: dict, f: dict) -> dict:
    """The neck frame in the controller model's frame (Unity axes, metres).

    The reference is the origin and axes of Meta's Touch Plus model as Unity
    draws it -- the pose the Meta SDK's OVRControllerPrefab puts the model at.
    The neck frame in Unity is X toward the body (CAD +X), Y out of the
    fretboard (CAD +Z), Z toward the highest string (CAD +Y). Approximate only
    as far as the guitar measurements are: the headset's alignment check is
    what makes it exact; the mount only has to land in the same place.
    """
    back = f["R"].transposed()
    origin = _unity_model(back @ -f["t"]) / 1000
    axes = [_unity_model(back @ Vector(d)) for d in ((1, 0, 0), (0, 0, 1), (0, 1, 0))]
    rot = Matrix(axes).transposed()  # columns: the neck's axes in model axes
    if abs(rot.determinant() - 1) > 1e-6:
        raise RuntimeError("mount_offset: not a rotation")
    q = rot.to_quaternion()
    hand = p["controller_hand"]
    return {
        "controller": "meta-quest-touch-plus",
        "pose": "meta-sdk-model",
        "reference_pose": f"origin and axes of Meta's MetaQuestTouchPlus_{hand.capitalize()} model as Unity draws it "
        "(com.meta.xr.sdk.core; OVRControllerPrefab draws it at the controller anchor)",
        "hand": hand,
        "neck_origin_in_grip_m": [round(c, 4) for c in origin],
        "neck_rotation_in_grip_quat": [round(q.x, 5), round(q.y, 5), round(q.z, 5), round(q.w, 5)],
        "note": "First guess from the CAD: the neck frame relative to the Meta SDK controller model's origin (see "
        "reference_pose; the *_in_grip_* key names are kept for the Unity reader). The headset's alignment check replaces it.",
        "face_to_eyes_deg": round(f["face_to_eyes_deg"], 1),
        "params": {k: p[k] for k in ("nut_to_tip", "d_top_z", "d_depth", "arm_len", "arm_back", "grip_rise", "grip_tilt", "face_roll")},
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


def _mesh(name: str, verts: list[Vector], faces: list[list[int]]) -> bpy.types.Object:
    bm = bmesh.new()
    vs = [bm.verts.new(q) for q in verts]
    for face in faces:
        try:
            bm.faces.new([vs[i] for i in face])
        except ValueError:  # a repeated face
            pass
    return _link(name, bm)


def _d_cover(name: str, p: dict, x: float, thickness: float) -> bpy.types.Object:
    """The D outline on the end face (flat edge up), as a thin plate standing out by ``thickness``."""
    w, depth, top = p["d_width"] / 2, p["d_depth"], p["d_top_z"]
    ring = [Vector((x, w * math.cos(t), top - depth * math.sin(t))) for t in (math.pi * k / 32 for k in range(33))]
    bm = bmesh.new()
    face = bm.faces.new([bm.verts.new(q) for q in ring])
    res = bmesh.ops.extrude_face_region(bm, geom=[face])
    bmesh.ops.translate(bm, vec=(-thickness, 0, 0), verts=[g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _link(name, bm)


def ghosts(p: dict, f: dict, ctrl: dict, cad: list[Vector]) -> dict[str, tuple[bpy.types.Object, tuple]]:
    """Stand-ins for the guitar and Meta's controller, for the previews and mount.blend."""
    tip, top = f["tip"], p["head_top_z"]
    out = {"neck": (box("ghost_neck", (tip, -p["head_width"] / 2, top - p["head_thickness"]), (260, p["head_width"] / 2, top)), (0.36, 0.25, 0.18, 1))}
    out["d"] = (_d_cover("ghost_d_face", p, tip, 0.4), (0.05, 0.05, 0.05, 1))
    holes_z = (top + p["d_top_z"]) / 2
    for i in range(6):
        y = -p["string_spread_at_clamp"] / 2 + i * p["string_spread_at_clamp"] / 5
        out[f"string{i}"] = (box(f"ghost_string{i}", (tip + 6, y - 0.4, top + 0.5), (260, y + 0.4, top + 1.3)), (0.9, 0.9, 0.85, 1))
        hole = prism(f"ghost_hole{i}", 1.3, 1.3, 0.8, at(Vector((tip, y, holes_z)), Vector((0, 1, 0)), Vector((0, 0, 1)), Vector((1, 0, 0))))
        out[f"hole{i}"] = (hole, (0.02, 0.02, 0.02, 1))
    moving = {i for idx in ctrl["parts"].values() for i in idx}
    body = [face for face in ctrl["faces"] if face[0] not in moving]
    buttons = [face for face in ctrl["faces"] if face[0] in moving]
    out["controller"] = (_mesh("controller (Meta Touch Plus, do not commit)", cad, body), (0.85, 0.86, 0.88, 1))
    out["buttons"] = (_mesh("controller buttons", cad, buttons), (1.0, 0.55, 0.15, 1))
    fz = f["face"]
    fx = fz.orthogonal().normalized()
    marker = prism("top_surface_normal", 1.2, 1.2, 50, at(f["thumb"] + fz * 25, fx, fz.cross(fx), fz), segments=16)
    out["normal"] = (marker, (0.13, 0.83, 0.93, 1))
    return out


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
    middle = f["E"] + f["A"] * 60
    target = (f["iface_pos"] + middle) / 2

    def look(name: str, position: Vector, aim: Vector, up: Vector, hide: tuple[str, ...] = ()) -> None:
        for key, (ob, _) in objects.items():
            ob.hide_render = key in hide
        cam.location = position
        fwd = (aim - position).normalized()
        right = fwd.cross(up).normalized()
        cam.rotation_euler = Matrix((right, right.cross(fwd), -fwd)).transposed().to_euler()
        scene.render.filepath = str(out / f"preview_{name}.png")
        bpy.ops.render.render(write_still=True)

    up_when_playing = Vector((0, -1, 0))  # the low-E edge points at the ceiling
    # What the headset sees: from the eye position the tracking check uses, so the
    # controller's top surface (cyan normal) should point at the camera.
    look("eyes", Vector((p["eye_toward_body"], -p["eye_up"], -p["eye_back"])), f["thumb"], up_when_playing)
    # From the audience, as a webcam on the desk sees it: fretboard face on, head end to the right.
    look("front", target + Vector((30, 0, 440)), target, up_when_playing)
    look("below", target + Vector((-60, 200, -300)), target, Vector((0, 0, 1)))
    look("side", target + Vector((-90, -420, -20)), target, Vector((0, 0, 1)))
    # The empty seat, looking in through its opening, and the plate straight down
    # the bolts (both heads and the string holes should show).
    seat = f["E"] + f["A"] * (p["cradle_len"] / 2)
    controller = ("controller", "buttons", "normal")
    look("seat", seat - f["floor"] * 260 + f["A"] * 40, seat, f["A"], hide=controller)
    look("plate", f["iface_pos"] + f["iface_out"] * 260, f["iface_pos"], f["iface_up"], hide=controller)
    for ob, _ in objects.values():
        ob.hide_render = False


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="build_mount")
    ap.add_argument("--params", type=Path, default=None, help="JSON of DEFAULTS / TEXT_DEFAULTS keys to override")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "out")
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args(argv)

    p: dict = {**DEFAULTS, **TEXT_DEFAULTS}
    if args.params:
        overrides = json.loads(args.params.read_text(encoding="utf-8"))
        unknown = sorted(set(overrides) - set(p))
        if unknown:
            raise SystemExit(f"unknown parameter(s): {', '.join(unknown)}")
        p.update({k: str(v) if k in TEXT_DEFAULTS else float(v) for k, v in overrides.items()})
    if p["controller_hand"] not in ("left", "right"):
        raise SystemExit("controller_hand must be 'left' or 'right'")
    args.out.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    ctrl = read_touch_plus(p)
    cf = controller_frame(ctrl)
    f = frame(p, cf)
    cad = place(p, f, cf, ctrl)
    fmt = lambda v: "(" + ", ".join(f"{c:+.3f}" for c in v) + ")"  # noqa: E731
    print(f"[mount] controller: {ctrl['path'].name}; model frame: grip axis {fmt(cf['a'])}, top surface {fmt(cf['top'])} "
          f"({cf['cant']:.1f} deg off the grip axis, leaning to the back), trigger side {fmt(-cf['b'])}")
    span = cf["span"]
    print(f"[mount] up the grip from its end: grip button {span['trigger_grip'][0]:.0f}-{span['trigger_grip'][1]:.0f} mm, "
          f"trigger {span['trigger_front'][0]:.0f}-{span['trigger_front'][1]:.0f} mm, thumbstick {span['thumbstick'][0]:.0f}-"
          f"{span['thumbstick'][1]:.0f} mm; whole controller {cf['length']:.0f} mm")
    section = grip_section(cad, ctrl["faces"], f["E"], f["A"], p["cradle_len"])
    across = [(q - f["E"]).dot(f["across"]) for q in section]
    deep = [(q - f["E"]).dot(f["floor"]) for q in section]
    print(f"[mount] the seat holds the grip's lowest {p['cradle_len']:.0f} mm: {max(across) - min(across):.1f} mm rim to rim, "
          f"{max(deep) - min(deep):.1f} mm floor to opening, {p['cradle_clear']:.1f} mm clearance")

    cradle = build_cradle(p, f, ctrl, cad)
    notes = check(p, [cradle], f) + check_cradle(p, f, cf, cradle, ctrl, cad)

    # Print the cradle on its mating face: +X (toward the guitar) -> down.
    export_stl(cradle, args.out / "cradle.stl", Matrix.Rotation(math.radians(90), 4, "Y"))
    export_stl(cradle, args.out / "assembly.stl")

    offset = mount_offset(p, f)
    (args.out / "mount_offset.json").write_text(json.dumps(offset, indent=2) + "\n", encoding="utf-8")
    print("[mount] mount_offset:", json.dumps(offset))

    colours = {"cradle": (cradle, (0.65, 0.55, 0.98, 1))}
    colours.update(ghosts(p, f, ctrl, cad))
    if not args.no_render:
        render(colours, args.out, f, p)

    # For inspecting in Blender: everything in place, with the guitar and Meta's
    # controller, measured in millimetres and shown in object colours. It holds
    # Meta's model, so it stays in out/.
    for ob, colour in colours.values():
        ob.color = colour
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 0.001
    scene.unit_settings.length_unit = "MILLIMETERS"
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    space.shading.color_type = "OBJECT"
                    space.clip_end = 10000
    bpy.ops.wm.save_as_mainfile(filepath=str((args.out / "mount.blend").resolve()))

    for n in notes:
        print(f"[mount] WARNING: {n}")
    print(f"[mount] wrote {args.out}")


if __name__ == "__main__":
    main()
