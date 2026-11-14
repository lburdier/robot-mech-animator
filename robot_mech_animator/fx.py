"""Weapon effects: gatling muzzle flashes, rockets with exhaust flame, volumetric
smoke trails and impact explosions.

Everything is keyframed (no simulation cache): scrub the timeline and the effects
follow. Smoke is a point cloud whose points carry their birth frame; a Geometry
Nodes modifier ages them with the scene time (drift, rise, turbulence, growth,
fade) and instances small volumetric puffs, so it renders in Cycles and EEVEE
and needs no baking.
"""
import math
import random

import bpy
from mathutils import Matrix, Vector

from . import animdata, rig as R

FX_COLLECTION = "FX_Robot"
SMOKE_GROUP = "FX_Fumee_Robot"


# =========================================================================== helpers

def fx_collection(scene):
    col = bpy.data.collections.get(FX_COLLECTION)
    if col is None:
        col = bpy.data.collections.new(FX_COLLECTION)
    if col.name not in scene.collection.children and not any(col.name in c.children for c in scene.collection.children_recursive):
        scene.collection.children.link(col)
    return col


def _link(obj, scene):
    fx_collection(scene).objects.link(obj)
    obj["robot_fx"] = True
    return obj


def _nodes(mat):
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    return nt, nt.nodes, nt.links


def _set_blend(mat):
    for attr, val in (("surface_render_method", 'BLENDED'), ("blend_method", 'BLEND')):
        if hasattr(mat, attr):
            try:
                setattr(mat, attr, val)
            except (TypeError, ValueError):
                pass
    if hasattr(mat, "use_transparent_shadow"):
        mat.use_transparent_shadow = True
    if hasattr(mat, "shadow_method"):
        try:
            mat.shadow_method = 'NONE'
        except (TypeError, ValueError):
            pass


def _sock(node, *names):
    for n in names:
        s = node.inputs.get(n)
        if s is not None:
            return s
    raise KeyError(f"{node.bl_idname}: none of {names}")


def _out(node, *names):
    for n in names:
        s = node.outputs.get(n)
        if s is not None:
            return s
    raise KeyError(f"{node.bl_idname}: none of {names}")


# =========================================================================== materials

def mat_flash():
    """Additive-looking emission with a soft, star-shaped alpha. Intensity = object colour alpha."""
    m = bpy.data.materials.get("FX_Flash")
    if m:
        return m
    m = bpy.data.materials.new("FX_Flash")
    nt, N, Lk = _nodes(m)
    out = N.new("ShaderNodeOutputMaterial")
    uv = N.new("ShaderNodeUVMap")
    sep = N.new("ShaderNodeSeparateXYZ")
    Lk.new(_out(uv, "UV"), sep.inputs[0])
    # radial falloff around (0.5, 0.5) on the front card, length falloff on the side cards
    cx = N.new("ShaderNodeMath"); cx.operation = 'SUBTRACT'; cx.inputs[1].default_value = 0.5
    cy = N.new("ShaderNodeMath"); cy.operation = 'SUBTRACT'; cy.inputs[1].default_value = 0.5
    Lk.new(sep.outputs[0], cx.inputs[0]); Lk.new(sep.outputs[1], cy.inputs[0])
    comb = N.new("ShaderNodeCombineXYZ")
    Lk.new(cx.outputs[0], comb.inputs[0]); Lk.new(cy.outputs[0], comb.inputs[1])
    ln = N.new("ShaderNodeVectorMath"); ln.operation = 'LENGTH'
    Lk.new(comb.outputs[0], ln.inputs[0])
    fall = N.new("ShaderNodeMapRange"); fall.inputs[1].default_value = 0.0; fall.inputs[2].default_value = 0.5
    fall.inputs[3].default_value = 1.0; fall.inputs[4].default_value = 0.0
    fall.interpolation_type = 'SMOOTHERSTEP'
    Lk.new(ln.outputs["Value"], fall.inputs[0])
    noise = N.new("ShaderNodeTexNoise"); noise.inputs["Scale"].default_value = 7.0
    obinfo = N.new("ShaderNodeObjectInfo")
    seedv = N.new("ShaderNodeVectorMath"); seedv.operation = 'ADD'
    Lk.new(_out(uv, "UV"), seedv.inputs[0]); Lk.new(obinfo.outputs["Random"], seedv.inputs[1])
    Lk.new(seedv.outputs[0], noise.inputs["Vector"])
    nmul = N.new("ShaderNodeMapRange"); nmul.inputs[1].default_value = 0.3; nmul.inputs[2].default_value = 0.7
    nmul.inputs[3].default_value = 0.35; nmul.inputs[4].default_value = 1.0
    Lk.new(noise.outputs["Fac"], nmul.inputs[0])
    mask = N.new("ShaderNodeMath"); mask.operation = 'MULTIPLY'
    Lk.new(fall.outputs[0], mask.inputs[0]); Lk.new(nmul.outputs[0], mask.inputs[1])
    pw = N.new("ShaderNodeMath"); pw.operation = 'POWER'; pw.inputs[1].default_value = 1.6
    Lk.new(mask.outputs[0], pw.inputs[0])
    inten = N.new("ShaderNodeMath"); inten.operation = 'MULTIPLY'
    Lk.new(pw.outputs[0], inten.inputs[0]); Lk.new(obinfo.outputs["Alpha"], inten.inputs[1])
    ramp = N.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (1.0, 0.18, 0.02, 1)
    ramp.color_ramp.elements[1].color = (1.0, 0.92, 0.65, 1)
    Lk.new(pw.outputs[0], ramp.inputs[0])
    em = N.new("ShaderNodeEmission")
    Lk.new(ramp.outputs[0], em.inputs["Color"])
    st = N.new("ShaderNodeMath"); st.operation = 'MULTIPLY'; st.inputs[1].default_value = 60.0
    Lk.new(inten.outputs[0], st.inputs[0]); Lk.new(st.outputs[0], em.inputs["Strength"])
    tr = N.new("ShaderNodeBsdfTransparent")
    mix = N.new("ShaderNodeMixShader")
    cl = N.new("ShaderNodeMath"); cl.operation = 'MINIMUM'; cl.inputs[1].default_value = 1.0
    Lk.new(inten.outputs[0], cl.inputs[0])
    Lk.new(cl.outputs[0], mix.inputs[0]); Lk.new(tr.outputs[0], mix.inputs[1]); Lk.new(em.outputs[0], mix.inputs[2])
    Lk.new(mix.outputs[0], out.inputs["Surface"])
    _set_blend(m)
    return m


