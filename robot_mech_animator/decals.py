"""Decals (markings, warning labels, data plates) placed on the robot.

Each decal is a small subdivided plane that uses one region of the atlas
``assets/decals.png``. It is placed automatically on a flat area of the target part
(ray casting in the rest pose, rejecting curved spots and the visor), parented to
the same bone as the part, and hugs the surface with a Shrinkwrap modifier. Decals
can be moved or scaled by hand afterwards; "Retirer" deletes them all.
"""
import json
import math
import os

import bmesh
import bpy
from bpy.types import Operator
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from . import rig as R

ASSETS = os.path.join(os.path.dirname(__file__), "assets")
ATLAS = "decals.png"
COLLECTION = "Marquages_Robot"
MATERIAL = "Marquage_Robot"
TAG = "robot_decal"
OFFSET = 0.004

# (decal, part pattern, outward direction in the robot's rest space, width m, region (u0, u1, v0, v1) or None)
# robot: front = -Y, left (".L") = +X. {s} = side, {x} = +1 for L / -1 for R.
FULL = [
    ("{num}", "GEO-shoulder.{s}", ("{x}", 0, 0), 0.46, (0.0, 0.55, 0.0, 1.0)),
    ("insignia", "GEO-shoulder.{s}", ("{x}", 0, 0), 0.30, (0.45, 1.0, 0.0, 1.0)),
    ("danger", "GEO-shoulder.{s}", (0, 0, 1), 0.40, None),
    ("ammo", "GEO-turret.{s}", ("{x}", 0, 0), 0.30, None),
    ("hydraulic", "GEO-shin.{s}", ("{x}", 0, 0), 0.24, None),
    ("hazard", "GEO-toe_flap.{s}", (0, -0.6, 0.8), 0.42, None),
    ("warning", "GEO-shin.{s}", (0, -1, 0), 0.15, None),
    ("tag", "GEO-thigh.{s}", ("{x}", 0, 0), 0.14, (0.0, 1.0, 0.0, 0.5)),
    ("{num}", "GEO-thigh.{s}", ("{x}", 0, 0), 0.26, (0.0, 1.0, 0.45, 1.0)),
    ("nostep", "GEO-torso", (0, 0, 1), 0.36, None),
    ("unit", "GEO-torso", (0, 1, 0), 0.50, None),
    ("rescue", "GEO-torso", (0, 1, 0), 0.28, (0.0, 1.0, 0.0, 0.45)),
    ("dataplate", "GEO-pelvis", (0, -1, 0), 0.22, None),
    ("lift", "GEO-pelvis", ("{x}", 0, 0), 0.14, None),
]
LIGHT = [d for d in FULL if d[0] in ("{num}", "dataplate", "hydraulic", "hazard")]


def atlas_rects():
    with open(os.path.join(ASSETS, "decals.json"), encoding="utf-8") as fh:
        return json.load(fh)


def atlas_image():
    path = os.path.join(ASSETS, ATLAS)
    img = bpy.data.images.get("Marquages_Robot_atlas")
    if img is None:
        img = bpy.data.images.load(path, check_existing=False)
        img.name = "Marquages_Robot_atlas"
        img.alpha_mode = 'STRAIGHT'
        try:
            img.pack()            # the .blend stays self-contained
        except RuntimeError:
            pass
    return img


def decal_material(wear=0.3):
    mat = bpy.data.materials.get(MATERIAL)
    if mat is None:
        mat = bpy.data.materials.new(MATERIAL)
        mat.use_nodes = True
        nt = mat.node_tree
        N, L = nt.nodes, nt.links
        bsdf = next(n for n in N if n.type == 'BSDF_PRINCIPLED')
        tex = N.new("ShaderNodeTexImage")
        tex.image = atlas_image()
        tex.interpolation = 'Cubic'
        tex.extension = 'CLIP'
        L.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        # worn paint: chips and scratches remove the marking (alpha)
        tc = N.new("ShaderNodeTexCoord")
        nz = N.new("ShaderNodeTexNoise")
        nz.inputs["Scale"].default_value = 22.0
        nz.inputs["Detail"].default_value = 8.0
        L.new(tc.outputs["Object"], nz.inputs["Vector"])
        val = N.new("ShaderNodeValue")
        val.name = val.label = "Usure"
        val.outputs[0].default_value = wear
        thr = N.new("ShaderNodeMath"); thr.operation = 'MULTIPLY_ADD'
        L.new(val.outputs[0], thr.inputs[0]); thr.inputs[1].default_value = -0.3; thr.inputs[2].default_value = 0.85
        rng = N.new("ShaderNodeMapRange"); rng.clamp = True
        L.new(nz.outputs["Fac"], rng.inputs[0]); L.new(thr.outputs[0], rng.inputs[1])
        add = N.new("ShaderNodeMath"); add.operation = 'ADD'; add.inputs[1].default_value = 0.02
        L.new(thr.outputs[0], add.inputs[0]); L.new(add.outputs[0], rng.inputs[2])
        rng.inputs[3].default_value, rng.inputs[4].default_value = 1.0, 0.0
        mul = N.new("ShaderNodeMath"); mul.operation = 'MULTIPLY'
        L.new(tex.outputs["Alpha"], mul.inputs[0]); L.new(rng.outputs[0], mul.inputs[1])
        L.new(mul.outputs[0], bsdf.inputs["Alpha"])
        bsdf.inputs["Roughness"].default_value = 0.55
        bsdf.inputs["Metallic"].default_value = 0.0
        if hasattr(mat, "surface_render_method"):
            mat.surface_render_method = 'DITHERED'
        else:  # pragma: no cover - older Blender
            mat.blend_method = 'HASHED'
    else:
        node = mat.node_tree.nodes.get("Usure")
        if node is not None:
            node.outputs[0].default_value = wear
    return mat


