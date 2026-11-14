"""Military paint for the robot's materials.

A node group is inserted between the original base colour and the Principled BSDF:
the camouflage colours are modulated by the brightness of the original texture,
so dirt, edges, decals and wear stay visible, and the paint is made more matte and
less metallic. Bare-metal scratches come back through a wear mask. Fully reversible.
"""
import bpy
from bpy.types import Operator

from . import rig as R

GROUP = "Peinture_Militaire_Robot"
TAG = "robot_paint"

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


def _group():
    ng = bpy.data.node_groups.get(GROUP)
    if ng:
        return ng
    ng = bpy.data.node_groups.new(GROUP, 'ShaderNodeTree')
    io = ng.interface
    io.new_socket("Couleur d'origine", in_out='INPUT', socket_type='NodeSocketColor')
    for i in range(4):
        s = io.new_socket(f"Teinte {i + 1}", in_out='INPUT', socket_type='NodeSocketColor')
        s.default_value = SCHEMES['WOODLAND'][0][i] + (1.0,)
    s = io.new_socket("Échelle", in_out='INPUT', socket_type='NodeSocketFloat')
    s.default_value = 1.0
    s = io.new_socket("Intensité", in_out='INPUT', socket_type='NodeSocketFloat')
    s.default_value = 0.9
    s.min_value, s.max_value = 0.0, 1.0
    s = io.new_socket("Usure", in_out='INPUT', socket_type='NodeSocketFloat')
    s.default_value = 0.35
    s.min_value, s.max_value = 0.0, 1.0
    s = io.new_socket("Graine", in_out='INPUT', socket_type='NodeSocketFloat')
    io.new_socket("Couleur", in_out='OUTPUT', socket_type='NodeSocketColor')
    io.new_socket("Peinture", in_out='OUTPUT', socket_type='NodeSocketFloat')
    N, L = ng.nodes, ng.links
    gi, go = N.new("NodeGroupInput"), N.new("NodeGroupOutput")
    tc = N.new("ShaderNodeTexCoord")
    sc = N.new("ShaderNodeVectorMath"); sc.operation = 'SCALE'
    L.new(tc.outputs["Object"], sc.inputs[0])
    k = N.new("ShaderNodeMath"); k.operation = 'MULTIPLY'; k.inputs[1].default_value = 0.55
    L.new(gi.outputs["Échelle"], k.inputs[0]); L.new(k.outputs[0], sc.inputs["Scale"])
    off = N.new("ShaderNodeVectorMath"); off.operation = 'ADD'
    L.new(sc.outputs[0], off.inputs[0])
    comb = N.new("ShaderNodeCombineXYZ")
    L.new(gi.outputs["Graine"], comb.inputs[0]); L.new(gi.outputs["Graine"], comb.inputs[2])
    L.new(comb.outputs[0], off.inputs[1])

    def blob(scale, detail, w):
        n = N.new("ShaderNodeTexNoise")
        n.noise_dimensions = '4D'
        n.inputs["Scale"].default_value = scale
        n.inputs["Detail"].default_value = detail
        n.inputs["Roughness"].default_value = 0.55
        n.inputs["W"].default_value = w
        if "Distortion" in n.inputs:
            n.inputs["Distortion"].default_value = 0.35
        L.new(off.outputs[0], n.inputs["Vector"])
        return n.outputs["Fac"]

    def step(fac, edge):
        r = N.new("ShaderNodeMapRange")
        r.inputs[1].default_value = edge - 0.012
        r.inputs[2].default_value = edge + 0.012
        L.new(fac, r.inputs[0])
        return r.outputs[0]

    def mix(a, b, fac):
        m = N.new("ShaderNodeMix"); m.data_type = 'RGBA'
        L.new(fac, m.inputs["Factor"])
        for sock, v in (("A", a), ("B", b)):
            L.new(v, m.inputs[sock])
        return m.outputs["Result"]

    c = mix(gi.outputs["Teinte 1"], gi.outputs["Teinte 2"], step(blob(1.6, 3.0, 0.0), 0.5))
    c = mix(c, gi.outputs["Teinte 3"], step(blob(2.1, 3.0, 4.2), 0.56))
    c = mix(c, gi.outputs["Teinte 4"], step(blob(2.8, 2.0, 9.7), 0.62))
    # keep the detail of the original texture: camo * (luminance / mean)
    bw = N.new("ShaderNodeRGBToBW")
    L.new(gi.outputs["Couleur d'origine"], bw.inputs[0])
    det = N.new("ShaderNodeMapRange")
    det.inputs[1].default_value = 0.0; det.inputs[2].default_value = 0.45
    det.inputs[3].default_value = 0.25; det.inputs[4].default_value = 1.35
    L.new(bw.outputs[0], det.inputs[0])
    shade = N.new("ShaderNodeMix"); shade.data_type = 'RGBA'; shade.blend_type = 'MULTIPLY'
    shade.inputs["Factor"].default_value = 1.0
    L.new(c, shade.inputs["A"]); L.new(det.outputs[0], shade.inputs["B"])
    # wear: small scratches where the original metal shows
    wn = N.new("ShaderNodeTexNoise"); wn.inputs["Scale"].default_value = 28.0; wn.inputs["Detail"].default_value = 10.0
    L.new(tc.outputs["Object"], wn.inputs["Vector"])
    wthr = N.new("ShaderNodeMath"); wthr.operation = 'SUBTRACT'; wthr.inputs[0].default_value = 0.78
    wsc = N.new("ShaderNodeMath"); wsc.operation = 'MULTIPLY'; wsc.inputs[1].default_value = 0.16
    L.new(gi.outputs["Usure"], wsc.inputs[0]); L.new(wsc.outputs[0], wthr.inputs[1])
    wm = N.new("ShaderNodeMapRange")
    L.new(wn.outputs["Fac"], wm.inputs[0]); L.new(wthr.outputs[0], wm.inputs[1])
    add = N.new("ShaderNodeMath"); add.operation = 'ADD'; add.inputs[1].default_value = 0.03
    L.new(wthr.outputs[0], add.inputs[0]); L.new(add.outputs[0], wm.inputs[2])
    paint = N.new("ShaderNodeMath"); paint.operation = 'MULTIPLY'
    inv = N.new("ShaderNodeMath"); inv.operation = 'SUBTRACT'; inv.inputs[0].default_value = 1.0
    L.new(wm.outputs[0], inv.inputs[1])
    L.new(inv.outputs[0], paint.inputs[0]); L.new(gi.outputs["Intensité"], paint.inputs[1])
    final = N.new("ShaderNodeMix"); final.data_type = 'RGBA'
    L.new(paint.outputs[0], final.inputs["Factor"])
    L.new(gi.outputs["Couleur d'origine"], final.inputs["A"]); L.new(shade.outputs["Result"], final.inputs["B"])
    L.new(final.outputs["Result"], go.inputs["Couleur"])
    L.new(paint.outputs[0], go.inputs["Peinture"])
    return ng


