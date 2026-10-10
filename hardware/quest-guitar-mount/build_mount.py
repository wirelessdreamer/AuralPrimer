"""Parametric, 3D-printable mount for a Meta Quest 3/3S Touch Plus controller
on the head end of a headless guitar (defaults: Jamstik Studio).

One printed part, the ``holder``, in one piece:

* a plug that replaces the guitar's black D-shaped end cover: a copy of the
  cover's D, 45 mm long, with its locating tab, pressed into the pocket under
  the string holes (crush ribs make the grip);
* a block on the end face below the string holes, which the plug comes out of;
* a solid from the block's outside face to the back of the sleeve;
* a sleeve that follows the controller's real grip, a thin wall a clearance
  off it, wrapping past the grip's middle so the controller clips in from the
  far side, with the grip's end in a cup at the bottom, so it lands in the
  same place every time. It stops short of the grip button and keeps clear of
  the trigger and thumbstick.

Nothing touches the strings, the top face or the nut screws.

The controller's shape comes from Meta's own Touch Plus model, read at build
time from the Meta XR SDK (``controller_mesh``) -- never copied. Meta's model
is SDK content: never commit it, or any file holding its geometry. Everything
written to ``out/`` (git-ignored) may hold it.

Run (Blender 4.2+; Manifold booleans need 4.5+, EXACT is the fallback)::

    blender --background --factory-startup --python build_mount.py -- \
        [--params my_measurements.json] [--out out] [--no-render]

Writes ``<out>/holder.stl`` (already turned to print standing on the plug's
tip), ``d_plug_fit_test.stl`` (just the plug on a thin plate, to try in the
pocket first), ``assembly.stl`` (the holder in place, in the CAD frame),
``mount.blend`` (the same, with the guitar and Meta's controller in place,
shown in millimetres), ``preview_*.png`` and ``mount_offset.json`` (where the
neck sits relative to the controller model's origin, for the headset's first
guess before its alignment check).

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
    "head_width": 43.16,  # side to side, at the end face (measured)
    "head_thickness": 19.0,  # top face to back, at the end face (estimate from a photo)
    "head_top_z": 0.0,  # head top face height vs the fretboard at the nut (+ = higher)
    "nut_to_tip": 40.0,  # nut (fretboard side) to the very end of the head
    "string_spread": 36.0,  # outer edge of low-E to outer edge of high-e, at the end face
    # --- The D pocket under the string holes, and its black cover ------------
    "d_top_z": -6.6,  # the D's flat top edge, just under the row of string holes (estimate from a photo)
    "d_width": 37.0,  # the cover across its flat edge (measured)
    "d_depth": 10.4,  # the cover from its flat edge to the bottom of its curve, without the tab
    # (12.38 measured with it); the curve is an arc through those three points
    "d_tab_w": 2.9,  # the locating tab at the middle of the curve: its width...
    "d_tab_len": 1.9,  # ...and how far it stands out of the curve
    "d_cover_t": 1.5,  # the cover's thickness: the plug's tab is this deep (guess: measure)
    "d_plug_len": 45.0,  # how far the plug reaches into the pocket along the neck
    "d_fit": 0.15,  # the plug's D is this much under size all round...
    "d_rib": 0.2,  # ...and its crush ribs stand this far proud of the measured D, for a press fit
    "string_clear": 100.0,  # how far out from the end face nothing of the holder may sit in front of the string holes
    # --- Controller sleeve (the grip itself comes from Meta's model) ---------
    "sleeve_len": 48.0,  # how far up the grip, from its end, the sleeve reaches
    "sleeve_clear": 0.6,  # room between the grip and the sleeve
    "sleeve_wall": 2.5,  # the sleeve's wall
    "snap": 0.6,  # the sleeve wraps past the grip's widest line until each rim flexes this far as it clips on
    "button_clear": 2.5,  # extra room kept around the trigger, grip button and thumbstick
    # --- Layout -----------------------------------------------------------
    "arm_len": 25.0,  # the block's outside face to where the sleeve's back meets the solid
    "arm_back": 60.0,  # ...in a direction angled from the neck line toward the back of the neck
    "mount_roll": 90.0,  # then the sleeve and controller turn together about the neck's direction, through the
    # block's middle: clockwise seen from the body, looking down the neck toward the head end
    "grip_rise": 40.0,  # controller raised from the neck line toward the low-E edge (up, when playing), like a raised headstock
    "grip_tilt": 45.0,  # ...and tipped back toward the player
    "face_roll": 60.0,  # turns the controller about its grip: 0 leans its top surface toward the player,
    # positive toward the low-E edge (up, when playing)
    # --- Where the player's eyes are, from the nut (only for the tracking check) ---
    "eye_toward_body": 350.0,
    "eye_up": 300.0,  # toward the low-E edge
    "eye_back": 250.0,  # behind the fretboard face, toward the player
    # --- The block on the end face ----------------------------------------
    "end_wall": 6.0,  # its thickness, out from the end face
    "block_height": 14.0,  # from just under the string holes down
}

# Meta's Touch Plus model, read in place (never copied). {Hand} becomes Left or Right.
TEXT_DEFAULTS: dict[str, str] = {
    "controller_mesh": "D:/AuralPrimer/UnityClient/Aural Primer/Library/PackageCache/"
    "com.meta.xr.sdk.core@4ea2676097b9/Meshes/MetaQuestTouchPlus/MetaQuestTouchPlus_{Hand}.fbx",
    "controller_hand": "left",
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
    f = {"tip": tip}
    # The cover's D: an arc through the ends of its flat edge and the bottom of its curve.
    half, depth = p["d_width"] / 2, p["d_depth"]
    f["d_r"] = (half * half + depth * depth) / (2 * depth)
    f["d_zc"] = p["d_top_z"] - depth + f["d_r"]  # the arc's centre, above the flat edge
    f["d_bottom"] = p["d_top_z"] - depth
    # The outside face of the block on the end face, and its axes: the solid
    # grows from here to the back of the sleeve. Its top edge is level with the
    # D's flat edge, so below the string holes.
    f["block_h"] = p["block_height"]
    f["iface_pos"] = Vector((tip - p["end_wall"], 0.0, p["d_top_z"] - p["d_fit"] - f["block_h"] / 2))
    f["iface_out"] = Vector((-1.0, 0.0, 0.0))  # away from the guitar
    f["iface_up"] = Vector((0.0, 0.0, 1.0))  # toward the string holes
    f["iface_across"] = f["iface_up"].cross(f["iface_out"])  # (across, up, out) is right-handed
    # Where the sleeve meets the solid: out from the block, angled back, away from the string holes.
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
    # mount_roll turns the controller about the neck's direction (clockwise seen
    # from the body, which is a negative turn about +X).
    spin = Matrix.Rotation(-math.radians(p["mount_roll"]), 3, "X")
    A, L = spin @ A, spin @ L
    # R turns the model frame into the CAD frame: grip axis a -> A, lean b -> L.
    R = Matrix((A, L, A.cross(L))).transposed() @ Matrix((cf["a"], cf["b"], cf["c"]))
    f["R"], f["A"], f["L"] = R, A, L
    # The sleeve's back faces the block and its opening faces away, so the
    # controller clips in from the far side.
    f["floor"] = (-f["arm_dir"] - A * (-f["arm_dir"]).dot(A)).normalized()
    f["across"] = A.cross(-f["floor"])  # rim to rim
    return f


def place(p: dict, f: dict, cf: dict, ctrl: dict) -> list[Vector]:
    """Put the controller so the sleeve's back meets the solid ``arm_len`` out
    from the block (along arm_dir), 12 mm up the grip; returns its vertices in
    CAD (mm)."""
    R, A, floor = f["R"], f["A"], f["floor"]
    rel = [R @ (v - cf["end"]) for v in ctrl["verts"]]
    anchor = f["iface_pos"] + f["arm_dir"] * p["arm_len"]
    meet = 12.0
    back = max(r.dot(floor) for r in rel if abs(r.dot(A) - meet) < 6)
    f["anchor"] = anchor
    f["E"] = anchor - floor * (back + p["sleeve_clear"] + p["sleeve_wall"] - 1.0) - A * meet  # the grip's end, in CAD
    f["t"] = f["E"] - R @ cf["end"]
    cad = [f["E"] + r for r in rel]
    thumb = sum((cad[i] for i in ctrl["parts"]["thumbstick"]), Vector()) / len(ctrl["parts"]["thumbstick"])
    f["face"] = (R @ cf["top"]).normalized()
    f["thumb"] = thumb
    eye = Vector((p["eye_toward_body"], -p["eye_up"], -p["eye_back"]))
    f["face_to_eyes_deg"] = math.degrees(f["face"].angle(eye - thumb))
    return cad


def d_prism(name: str, p: dict, f: dict, grow: float, x0: float, x1: float, n: int = 72) -> bpy.types.Object:
    """The cover's D (flat edge up), grown by ``grow`` all round (negative shrinks), from x0 to x1."""
    r, zc, zf = f["d_r"] + grow, f["d_zc"], p["d_top_z"] + grow
    half = math.sqrt(r * r - (zf - zc) ** 2)
    a0 = math.atan2(zf - zc, half)  # the flat edge's +Y end; the arc runs down through the bottom to its -Y end
    a1 = -math.pi - a0
    ring = [Vector((x0, r * math.cos(a), zc + r * math.sin(a))) for a in (a0 + (a1 - a0) * k / n for k in range(n + 1))]
    bm = bmesh.new()
    face = bm.faces.new([bm.verts.new(q) for q in ring])
    res = bmesh.ops.extrude_face_region(bm, geom=[face])
    bmesh.ops.translate(bm, vec=(x1 - x0, 0, 0), verts=[g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _link(name, bm)


def _along_neck(x: float, y: float, z: float) -> Matrix:
    """Placement with local Z along the neck (+X)."""
    return at(Vector((x, y, z)), Vector((0, 1, 0)), Vector((0, 0, 1)), Vector((1, 0, 0)))


def build_d_plug(p: dict, f: dict, fit_test: bool = False) -> bpy.types.Object:
    """The plug that replaces the black D cover, on the block the sleeve's solid grows from.

    ``fit_test``: just the plug on a thin plate, to try in the pocket before
    printing the whole part.
    """
    tip, fit = f["tip"], p["d_fit"]
    top = p["d_top_z"] - fit  # level with the plug's flat top
    if fit_test:
        lo = (tip - 1.2, -p["head_width"] / 2, f["d_bottom"] - p["d_tab_len"] - 2.0)
    else:
        lo = (f["iface_pos"].x, -p["head_width"] / 2, f["iface_pos"].z - f["block_h"] / 2)
    body = box("d_plug_fit_test" if fit_test else "d_plug", lo, (tip, p["head_width"] / 2, top))
    # The cover's D, a little under size, with a step at its tip to lead it in.
    lead = 0.6
    end = tip + (min(p["d_plug_len"], 10.0) if fit_test else p["d_plug_len"])  # the fit test only needs to try the D
    union(body, d_prism("plug", p, f, -fit, tip - 0.5, end - lead))
    union(body, d_prism("lead", p, f, -fit - 0.5, end - lead - 0.1, end))
    # The locating tab, only as deep as the cover is thick.
    tw = p["d_tab_w"] / 2 - fit
    union(body, box("tab", (tip - 0.5, -tw, f["d_bottom"] - p["d_tab_len"] + fit), (tip + p["d_cover_t"], tw, f["d_bottom"] + 0.5)))
    # Crush ribs, d_rib proud of the measured D: they make the grip and shave
    # down to fit. Four round the curve (clear of the tab), two on the flat.
    rr = 0.6
    x0, x1 = tip + p["d_cover_t"] + 0.3, end - lead - 0.4
    mid, length = (x0 + x1) / 2, x1 - x0
    ribs = []
    for deg in (-138, -112, -68, -42):
        a, d = math.radians(deg), f["d_r"] + p["d_rib"] - rr
        ribs.append(prism(f"rib{deg}", rr, rr, length, _along_neck(mid, d * math.cos(a), f["d_zc"] + d * math.sin(a)), segments=16))
    for y in (-9.0, 9.0):
        ribs.append(prism(f"rib_flat{y:+.0f}", rr, rr, length, _along_neck(mid, y, p["d_top_z"] + p["d_rib"] - rr), segments=16))
    union(body, *ribs)
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


def _hull2d(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Convex hull of 2D points, counter-clockwise (monotone chain)."""
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def turn(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list = []
    upper: list = []
    for q in pts:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    for q in reversed(pts):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return lower[:-1] + upper[:-1]


def _grow2d(poly: list[tuple[float, float]], grow: float) -> list[tuple[float, float]]:
    """A convex outline grown by ``grow`` all round."""
    if grow <= 0:
        return poly
    disc = [(grow * math.cos(2 * math.pi * k / 24), grow * math.sin(2 * math.pi * k / 24)) for k in range(24)]
    return _hull2d([(x + dx, y + dy) for x, y in poly for dx, dy in disc])


def _chord(poly: list[tuple[float, float]], x: float) -> float:
    """How long the line (first coordinate = x) runs inside a convex outline."""
    ys = [y1 + (y2 - y1) * (x - x1) / (x2 - x1) for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1])
          if x1 != x2 and (x1 - x) * (x2 - x) <= 0]
    return max(ys) - min(ys) if len(ys) >= 2 else 0.0


def _ring(poly: list[tuple[float, float]], grow: float, n: int) -> list[tuple[float, float]]:
    """A convex outline grown by ``grow`` all round, sampled at n angles about its centre."""
    poly = _grow2d(poly, grow)
    cx = sum(x for x, _ in poly) / len(poly)
    cy = sum(y for _, y in poly) / len(poly)
    ring = []
    for k in range(n):
        dx, dy = math.cos(2 * math.pi * k / n), math.sin(2 * math.pi * k / n)
        best = math.inf
        for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
            ex, ey = x2 - x1, y2 - y1
            den = dx * ey - dy * ex
            if abs(den) < 1e-12:
                continue
            px, py = x1 - cx, y1 - cy
            r, w = (px * ey - py * ex) / den, (px * dy - py * dx) / den
            if r > 0 and -1e-9 <= w <= 1 + 1e-9:
                best = min(best, r)
        ring.append((cx + best * dx, cy + best * dy))
    return ring


def grip_sections(cad: list[Vector], faces: list[list[int]], E: Vector, A: Vector, u: Vector, v: Vector):
    """``outline(level)``: the convex outline, in (u, v), of where the mesh's
    edges cross the plane ``level`` mm up the grip axis from E; plus every
    vertex's height and (u, v)."""
    s = [(q - E).dot(A) for q in cad]
    uv = [((q - E).dot(u), (q - E).dot(v)) for q in cad]
    edges = {tuple(sorted((i, j))) for face in faces for i, j in zip(face, face[1:] + face[:1])}

    def outline(level: float) -> list[tuple[float, float]]:
        pts = []
        for i, j in edges:
            if (s[i] - level) * (s[j] - level) < 0:
                w = (level - s[i]) / (s[j] - s[i])
                pts.append((uv[i][0] + (uv[j][0] - uv[i][0]) * w, uv[i][1] + (uv[j][1] - uv[i][1]) * w))
        return _hull2d(pts)

    return outline, s, uv


def snap_line(p: dict, f: dict, ctrl: dict, cad: list[Vector]) -> tuple[float, float]:
    """Where the sleeve's opening starts (u, from the grip axis) and how far each rim then flexes.

    The grip goes in along -u, so at every height its widest chord across v
    has to pass the opening, which is the pocket's chord at the cut. The cut
    goes as far past the grip's widest line as keeps that squeeze, per rim,
    within ``snap``.
    """
    u, v = -f["floor"], f["across"]
    outline, _, _ = grip_sections(cad, ctrl["faces"], f["E"], f["A"], u, v)
    clear = p["sleeve_clear"]
    sections = []
    for level in [10.0 + 2.0 * k for k in range(int((p["sleeve_len"] - 12.0) / 2.0) + 1)]:
        poly = outline(level)
        if len(poly) >= 3:
            xs = [x for x, _ in poly]
            widest = max(_chord(poly, min(xs) + (max(xs) - min(xs)) * k / 80) for k in range(1, 80))
            sections.append((widest, _grow2d(poly, clear)))

    def flex(cut: float) -> float:
        return max((widest - _chord(room, cut)) / 2 for widest, room in sections)

    cut = best = min(min(x for x, _ in room) for _, room in sections) + 1.0
    while cut < 60.0:  # past the widest line the opening narrows: keep the furthest cut within the squeeze
        if flex(cut) <= p["snap"]:
            best = cut
        cut += 0.1
    return best, flex(best)


def grip_loft(name: str, cad: list[Vector], faces: list[list[int]], E: Vector, A: Vector, u: Vector, v: Vector,
              grow: float, top: float, step: float = 2.0, n: int = 64) -> bpy.types.Object:
    """The grip grown by ``grow``: a closed loft through the controller's cross-sections.

    Every ``step`` mm up the grip axis from the grip's end to ``top`` (every
    0.5 mm for the first 6 mm, where the end rounds off), the
    section is the convex outline of where the mesh's edges cross that height,
    grown by ``grow`` all round. So the loft follows the grip's shape along its
    length; across it, each section is convex. Below the grip's end it closes
    in a rounded cup, ``grow`` below the grip's lowest point.
    """
    outline, s, uv = grip_sections(cad, faces, E, A, u, v)
    lowest = min(s)  # the grip's end can reach a little below where its axis leaves it
    first = lowest + 1.0
    while len(outline(first)) < 3:
        first += 0.5
    base = _hull2d(outline(first) + [q for q, sq in zip(uv, s) if sq <= first])  # all of the end, seen from below
    depth = first - lowest + grow
    levels = []
    for deg in (90, 70, 50, 30, 15):  # the cup: the first section grown less and less below it
        a = math.radians(deg)
        levels.append((first - depth * math.sin(a), _ring(base, grow * math.cos(a), n)))
    heights = [first + 0.5 * k for k in range(12)]  # finer where the grip's end rounds off
    while heights[-1] < top:
        heights.append(min(heights[-1] + step, top))
    for level in heights:
        poly = outline(level)
        if len(poly) >= 3:
            levels.append((level, _ring(poly, grow, n)))
    bm = bmesh.new()
    rings = [[bm.verts.new(E + A * lv + u * x + v * y) for x, y in ring] for lv, ring in levels]
    for lo, hi in zip(rings, rings[1:]):
        for k in range(n):
            bm.faces.new((lo[k], lo[(k + 1) % n], hi[(k + 1) % n], hi[k]))
    bm.faces.new(rings[0][::-1])
    bm.faces.new(rings[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _link(name, bm)


def build_holder(p: dict, f: dict, ctrl: dict, cad: list[Vector], plug: bpy.types.Object) -> bpy.types.Object:
    """The whole printed part: the plug and its block, a solid out to the back
    of the sleeve, and the sleeve, which wraps the grip past its middle so the
    controller clips in from the far side."""
    A, E = f["A"], f["E"]
    u, v = -f["floor"], f["across"]  # u: out of the sleeve's opening; v: rim to rim
    seat_m = at(E, u, v, A)  # local x = u, y = v, z = up the grip from its end
    clear, wall, top = p["sleeve_clear"], p["sleeve_wall"], p["sleeve_len"]
    holder = grip_loft("holder", cad, ctrl["faces"], E, A, u, v, clear + wall, top)
    # The solid: from the block's outside face to the back half of the sleeve.
    back = [q.co.copy() for q in holder.data.vertices if (q.co - E).dot(u) < 0]
    iface = at(f["iface_pos"], f["iface_across"], f["iface_up"], f["iface_out"])
    hw, hh = p["head_width"] / 2, f["block_h"] / 2
    face = [iface @ Vector((x, y, 0.0)) for x in (-hw, hw) for y in (-hh, hh)]
    union(holder, hull("solid", face + back))
    # The opening, past the snap line, and the grip's own room (no sweep: it clips in).
    f["snap_at"], f["snap_flex"] = snap_line(p, f, ctrl, cad)
    cut(holder, box("opening", (f["snap_at"], -300, -300), (300, 300, 300), seat_m))
    cut(holder, grip_loft("pocket", cad, ctrl["faces"], E, A, u, v, clear, top + 60))
    # Room around everything that moves: the holder never touches a button.
    for name, idx in ctrl["parts"].items():
        travel = f["L"] * 10 if name == "trigger_front" else None  # a pulled trigger swings back toward the grip
        cut(holder, hull(f"keep_{name}", [cad[i] for i in idx], grow=clear + p["button_clear"], sweep=travel))
    # Nothing past the block's outside face but the block and plug themselves.
    cut(holder, box("behind", (-300, -300, -300), (300, 300, 0), iface))
    union(holder, plug)
    return holder


# ---------------------------------------------------------------------------
# Checks, offset, export, preview.
# ---------------------------------------------------------------------------


def check(p: dict, parts: list[bpy.types.Object], f: dict | None = None) -> list[str]:
    notes = []
    if f is not None:
        print(f"[mount] top control surface points {f['face_to_eyes_deg']:.0f} deg from the player's eyes")
        if f["face_to_eyes_deg"] > 70:
            notes.append(f"the controller's top surface points {f['face_to_eyes_deg']:.0f} deg away from the eyes: tracking may drop")
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


def check_holder(p: dict, f: dict, holder: bpy.types.Object, ctrl: dict, cad: list[Vector]) -> list[str]:
    """The fit checks: the grip and buttons, the snap, the string holes, the guitar, the plug."""
    notes = []
    tip = f["tip"]
    # Nothing may touch the controller, and least of all a button.
    bm = bmesh.new()
    bm.from_mesh(holder.data)
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
    print("[mount] closest the holder comes: " + ", ".join(f"{names[k]} {v:.1f} mm" for k, v in sorted(nearest.items())))
    for group, gap in nearest.items():
        if gap < (0.0 if group == "body" else p["button_clear"]):
            notes.append(f"the holder comes within {gap:.1f} mm of the controller's {names[group]}")
    print(f"[mount] snap: the opening starts {f['snap_at']:.1f} mm past the grip axis; each rim flexes "
          f"{f['snap_flex']:.2f} mm as the controller clips in")
    if f["snap_flex"] < 0.2:
        notes.append(f"the sleeve barely grips: each rim flexes only {f['snap_flex']:.2f} mm (raise 'snap')")
    # The string holes stay reachable: nothing of the holder out in front of them.
    half = max(p["head_width"], p["d_width"]) / 2
    corridor = box("strings", (tip - p["string_clear"], -half, p["d_top_z"]), (tip + 0.01, half, p["head_top_z"] + 15))
    blocked = _shared_volume(holder, corridor)
    lo_x, hi_z = tip - p["string_clear"], p["head_top_z"] + 15
    hidden = sum(1 for q in cad if lo_x < q.x < tip and abs(q.y) < half and p["d_top_z"] < q.z < hi_z)
    print(f"[mount] in front of the string holes: holder {blocked:.1f} mm3; controller {'yes, unclip it to change strings' if hidden else 'no'}")
    if blocked > 0.5:
        notes.append(f"the holder passes in front of the string holes ({blocked:.0f} mm3 within {p['string_clear']:.0f} mm of the end face)")
    # The controller stays out of the guitar.
    inside = sum(1 for q in cad if q.x > tip - 0.5 and abs(q.y) < p["head_width"] / 2
                 and p["head_top_z"] - p["head_thickness"] < q.z < p["head_top_z"] + 12)
    if inside:
        notes.append(f"the controller reaches into the guitar's head end ({inside} points)")
    if p["d_plug_len"] < p["d_cover_t"] + 2.0:
        notes.append("d_plug_len leaves no room for the crush ribs behind the tab: the plug won't grip")
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
        "params": {k: p[k] for k in ("nut_to_tip", "d_top_z", "end_wall", "block_height", "arm_len", "arm_back", "mount_roll", "grip_rise", "grip_tilt", "face_roll")},
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


def ghosts(p: dict, f: dict, ctrl: dict, cad: list[Vector]) -> dict[str, tuple[bpy.types.Object, tuple]]:
    """Stand-ins for the guitar and Meta's controller, for the previews and mount.blend."""
    tip, top = f["tip"], p["head_top_z"]
    out = {"neck": (box("ghost_neck", (tip, -p["head_width"] / 2, top - p["head_thickness"]), (260, p["head_width"] / 2, top)), (0.36, 0.25, 0.18, 1))}
    holes_z = (top + p["d_top_z"]) / 2
    for i in range(6):
        y = -p["string_spread"] / 2 + i * p["string_spread"] / 5
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
    # The empty sleeve, looking in through its opening, and the head end straight
    # on (the string holes should show above the block).
    seat = f["E"] + f["A"] * (p["sleeve_len"] / 2)
    controller = ("controller", "buttons", "normal")
    look("seat", seat - f["floor"] * 260 + f["A"] * 40, seat, f["A"], hide=controller)
    look("end", f["iface_pos"] + f["iface_out"] * 260, f["iface_pos"], f["iface_up"], hide=controller)
    # The holder on its own, seen from inside the guitar: the plug's D, tab and crush ribs.
    plug = Vector((f["tip"], 0.0, (p["d_top_z"] + f["iface_pos"].z) / 2))
    look("plug", plug + Vector((160, -90, -90)), plug + Vector((-30, -20, 0)), Vector((0, 0, 1)),
         hide=tuple(k for k in objects if k != "holder"))
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
    print(f"[mount] the sleeve holds the grip's lowest {p['sleeve_len']:.0f} mm, {p['sleeve_wall']:.1f} mm wall, "
          f"{p['sleeve_clear']:.1f} mm clearance")

    holder = build_holder(p, f, ctrl, cad, build_d_plug(p, f))
    fit_test = build_d_plug(p, f, fit_test=True)
    notes = check(p, [holder, fit_test], f) + check_holder(p, f, holder, ctrl, cad)

    # Print the holder standing on the plug's tip (+X -> down), so the D is drawn
    # in single layers; the fit test on its plate, the D up (-X -> down).
    # assembly.stl is the holder in the guitar's frame.
    export_stl(holder, args.out / "holder.stl", Matrix.Rotation(math.radians(90), 4, "Y"))
    export_stl(holder, args.out / "assembly.stl")
    export_stl(fit_test, args.out / "d_plug_fit_test.stl", Matrix.Rotation(math.radians(-90), 4, "Y"))
    fit_mesh = fit_test.data
    bpy.data.objects.remove(fit_test)
    bpy.data.meshes.remove(fit_mesh)

    offset = mount_offset(p, f)
    (args.out / "mount_offset.json").write_text(json.dumps(offset, indent=2) + "\n", encoding="utf-8")
    print("[mount] mount_offset:", json.dumps(offset))

    colours = {"holder": (holder, (0.65, 0.55, 0.98, 1))}
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
