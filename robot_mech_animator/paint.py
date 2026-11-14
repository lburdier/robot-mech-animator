"""Military PBR paint for the robot's materials.

A node group (``PBR_Militaire_Robot``) takes over Base Color, Metallic, Roughness and
Coat of each Principled BSDF and rebuilds a military finish from the original textures:

* panels that were painted in the original texture (saturated colours) get the camouflage,
  modulated by the original brightness so dirt, edges and details stay visible;
* the dark parts of the original texture become dark gunmetal;
* paint wear: fine scratches and chips that expose bright steel;
* grime: dirt in the cavities (ambient occlusion) and vertical streaks;
* per part, through the object property ``robot_mat``: 1 = bare gunmetal (gatlings,
  guns), 2 = polished chrome (piston rods);
* the visor (face attribute ``robot_visor``) is glossy black with a clear coat.

Emission and normal maps are left untouched. Removing the paint restores the original
links and values exactly.
"""
import bmesh
import bpy
from bpy.types import Operator
from mathutils import Vector

from . import rig as R

GROUP = "PBR_Militaire_Robot"
LEGACY_GROUP = "Peinture_Militaire_Robot"
TAG = "robot_paint"
SOCKETS = (("Base Color", "Couleur d'origine", "Couleur"), ("Metallic", "Métal d'origine", "Métal"),
           ("Roughness", "Rugosité d'origine", "Rugosité"), ("Coat Weight", "Vernis d'origine", "Vernis"))
PART_PROP = "robot_mat"
VISOR_ATTR = "robot_visor"
BARE, CHROME = 1, 2

SCHEMES = {
    # name: (colours 1..4 linear RGB, pattern scale, description)
    'WOODLAND': ([(0.042, 0.052, 0.024), (0.105, 0.115, 0.055), (0.07, 0.045, 0.022), (0.012, 0.012, 0.010)], 1.0,
                 "Camouflage forêt (vert, brun, noir)"),
    'DESERT': ([(0.36, 0.26, 0.14), (0.25, 0.18, 0.09), (0.45, 0.36, 0.22), (0.16, 0.11, 0.06)], 0.8,
               "Camouflage désert (sable, ocre)"),
    'URBAN': ([(0.16, 0.165, 0.17), (0.075, 0.078, 0.082), (0.30, 0.30, 0.30), (0.03, 0.03, 0.032)], 1.2,
              "Camouflage urbain (gris)"),
    'OD': ([(0.07, 0.08, 0.035), (0.065, 0.075, 0.032), (0.075, 0.085, 0.038), (0.06, 0.07, 0.03)], 1.0,
           "Vert olive uni (OTAN)"),
    'ARCTIC': ([(0.62, 0.64, 0.66), (0.42, 0.44, 0.46), (0.75, 0.76, 0.78), (0.20, 0.21, 0.22)], 1.0,
               "Camouflage hiver (blanc, gris)"),
}
SCHEME_ITEMS = [(k, {'WOODLAND': "Forêt", 'DESERT': "Désert", 'URBAN': "Urbain", 'OD': "Vert olive",
                     'ARCTIC': "Hiver"}[k], v[2]) for k, v in SCHEMES.items()]
METAL_ITEMS = [
    ('GUNS', "Canons", "Gatlings et canons en métal nu, nacelles peintes"),
    ('ALL', "Tourelles entières", "Canons, nacelles lance-roquettes et épaules en métal nu"),
    ('NONE', "Aucune", "Tout est peint"),
]