def decal_collection(scene):
    col = bpy.data.collections.get(COLLECTION)
    if col is None:
        col = bpy.data.collections.new(COLLECTION)
    if col.name not in scene.collection.children and not any(col.name in c.children for c in scene.collection.children_recursive):
        scene.collection.children.link(col)
    return col


def _decal_mesh(name, uv, w, h, segs=(10, 4)):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=segs[0], y_segments=segs[1], size=0.5)
    for v in bm.verts:
        v.co.x *= w
        v.co.y *= h
    uvl = bm.loops.layers.uv.new("UVMap")
    u0, v0, u1, v1 = uv
    for f in bm.faces:
        for lp in f.loops:
            x = lp.vert.co.x / w + 0.5
            y = lp.vert.co.y / h + 0.5
            lp[uvl].uv = (u0 + (u1 - u0) * x, v0 + (v1 - v0) * y)
    bm.to_mesh(me)
    bm.free()
    return me


# --------------------------------------------------------------------------- placement
class _Target:
    """BVH of an evaluated part in world space (rest pose)."""

    def __init__(self, obj, depsgraph):
        self.obj = obj
        ev = obj.evaluated_get(depsgraph)
        me = ev.to_mesh()
        mw = obj.matrix_world
        verts = [mw @ v.co for v in me.vertices]
        polys = [tuple(p.vertices) for p in me.polygons]
        visor = me.attributes.get("robot_visor")
        self.visor = set()
        if visor is not None and visor.domain == 'FACE':
            self.visor = {i for i, d in enumerate(visor.data) if d.value > 0.5}
        ev.to_mesh_clear()
        self.verts = verts
        self.tree = BVHTree.FromPolygons(verts, polys)

    def cast(self, origin, direction, dist=20.0):
        loc, nrm, idx, d = self.tree.ray_cast(origin, direction, dist)
        if loc is None:
            return None
        if nrm.dot(direction) > 0:
            nrm = -nrm
        return loc, nrm.normalized(), idx, d


def _axes(n, up=Vector((0, 0, 1))):
    if abs(n.dot(up)) > 0.9:
        up = Vector((0, 1, 0))            # horizontal surface: text reads from the back towards the front
    x = up.cross(n).normalized()
    y = n.cross(x).normalized()
    return x, y


def find_spot(target, n_out, w, h, region=None, avoid=(), grid=26, relaxed=False):
    """Best flat spot of size w x h on ``target`` facing ``n_out`` (world). Returns (point, normal) or None."""
    n_out = n_out.normalized()
    ax, ay = _axes(n_out)
    us = [v.dot(ax) for v in target.verts]
    vs = [v.dot(ay) for v in target.verts]
    ds = [v.dot(n_out) for v in target.verts]
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    if region:
        ru0, ru1, rv0, rv1 = region
        u0, u1 = u0 + (u1 - u0) * ru0, u0 + (u1 - u0) * ru1
        v0, v1 = v0 + (v1 - v0) * rv0, v0 + (v1 - v0) * rv1
    far = max(ds) + 1.0
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    best, best_score = None, -1.0
    for i in range(grid):
        for j in range(grid):
            u = u0 + (u1 - u0) * (i + 0.5) / grid
            v = v0 + (v1 - v0) * (j + 0.5) / grid
            o = ax * u + ay * v + n_out * far
            hit = target.cast(o, -n_out)
            if hit is None:
                continue
            p, nrm, idx, _d = hit
            if nrm.dot(n_out) < 0.6 or idx in target.visor:
                continue
            if any((p - q).length < r for q, r in avoid):
                continue
            bx, by = _axes(nrm)
            ok = 0
            total = 0
            for a in (-0.5, -0.25, 0.0, 0.25, 0.5):
                for b in (-0.5, 0.0, 0.5):
                    total += 1
                    q = p + bx * (a * w) + by * (b * h) + nrm * 0.25
                    hq = target.cast(q, -nrm, 0.6)
                    if hq is None:
                        continue
                    if (hq[1].dot(nrm) > (0.75 if relaxed else 0.93) and abs(hq[3] - 0.25) < (0.035 if relaxed else 0.012)
                            and hq[2] not in target.visor):
                        ok += 1
            flat = ok / total
            centred = 1.0 - min(1.0, math.hypot(u - cu, v - cv) / max(u1 - u0, v1 - v0, 1e-6))
            score = flat + 0.15 * centred + 0.1 * nrm.dot(n_out)
            if flat >= 0.8 and score > best_score:
                best, best_score = (p, nrm), score
    return best


