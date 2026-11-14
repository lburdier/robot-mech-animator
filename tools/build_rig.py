"""Rebuild a clean, symmetric animation rig for the mech robot.

Usage (from the RobotRig folder):
    blender -b --factory-startup --disable-autoexec "Rig robot.blend" \
        --python tools/build_rig.py -- <output.blend>

What it fixes / builds
  * removes the broken armature (7 objects pointed to bones that no longer existed,
    non-normalised quaternions, no IK constraint at all) and the 162 empty FBX nodes
  * re-aligns the right leg (rotated ~4 deg, foot 10-14 cm above the ground, piston
    16 cm too high, one toe plate lost 4 m away) on the exact mirror of the left leg
  * moves the robot so that the soles rest on Z = 0
  * builds a new armature "RIG_Robot": IK legs (3 segments + foot + toes),
    body / torso, two turret controllers (yaw / pitch / recoil), gatling spin,
    aim target, custom shapes, bone collections and colours
"""
import bpy
import bmesh
import math
import sys
import numpy as np
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else None
RIG_ID = "MECH_ROBOT_V1"

# --------------------------------------------------------------------------- utils

def log(*a):
    print("[build_rig]", *a)


def world_pts(o):
    mw = np.array(o.matrix_world)
    co = np.empty(len(o.data.vertices) * 3)
    o.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    return co @ mw[:3, :3].T + mw[:3, 3]


def kabsch(a, b):
    ca, cb = a.mean(0), b.mean(0)
    u, _s, vt = np.linalg.svd((a - ca).T @ (b - cb))
    d = np.sign(np.linalg.det(vt.T @ u.T))
    r = vt.T @ np.diag([1, 1, d]) @ u.T
    return r, cb - r @ ca


def icp(src, dst, iters=80):
    """Rigid transform (R, t) so that R @ src + t ~= dst."""
    kd = KDTree(len(dst))
    for i, p in enumerate(dst):
        kd.insert(p, i)
    kd.balance()
    R, t = np.eye(3), dst.mean(0) - src.mean(0)
    for _ in range(iters):
        cur = src @ R.T + t
        idx = np.array([kd.find(p)[1] for p in cur])
        r2, t2 = kabsch(cur, dst[idx])
        R, t = r2 @ R, r2 @ t + t2
    cur = src @ R.T + t
    rms = float(np.sqrt(np.mean([kd.find(p)[2] ** 2 for p in cur])))
    return R, t, rms


def to_matrix(R, t):
    m = Matrix.Identity(4)
    for i in range(3):
        for j in range(3):
            m[i][j] = float(R[i, j])
        m[i][3] = float(t[i])
    return m


MIRROR_X = Matrix.Scale(-1, 4, (1, 0, 0))

# --------------------------------------------------------------------------- 0. scene

if bpy.context.object and bpy.context.object.mode != 'OBJECT':
    bpy.ops.object.mode_set(mode='OBJECT')

scene = bpy.context.scene
robot_col = bpy.data.collections.get("Robot") or scene.collection
meshes = [o for o in bpy.data.objects if o.type == 'MESH']
saved_mw = {o.name: o.matrix_world.copy() for o in meshes}

# --------------------------------------------------------------------------- 1. cleanup
old_arm = [o for o in bpy.data.objects if o.type == 'ARMATURE']
for o in meshes:
    o.parent = None
    o.matrix_parent_inverse = Matrix.Identity(4)
    o.matrix_world = saved_mw[o.name]
for o in old_arm:
    data = o.data
    bpy.data.objects.remove(o)
    if data.users == 0:
        bpy.data.armatures.remove(data)
empties = [o for o in bpy.data.objects if o.type == 'EMPTY']
for o in empties:
    bpy.data.objects.remove(o)
log(f"removed {len(old_arm)} broken armature(s) and {len(empties)} empty FBX nodes")

O = bpy.data.objects
bpy.context.view_layer.update()