# =========================================================================== node group
class _G:
    """Small helper to write shader node graphs."""

    def __init__(self, ng):
        self.ng, self.N, self.L = ng, ng.nodes, ng.links

    def link(self, src, dst):
        if isinstance(src, (int, float)):
            dst.default_value = src
        elif isinstance(src, tuple):
            dst.default_value = src if len(src) == 4 or dst.type != 'RGBA' else src + (1.0,)
        else:
            self.L.new(src, dst)

    def math(self, op, a, b=None, clamp=False):
        n = self.N.new("ShaderNodeMath")
        n.operation, n.use_clamp = op, clamp
        self.link(a, n.inputs[0])
        if b is not None:
            self.link(b, n.inputs[1])
        return n.outputs[0]

    def remap(self, v, a, b, c=0.0, d=1.0):
        n = self.N.new("ShaderNodeMapRange")
        n.clamp = True
        self.link(v, n.inputs[0])
        for i, x in enumerate((a, b, c, d)):
            n.inputs[1 + i].default_value = x
        return n.outputs[0]

    def mixf(self, a, b, fac):
        n = self.N.new("ShaderNodeMix")
        n.data_type = 'FLOAT'
        n.clamp_factor = True
        ins = [s for s in n.inputs if s.type == 'VALUE']          # Factor, A, B
        self.link(fac, ins[0]); self.link(a, ins[1]); self.link(b, ins[2])
        return [s for s in n.outputs if s.type == 'VALUE'][0]

    def mixc(self, a, b, fac, blend='MIX'):
        n = self.N.new("ShaderNodeMix")
        n.data_type, n.blend_type, n.clamp_factor = 'RGBA', blend, True
        self.link(fac, n.inputs["Factor"])
        rgba = [s for s in n.inputs if s.type == 'RGBA']
        self.link(a, rgba[0]); self.link(b, rgba[1])
        return n.outputs["Result"]

    def noise(self, vec, scale, detail=2.0, w=0.0, dims='4D'):
        n = self.N.new("ShaderNodeTexNoise")
        n.noise_dimensions = dims
        n.inputs["Scale"].default_value = scale
        n.inputs["Detail"].default_value = detail
        if "W" in n.inputs:
            n.inputs["W"].default_value = w
        self.link(vec, n.inputs["Vector"])
        return n

    def attr(self, name, kind='GEOMETRY'):
        n = self.N.new("ShaderNodeAttribute")
        n.attribute_type, n.attribute_name = kind, name
        return n.outputs["Fac"]


_DEBUG = None