def mat_flame():
    """Rocket exhaust: hot core to orange tip, alpha fading along the plume (UV v)."""
    m = bpy.data.materials.get("FX_Flamme")
    if m:
        return m
    m = bpy.data.materials.new("FX_Flamme")
    nt, N, Lk = _nodes(m)
    out = N.new("ShaderNodeOutputMaterial")
    uv = N.new("ShaderNodeUVMap")
    sep = N.new("ShaderNodeSeparateXYZ")
    Lk.new(_out(uv, "UV"), sep.inputs[0])
    lw = N.new("ShaderNodeLayerWeight"); lw.inputs["Blend"].default_value = 0.35
    edge = N.new("ShaderNodeMath"); edge.operation = 'SUBTRACT'; edge.inputs[0].default_value = 1.0
    Lk.new(lw.outputs["Facing"], edge.inputs[1])
    along = N.new("ShaderNodeMapRange"); along.inputs[1].default_value = 0.0; along.inputs[2].default_value = 1.0
    along.inputs[3].default_value = 1.0; along.inputs[4].default_value = 0.0
    Lk.new(sep.outputs[1], along.inputs[0])
    noise = N.new("ShaderNodeTexNoise"); noise.inputs["Scale"].default_value = 4.0
    obinfo = N.new("ShaderNodeObjectInfo")
    tc = N.new("ShaderNodeTexCoord")
    add = N.new("ShaderNodeVectorMath"); add.operation = 'ADD'
    Lk.new(tc.outputs["Object"], add.inputs[0]); Lk.new(obinfo.outputs["Random"], add.inputs[1])
    Lk.new(add.outputs[0], noise.inputs["Vector"])
    a1 = N.new("ShaderNodeMath"); a1.operation = 'MULTIPLY'
    Lk.new(along.outputs[0], a1.inputs[0]); Lk.new(edge.outputs[0], a1.inputs[1])
    a2 = N.new("ShaderNodeMath"); a2.operation = 'MULTIPLY'
    Lk.new(a1.outputs[0], a2.inputs[0]); Lk.new(noise.outputs["Fac"], a2.inputs[1])
    a3 = N.new("ShaderNodeMath"); a3.operation = 'MULTIPLY'; a3.use_clamp = True; a3.inputs[1].default_value = 2.2
    Lk.new(a2.outputs[0], a3.inputs[0])
    ramp = N.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.9, 0.12, 0.01, 1)
    ramp.color_ramp.elements[1].color = (1.0, 0.95, 0.8, 1)
    Lk.new(a1.outputs[0], ramp.inputs[0])
    em = N.new("ShaderNodeEmission"); em.inputs["Strength"].default_value = 45.0
    Lk.new(ramp.outputs[0], em.inputs["Color"])
    tr = N.new("ShaderNodeBsdfTransparent")
    mix = N.new("ShaderNodeMixShader")
    Lk.new(a3.outputs[0], mix.inputs[0]); Lk.new(tr.outputs[0], mix.inputs[1]); Lk.new(em.outputs[0], mix.inputs[2])
    Lk.new(mix.outputs[0], out.inputs["Surface"])
    _set_blend(m)
    return m


def mat_rocket():
    m = bpy.data.materials.get("FX_Roquette")
    if m:
        return m
    m = bpy.data.materials.new("FX_Roquette")
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    if b:
        b.inputs["Base Color"].default_value = (0.13, 0.15, 0.09, 1)
        b.inputs["Metallic"].default_value = 0.6
        b.inputs["Roughness"].default_value = 0.45
    m.diffuse_color = (0.13, 0.15, 0.09, 1)
    return m