# --------------------------------------------------------------------------- 2. right leg re-alignment
# (right piece, left counterpart). The right piece is moved onto mirror(left).
REALIGN = [
    ("Cube.040_Low_legs_0", "Cube.048_Low_legs_0.001"),
    ("Entre cuisse tibia.001", "Entre cuisse tibia"),
    ("Piston.001", "Piston"),
    ("Cylinder.061_Low_legs_0", "Cylinder.061_Low_legs_0.001"),
    ("Plane.010_Low_legs_0", "Plane.010_Low_legs_0.003"),
    ("Plane.010_Low_legs_0.004", "Plane.010_Low_legs_0.007"),
    ("Plane.010_Low_legs_0.001", "Plane.010_Low_legs_0.006"),
    ("Plane.010_Low_legs_0.002", "Plane.010_Low_legs_0.005"),
]
for right, left in REALIGN:
    L = world_pts(O[left]) * np.array([-1.0, 1.0, 1.0])
    Rp = world_pts(O[right])
    R, t, rms = icp(L, Rp)
    # Rp ~= R L + t  ->  apply the inverse to the right piece
    inv = to_matrix(R.T, -R.T @ t)
    O[right].matrix_world = inv @ O[right].matrix_world
    ang = math.degrees(math.acos(max(-1, min(1, (np.trace(R) - 1) / 2))))
    log(f"realigned {right:28s} rot {ang:5.2f} deg, shift {np.round(-t, 3)} (fit rms {rms:.4f})")

# lost toe plate: rebuild it as the mirror of its left twin
lost, twin = O["Plane.010_Low_legs_0.008"], O["Plane.010_Low_legs_0.009"]
new_me = twin.data.copy()
new_me.name = lost.data.name
new_me.transform(MIRROR_X @ twin.matrix_world)
bm = bmesh.new()
bm.from_mesh(new_me)
bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
bm.to_mesh(new_me)
bm.free()
old_me = lost.data
lost.data = new_me
if old_me.users == 0:
    bpy.data.meshes.remove(old_me)
lost.matrix_world = Matrix.Identity(4)
log("rebuilt lost toe plate Plane.010_Low_legs_0.008 as mirror of .009")

# bake transforms, delete loose vertices
loose_total = 0
for o in [o for o in bpy.data.objects if o.type == 'MESH']:
    o.data.transform(o.matrix_world)
    o.matrix_world = Matrix.Identity(4)
    bm = bmesh.new()
    bm.from_mesh(o.data)
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        loose_total += len(loose)
        bmesh.ops.delete(bm, geom=loose, context='VERTS')
        bm.to_mesh(o.data)
    bm.free()
    o.data.update()
log(f"removed {loose_total} loose vertices")

# put the soles on Z = 0
ground = min(world_pts(O[n])[:, 2].min() for n in ("Plane.010_Low_legs_0.007", "Plane.010_Low_legs_0.005", "Plane.010_Low_legs_0.006"))
shift = Matrix.Translation((0, 0, -ground))
for o in bpy.data.objects:
    if o.type == 'MESH':
        o.data.transform(shift)
    elif o.parent is None:
        o.matrix_world = shift @ o.matrix_world
log(f"ground was at z={ground:.4f}; robot moved up so that soles rest on z=0")
Z = -ground  # old -> new z offset


def P(x, y, z_old):
    """Point given in the original coordinates (old z)."""
    return Vector((x, y, z_old + Z))


# --------------------------------------------------------------------------- 3. pivots from geometry
bpy.context.view_layer.update()


def bbox_center(pts):
    return Vector(((pts.min(0) + pts.max(0)) / 2).tolist())