def robot_materials(rig):
    mats = []
    for o in bpy.data.objects:
        if o.type == 'MESH' and o.parent == rig and not o.get("robot_fx"):
            for slot in o.material_slots:
                m = slot.material
                if m and m.use_nodes and m not in mats:
                    mats.append(m)
    return mats


def _principled(mat):
    for n in mat.node_tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            return n
    return None


def apply(mat, scheme='WOODLAND', strength=0.9, wear=0.35, scale=1.0, seed=0.0):
    nt = mat.node_tree
    bsdf = _principled(mat)
    if bsdf is None:
        return False
    remove(mat)
    base = bsdf.inputs["Base Color"]
    src = base.links[0].from_socket if base.links else None
    g = nt.nodes.new("ShaderNodeGroup")
    g.node_tree = _group()
    g.name = g.label = TAG
    g.location = bsdf.location.x - 260, bsdf.location.y + 160
    if src is not None:
        nt.links.new(src, g.inputs[0])
    else:
        g.inputs[0].default_value = base.default_value
    cols, sc, _d = SCHEMES[scheme]
    for i in range(4):
        g.inputs[1 + i].default_value = cols[i] + (1.0,)
    g.inputs["Échelle"].default_value = scale * sc
    g.inputs["Intensité"].default_value = strength
    g.inputs["Usure"].default_value = wear
    g.inputs["Graine"].default_value = seed
    nt.links.new(g.outputs["Couleur"], base)
    # painted metal is matte: metallic * (1 - paint), roughness towards 0.62
    for name, target, op in (("met", "Metallic", 'MET'), ("rough", "Roughness", 'ROUGH')):
        sock = bsdf.inputs[target]
        orig = sock.links[0].from_socket if sock.links else None
        m = nt.nodes.new("ShaderNodeMix")
        m.data_type = 'FLOAT'
        m.name = m.label = f"{TAG}_{name}"
        m.location = bsdf.location.x - 260, bsdf.location.y - (80 if name == "met" else 200)
        # Mix(FLOAT) inputs: 0 Factor, 2 A, 3 B (float variants)
        fa = [s for s in m.inputs if s.name == "A" and s.type == 'VALUE'][0]
        fb = [s for s in m.inputs if s.name == "B" and s.type == 'VALUE'][0]
        if orig is not None:
            nt.links.new(orig, fa)
        else:
            fa.default_value = sock.default_value
        fb.default_value = 0.08 if op == 'MET' else 0.62
        nt.links.new(g.outputs["Peinture"], m.inputs["Factor"])
        out = [s for s in m.outputs if s.type == 'VALUE'][0]
        nt.links.new(out, sock)
    # remember which Mix input holds the original value (used by remove())
    for name in ("met", "rough"):
        n = nt.nodes[f"{TAG}_{name}"]
        fa = [s for s in n.inputs if s.name == "A" and s.type == 'VALUE'][0]
        n["orig_index"] = list(n.inputs).index(fa)
    return True