def _group():
    ng = bpy.data.node_groups.get(GROUP)
    if ng:
        return ng
    ng = bpy.data.node_groups.new(GROUP, 'ShaderNodeTree')
    io = ng.interface
    io.new_socket("Couleur d'origine", in_out='INPUT', socket_type='NodeSocketColor')
    for name in ("Métal d'origine", "Rugosité d'origine", "Vernis d'origine"):
        io.new_socket(name, in_out='INPUT', socket_type='NodeSocketFloat')
    for i in range(4):
        s = io.new_socket(f"Teinte {i + 1}", in_out='INPUT', socket_type='NodeSocketColor')
        s.default_value = SCHEMES['WOODLAND'][0][i] + (1.0,)
    for name, val in (("Échelle", 1.0), ("Intensité", 0.9), ("Usure", 0.35), ("Graine", 0.0), ("Saleté", 0.5)):
        s = io.new_socket(name, in_out='INPUT', socket_type='NodeSocketFloat')
        s.default_value = val
    for name, kind in (("Couleur", 'NodeSocketColor'), ("Métal", 'NodeSocketFloat'), ("Rugosité", 'NodeSocketFloat'),
                       ("Vernis", 'NodeSocketFloat'), ("Peinture", 'NodeSocketFloat')):
        io.new_socket(name, in_out='OUTPUT', socket_type=kind)
    g = _G(ng)
    N = g.N
    gi, go = N.new("NodeGroupInput"), N.new("NodeGroupOutput")
    I = gi.outputs
    orig = I["Couleur d'origine"]

    # ---- camouflage pattern (object space, scaled, seeded)
    tc = N.new("ShaderNodeTexCoord")
    sc = N.new("ShaderNodeVectorMath"); sc.operation = 'SCALE'
    g.link(tc.outputs["Object"], sc.inputs[0])
    g.link(g.math('MULTIPLY', I["Échelle"], 0.55), sc.inputs["Scale"])
    comb = N.new("ShaderNodeCombineXYZ")
    g.link(I["Graine"], comb.inputs[0]); g.link(I["Graine"], comb.inputs[2])
    off = N.new("ShaderNodeVectorMath"); off.operation = 'ADD'
    g.link(sc.outputs[0], off.inputs[0]); g.link(comb.outputs[0], off.inputs[1])
    pos = off.outputs[0]

    def blob(scale, detail, w):
        n = g.noise(pos, scale, detail, w)
        n.inputs["Roughness"].default_value = 0.55
        if "Distortion" in n.inputs:
            n.inputs["Distortion"].default_value = 0.35
        return n.outputs["Fac"]

    def step(fac, edge):
        return g.remap(fac, edge - 0.012, edge + 0.012)

    camo = g.mixc(I["Teinte 1"], I["Teinte 2"], step(blob(1.6, 3.0, 0.0), 0.5))
    camo = g.mixc(camo, I["Teinte 3"], step(blob(2.1, 3.0, 4.2), 0.56))
    camo = g.mixc(camo, I["Teinte 4"], step(blob(2.8, 2.0, 9.7), 0.62))

    # ---- what was painted in the original texture (saturated colours)
    bw = N.new("ShaderNodeRGBToBW"); g.link(orig, bw.inputs[0])
    lum = bw.outputs[0]
    hsv = N.new("ShaderNodeSeparateColor"); hsv.mode = 'HSV'; g.link(orig, hsv.inputs[0])
    paintable = g.math('MULTIPLY', g.remap(hsv.outputs[1], 0.35, 0.6), g.remap(hsv.outputs[2], 0.008, 0.025))

    # ---- parts
    part = g.attr(PART_PROP, 'OBJECT')
    bare = g.math('MULTIPLY', g.math('GREATER_THAN', part, 0.5), g.math('LESS_THAN', part, 1.5))
    chrome = g.math('GREATER_THAN', part, 1.5)
    visor = g.math('MINIMUM', g.attr(VISOR_ATTR), 1.0)
    not_metal = g.math('SUBTRACT', 1.0, g.math('ADD', bare, chrome), clamp=True)

    # ---- wear: fine scratches + chips, both scaled by "Usure"
    scr = g.noise(tc.outputs["Object"], 28.0, 10.0, dims='3D').outputs["Fac"]
    t1 = g.math('SUBTRACT', 0.78, g.math('MULTIPLY', I["Usure"], 0.16))
    wm = N.new("ShaderNodeMapRange"); wm.clamp = True
    g.link(scr, wm.inputs[0]); g.link(t1, wm.inputs[1]); g.link(g.math('ADD', t1, 0.03), wm.inputs[2])
    chip_n = g.noise(pos, 7.0, 6.0, 2.3).outputs["Fac"]
    t2 = g.math('SUBTRACT', 0.80, g.math('MULTIPLY', I["Usure"], 0.22))
    cm = N.new("ShaderNodeMapRange"); cm.clamp = True
    g.link(chip_n, cm.inputs[0]); g.link(t2, cm.inputs[1]); g.link(g.math('ADD', t2, 0.015), cm.inputs[2])
    wear = g.math('MAXIMUM', wm.outputs[0], cm.outputs[0])
    paint_f = g.math('MULTIPLY', g.math('MULTIPLY', paintable, I["Intensité"]),
                     g.math('MULTIPLY', g.math('SUBTRACT', 1.0, wear), not_metal))
    worn_f = g.math('MULTIPLY', g.math('MULTIPLY', paintable, wear), not_metal)

    # ---- grime: cavities (AO) + vertical streaks
    ao = N.new("ShaderNodeAmbientOcclusion")
    ao.samples, ao.only_local = 8, True
    ao.inputs["Distance"].default_value = 0.25
    aobw = N.new("ShaderNodeRGBToBW"); g.link(ao.outputs["Color"], aobw.inputs[0])
    cav = g.remap(aobw.outputs[0], 0.2, 0.7, 1.0, 0.0)
    stretch = N.new("ShaderNodeVectorMath"); stretch.operation = 'MULTIPLY'
    g.link(tc.outputs["Object"], stretch.inputs[0]); stretch.inputs[1].default_value = (6.0, 6.0, 0.6)
    streak = g.remap(g.noise(stretch.outputs[0], 1.0, 4.0, dims='3D').outputs["Fac"], 0.58, 0.76)
    grime = g.math('MULTIPLY', g.math('ADD', g.math('MULTIPLY', cav, 0.7), g.math('MULTIPLY', streak, 0.22)),
                   I["Saleté"], clamp=True)
    grime = g.math('MULTIPLY', grime, g.math('SUBTRACT', 1.0, g.math('MULTIPLY', chrome, 0.7)))

    # ---- colours
    det = g.remap(lum, 0.0, 0.11, 0.4, 1.3)        # linear luminance of the yellow panels
    det_c = N.new("ShaderNodeCombineColor")
    for k in range(3):
        g.link(det, det_c.inputs[k])
    paint_col = g.mixc(camo, det_c.outputs[0], 1.0, 'MULTIPLY')
    steel_det = N.new("ShaderNodeCombineColor")
    sd = g.remap(lum, 0.0, 0.5, 0.6, 1.2)
    for k in range(3):
        g.link(sd, steel_det.inputs[k])
    steel_col = g.mixc((0.42, 0.42, 0.43, 1.0), steel_det.outputs[0], 1.0, 'MULTIPLY')
    gun = g.mixc((0.075, 0.077, 0.083, 1.0), (0.34, 0.34, 0.35, 1.0), g.math('MULTIPLY', wm.outputs[0], 0.8))
    gun_det = N.new("ShaderNodeCombineColor")
    gd = g.remap(lum, 0.0, 0.5, 0.7, 1.6)
    for k in range(3):
        g.link(gd, gun_det.inputs[k])
    gun_col = g.mixc(gun, gun_det.outputs[0], 1.0, 'MULTIPLY')

    col = g.mixc(orig, steel_col, worn_f)
    col = g.mixc(col, paint_col, paint_f)
    col = g.mixc(col, gun_col, bare)
    col = g.mixc(col, (0.78, 0.78, 0.80, 1.0), chrome)
    dirt = g.mixc((1.0, 1.0, 1.0, 1.0), (0.42, 0.37, 0.3, 1.0), grime)
    col = g.mixc(col, dirt, 1.0, 'MULTIPLY')
    col = g.mixc(col, (0.003, 0.004, 0.005, 1.0), visor)

    # ---- metallic
    m = g.mixf(I["Métal d'origine"], 0.85, 0.5)
    m = g.mixf(m, 1.0, worn_f)
    m = g.mixf(m, 0.0, paint_f)
    m = g.mixf(m, 1.0, bare)
    m = g.mixf(m, 1.0, chrome)
    m = g.mixf(m, 0.0, g.math('MULTIPLY', grime, 0.6))
    m = g.mixf(m, 0.0, visor)

    # ---- roughness
    rn = g.remap(g.noise(tc.outputs["Object"], 14.0, 4.0, dims='3D').outputs["Fac"], 0.3, 0.7, -0.08, 0.08)
    r = g.math('ADD', g.math('MULTIPLY', I["Rugosité d'origine"], 0.6), 0.18)
    r = g.mixf(r, 0.3, worn_f)
    r = g.mixf(r, g.math('ADD', 0.62, rn), paint_f)
    r = g.mixf(r, g.math('ADD', 0.28, rn), bare)
    r = g.mixf(r, 0.12, chrome)
    r = g.mixf(r, 0.9, g.math('MULTIPLY', grime, 0.7))
    r = g.mixf(r, 0.03, visor)

    coat = g.mixf(I["Vernis d'origine"], 1.0, visor)
    g.link(col, go.inputs["Couleur"])
    g.link(m, go.inputs["Métal"])
    g.link(r, go.inputs["Rugosité"])
    g.link(coat, go.inputs["Vernis"])
    dbg = {"paintable": paintable, "wear": wear, "bare": bare, "notmetal": not_metal, "visor": visor,
           "sat": hsv.outputs[1], "val": hsv.outputs[2], "part": part}.get(_DEBUG)
    g.link(dbg if dbg is not None else paint_f, go.inputs["Peinture"])
    return ng