link = world_pts(O["Entre cuisse tibia"])
K1 = bbox_center(link[link[:, 1] < link[:, 1].min() + 0.32])          # front knob (thigh / link)
K2 = bbox_center(link[link[:, 1] > link[:, 1].max() - 0.30])          # rear end (link / shin)
ank = world_pts(O["Plane.010_Low_legs_0.003"])
A = bbox_center(ank)
turret = world_pts(O["Cylinder.053_Low_body_0.003"])
hub = turret[turret[:, 0] < turret[:, 0].min() + 0.06]
TP = bbox_center(hub)
TP.x = turret[:, 0].min() + 0.02
barrel = world_pts(O["Cylinder.053_Low_body_0.001"])
ys = np.linspace(barrel[:, 1].min() + 0.05, barrel[:, 1].min() + 0.9, 8)
centres = []
for yv in ys:
    sl = barrel[np.abs(barrel[:, 1] - yv) < 0.03]
    if len(sl) > 6:
        centres.append(((sl[:, 0].min() + sl[:, 0].max()) / 2, yv, (sl[:, 2].min() + sl[:, 2].max()) / 2))
centres = np.array(centres)
axis_dir = Vector((centres[0] - centres[-1]).tolist()).normalized()        # towards the muzzle
axis_pt = Vector(centres.mean(0).tolist())
y_back, y_tip = barrel[:, 1].max(), barrel[:, 1].min()
G_back = axis_pt + axis_dir * ((y_back - axis_pt.y) / axis_dir.y)
G_tip = axis_pt + axis_dir * ((y_tip - axis_pt.y) / axis_dir.y)
LEG_X = 1.02
H = Vector((LEG_X, 0.58, -1.12 + Z))
for v in (K1, K2, A):
    v.x = LEG_X
log("pivots  hip", H, " knee1", K1, " knee2", K2, " ankle", A)
log("turret hub", TP, " gatling", G_back, "->", G_tip)

ANKLE_H = A.z
TOE_F = (Vector((LEG_X, -0.45, ANKLE_H - 0.30)), Vector((LEG_X, -1.12, 0.10)))
TOE_B = (Vector((LEG_X, 0.22, ANKLE_H - 0.30)), Vector((LEG_X, 0.78, 0.10)))
PELVIS = Vector((0, 0.45, H.z))
TORSO = P(0, 0.42, -0.62)

# --------------------------------------------------------------------------- 4. widgets
wgt_col = bpy.data.collections.get("WGT_Robot") or bpy.data.collections.new("WGT_Robot")
if wgt_col.name not in scene.collection.children:
    scene.collection.children.link(wgt_col)
wgt_col.hide_viewport = True
wgt_col.hide_render = True


def widget(name, verts, edges):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, edges, [])
    ob = bpy.data.objects.new(name, me)
    wgt_col.objects.link(ob)
    return ob


def circle_pts(n=32, r=1.0, plane='XZ', off=(0, 0, 0)):
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        c, s = math.cos(a) * r, math.sin(a) * r
        p = {'XZ': (c, 0, s), 'XY': (c, s, 0), 'YZ': (0, c, s)}[plane]
        pts.append((p[0] + off[0], p[1] + off[1], p[2] + off[2]))
    return pts


def ring_edges(n, start=0):
    return [(start + i, start + (i + 1) % n) for i in range(n)]


W = {}
v = circle_pts(48, 1.0, 'XY')
v += [(0, 1.0, 0), (0, 1.35, 0), (-0.12, 1.2, 0), (0.12, 1.2, 0)]
W["root"] = widget("WGT-root", v, ring_edges(48) + [(48, 49), (49, 50), (49, 51)])
v = [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)]
W["body"] = widget("WGT-body", v, ring_edges(4))
v = circle_pts(32, 1.0, 'XZ')
W["circle"] = widget("WGT-circle", v, ring_edges(32))
# turret: crosshair ring + forward arrow
v = circle_pts(32, 1.0, 'XZ') + [(-1.3, 0, 0), (-0.7, 0, 0), (0.7, 0, 0), (1.3, 0, 0), (0, 0, -1.3), (0, 0, -0.7), (0, 0, 0.7), (0, 0, 1.3)]
e = ring_edges(32) + [(32, 33), (34, 35), (36, 37), (38, 39)]
W["turret"] = widget("WGT-turret", v, e)
v = circle_pts(24, 1.0, 'XZ') + circle_pts(24, 1.0, 'XZ', (0, 0.25, 0))
e = ring_edges(24) + ring_edges(24, 24) + [(i, i + 24) for i in range(0, 24, 6)]
W["gatling"] = widget("WGT-gatling", v, e)
v = [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0), (0, 1, 0), (0, 1.4, 0)]
W["foot"] = widget("WGT-foot", v, ring_edges(4) + [(4, 5)])
v = [(0, 0, 1), (1, 0, 0), (0, 0, -1), (-1, 0, 0), (0, 1, 0), (0, -1, 0)]
e = [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (4, 2), (2, 5), (5, 0), (1, 4), (4, 3), (3, 5), (5, 1)]
W["diamond"] = widget("WGT-diamond", v, e)
v = circle_pts(24, 1.0, 'XY') + circle_pts(24, 1.0, 'XZ') + circle_pts(24, 1.0, 'YZ')
W["sphere"] = widget("WGT-sphere", v, ring_edges(24) + ring_edges(24, 24) + ring_edges(24, 48))

