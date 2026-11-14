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
  * fixes the gatling spin axis (it was tilted the wrong way: the barrels wobbled)
  * splits each weapon arm into shoulder hub (fixed) / shoulder (pitch) / gun (yaw,
    pitch, recoil) and measures collision-free limits on the real geometry
  * builds a new armature "RIG_Robot": IK legs (3 segments + foot + toes + front
    flap), hydraulic pistons that slide with the knee, body / torso (head),
    shoulders, turrets, gatling spin, muzzle and rocket ports for the effects,
    aim target, custom shapes, bone collections and colours
"""
import bpy
import bmesh
import math
import sys
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else None
RIG_ID = "MECH_ROBOT_V2"

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
LEG_X = 1.02
H = Vector((LEG_X, 0.58, -1.12 + Z))
for v in (K1, K2, A):
    v.x = LEG_X
log("pivots  hip", H, " knee1", K1, " knee2", K2, " ankle", A)
ANKLE_H = A.z
TOE_F = (Vector((LEG_X, -0.45, ANKLE_H - 0.30)), Vector((LEG_X, -1.12, 0.10)))
TOE_B = (Vector((LEG_X, 0.22, ANKLE_H - 0.30)), Vector((LEG_X, 0.78, 0.10)))
PELVIS = Vector((0, 0.45, H.z))
TORSO = P(0, 0.42, -0.62)


def mx(v):
    return Vector((-v.x, v.y, v.z))


# --------------------------------------------------------------------------- 3b. split multi-part meshes
def islands_of(me):
    """Connected component id for every vertex (union-find over the edges)."""
    n = len(me.vertices)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    ev = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", ev)
    for a, b in ev.reshape(-1, 2):
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[ra] = rb
    return np.array([find(i) for i in range(n)])


def split_object(obj, classify):
    """Split ``obj`` by loose parts. ``classify(min, max)`` -> group name. Returns {group: object}.

    Uses the mesh separate operator so UVs, custom normals and materials are kept."""
    me = obj.data
    comp = islands_of(me)
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)   # transforms are baked: local == world
    group_of = {}
    for c in np.unique(comp):
        pts = co[comp == c]
        group_of[c] = classify(pts.min(0), pts.max(0))
    vgroup = np.array([group_of[c] for c in comp])
    names = sorted(set(group_of.values()))
    out = {}
    for gname in names[1:]:
        me = obj.data
        sel = vgroup == gname
        me.vertices.foreach_set("select", sel)
        ev = np.empty(len(me.edges) * 2, dtype=np.int64)
        me.edges.foreach_get("vertices", ev)
        me.edges.foreach_set("select", sel[ev.reshape(-1, 2)].all(1))
        psel = np.array([bool(sel[list(p.vertices)].all()) for p in me.polygons], dtype=bool)
        me.polygons.foreach_set("select", psel)
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='VERT')
        bpy.ops.object.mode_set(mode='OBJECT')
        me.vertices.foreach_set("select", sel)         # edit mode toggling may reset flags
        me.edges.foreach_set("select", sel[ev.reshape(-1, 2)].all(1))
        me.polygons.foreach_set("select", psel)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.separate(type='SELECTED')
        bpy.ops.object.mode_set(mode='OBJECT')
        new = [o for o in bpy.context.selected_objects if o != obj][0]
        out[gname] = new
        vgroup = vgroup[~sel]
    out[names[0]] = obj
    return out


def turret_class(mn, mx_):
    ax = max(abs(mn[0]), abs(mx_[0]))
    if ax < 0.97:
        return "a_hub"
    if mn[2] >= 4.55 or (mn[2] >= 3.92 and ax <= 1.38):
        return "b_shoulder"
    return "c_gun"


def piston_class(mn, mx_):
    if mn[2] > 2.25:
        return "a_guide"       # cross bar fixed in the thigh
    if mx_[2] < 1.72:
        return "c_eye"         # lower eyes on the knee link
    return "b_rod"


SPLIT = {}
for side, name in (("L", "Cylinder.053_Low_body_0.003"), ("R", "Cylinder.053_Low_body_0.004")):
    parts = split_object(O[name], turret_class)
    SPLIT["hub" + side], SPLIT["shoulder" + side], SPLIT["gun" + side] = parts["a_hub"], parts["b_shoulder"], parts["c_gun"]
for side, name in (("L", "Piston"), ("R", "Piston.001")):
    parts = split_object(O[name], piston_class)
    SPLIT["guide" + side], SPLIT["rod" + side], SPLIT["eye" + side] = parts["a_guide"], parts["b_rod"], parts["c_eye"]
log("split:", {k: (o.name, len(o.data.vertices)) for k, o in SPLIT.items()})


# --------------------------------------------------------------------------- 3c. mechanical axes
def fit_spin_axis(pts, fold=60.0):
    """Axis that maps the gatling onto itself after a ``fold`` degree turn (6 barrels)."""
    kd = KDTree(len(pts))
    for i, p in enumerate(pts):
        kd.insert(p, i)
    kd.balance()
    y0, y1 = pts[:, 1].max(), pts[:, 1].min()
    bar = [Vector(p) for p in pts[pts[:, 1] < y1 + 0.95][::7]]
    c = (pts.min(0) + pts.max(0)) / 2
    a = np.array([c[0], y0 - 0.11, c[2]])
    b = np.array([c[0], y1 + 0.05, c[2]])

    def score(a, b):
        ax = Vector(b) - Vector(a)
        M = Matrix.Translation(a) @ Matrix.Rotation(math.radians(fold), 4, ax.normalized()) @ Matrix.Translation(-Vector(a))
        return sum(kd.find(M @ p)[2] for p in bar) / len(bar)
    best, step = score(a, b), 0.05
    while step > 0.0005:
        improved = False
        for v, i in ((a, 0), (a, 2), (b, 0), (b, 2)):
            for d in (step, -step):
                v[i] += d
                s = score(a, b)
                if s < best - 1e-7:
                    best, improved = s, True
                else:
                    v[i] -= d
        if not improved:
            step /= 2
    a, b = Vector(a), Vector(b)

    def at(y):
        return a + (b - a) * ((y - a.y) / (b.y - a.y))
    return at(y0), at(y1), best


AX = {}
for side, name in (("L", "Cylinder.053_Low_body_0.001"), ("R", "Cylinder.053_Low_body_0.002")):
    sgn = 1 if side == "L" else -1
    g = world_pts(O[name])
    back, tip, err = fit_spin_axis(g)
    gdir = (tip - back).normalized()
    hub = world_pts(SPLIT["hub" + side])
    hc = bbox_center(hub)
    hc.x = hub[:, 0].max() if side == "L" else hub[:, 0].min()
    gp = hc + Vector((sgn * 0.55, -0.325, 0.06))          # gun yaw / pitch mount (collision search)
    gun = world_pts(SPLIT["gun" + side])
    front = gun[(gun[:, 1] < gun[:, 1].min() + 0.08) & (np.abs(gun[:, 0] - sgn * 1.51) < 0.13)]
    zm = (front[:, 2].min() + front[:, 2].max()) / 2
    ports = []
    for sel in (front[front[:, 2] > zm], front[front[:, 2] <= zm]):
        ports.append(Vector(((sel[:, 0].min() + sel[:, 0].max()) / 2, sel[:, 1].min(), (sel[:, 2].min() + sel[:, 2].max()) / 2)))
    plate = world_pts(O["Plane.010_Low_legs_0.009" if side == "L" else "Plane.010_Low_legs_0.008"])
    low = plate[plate[:, 2] < plate[:, 2].min() + 0.012]
    top = plate[plate[:, 2] > plate[:, 2].max() - 0.04]
    cx = (plate[:, 0].min() + plate[:, 0].max()) / 2
    flap = (Vector((cx, low[:, 1].mean(), plate[:, 2].min() + 0.07)), Vector((cx, top[:, 1].mean(), plate[:, 2].max())))
    guide, eye = world_pts(SPLIT["guide" + side]), world_pts(SPLIT["eye" + side])
    ta, ba = bbox_center(guide), bbox_center(eye)
    ta.x = ba.x = sgn * LEG_X
    AX[side] = dict(g_back=back, g_tip=tip, gdir=gdir, hub=hc, gun=gp, ports=ports, flap=flap, piston=(ba, ta))
    log(f"{side}: gatling axis {back} -> {tip} (60 deg symmetry error {err * 1000:.2f} mm)")
    log(f"{side}: shoulder hub {hc}  gun mount {gp}  rocket ports {ports[0]} {ports[1]}")
    log(f"{side}: flap hinge {flap[0]}  piston {ba} -> {ta}")


# --------------------------------------------------------------------------- 3d. collision-free limits
def tri_data(objs):
    V, T = [], []
    for o in objs:
        base = len(V)
        V += [o.matrix_world @ v.co for v in o.data.vertices]
        for p in o.data.polygons:
            vs = p.vertices
            for t in range(1, len(vs) - 1):
                T.append((base + vs[0], base + vs[t], base + vs[t + 1]))
    return V, T


def bvh(objs):
    return BVHTree.FromPolygons(*tri_data(objs))


def penetration(V, M, trees, step=3, depth=0.02):
    n = 0
    for v in V[::step]:
        w = M @ v
        for t in trees:
            hit = t.find_nearest(w, 0.3)
            if hit[0] is not None and (w - hit[0]).dot(hit[1]) < -depth:
                n += 1
                break
    return n


def free_range(pivot, axis, objs, trees, cap, step=2):
    """Largest rotation (deg) in the + and - direction that adds no penetration."""
    V = tri_data(objs)[0]
    base = penetration(V, Matrix(), trees)
    res = []
    for sgn in (1, -1):
        last = 0
        for a in range(step, cap + 1, step):
            M = Matrix.Translation(pivot) @ Matrix.Rotation(math.radians(sgn * a), 4, axis) @ Matrix.Translation(-pivot)
            if penetration(V, M, trees) > base + 6:
                break
            last = a
        res.append(last)
    return res


body_static = [O[n] for n in ("Cube.046_Low_body_0", "Cube.043_Low_legs_0", "Cube.042_Low_body_0", "Cube.044_Low_body_0",
                               "Cylinder.047_Low_body_0", "Cube.048_Low_legs_0.001", "Entre cuisse tibia")] + [SPLIT["guideL"]]
torso_obj = O["Cylinder.053_Low_body_0"]
gunL = [SPLIT["gunL"], O["Cylinder.053_Low_body_0.001"]]
t_body = bvh(body_static + [torso_obj, SPLIT["hubL"]])
t_sh = bvh([SPLIT["shoulderL"]])
XA, ZA = Vector((1, 0, 0)), Vector((0, 0, 1))
yaw_out, yaw_in = free_range(AX["L"]["gun"], ZA, gunL, [t_body, t_sh], 36)
down, up = free_range(AX["L"]["gun"], XA, gunL, [t_body, t_sh], 40)
sh_down, sh_up = free_range(AX["L"]["hub"], XA, [SPLIT["shoulderL"]] + gunL, [t_body], 30)
moving_upper = [torso_obj] + [SPLIT[k + s] for k in ("hub", "shoulder", "gun") for s in "LR"] + \
               [O["Cylinder.053_Low_body_0.001"], O["Cylinder.053_Low_body_0.002"]]
legs_static = [o for o in body_static if o.name != "Cube.046_Low_body_0"] + \
              [O[n] for n in ("Cube.040_Low_legs_0", "Entre cuisse tibia.001")] + [SPLIT["guideR"]]
torso_l, torso_r = free_range(TORSO, ZA, moving_upper, [bvh(legs_static)], 120, step=4)


def margin(v, lo, hi):
    return float(max(lo, min(hi, v - 2)))


LIM = dict(
    turret_yaw_in=margin(yaw_in, 10, 34), turret_yaw_out=margin(yaw_out, 6, 34),
    turret_up=margin(up, 10, 30), turret_down=margin(down, 10, 35),
    shoulder_up=margin(sh_up, 5, 28), shoulder_down=margin(sh_down, 5, 20),
    torso_yaw=margin(min(torso_l, torso_r), 30, 110),
)
log("collision-free limits (deg):", {k: round(v, 1) for k, v in LIM.items()},
    f"| raw gun yaw out/in {yaw_out}/{yaw_in} pitch up/down {up}/{down} shoulder up/down {sh_up}/{sh_down} torso {torso_l}/{torso_r}")

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


def circle_pts(n=32, r=1.0, plane='XZ', off=(0, 0, 0), a0=0.0, a1=2 * math.pi):
    pts = []
    full = abs(a1 - a0 - 2 * math.pi) < 1e-6
    for i in range(n if full else n + 1):
        a = a0 + (a1 - a0) * i / n
        c, s = math.cos(a) * r, math.sin(a) * r
        p = {'XZ': (c, 0, s), 'XY': (c, s, 0), 'YZ': (0, c, s)}[plane]
        pts.append((p[0] + off[0], p[1] + off[1], p[2] + off[2]))
    return pts


def ring_edges(n, start=0):
    return [(start + i, start + (i + 1) % n) for i in range(n)]


def chain_edges(n, start=0):
    return [(start + i, start + i + 1) for i in range(n - 1)]


W = {}
v = circle_pts(48, 1.0, 'XY')
v += [(0, 1.0, 0), (0, 1.35, 0), (-0.12, 1.2, 0), (0.12, 1.2, 0)]
W["root"] = widget("WGT-root", v, ring_edges(48) + [(48, 49), (49, 50), (49, 51)])
v = [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)]
W["body"] = widget("WGT-body", v, ring_edges(4))
v = circle_pts(32, 1.0, 'XZ')
W["circle"] = widget("WGT-circle", v, ring_edges(32))
v = circle_pts(32, 1.0, 'XZ') + [(-1.3, 0, 0), (-0.7, 0, 0), (0.7, 0, 0), (1.3, 0, 0), (0, 0, -1.3), (0, 0, -0.7), (0, 0, 0.7), (0, 0, 1.3)]
W["turret"] = widget("WGT-turret", v, ring_edges(32) + [(32, 33), (34, 35), (36, 37), (38, 39)])
v = circle_pts(24, 1.0, 'XZ') + circle_pts(24, 1.0, 'XZ', (0, 0.25, 0))
W["gatling"] = widget("WGT-gatling", v, ring_edges(24) + ring_edges(24, 24) + [(i, i + 24) for i in range(0, 24, 6)])
v = [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0), (0, 1, 0), (0, 1.4, 0)]
W["foot"] = widget("WGT-foot", v, ring_edges(4) + [(4, 5)])
v = [(0, 0, 1), (1, 0, 0), (0, 0, -1), (-1, 0, 0), (0, 1, 0), (0, -1, 0)]
e = [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (4, 2), (2, 5), (5, 0), (1, 4), (4, 3), (3, 5), (5, 1)]
W["diamond"] = widget("WGT-diamond", v, e)
v = circle_pts(24, 1.0, 'XY') + circle_pts(24, 1.0, 'XZ') + circle_pts(24, 1.0, 'YZ')
W["sphere"] = widget("WGT-sphere", v, ring_edges(24) + ring_edges(24, 24) + ring_edges(24, 48))
v = circle_pts(20, 1.0, 'YZ', a0=-0.9, a1=0.9)            # pitch arc around local X
W["arc_x"] = widget("WGT-arc_x", v + [(0, 0.78, -0.75), (0, 0.55, -0.62)], chain_edges(21) + [(0, 21), (0, 22)])
v = [(-1, 0, 0), (1, 0, 0), (1, 1, 0), (-1, 1, 0)]
W["plate"] = widget("WGT-plate", v, ring_edges(4))

# --------------------------------------------------------------------------- 5. armature
arm_data = bpy.data.armatures.new("RIG_Robot")
rig = bpy.data.objects.new("RIG_Robot", arm_data)
robot_col.objects.link(rig)
arm_data["robot_rig"] = RIG_ID
for k, val in LIM.items():
    arm_data["lim_" + k] = val
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


bone("root", (0, 0, 0), (0, 1.6, 0))
bone("body", PELVIS, PELVIS + Vector((0, 1.3, 0)), "root")
bone("DEF-pelvis", PELVIS, PELVIS + Vector((0, 0, 0.5)), "body", deform=True)
bone("torso", TORSO, TORSO + Vector((0, 0, 0.9)), "DEF-pelvis")
bone("aim_target", Vector((0, -9.0, AX["L"]["gun"].z)), Vector((0, -9.6, AX["L"]["gun"].z)), "root")
for side, f in (("L", lambda v: v.copy()), ("R", mx)):
    s = "." + side
    ax = AX[side]
    bone("shoulder" + s, ax["hub"], ax["hub"] + Vector((0, -0.7, 0)), "torso")
    bone("turret" + s, ax["gun"], ax["gun"] + Vector((0, -0.9, 0)), "shoulder" + s)
    bone("gatling" + s, ax["g_back"], ax["g_tip"], "turret" + s)
    bone("muzzle" + s, ax["g_tip"], ax["g_tip"] + ax["gdir"] * 0.3, "turret" + s)
    for i, p in enumerate(ax["ports"], 1):
        bone(f"rocket_port_{i}" + s, p, p + ax["gdir"] * 0.3, "turret" + s)
    bone("DEF-thigh" + s, f(H), f(K1), "DEF-pelvis", deform=True)
    bone("DEF-knee" + s, f(K1), f(K2), "DEF-thigh" + s, connect=True, deform=True)
    bone("DEF-shin" + s, f(K2), f(A), "DEF-knee" + s, connect=True, deform=True)
    bone("DEF-foot" + s, f(A), f(A) + Vector((0, -0.55, 0)), "DEF-shin" + s, connect=True, deform=True)
    bone("toe_front" + s, f(TOE_F[0]), f(TOE_F[1]), "DEF-foot" + s)
    bone("toe_back" + s, f(TOE_B[0]), f(TOE_B[1]), "DEF-foot" + s)
    bone("toe_flap" + s, ax["flap"][0], ax["flap"][1], "toe_front" + s)
    ba, ta = ax["piston"]
    bone("piston_anchor" + s, ta, ta + (ta - ba).normalized() * 0.15, "DEF-thigh" + s)
    bone("piston_rod" + s, ba, ta, "DEF-knee" + s)
    bone("foot_ik" + s, f(A), f(A) + Vector((0, -0.55, 0)), "root")
    pole = f(Vector((LEG_X, K2.y + 2.4, K2.z)))
    bone("knee_pole" + s, pole, pole + Vector((0, 0.35, 0)), "root")
bpy.ops.object.mode_set(mode='POSE')
pbs = rig.pose.bones

COLS = {
    "Corps": ["root", "body", "torso"],
    "Tourelles": ["shoulder.L", "shoulder.R", "turret.L", "turret.R", "gatling.L", "gatling.R", "aim_target"],
    "Jambes": ["foot_ik.L", "foot_ik.R", "knee_pole.L", "knee_pole.R", "toe_front.L", "toe_front.R",
               "toe_back.L", "toe_back.R", "toe_flap.L", "toe_flap.R"],
}
for cname, names in COLS.items():
    bc = arm_data.collections.new(cname)
    for n in names:
        bc.assign(arm_data.bones[n])
mech = arm_data.collections.new("Mecanique")
for b in arm_data.bones:
    if b.name.startswith(("DEF-", "piston_", "muzzle", "rocket_port")):
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
    shape("shoulder" + s, "arc_x", (1, 0.55, 0.55))
    shape("turret" + s, "turret", (0.32, 0.32, 0.32), (0, 2.0, 0))
    shape("gatling" + s, "gatling", (0.24, 1, 0.24), (0, 0.9, 0))
    shape("foot_ik" + s, "foot", (0.62, 0.95, 1), (0, 0.15, ANKLE_H - 0.02))
    shape("knee_pole" + s, "diamond", (0.16, 0.16, 0.16))
    shape("toe_front" + s, "circle", (0.3, 1, 0.3), (0, 0.15, 0))
    shape("toe_back" + s, "circle", (0.25, 1, 0.25), (0, 0.15, 0))
    shape("toe_flap" + s, "plate", (0.3, 0.6, 1))

# --------------------------------------------------------------------------- 6. constraints & locks
DEG = math.radians


def limit_rot(pb, x=None, y=None, z=None):
    c = pb.constraints.new('LIMIT_ROTATION')
    c.name = "Limites"
    c.owner_space = 'LOCAL'
    c.use_transform_limit = True
    for ax_, rng in (("x", x), ("y", y), ("z", z)):
        if rng is not None:
            setattr(c, f"use_limit_{ax_}", True)
            setattr(c, f"min_{ax_}", DEG(rng[0]))
            setattr(c, f"max_{ax_}", DEG(rng[1]))
    return c


def lock(pb, loc=(False, False, False), rot=(False, False, False), scale=(True, True, True)):
    pb.lock_location = loc
    pb.lock_rotation = rot
    pb.lock_scale = scale


lock(pbs["root"])
lock(pbs["body"])
lock(pbs["torso"], loc=(True, True, True))
limit_rot(pbs["torso"], x=(-12, 12), y=(-LIM["torso_yaw"], LIM["torso_yaw"]), z=(-8, 8))
lock(pbs["aim_target"])

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


LINK_STIFFNESS = float(__import__("os").environ.get("ROBOT_LINK_STIFFNESS", "0.55"))
for side in ("L", "R"):
    s = "." + side
    sh = pbs["shoulder" + s]
    lock(sh, loc=(True, True, True), rot=(False, True, True))
    limit_rot(sh, x=(-LIM["shoulder_up"], LIM["shoulder_down"]))
    t = pbs["turret" + s]
    lock(t, loc=(True, False, True), rot=(False, True, False))
    dt = t.constraints.new('DAMPED_TRACK')
    dt.name = "Suivi cible"
    dt.target, dt.subtarget, dt.track_axis = rig, "aim_target", 'TRACK_Y'
    add_driver_influence(dt, "turret_track")
    # local X: negative = barrel up ; local Z: positive = towards the robot's right
    zr = (-LIM["turret_yaw_out"], LIM["turret_yaw_in"]) if side == "L" else (-LIM["turret_yaw_in"], LIM["turret_yaw_out"])
    limit_rot(t, x=(-LIM["turret_up"], LIM["turret_down"]), z=zr)
    ll = t.constraints.new('LIMIT_LOCATION')
    ll.name = "Recul"
    ll.owner_space = 'LOCAL'
    ll.use_transform_limit = True
    ll.use_min_y, ll.use_max_y, ll.min_y, ll.max_y = True, True, -0.3, 0.05
    g = pbs["gatling" + s]
    lock(g, loc=(True, True, True), rot=(True, False, True))
    for n in ("muzzle", "rocket_port_1", "rocket_port_2", "piston_anchor"):
        lock(pbs[n + s], loc=(True, True, True), rot=(True, True, True))
    for toe in ("toe_front" + s, "toe_back" + s):
        lock(pbs[toe], loc=(True, True, True), rot=(False, True, True))
        limit_rot(pbs[toe], x=(-45, 45))
    fl = pbs["toe_flap" + s]
    lock(fl, loc=(True, True, True), rot=(False, True, True))
    limit_rot(fl, x=(-55, 40))
    lock(pbs["foot_ik" + s])
    lock(pbs["knee_pole" + s], rot=(True, True, True))
    rod = pbs["piston_rod" + s]
    lock(rod, loc=(True, True, True), rot=(True, True, True))
    pt = rod.constraints.new('DAMPED_TRACK')
    pt.name = "Vérin"
    pt.target, pt.subtarget, pt.track_axis = rig, "piston_anchor" + s, 'TRACK_Y'

    thigh, knee, shin, foot = (pbs[n + s] for n in ("DEF-thigh", "DEF-knee", "DEF-shin", "DEF-foot"))
    thigh.lock_ik_y = True
    knee.lock_ik_y = knee.lock_ik_z = True                    # link hinges on X only (drives the pistons)
    knee.ik_stiffness_x = LINK_STIFFNESS
    knee.use_ik_limit_x = True
    knee.ik_min_x, knee.ik_max_x = DEG(-25), DEG(25)
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
    "torso": [("Cylinder.053_Low_body_0", "GEO-torso"), (SPLIT["hubL"].name, "GEO-shoulder_hub.L"),
              (SPLIT["hubR"].name, "GEO-shoulder_hub.R")],
    "shoulder.L": [(SPLIT["shoulderL"].name, "GEO-shoulder.L")],
    "shoulder.R": [(SPLIT["shoulderR"].name, "GEO-shoulder.R")],
    "turret.L": [(SPLIT["gunL"].name, "GEO-turret.L")],
    "turret.R": [(SPLIT["gunR"].name, "GEO-turret.R")],
    "gatling.L": [("Cylinder.053_Low_body_0.001", "GEO-gatling.L")],
    "gatling.R": [("Cylinder.053_Low_body_0.002", "GEO-gatling.R")],
    "DEF-thigh.L": [("Cube.048_Low_legs_0.001", "GEO-thigh.L"), (SPLIT["guideL"].name, "GEO-piston_guide.L")],
    "DEF-thigh.R": [("Cube.040_Low_legs_0", "GEO-thigh.R"), (SPLIT["guideR"].name, "GEO-piston_guide.R")],
    "piston_rod.L": [(SPLIT["rodL"].name, "GEO-piston_rod.L"), (SPLIT["eyeL"].name, "GEO-piston_eye.L")],
    "piston_rod.R": [(SPLIT["rodR"].name, "GEO-piston_rod.R"), (SPLIT["eyeR"].name, "GEO-piston_eye.R")],
    "DEF-knee.L": [("Entre cuisse tibia", "GEO-knee_link.L")],
    "DEF-knee.R": [("Entre cuisse tibia.001", "GEO-knee_link.R")],
    "DEF-shin.L": [("Cylinder.061_Low_legs_0.001", "GEO-shin.L"), ("Cylinder.067_Low_legs_0.001", "GEO-shin_bolt.L"),
                   ("NurbsPath.004_Low_legs_0.001", "GEO-shin_cable.L"), ("Cube.057_Low_legs_0.001", "GEO-shin_clip.L")],
    "DEF-shin.R": [("Cylinder.061_Low_legs_0", "GEO-shin.R")],
    "DEF-foot.L": [("Plane.010_Low_legs_0.003", "GEO-ankle.L"), ("Plane.010_Low_legs_0.007", "GEO-foot.L")],
    "DEF-foot.R": [("Plane.010_Low_legs_0", "GEO-ankle.R"), ("Plane.010_Low_legs_0.004", "GEO-foot.R")],
    "toe_front.L": [("Plane.010_Low_legs_0.006", "GEO-toe_front.L")],
    "toe_front.R": [("Plane.010_Low_legs_0.001", "GEO-toe_front.R")],
    "toe_flap.L": [("Plane.010_Low_legs_0.009", "GEO-toe_flap.L")],
    "toe_flap.R": [("Plane.010_Low_legs_0.008", "GEO-toe_flap.R")],
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
left_over = [o.name for o in bpy.data.objects if o.type == 'MESH' and o.name not in done and not o.name.startswith("WGT-")]
log("unparented meshes left:", left_over)
rig.data.pose_position = 'POSE'

# --------------------------------------------------------------------------- 8. sanity
bpy.context.view_layer.update()
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