# =========================================================================== parts
def robot_meshes(rig):
    return [o for o in bpy.data.objects if o.type == 'MESH' and o.parent == rig and not o.get("robot_fx")
            and not o.get("robot_decal")]


def robot_materials(rig):
    mats = []
    for o in robot_meshes(rig):
        for slot in o.material_slots:
            m = slot.material
            if m and m.use_nodes and m not in mats:
                mats.append(m)
    return mats


def part_kind(name, turret_metal='GUNS'):
    base = name.split(".")[0]
    if base == "GEO-piston_rod":
        return CHROME
    if turret_metal == 'NONE':
        return 0
    if base in ("GEO-gatling", "GEO-turret"):
        return BARE
    if turret_metal == 'ALL' and base in ("GEO-shoulder", "GEO-shoulder_hub"):
        return BARE
    return 0


def mark_visor(rig):
    """Face attribute ``robot_visor`` = 1 on the front plate of the head (the mesh island hit
    straight ahead at the head's height). Returns the number of faces marked."""
    tor = bpy.data.objects.get("GEO-torso")
    if tor is None or tor.type != 'MESH':
        return 0
    me = tor.data
    # work in the armature's rest space: front = -Y, centre x = 0, whatever the robot's pose/placement
    old_pos = rig.data.pose_position
    rig.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    mw = rig.matrix_world.inverted() @ tor.matrix_world
    rig.data.pose_position = old_pos
    bpy.context.view_layer.update()
    inv = mw.inverted()
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    from mathutils.bvhtree import BVHTree
    tree = BVHTree.FromBMesh(bm)
    co = [mw @ v.co for v in bm.verts]
    zmin, zmax = min(c.z for c in co), max(c.z for c in co)
    ymin = min(c.y for c in co)
    hits = {}
    for k in range(9):
        z = zmin + (zmax - zmin) * (0.35 + 0.05 * k)
        o = inv @ Vector((0.0, ymin - 5.0, z))
        d = (inv.to_3x3() @ Vector((0, 1, 0))).normalized()
        loc, nrm, fi, _dist = tree.ray_cast(o, d)
        if fi is not None:
            hits[fi] = hits.get(fi, 0) + 1
    if not hits:
        bm.free()
        return 0
    # island of each hit face, keep the one hit most often
    best, best_n = None, 0
    seen_islands = []
    for fi, n in hits.items():
        if any(fi in isl for isl in seen_islands):
            continue
        isl, stack = {fi}, [bm.faces[fi]]
        while stack:
            f = stack.pop()
            for e in f.edges:
                for h in e.link_faces:
                    if h.index not in isl:
                        isl.add(h.index)
                        stack.append(h)
        seen_islands.append(isl)
        score = sum(c for f2, c in hits.items() if f2 in isl)
        width = max((mw @ bm.faces[i].calc_center_median()).x for i in isl) - min(
            (mw @ bm.faces[i].calc_center_median()).x for i in isl)
        if score > best_n and width < 1.2 and len(isl) < len(bm.faces) * 0.2:
            best, best_n = isl, score
    bm.free()
    if best is None:
        return 0
    attr = me.attributes.get(VISOR_ATTR)
    if attr is None:
        attr = me.attributes.new(VISOR_ATTR, 'FLOAT', 'FACE')
    vals = [1.0 if i in best else 0.0 for i in range(len(me.polygons))]
    attr.data.foreach_set("value", vals)
    me.update()
    return len(best)