# --------------------------------------------------------------------------- 5. armature
arm_data = bpy.data.armatures.new("RIG_Robot")
rig = bpy.data.objects.new("RIG_Robot", arm_data)
robot_col.objects.link(rig)
arm_data["robot_rig"] = RIG_ID
arm_data.display_type = 'OCTAHEDRAL'
rig.show_in_front = True
for o in bpy.context.view_layer.objects:
    o.select_set(False)
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
eb = arm_data.edit_bones
X_AXIS = Vector((1, 0, 0))


def bone(name, head, tail, parent=None, connect=False, deform=False):
    b = eb.new(name)
    b.head, b.tail = Vector(head), Vector(tail)
    d = (b.tail - b.head).normalized()
    z = X_AXIS.cross(d)
    if z.length > 1e-4:
        b.align_roll(z)   # local X axis == world +X for every bone (clean mirroring)
    if parent:
        b.parent = eb[parent]
        b.use_connect = connect
    b.use_deform = deform
    return b


def mx(v):
    return Vector((-v.x, v.y, v.z))


bone("root", (0, 0, 0), (0, 1.6, 0))
bone("body", PELVIS, PELVIS + Vector((0, 1.3, 0)), "root")
bone("DEF-pelvis", PELVIS, PELVIS + Vector((0, 0, 0.5)), "body", deform=True)
bone("torso", TORSO, TORSO + Vector((0, 0, 0.9)), "DEF-pelvis")
bone("aim_target", Vector((0, -9.0, TP.z)), Vector((0, -9.6, TP.z)), "root")
for side, f in (("L", lambda v: v.copy()), ("R", mx)):
    s = "." + side
    tp = f(TP)
    bone("turret" + s, tp, tp + Vector((0, -0.9, 0)), "torso")
    bone("gatling" + s, f(G_back), f(G_tip), "turret" + s)
    bone("DEF-thigh" + s, f(H), f(K1), "DEF-pelvis", deform=True)
    bone("DEF-knee" + s, f(K1), f(K2), "DEF-thigh" + s, connect=True, deform=True)
    bone("DEF-shin" + s, f(K2), f(A), "DEF-knee" + s, connect=True, deform=True)
    bone("DEF-foot" + s, f(A), f(A) + Vector((0, -0.55, 0)), "DEF-shin" + s, connect=True, deform=True)
    bone("toe_front" + s, f(TOE_F[0]), f(TOE_F[1]), "DEF-foot" + s)
    bone("toe_back" + s, f(TOE_B[0]), f(TOE_B[1]), "DEF-foot" + s)
    bone("foot_ik" + s, f(A), f(A) + Vector((0, -0.55, 0)), "root")
    pole = f(Vector((LEG_X, K2.y + 2.4, K2.z)))
    bone("knee_pole" + s, pole, pole + Vector((0, 0.35, 0)), "root")
bpy.ops.object.mode_set(mode='POSE')
pbs = rig.pose.bones