def mat_blast():
    """Explosion fireball: emission driven by the object colour alpha."""
    m = bpy.data.materials.get("FX_Explosion")
    if m:
        return m
    m = bpy.data.materials.new("FX_Explosion")
    nt, N, Lk = _nodes(m)
    out = N.new("ShaderNodeOutputMaterial")
    obinfo = N.new("ShaderNodeObjectInfo")
    tc = N.new("ShaderNodeTexCoord")
    noise = N.new("ShaderNodeTexNoise"); noise.inputs["Scale"].default_value = 2.5; noise.inputs["Detail"].default_value = 8.0
    Lk.new(tc.outputs["Object"], noise.inputs["Vector"])
    lw = N.new("ShaderNodeLayerWeight"); lw.inputs["Blend"].default_value = 0.6
    core = N.new("ShaderNodeMath"); core.operation = 'MULTIPLY'
    Lk.new(lw.outputs["Facing"], core.inputs[0]); Lk.new(noise.outputs["Fac"], core.inputs[1])
    inv = N.new("ShaderNodeMath"); inv.operation = 'SUBTRACT'; inv.inputs[0].default_value = 1.0
    Lk.new(core.outputs[0], inv.inputs[1])
    ramp = N.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (1.0, 0.9, 0.6, 1)
    ramp.color_ramp.elements[1].color = (0.8, 0.12, 0.01, 1)
    Lk.new(inv.outputs[0], ramp.inputs[0])
    em = N.new("ShaderNodeEmission")
    st = N.new("ShaderNodeMath"); st.operation = 'MULTIPLY'; st.inputs[1].default_value = 40.0
    Lk.new(obinfo.outputs["Alpha"], st.inputs[0])
    Lk.new(ramp.outputs[0], em.inputs["Color"]); Lk.new(st.outputs[0], em.inputs["Strength"])
    alpha = N.new("ShaderNodeMath"); alpha.operation = 'MULTIPLY'; alpha.use_clamp = True
    Lk.new(core.outputs[0], alpha.inputs[0]); Lk.new(obinfo.outputs["Alpha"], alpha.inputs[1])
    a2 = N.new("ShaderNodeMath"); a2.operation = 'MULTIPLY'; a2.use_clamp = True; a2.inputs[1].default_value = 3.0
    Lk.new(alpha.outputs[0], a2.inputs[0])
    tr = N.new("ShaderNodeBsdfTransparent")
    mix = N.new("ShaderNodeMixShader")
    Lk.new(a2.outputs[0], mix.inputs[0]); Lk.new(tr.outputs[0], mix.inputs[1]); Lk.new(em.outputs[0], mix.inputs[2])
    Lk.new(mix.outputs[0], out.inputs["Surface"])
    _set_blend(m)
    return m


def mat_smoke():
    """Volumetric puff. Reads the per-instance attributes written by the smoke node group."""
    m = bpy.data.materials.get("FX_Fumee")
    if m:
        return m
    m = bpy.data.materials.new("FX_Fumee")
    nt, N, Lk = _nodes(m)
    out = N.new("ShaderNodeOutputMaterial")

    def attr(name):
        a = N.new("ShaderNodeAttribute")
        a.attribute_type = 'INSTANCER'
        a.attribute_name = name
        return a
    fade, heat, dark, seed = attr("fx_fade"), attr("fx_heat"), attr("dark"), attr("seed")
    tc = N.new("ShaderNodeTexCoord")
    ln = N.new("ShaderNodeVectorMath"); ln.operation = 'LENGTH'
    Lk.new(tc.outputs["Object"], ln.inputs[0])
    sph = N.new("ShaderNodeMapRange"); sph.inputs[1].default_value = 0.25; sph.inputs[2].default_value = 1.0
    sph.inputs[3].default_value = 1.0; sph.inputs[4].default_value = 0.0; sph.interpolation_type = 'SMOOTHSTEP'
    Lk.new(ln.outputs["Value"], sph.inputs[0])
    sc = N.new("ShaderNodeVectorMath"); sc.operation = 'SCALE'; sc.inputs["Scale"].default_value = 1.4
    Lk.new(tc.outputs["Object"], sc.inputs[0])
    off = N.new("ShaderNodeVectorMath"); off.operation = 'ADD'
    Lk.new(sc.outputs[0], off.inputs[0]); Lk.new(seed.outputs["Vector"], off.inputs[1])
    noise = N.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 1.6
    noise.inputs["Detail"].default_value = 6.0
    noise.inputs["Roughness"].default_value = 0.62
    Lk.new(off.outputs[0], noise.inputs["Vector"])
    nmap = N.new("ShaderNodeMapRange"); nmap.inputs[1].default_value = 0.38; nmap.inputs[2].default_value = 0.72
    nmap.inputs[3].default_value = 0.0; nmap.inputs[4].default_value = 1.0
    Lk.new(noise.outputs["Fac"], nmap.inputs[0])
    d1 = N.new("ShaderNodeMath"); d1.operation = 'MULTIPLY'
    Lk.new(sph.outputs[0], d1.inputs[0]); Lk.new(nmap.outputs[0], d1.inputs[1])
    d2 = N.new("ShaderNodeMath"); d2.operation = 'MULTIPLY'
    Lk.new(d1.outputs[0], d2.inputs[0]); Lk.new(fade.outputs["Fac"], d2.inputs[1])
    dens = N.new("ShaderNodeMath"); dens.operation = 'MULTIPLY'; dens.inputs[1].default_value = 3.5
    Lk.new(d2.outputs[0], dens.inputs[0])
    col = N.new("ShaderNodeMix"); col.data_type = 'RGBA'
    col.inputs["A"].default_value = (0.62, 0.6, 0.57, 1)
    col.inputs["B"].default_value = (0.07, 0.065, 0.06, 1)
    Lk.new(dark.outputs["Fac"], col.inputs["Factor"])
    vol = N.new("ShaderNodeVolumePrincipled")
    Lk.new(_out(col, "Result"), vol.inputs["Color"])
    Lk.new(dens.outputs[0], vol.inputs["Density"])
    vol.inputs["Anisotropy"].default_value = 0.25
    em = N.new("ShaderNodeMath"); em.operation = 'MULTIPLY'
    Lk.new(heat.outputs["Fac"], em.inputs[0]); Lk.new(d1.outputs[0], em.inputs[1])
    em2 = N.new("ShaderNodeMath"); em2.operation = 'MULTIPLY'; em2.inputs[1].default_value = 18.0
    Lk.new(em.outputs[0], em2.inputs[0])
    Lk.new(em2.outputs[0], _sock(vol, "Emission Strength"))
    _sock(vol, "Emission Color").default_value = (1.0, 0.38, 0.07, 1)
    Lk.new(vol.outputs[0], out.inputs["Volume"])
    return m