def prepare_parts(rig, turret_metal='GUNS'):
    counts = {BARE: 0, CHROME: 0}
    for o in robot_meshes(rig):
        k = part_kind(o.name, turret_metal)
        o[PART_PROP] = k
        if k:
            counts[k] += 1
    return counts, mark_visor(rig)


# =========================================================================== apply / remove
def _principled(mat):
    for n in mat.node_tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            return n
    return None


def apply(mat, scheme='WOODLAND', strength=0.9, wear=0.35, scale=1.0, seed=0.0, grime=0.5):
    nt = mat.node_tree
    bsdf = _principled(mat)
    if bsdf is None:
        return False
    remove(mat)
    g = nt.nodes.new("ShaderNodeGroup")
    g.node_tree = _group()
    g.name = g.label = TAG
    g.location = bsdf.location.x - 280, bsdf.location.y + 120
    g.width = 220
    for bsdf_in, g_in, g_out in SOCKETS:
        sock = bsdf.inputs.get(bsdf_in)
        if sock is None:
            continue
        if sock.links:
            nt.links.new(sock.links[0].from_socket, g.inputs[g_in])
        else:
            v = sock.default_value
            g.inputs[g_in].default_value = tuple(v) if hasattr(v, "__len__") else v
        nt.links.new(g.outputs[g_out], sock)
    cols, sc, _d = SCHEMES[scheme]
    for i in range(4):
        g.inputs[f"Teinte {i + 1}"].default_value = cols[i] + (1.0,)
    g.inputs["Échelle"].default_value = scale * sc
    g.inputs["Intensité"].default_value = strength
    g.inputs["Usure"].default_value = wear
    g.inputs["Graine"].default_value = seed
    g.inputs["Saleté"].default_value = grime
    return True