# collections & colours
COLS = {
    "Corps": ["root", "body", "torso"],
    "Tourelles": ["turret.L", "turret.R", "gatling.L", "gatling.R", "aim_target"],
    "Jambes": ["foot_ik.L", "foot_ik.R", "knee_pole.L", "knee_pole.R", "toe_front.L", "toe_front.R", "toe_back.L", "toe_back.R"],
}
for cname, names in COLS.items():
    bc = arm_data.collections.new(cname)
    for n in names:
        bc.assign(arm_data.bones[n])
mech = arm_data.collections.new("Mecanique")
for b in arm_data.bones:
    if b.name.startswith("DEF-"):
        mech.assign(b)
mech.is_visible = False

for pb in pbs:
    pal = 'THEME09'
    if pb.name.endswith(".L"):
        pal = 'THEME04'
    elif pb.name.endswith(".R"):
        pal = 'THEME01'
    if pb.name in ("root", "aim_target"):
        pal = 'THEME03'
    pb.color.palette = pal
    pb.bone.color.palette = pal
    pb.rotation_mode = 'XYZ'


def shape(name, wgt, scale=(1, 1, 1), trans=(0, 0, 0), rot=(0, 0, 0), wire=2.0):
    pb = pbs[name]
    pb.custom_shape = W[wgt]
    pb.custom_shape_scale_xyz = scale
    pb.custom_shape_translation = trans
    pb.custom_shape_rotation_euler = rot
    pb.use_custom_shape_bone_size = False
    if hasattr(pb.bone, "show_wire"):
        pb.bone.show_wire = True
    if hasattr(pb, "custom_shape_wire_width"):
        pb.custom_shape_wire_width = wire


shape("root", "root", (2.6, 2.6, 2.6), wire=3)
shape("body", "body", (1.0, 0.85, 1), (0, -0.55, 0))
shape("torso", "circle", (1.15, 1, 1.15), (0, 0.25, 0))
shape("aim_target", "sphere", (0.35, 0.35, 0.35))
for side in ("L", "R"):
    s = "." + side
    sgn = 1 if side == "L" else -1
    shape("turret" + s, "turret", (0.32, 0.32, 0.32), (0, 2.25, 0))
    shape("gatling" + s, "gatling", (0.24, 1, 0.24), (0, 0.9, 0))
    # foot bone points forward: local z == world -Z, so +z moves the shape down to the sole
    shape("foot_ik" + s, "foot", (0.62, 0.95, 1), (0, 0.15, ANKLE_H - 0.02))
    shape("knee_pole" + s, "diamond", (0.16, 0.16, 0.16))
    shape("toe_front" + s, "circle", (0.3, 1, 0.3), (0, 0.15, 0))
    shape("toe_back" + s, "circle", (0.25, 1, 0.25), (0, 0.15, 0))

# --------------------------------------------------------------------------- 6. constraints & locks
DEG = math.radians


def limit_rot(pb, x=None, y=None, z=None):
    c = pb.constraints.new('LIMIT_ROTATION')
    c.name = "Limites"
    c.owner_space = 'LOCAL'
    c.use_transform_limit = True
    for ax, rng in (("x", x), ("y", y), ("z", z)):
        if rng is not None:
            setattr(c, f"use_limit_{ax}", True)
            setattr(c, f"min_{ax}", DEG(rng[0]))
            setattr(c, f"max_{ax}", DEG(rng[1]))
    return c


def lock(pb, loc=(False, False, False), rot=(False, False, False), scale=(True, True, True)):
    pb.lock_location = loc
    pb.lock_rotation = rot
    pb.lock_scale = scale


lock(pbs["root"])
lock(pbs["body"])
lock(pbs["torso"], loc=(True, True, True))
limit_rot(pbs["torso"], x=(-12, 12), y=(-75, 75), z=(-8, 8))
lock(pbs["aim_target"])

# armature level custom properties (drive the aim-tracking influence)
rig["turret_track"] = 0.0
rig.id_properties_ui("turret_track").update(min=0.0, max=1.0, description="Les tourelles visent le contrôleur aim_target")