# =========================================================================== smoke node group

def smoke_group():
    ng = bpy.data.node_groups.get(SMOKE_GROUP)
    if ng:
        return ng
    ng = bpy.data.node_groups.new(SMOKE_GROUP, 'GeometryNodeTree')
    ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    s = ng.interface.new_socket("Turbulence", in_out='INPUT', socket_type='NodeSocketFloat')
    s.default_value = 0.6
    s = ng.interface.new_socket("Wind", in_out='INPUT', socket_type='NodeSocketVector')
    s.default_value = (0.4, 0.15, 0.0)
    s = ng.interface.new_socket("Puff Detail", in_out='INPUT', socket_type='NodeSocketInt')
    s.default_value = 2
    N, Lk = ng.nodes, ng.links
    gin = N.new("NodeGroupInput")
    gout = N.new("NodeGroupOutput")
    st = N.new("GeometryNodeInputSceneTime")

    def named(name, dtype='FLOAT'):
        n = N.new("GeometryNodeInputNamedAttribute")
        n.data_type = dtype
        n.inputs["Name"].default_value = name
        return n.outputs["Attribute"]

    def math(op, a, b=None, clamp=False):
        n = N.new("ShaderNodeMath")
        n.operation = op
        n.use_clamp = clamp
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                Lk.new(v, n.inputs[i])
        return n.outputs[0]

    def vmath(op, a, b=None, scale=None):
        n = N.new("ShaderNodeVectorMath")
        n.operation = op
        if a is not None:
            Lk.new(a, n.inputs[0])
        if b is not None:
            if isinstance(b, tuple):
                n.inputs[1].default_value = b
            else:
                Lk.new(b, n.inputs[1])
        if scale is not None:
            if isinstance(scale, (int, float)):
                n.inputs["Scale"].default_value = scale
            else:
                Lk.new(scale, n.inputs["Scale"])
        return n.outputs[0]

    now = _out(st, "Seconds")
    birth, life = named("birth"), named("life")          # seconds
    age = math('SUBTRACT', now, birth)
    u = math('DIVIDE', age, life)
    c1 = N.new("FunctionNodeCompare"); c1.data_type = 'FLOAT'; c1.operation = 'LESS_THAN'
    Lk.new(age, c1.inputs[0]); c1.inputs[1].default_value = 0.0
    c2 = N.new("FunctionNodeCompare"); c2.data_type = 'FLOAT'; c2.operation = 'GREATER_THAN'
    Lk.new(u, c2.inputs[0]); c2.inputs[1].default_value = 1.0
    orr = N.new("FunctionNodeBooleanMath"); orr.operation = 'OR'
    Lk.new(c1.outputs[0], orr.inputs[0]); Lk.new(c2.outputs[0], orr.inputs[1])
    dele = N.new("GeometryNodeDeleteGeometry"); dele.domain = 'POINT'
    Lk.new(gin.outputs["Geometry"], dele.inputs["Geometry"])
    Lk.new(orr.outputs[0], dele.inputs["Selection"])

    t = age
    drag = named("drag")
    e = math('EXPONENT', math('MULTIPLY', math('MULTIPLY', drag, t), -1.0))
    f = math('DIVIDE', math('SUBTRACT', 1.0, e), math('MAXIMUM', drag, 0.01))
    disp = vmath('SCALE', named("vel", 'FLOAT_VECTOR'), scale=f)
    rise = math('MULTIPLY', named("rise"), t)
    upv = N.new("ShaderNodeCombineXYZ"); Lk.new(rise, upv.inputs[2])
    wind = vmath('SCALE', gin.outputs["Wind"], scale=t)
    pos = N.new("GeometryNodeInputPosition")
    nvec = vmath('SCALE', pos.outputs[0], scale=0.35)
    noise = N.new("ShaderNodeTexNoise"); noise.noise_dimensions = '4D'
    noise.inputs["Scale"].default_value = 1.0
    Lk.new(nvec, noise.inputs["Vector"])
    Lk.new(math('ADD', named("seed"), math('MULTIPLY', t, 0.3)), noise.inputs["W"])
    turb = vmath('SCALE', vmath('SUBTRACT', noise.outputs["Color"], (0.5, 0.5, 0.5)),
                 scale=math('MULTIPLY', gin.outputs["Turbulence"], math('MULTIPLY', t, 2.0)))
    off = vmath('ADD', vmath('ADD', disp, upv.outputs[0]), vmath('ADD', wind, turb))
    setp = N.new("GeometryNodeSetPosition")
    Lk.new(dele.outputs["Geometry"], setp.inputs["Geometry"])
    Lk.new(off, setp.inputs["Offset"])

    radius = math('ADD', named("r0"), math('MULTIPLY', named("grow"), math('POWER', math('MAXIMUM', t, 0.0), 0.5)))
    fade = math('MULTIPLY', math('POWER', math('SUBTRACT', 1.0, u, clamp=True), 1.6), math('DIVIDE', age, 0.08, clamp=True))
    heat = math('MULTIPLY', named("heat"), math('POWER', math('SUBTRACT', 1.0, math('DIVIDE', t, math('MAXIMUM', named("htime"), 0.01)), clamp=True), 2.0))
    store1 = N.new("GeometryNodeStoreNamedAttribute"); store1.data_type = 'FLOAT'; store1.domain = 'POINT'
    store1.inputs["Name"].default_value = "fx_fade"
    Lk.new(setp.outputs[0], store1.inputs["Geometry"]); Lk.new(fade, store1.inputs["Value"])
    store2 = N.new("GeometryNodeStoreNamedAttribute"); store2.data_type = 'FLOAT'; store2.domain = 'POINT'
    store2.inputs["Name"].default_value = "fx_heat"
    Lk.new(store1.outputs[0], store2.inputs["Geometry"]); Lk.new(heat, store2.inputs["Value"])

    ico = N.new("GeometryNodeMeshIcoSphere")
    ico.inputs["Radius"].default_value = 1.0
    Lk.new(gin.outputs["Puff Detail"], ico.inputs["Subdivisions"])
    smat = N.new("GeometryNodeSetMaterial")
    smat.inputs["Material"].default_value = mat_smoke()
    Lk.new(ico.outputs["Mesh"], smat.inputs["Geometry"])
    rnd = N.new("FunctionNodeRandomValue"); rnd.data_type = 'FLOAT_VECTOR'
    rnd.inputs["Max"].default_value = (6.283, 6.283, 6.283)
    inst = N.new("GeometryNodeInstanceOnPoints")
    Lk.new(store2.outputs[0], inst.inputs["Points"])
    Lk.new(smat.outputs[0], inst.inputs["Instance"])
    Lk.new(radius, inst.inputs["Scale"])
    Lk.new(rnd.outputs["Value"], inst.inputs["Rotation"])
    Lk.new(inst.outputs[0], gout.inputs["Geometry"])
    return ng