def remove(mat):
    """Take the paint off and restore the original links."""
    nt = mat.node_tree
    bsdf = _principled(mat)
    for name, target in (("met", "Metallic"), ("rough", "Roughness")):
        n = nt.nodes.get(f"{TAG}_{name}")
        if n is None:
            continue
        idx = n.get("orig_index", 2)
        orig = n.inputs[idx].links[0].from_socket if n.inputs[idx].links else None
        val = n.inputs[idx].default_value
        nt.nodes.remove(n)
        if bsdf is not None:
            if orig is not None:
                nt.links.new(orig, bsdf.inputs[target])
            else:
                bsdf.inputs[target].default_value = val
    g = nt.nodes.get(TAG)
    if g is None:
        return False
    src = g.inputs[0].links[0].from_socket if g.inputs[0].links else None
    nt.nodes.remove(g)
    if src is not None and bsdf is not None:
        nt.links.new(src, bsdf.inputs["Base Color"])
    return True



class ROBOT_OT_military_paint(Operator):
    """Appliquer une peinture militaire (camouflage) sur les matériaux du robot, sans perdre les détails des textures"""
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
        for m in mats:
            if self.remove_paint:
                n += bool(remove(m))
            else:
                n += bool(apply(m, s.paint_scheme, s.paint_strength, s.paint_wear, s.paint_scale, s.paint_seed))
        if self.remove_paint:
            self.report({'INFO'}, f"Peinture retirée de {n} matériau(x)")
        else:
            self.report({'INFO'}, f"Peinture « {dict((k, l) for k, l, _d in SCHEME_ITEMS)[s.paint_scheme]} » appliquée sur {n} matériau(x)")
        return {'FINISHED'}


classes = (ROBOT_OT_military_paint,)