def add_driver_influence(con, prop):
    fc = con.driver_add("influence")
    drv = fc.driver
    drv.type = 'AVERAGE'
    var = drv.variables.new()
    var.name = "v"
    var.type = 'SINGLE_PROP'
    var.targets[0].id = rig
    var.targets[0].data_path = f'["{prop}"]'


for side in ("L", "R"):
    s = "." + side
    t = pbs["turret" + s]
    lock(t, loc=(True, False, True), rot=(False, True, False))
    dt = t.constraints.new('DAMPED_TRACK')
    dt.name = "Suivi cible"
    dt.target, dt.subtarget, dt.track_axis = rig, "aim_target", 'TRACK_Y'
    add_driver_influence(dt, "turret_track")
    # local X: negative = barrel up ; local Z: positive = towards robot right
    limit_rot(t, x=(-40, 22), z=(-28, 28))
    ll = t.constraints.new('LIMIT_LOCATION')
    ll.name = "Recul"
    ll.owner_space = 'LOCAL'
    ll.use_transform_limit = True
    ll.use_min_y, ll.use_max_y, ll.min_y, ll.max_y = True, True, -0.3, 0.05
    g = pbs["gatling" + s]
    lock(g, loc=(True, True, True), rot=(True, False, True))
    for toe in ("toe_front" + s, "toe_back" + s):
        lock(pbs[toe], loc=(True, True, True), rot=(False, True, True))
        limit_rot(pbs[toe], x=(-45, 45))
    lock(pbs["foot_ik" + s])
    lock(pbs["knee_pole" + s], rot=(True, True, True))

    thigh, knee, shin, foot = (pbs[n + s] for n in ("DEF-thigh", "DEF-knee", "DEF-shin", "DEF-foot"))
    thigh.lock_ik_y = True
    knee.lock_ik_x = knee.lock_ik_y = knee.lock_ik_z = True   # link rigid with the thigh (piston)
    shin.lock_ik_y = shin.lock_ik_z = True                    # rear knee = hinge on X
    ik = shin.constraints.new('IK')
    ik.name = "IK jambe"
    ik.target, ik.subtarget = rig, "foot_ik" + s
    ik.pole_target, ik.pole_subtarget = rig, "knee_pole" + s
    ik.chain_count = 3
    ik.use_tail = True
    ik.use_stretch = False
    cr = foot.constraints.new('COPY_ROTATION')
    cr.name = "Rotation pied"
    cr.target, cr.subtarget = rig, "foot_ik" + s

bpy.ops.object.mode_set(mode='OBJECT')


# pole angle: pick the value that keeps the rest pose untouched
def chain_error(side):
    bpy.context.view_layer.update()
    err = 0.0
    for n in ("DEF-thigh", "DEF-knee", "DEF-shin"):
        pb = pbs[n + "." + side]
        err += (pb.matrix.to_translation() - pb.bone.matrix_local.to_translation()).length
        err += (pb.matrix.to_quaternion().rotation_difference(pb.bone.matrix_local.to_quaternion())).angle
        err += (pb.tail - pb.bone.tail_local).length
    return err


for side in ("L", "R"):
    ik = pbs["DEF-shin." + side].constraints["IK jambe"]
    best = (1e9, 0)
    for a in range(-180, 180, 5):
        ik.pole_angle = DEG(a)
        best = min(best, (chain_error(side), a))
    lo = best[1]
    for a10 in range((lo - 5) * 10, (lo + 5) * 10 + 1):
        ik.pole_angle = DEG(a10 / 10)
        best = min(best, (chain_error(side), a10 / 10))
    ik.pole_angle = DEG(best[1])
    log(f"pole angle {side}: {best[1]:.1f} deg (rest error {best[0]:.5f})")