# =========================================================================== keyframe helpers

def _key(obj, path, frame, index=-1, constant=False):
    obj.keyframe_insert(path, index=index, frame=frame)
    if constant:
        for fc in animdata.all_fcurves(obj):
            if fc.data_path == path:
                for kp in fc.keyframe_points:
                    if abs(kp.co.x - frame) < 1e-3:
                        kp.interpolation = 'CONSTANT'


def _key_visibility(obj, f_on, f_off):
    """Visible in [f_on, f_off)."""
    for f, hidden in ((f_on - 1, True), (f_on, False), (f_off, True)):
        obj.hide_viewport = obj.hide_render = hidden
        _key(obj, "hide_viewport", f, constant=True)
        _key(obj, "hide_render", f, constant=True)
    obj.hide_viewport = obj.hide_render = False


def set_constant(obj):
    for fc in animdata.all_fcurves(obj):
        for kp in fc.keyframe_points:
            kp.interpolation = 'CONSTANT'
        fc.update()


# =========================================================================== muzzle flash

def _flash_mesh(name):
    me = bpy.data.meshes.new(name)
    verts, faces, uvs = [], [], []
    length, width = 1.15, 0.42
    for k in range(3):                       # three crossed cards along +Y (the barrel axis)
        a = math.pi * k / 3.0
        ax, az = math.cos(a) * width, math.sin(a) * width
        base = len(verts)
        verts += [(-ax, 0.0, -az), (ax, 0.0, az), (ax * 0.35, length, az * 0.35), (-ax * 0.35, length, -az * 0.35)]
        faces.append((base, base + 1, base + 2, base + 3))
        uvs += [(0.0, 0.5), (1.0, 0.5), (1.0, 1.0), (0.0, 1.0)]
    base = len(verts)                        # front-facing star card
    r = 0.5
    verts += [(-r, 0.05, -r), (r, 0.05, -r), (r, 0.05, r), (-r, 0.05, r)]
    faces.append((base, base + 1, base + 2, base + 3))
    uvs += [(0, 0), (1, 0), (1, 1), (0, 1)]
    me.from_pydata(verts, [], faces)
    uvl = me.uv_layers.new(name="UVMap")
    for li, loop in enumerate(me.loops):
        uvl.data[li].uv = uvs[loop.vertex_index]
    me.materials.append(mat_flash())
    return me


def ensure_muzzle_flash(rig, side, scene):
    name = f"FX-flash.{side}"
    obj = bpy.data.objects.get(name)
    if obj is None:
        obj = bpy.data.objects.new(name, _flash_mesh(name))
        _link(obj, scene)
        obj.parent = rig
        obj.parent_type = 'BONE'
        obj.parent_bone = R.muzzle(side)
        bone = rig.data.bones[R.muzzle(side)]
        obj.location = (0.0, -bone.length + 0.02, 0.0)       # bone parenting starts at the tail
        obj.rotation_mode = 'XYZ'
        obj.color = (1, 1, 1, 0)
        obj.scale = (0, 0, 0)
        obj.visible_shadow = False
        ld = bpy.data.lights.new(name + "_lumiere", 'POINT')
        ld.color = (1.0, 0.62, 0.28)
        ld.energy = 0.0
        ld.shadow_soft_size = 0.25
        lo = bpy.data.objects.new(name + "_lumiere", ld)
        _link(lo, scene)
        lo.parent = obj
        lo.location = (0, 0.35, 0)
        obj["light"] = lo.name
    return obj


def flash_state(obj, intensity, rng):
    """Live (non keyed) flash. intensity 0..1+."""
    on = intensity > 0.01
    s = (0.65 + 0.6 * rng.random()) * intensity if on else 0.0
    obj.scale = (s, s * (0.8 + 0.5 * rng.random()), s)
    obj.rotation_euler = (0.0, rng.random() * math.tau, 0.0)
    obj.color = (1, 1, 1, min(1.5, intensity))
    lo = bpy.data.objects.get(obj.get("light", ""))
    if lo:
        lo.data.energy = 2500.0 * intensity if on else 0.0