def _remove_legacy(mat):
    """Paint of versions 1.1 / 1.2 (group on Base Color + two Mix nodes)."""
    nt = mat.node_tree
    bsdf = _principled(mat)
    found = False
    for name, target in (("met", "Metallic"), ("rough", "Roughness")):
        n = nt.nodes.get(f"{TAG}_{name}")
        if n is None:
            continue
        found = True
        idx = n.get("orig_index", 2)
        orig = n.inputs[idx].links[0].from_socket if n.inputs[idx].links else None
        val = n.inputs[idx].default_value
        nt.nodes.remove(n)
        if bsdf is not None:
            if orig is not None:
                nt.links.new(orig, bsdf.inputs[target])
            else:
                bsdf.inputs[target].default_value = val
    return found


def remove(mat):
    """Take the paint off and restore the original links and values."""
    nt = mat.node_tree
    bsdf = _principled(mat)
    legacy = _remove_legacy(mat)
    g = nt.nodes.get(TAG)
    if g is None:
        return legacy
    is_new = g.node_tree is not None and g.node_tree.name.startswith(GROUP)
    if bsdf is not None:
        if is_new:
            for bsdf_in, g_in, _g_out in SOCKETS:
                sock, gsock = bsdf.inputs.get(bsdf_in), g.inputs.get(g_in)
                if sock is None or gsock is None:
                    continue
                if gsock.links:
                    nt.links.new(gsock.links[0].from_socket, sock)
                else:
                    for l in list(sock.links):
                        nt.links.remove(l)
                    v = gsock.default_value
                    sock.default_value = tuple(v) if hasattr(v, "__len__") else v
        else:
            src = g.inputs[0].links[0].from_socket if g.inputs[0].links else None
            if src is not None:
                nt.links.new(src, bsdf.inputs["Base Color"])
    nt.nodes.remove(g)
    return True


class ROBOT_OT_military_paint(Operator):
    """Peinture militaire PBR : camouflage sur les panneaux, métal nu sur les canons, visière noire, usure et saleté"""
    bl_idname = "robot.military_paint"
    bl_label = "Peinture militaire"
    bl_options = {'REGISTER', 'UNDO'}

    remove_paint: bpy.props.BoolProperty(name="Retirer", default=False)

    @classmethod
    def poll(cls, context):
        return R.find_rig(context) is not None

    def execute(self, context):
        rig = R.find_rig(context)
        s = context.scene.robot_anim
        mats = robot_materials(rig)
        n = 0
        if not self.remove_paint:
            counts, visor = prepare_parts(rig, s.paint_turret_metal)
        for m in mats:
            if self.remove_paint:
                n += bool(remove(m))
            else:
                n += bool(apply(m, s.paint_scheme, s.paint_strength, s.paint_wear, s.paint_scale, s.paint_seed,
                                s.paint_grime))
        if self.remove_paint:
            self.report({'INFO'}, f"Peinture retirée de {n} matériau(x)")
        else:
            label = dict((k, l) for k, l, _d in SCHEME_ITEMS)[s.paint_scheme]
            self.report({'INFO'}, f"Peinture « {label} » sur {n} matériau(x) : {counts[BARE]} pièce(s) en métal nu, "
                                  f"{counts[CHROME]} tige(s) chromée(s), visière {'trouvée' if visor else 'introuvable'}")
        return {'FINISHED'}


classes = (ROBOT_OT_military_paint,)