# --------------------------------------------------------------------------- 7. mesh parenting + names
PARTS = {
    "DEF-pelvis": [("Cube.046_Low_body_0", "GEO-pelvis"), ("Cube.043_Low_legs_0", "GEO-hip_axle"),
                   ("Cube.042_Low_body_0", "GEO-pelvis_plate_front"), ("Cube.044_Low_body_0", "GEO-pelvis_plate_back"),
                   ("Cylinder.047_Low_body_0", "GEO-pelvis_rod")],
    "torso": [("Cylinder.053_Low_body_0", "GEO-torso")],
    "turret.L": [("Cylinder.053_Low_body_0.003", "GEO-turret.L")],
    "turret.R": [("Cylinder.053_Low_body_0.004", "GEO-turret.R")],
    "gatling.L": [("Cylinder.053_Low_body_0.001", "GEO-gatling.L")],
    "gatling.R": [("Cylinder.053_Low_body_0.002", "GEO-gatling.R")],
    "DEF-thigh.L": [("Cube.048_Low_legs_0.001", "GEO-thigh.L"), ("Piston", "GEO-piston.L")],
    "DEF-thigh.R": [("Cube.040_Low_legs_0", "GEO-thigh.R"), ("Piston.001", "GEO-piston.R")],
    "DEF-knee.L": [("Entre cuisse tibia", "GEO-knee_link.L")],
    "DEF-knee.R": [("Entre cuisse tibia.001", "GEO-knee_link.R")],
    "DEF-shin.L": [("Cylinder.061_Low_legs_0.001", "GEO-shin.L"), ("Cylinder.067_Low_legs_0.001", "GEO-shin_bolt.L"),
                   ("NurbsPath.004_Low_legs_0.001", "GEO-shin_cable.L"), ("Cube.057_Low_legs_0.001", "GEO-shin_clip.L")],
    "DEF-shin.R": [("Cylinder.061_Low_legs_0", "GEO-shin.R")],
    "DEF-foot.L": [("Plane.010_Low_legs_0.003", "GEO-ankle.L"), ("Plane.010_Low_legs_0.007", "GEO-foot.L")],
    "DEF-foot.R": [("Plane.010_Low_legs_0", "GEO-ankle.R"), ("Plane.010_Low_legs_0.004", "GEO-foot.R")],
    "toe_front.L": [("Plane.010_Low_legs_0.006", "GEO-toe_front.L"), ("Plane.010_Low_legs_0.009", "GEO-toe_front_plate.L")],
    "toe_front.R": [("Plane.010_Low_legs_0.001", "GEO-toe_front.R"), ("Plane.010_Low_legs_0.008", "GEO-toe_front_plate.R")],
    "toe_back.L": [("Plane.010_Low_legs_0.005", "GEO-toe_back.L")],
    "toe_back.R": [("Plane.010_Low_legs_0.002", "GEO-toe_back.R")],
}
done = set()
for bname, items in PARTS.items():
    b = arm_data.bones[bname]
    head_w = rig.matrix_world @ b.head_local
    for old, new in items:
        o = O[old]
        o.data.transform(Matrix.Translation(-head_w))      # origin on the joint
        o.matrix_world = Matrix.Translation(head_w)
        mw = o.matrix_world.copy()
        o.parent = rig
        o.parent_type = 'BONE'
        o.parent_bone = bname
        o.matrix_parent_inverse = Matrix.Identity(4)
        o.matrix_basis = (rig.matrix_world @ b.matrix_local @ Matrix.Translation((0, b.length, 0))).inverted() @ mw
        o.name = new
        o.data.name = new
        o.hide_select = False
        done.add(o.name)
left_over = [o.name for o in bpy.data.objects if o.type == 'MESH' and o.name not in done]
log("unparented meshes left:", left_over)
rig.data.pose_position = 'POSE'

# --------------------------------------------------------------------------- 8. sanity
bpy.context.view_layer.update()
# rest pose must not move anything: compare world bbox before/after evaluating constraints
for side in ("L", "R"):
    log(f"chain error {side}: {chain_error(side):.6f}")
sole = min(world_pts(O[n])[:, 2].min() for n in ("GEO-foot.L", "GEO-foot.R"))
log(f"lowest sole z after rig evaluation: {sole:.4f}")

scene.frame_start, scene.frame_end = 1, 240
scene.render.fps = 24
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
if OUT:
    bpy.ops.wm.save_as_mainfile(filepath=OUT, compress=True)
    log("saved", OUT)