def key_flash(obj, frame, intensity, rng):
    flash_state(obj, intensity, rng)
    for path in ("scale", "rotation_euler", "color"):
        _key(obj, path, frame, constant=True)
    lo = bpy.data.objects.get(obj.get("light", ""))
    if lo:
        lo.data.keyframe_insert("energy", frame=frame)
        set_constant(lo.data)


def key_flash_track(rig, side, scene, frames_on, f0, f1, seed=1, size=1.0):
    """Key flashes on [f0, f1]: ``frames_on`` is a set of frames where a shot is fired."""
    obj = ensure_muzzle_flash(rig, side, scene)
    rng = random.Random(seed)
    for f in range(f0, f1 + 1):
        key_flash(obj, f, size if f in frames_on else 0.0, rng)
    return obj


# =========================================================================== rockets

def _rocket_mesh():
    me = bpy.data.meshes.get("FX-roquette")
    if me:
        return me
    import bmesh
    bm = bmesh.new()
    r, L = 0.075, 0.85
    ret = bmesh.ops.create_cone(bm, cap_ends=True, segments=14, radius1=r, radius2=r, depth=L)
    bmesh.ops.rotate(bm, verts=ret["verts"], cent=(0, 0, 0), matrix=Matrix.Rotation(-math.pi / 2, 3, 'X'))
    nose = bmesh.ops.create_cone(bm, cap_ends=True, segments=14, radius1=r, radius2=0.0, depth=0.22)
    bmesh.ops.rotate(bm, verts=nose["verts"], cent=(0, 0, 0), matrix=Matrix.Rotation(-math.pi / 2, 3, 'X'))
    bmesh.ops.translate(bm, verts=nose["verts"], vec=(0, L / 2 + 0.11, 0))
    for k in range(4):
        a = math.pi / 2 * k + math.pi / 4
        c, s = math.cos(a), math.sin(a)
        fin = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, verts=fin["verts"], vec=(0.11, 0.2, 0.01))
        bmesh.ops.rotate(bm, verts=fin["verts"], cent=(0, 0, 0), matrix=Matrix.Rotation(a, 3, 'Y'))
        bmesh.ops.translate(bm, verts=fin["verts"], vec=(c * (r + 0.05), -L / 2 + 0.1, s * (r + 0.05)))
    me = bpy.data.meshes.new("FX-roquette")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat_rocket())
    for p in me.polygons:
        p.use_smooth = False
    return me


def _flame_mesh():
    me = bpy.data.meshes.get("FX-flamme")
    if me:
        return me
    seg, L = 16, 1.0
    verts, faces, uvs = [], [], []
    rings = [(0.0, 0.07), (0.15, 0.085), (0.45, 0.06), (1.0, 0.0)]
    for yi, (v, rad) in enumerate(rings):
        for i in range(seg):
            a = math.tau * i / seg
            verts.append((math.cos(a) * rad, -v * L, math.sin(a) * rad))
            uvs.append((i / seg, v))
    for yi in range(len(rings) - 1):
        for i in range(seg):
            a, b = yi * seg + i, yi * seg + (i + 1) % seg
            faces.append((a, b, b + seg, a + seg))
    me = bpy.data.meshes.new("FX-flamme")
    me.from_pydata(verts, [], faces)
    uvl = me.uv_layers.new(name="UVMap")
    for li, loop in enumerate(me.loops):
        uvl.data[li].uv = uvs[loop.vertex_index]
    me.materials.append(mat_flame())
    return me


def _blast_mesh():
    me = bpy.data.meshes.get("FX-boule")
    if me:
        return me
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=3, radius=1.0)
    me = bpy.data.meshes.new("FX-boule")
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(mat_blast())
    return me


def _scene_raycaster(scene, ignore):
    depsgraph = bpy.context.evaluated_depsgraph_get()

    def cast(origin, direction, dist):
        ok, loc, _n, _i, obj, _m = scene.ray_cast(depsgraph, origin, direction, distance=dist)
        guard = 0
        while ok and obj is not None and (obj.name in ignore or obj.get("robot_fx") or obj.name.startswith(("GEO-", "WGT-"))):
            guard += 1
            if guard > 16:
                return None
            origin = loc + direction * 0.01
            dist -= (loc - origin).length
            ok, loc, _n, _i, obj, _m = scene.ray_cast(depsgraph, origin, direction, distance=max(dist, 0.0))
        return loc if ok else None
    return cast