def place_set(context, rig, specs, number="num_07", wear=0.3):
    scene = context.scene
    rects = atlas_rects()
    mat = decal_material(wear)
    col = decal_collection(scene)
    old_pos = rig.data.pose_position
    rig.data.pose_position = 'REST'
    context.view_layer.update()
    dg = context.evaluated_depsgraph_get()
    rot = rig.matrix_world.to_3x3().normalized()
    targets, placed, avoid, skipped = {}, [], {}, []
    try:
        for name, pattern, out, width, region in specs:
            both = "{s}" in pattern or "{x}" in out
            for side in ("L", "R") if both else ("",):
                x = 1.0 if side == "L" else -1.0
                tname = pattern.format(s=side)
                obj = bpy.data.objects.get(tname)
                key = name.format(num=number)
                if obj is None or key not in rects:
                    skipped.append(f"{key}@{tname}")
                    continue
                if tname not in targets:
                    targets[tname] = _Target(obj, dg)
                tgt = targets[tname]
                n_out = rot @ Vector([x if c == "{x}" else float(c) for c in out])
                aspect = rects[key]["aspect"]
                w = width
                spot = None
                for attempt in range(6):          # shrink, then accept gently curved surfaces
                    h = w / aspect
                    spot = find_spot(tgt, n_out, w, h, region, avoid.get(tname, []), relaxed=attempt >= 3)
                    if spot:
                        break
                    w *= 0.85
                if spot is None:
                    skipped.append(f"{key}@{tname}")
                    continue
                p, nrm = spot
                h = w / aspect
                ob = _make_decal(scene, col, rig, obj, key, rects[key]["uv"], w, h, p, nrm, mat, side)
                placed.append(ob)
                avoid.setdefault(tname, []).append((p, 0.5 * math.hypot(w, h) + 0.02))
    finally:
        rig.data.pose_position = old_pos
        context.view_layer.update()
    return placed, skipped


def _make_decal(scene, col, rig, target, key, uv, w, h, p, nrm, mat, side):
    name = f"DEC-{key}{'.' + side if side else ''}"
    me = _decal_mesh(name, uv, w, h)
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    col.objects.link(ob)
    ob[TAG] = True
    ob.visible_shadow = False
    bx, by = _axes(nrm)
    m = Matrix.Identity(4)
    m.col[0].xyz, m.col[1].xyz, m.col[2].xyz = bx, by, nrm
    m.translation = p + nrm * OFFSET
    if target.parent == rig and target.parent_type == 'BONE':
        ob.parent = rig
        ob.parent_type = 'BONE'
        ob.parent_bone = target.parent_bone
    else:
        ob.parent = target
    bpy.context.view_layer.update()
    ob.matrix_world = m
    sw = ob.modifiers.new("Plaquage", 'SHRINKWRAP')
    sw.target = target
    sw.wrap_method = 'PROJECT'
    sw.use_project_z = True
    sw.use_negative_direction = True
    sw.use_positive_direction = True
    sw.project_limit = 0.05
    sw.wrap_mode = 'ON_SURFACE'
    sw.offset = OFFSET
    return ob


def remove_all():
    n = 0
    for o in [o for o in bpy.data.objects if o.get(TAG)]:
        me = o.data
        bpy.data.objects.remove(o)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)
        n += 1
    return n


class ROBOT_OT_decals(Operator):
    """Poser des marquages militaires (numéros, insignes, avertissements, plaques) sur les zones planes du robot"""
    bl_idname = "robot.decals"
    bl_label = "Marquages"
    bl_options = {'REGISTER', 'UNDO'}

    remove: bpy.props.BoolProperty(name="Retirer", default=False)

    @classmethod
    def poll(cls, context):
        return R.find_rig(context) is not None

    def execute(self, context):
        n_old = remove_all()
        if self.remove:
            self.report({'INFO'}, f"{n_old} marquage(s) retiré(s)")
            return {'FINISHED'}
        rig = R.find_rig(context)
        s = context.scene.robot_anim
        specs = FULL if s.decal_set == 'FULL' else LIGHT
        placed, skipped = place_set(context, rig, specs, s.decal_number, s.decal_wear)
        msg = f"{len(placed)} marquage(s) posé(s)"
        if skipped:
            msg += f", {len(skipped)} sans place libre ({', '.join(skipped[:3])}…)"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


classes = (ROBOT_OT_decals,)