def simulate_rocket(p0, d0, fps, target=None, max_range=120.0, seed=0, raycast=None, ground_z=None):
    """Rocket flight. Returns (samples[(t, pos, vel, burning)], impact_pos or None, t_end)."""
    rng = random.Random(seed)
    sub = 4
    dt = 1.0 / (fps * sub)
    pos, d = Vector(p0), Vector(d0).normalized()
    vel = d * 14.0
    t, travelled = 0.0, 0.0
    eject, vmax, acc = 0.12, 95.0, 220.0
    wob_a, wob_f = math.radians(rng.uniform(1.5, 3.0)), rng.uniform(5, 9)
    phase = rng.uniform(0, math.tau)
    samples = [(0.0, pos.copy(), vel.copy(), False)]
    impact = None
    while travelled < max_range and t < 6.0:
        t += dt
        burning = t > eject
        spd = vel.length
        if burning:
            spd = min(vmax, spd + acc * dt)
        heading = vel.normalized()
        if target is not None and burning:
            want = (Vector(target) - pos).normalized()
            ang = heading.angle(want, 0.0)
            maxturn = math.radians(110.0) * dt
            if ang > 1e-5:
                k = min(1.0, maxturn / ang)
                heading = heading.lerp(want, k).normalized()
        # small decaying wobble of the motor
        side = heading.cross(Vector((0, 0, 1)))
        if side.length < 1e-4:
            side = Vector((1, 0, 0))
        side.normalize()
        up = side.cross(heading).normalized()
        w = wob_a * math.exp(-t * 1.5)
        heading = (heading + side * w * math.sin(wob_f * t + phase) * 0.15 + up * w * math.cos(wob_f * 1.3 * t) * 0.15).normalized()
        vel = heading * spd
        if not burning:
            vel.z -= 9.81 * dt
        step = vel * dt
        if raycast is not None and step.length > 0:
            hit = raycast(pos, step.normalized(), step.length)
            if hit is not None:
                impact = hit
                pos = Vector(hit)
                samples.append((t, pos.copy(), vel.copy(), burning))
                break
        if ground_z is not None and pos.z + step.z < ground_z:
            impact = Vector((pos.x + step.x, pos.y + step.y, ground_z))
            pos = impact.copy()
            samples.append((t, pos.copy(), vel.copy(), burning))
            break
        pos += step
        travelled += step.length
        samples.append((t, pos.copy(), vel.copy(), burning))
    if impact is None and target is not None and (pos - Vector(target)).length < 3.0:
        impact = pos.copy()
    return samples, impact, t


def _smoke_object(name, scene, points):
    """points: list of dicts with co, birth, life, vel, r0, grow, rise, drag, heat, dark, seed."""
    fps = scene.render.fps / scene.render.fps_base
    for p in points:                       # the node group works in seconds (scene time)
        p["birth"] = p["birth"] / fps
        p["life"] = p["life"] / fps
    me = bpy.data.meshes.new(name)
    me.from_pydata([p["co"] for p in points], [], [])
    for key in ("birth", "life", "r0", "grow", "rise", "drag", "heat", "htime", "dark", "seed"):
        a = me.attributes.new(key, 'FLOAT', 'POINT')
        a.data.foreach_set("value", [float(p[key]) for p in points])
    a = me.attributes.new("vel", 'FLOAT_VECTOR', 'POINT')
    a.data.foreach_set("vector", [c for p in points for c in p["vel"]])
    obj = bpy.data.objects.new(name, me)
    _link(obj, scene)
    mod = obj.modifiers.new("Fumée", 'NODES')
    mod.node_group = smoke_group()
    obj.visible_shadow = False
    return obj


def launch_rocket(scene, rig, side, port, frame, direction=None, origin=None, target=None,
                  max_range=120.0, seed=0, smoke_density=1.0, explosion=True):
    """Create one rocket fired at ``frame`` from ``rocket_port_<port>.<side>``.

    Returns a dict with the created objects and the impact frame."""
    fps = scene.render.fps / scene.render.fps_base
    if origin is None or direction is None:
        pose = R.RigPose(rig)
        o, d = pose.bone_world(R.rocket_port(side, port))
        origin = origin or o
        direction = direction or d
    rng = random.Random(seed * 7919 + frame * 31 + port + (0 if side == "L" else 17))
    ignore = {o.name for o in bpy.data.objects if o.parent == rig} | {rig.name}
    raycast = _scene_raycaster(scene, ignore)
    samples, impact, t_end = simulate_rocket(origin, direction, fps, target=target, max_range=max_range,
                                             seed=rng.randrange(1 << 30), raycast=raycast, ground_z=None)
    tag = f"{side}{port}_{frame:05d}_{seed}"
    rocket = bpy.data.objects.new(f"FX-roquette.{tag}", _rocket_mesh())
    _link(rocket, scene)
    rocket.rotation_mode = 'QUATERNION'
    flame = bpy.data.objects.new(f"FX-flamme.{tag}", _flame_mesh())
    _link(flame, scene)
    flame.parent = rocket
    flame.location = (0, -0.43, 0)
    flame.visible_shadow = False
    ld = bpy.data.lights.new(f"FX-moteur.{tag}", 'POINT')
    ld.color = (1.0, 0.55, 0.2)
    ld.shadow_soft_size = 0.3
    light = bpy.data.objects.new(ld.name, ld)
    _link(light, scene)
    light.parent = rocket
    light.location = (0, -0.8, 0)

    # one key per frame
    f_end = frame + max(1, int(math.ceil(t_end * fps)))
    by_frame = {}
    for t, pos, vel, burning in samples:
        by_frame[frame + int(round(t * fps))] = (pos, vel, burning)
    last = None
    for f in range(frame, f_end + 1):
        if f in by_frame:
            last = by_frame[f]
        if last is None:
            continue
        pos, vel, burning = last
        rocket.location = pos
        rocket.rotation_quaternion = vel.to_track_quat('Y', 'Z')
        _key(rocket, "location", f)
        _key(rocket, "rotation_quaternion", f)
        s = rng.uniform(0.8, 1.35) if burning else 0.0
        flame.scale = (s * rng.uniform(0.85, 1.1), s * rng.uniform(1.0, 1.6), s * rng.uniform(0.85, 1.1))
        _key(flame, "scale", f, constant=True)
        ld.energy = rng.uniform(1500, 2600) if burning else 0.0
        ld.keyframe_insert("energy", frame=f)
    set_constant(ld)
    _key_visibility(rocket, frame, f_end)
    _key_visibility(flame, frame, f_end)
    _key_visibility(light, frame, f_end)

    # ---- smoke points along the trail
    pts = []
    spacing = 0.32 / max(smoke_density, 0.1)          # one puff every ~30 cm of flight
    acc_d, prev = 0.0, None
    for t, pos, vel, burning in samples:
        if prev is not None and burning:
            seg = pos - prev[1]
            L = seg.length
            while acc_d + L >= spacing and L > 1e-6:
                u = (spacing - acc_d) / L
                q = prev[1] + seg * u
                tq = prev[0] + (t - prev[0]) * u
                acc_d = 0.0
                L -= (q - prev[1]).length
                prev = (tq, q)
                seg = pos - q
                back = vel.normalized() * -0.5
                spread = Vector((rng.gauss(0, 0.22), rng.gauss(0, 0.22), rng.gauss(0, 0.22)))
                young = tq < 0.5
                pts.append(dict(co=tuple(q + back + Vector((rng.gauss(0, 0.03), rng.gauss(0, 0.03), rng.gauss(0, 0.03)))),
                                birth=frame + tq * fps, life=rng.uniform(90, 150), vel=tuple(vel * -0.025 + spread),
                                r0=rng.uniform(0.16, 0.24), grow=rng.uniform(0.45, 0.75), rise=rng.uniform(0.12, 0.3),
                                drag=1.6, heat=0.9 if young else 0.6, htime=0.06, dark=rng.uniform(0.0, 0.08),
                                seed=rng.uniform(0, 100)))
            acc_d += L
        prev = (t, pos)
    p0 = Vector(origin)
    d0 = Vector(direction).normalized()
    for _ in range(int(22 * smoke_density) + 4):            # launch blast / backblast
        v = -d0 * rng.uniform(2.0, 7.0) + Vector((rng.gauss(0, 1.4), rng.gauss(0, 1.4), rng.gauss(0, 1.0)))
        pts.append(dict(co=tuple(p0 - d0 * 0.3), birth=frame + rng.uniform(0, 3), life=rng.uniform(60, 110), vel=tuple(v),
                        r0=rng.uniform(0.15, 0.25), grow=rng.uniform(0.6, 0.9), rise=0.35, drag=2.2, heat=0.8, htime=0.15,
                        dark=rng.uniform(0.05, 0.25), seed=rng.uniform(0, 100)))
    created = {"rocket": rocket, "flame": flame, "light": light, "impact_frame": f_end, "impact": impact}

    # ---- explosion
    if explosion and impact is not None:
        fi = f_end
        blast = bpy.data.objects.new(f"FX-explosion.{tag}", _blast_mesh())
        _link(blast, scene)
        blast.location = impact
        blast.visible_shadow = False
        for i, (sc, a) in enumerate(((0.3, 1.5), (1.6, 1.3), (2.6, 0.9), (3.2, 0.45), (3.5, 0.15), (3.6, 0.0))):
            blast.scale = (sc, sc, sc * 0.85)
            blast.color = (1, 1, 1, a)
            _key(blast, "scale", fi + i * 2)
            _key(blast, "color", fi + i * 2)
        _key_visibility(blast, fi, fi + 12)
        bl = bpy.data.lights.new(f"FX-eclair.{tag}", 'POINT')
        bl.color = (1.0, 0.6, 0.25)
        bl.shadow_soft_size = 1.5
        blo = bpy.data.objects.new(bl.name, bl)
        _link(blo, scene)
        blo.location = Vector(impact) + Vector((0, 0, 1.0))
        for f, e in ((fi - 1, 0.0), (fi, 60000.0), (fi + 3, 25000.0), (fi + 10, 0.0)):
            bl.energy = e
            bl.keyframe_insert("energy", frame=f)
        n_burst = int(70 * smoke_density) + 10
        for _ in range(n_burst):
            dirv = Vector((rng.gauss(0, 1), rng.gauss(0, 1), abs(rng.gauss(0, 1)) * 0.8 + 0.2)).normalized()
            pts.append(dict(co=tuple(Vector(impact) + dirv * rng.uniform(0.1, 0.6)), birth=fi + rng.uniform(0, 4),
                            life=rng.uniform(140, 230), vel=tuple(dirv * rng.uniform(3, 9)), r0=rng.uniform(0.35, 0.6),
                            grow=rng.uniform(1.0, 1.6), rise=rng.uniform(0.6, 1.1), drag=2.8, heat=1.0, htime=0.6,
                            dark=rng.uniform(0.55, 0.85), seed=rng.uniform(0, 100)))
        created.update(blast=blast, blast_light=blo)
    if pts:
        created["smoke"] = _smoke_object(f"FX-fumee.{tag}", scene, pts)
    return created


def clear_fx(scene, after=None):
    """Delete generated rockets / smoke / explosions (all, or those launched after a frame)."""
    removed = 0
    for o in list(bpy.data.objects):
        if not o.get("robot_fx") or o.name.startswith("FX-flash"):
            continue
        if after is not None:
            try:
                f = int(o.name.split("_")[-2])
            except (ValueError, IndexError):
                continue
            if f < after:
                continue
        data = o.data
        bpy.data.objects.remove(o)
        removed += 1
        if data is not None and data.users == 0 and not isinstance(data, bpy.types.Mesh) or (
                isinstance(data, bpy.types.Mesh) and data.users == 0 and data.name.startswith("FX-fumee")):
            try:
                if isinstance(data, bpy.types.Light):
                    bpy.data.lights.remove(data)
                elif isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
            except (ReferenceError, RuntimeError):
                pass
    return removed
