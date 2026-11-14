"""Autopilot: follow a drawn path, or let an AI patrol on a navmesh.

Both drive the same planted-feet :class:`locomotion.Locomotor` as the gamepad pilot
and bake the result to keyframes, so a patrol looks exactly like a hand-piloted walk.
"""
import math
import random

import bpy
import numpy as np
from bpy.types import Operator
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from . import animdata, nav, rig as R
from . import locomotion as LM
from .pilot import make_locomotor

PATROL_COLLECTION = "Patrouille_Robot"
NAV_NAME = "NAV_Robot"


# =========================================================================== space helpers

def _to_arm(rig):
    return rig.matrix_world.inverted()


def world_mesh(obj, depsgraph=None):
    """(verts world, faces) of an object's evaluated mesh."""
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    me = ev.to_mesh()
    mw = obj.matrix_world
    verts = [tuple(mw @ v.co) for v in me.vertices]
    faces = [tuple(p.vertices) for p in me.polygons]
    ev.to_mesh_clear()
    return verts, faces


def ground_callback(rig, obj):
    """ground(x, y) in armature space from a navmesh / ground mesh (ray cast down)."""
    if obj is None or obj.type != 'MESH':
        return None
    verts, faces = world_mesh(obj)
    if not faces:
        return None
    tree = BVHTree.FromPolygons([Vector(v) for v in verts], faces)
    to_w, to_a = rig.matrix_world, _to_arm(rig)
    top = max(v[2] for v in verts) + 50.0

    def ground(x, y):
        w = to_w @ Vector((x, y, 0.0))
        hit = tree.ray_cast(Vector((w.x, w.y, top)), Vector((0, 0, -1)), 1000.0)
        if hit[0] is None:
            return None
        return (to_a @ hit[0]).z
    return ground


def navmesh_from_object(rig, obj):
    verts, faces = world_mesh(obj)
    to_a = _to_arm(rig)
    return nav.NavMesh([tuple(to_a @ Vector(v)) for v in verts], faces)


def curve_points(rig, obj, spacing=0.4):
    """Sampled points (armature space) of the first spline of a curve object."""
    verts, _f = world_mesh(obj)
    if len(verts) < 2:
        return []
    to_a = _to_arm(rig)
    pts = [tuple(to_a @ Vector(v)) for v in verts]
    out = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, out[-1]) >= spacing * 0.5:
            out.append(p)
    return out


def patrol_points(rig, scene):
    col = bpy.data.collections.get(PATROL_COLLECTION)
    if col is None:
        return []
    to_a = _to_arm(rig)
    objs = sorted((o for o in col.objects), key=lambda o: o.name)
    return [tuple(to_a @ o.matrix_world.translation) for o in objs]


# =========================================================================== behaviours

class Behaviour:
    """Produces locomotion commands + head / turret targets for every frame."""
    done = False
    status = ""

    def update(self, loco, dt):
        raise NotImplementedError


class FollowPath(Behaviour):
    def __init__(self, pts, loop=False, speed=1.0, sprint=False, laps=1):
        self.pf = nav.PathFollower(pts, loop=loop)
        self.speed, self.sprint, self.laps = speed, sprint, laps
        self.head_yaw = 0.0

    def update(self, loco, dt):
        fwd, turn, done = self.pf.command(loco.x, loco.y, loco.yaw, self.speed)
        if self.pf.loop and self.pf.laps >= self.laps:
            done = True
        self.done = done
        # look a little into the turn
        tx, ty, _ = self.pf.point_at(self.pf.s + 5.0)
        err = LM.wrap(LM.heading_of(tx - loco.x, ty - loco.y) - loco.yaw)
        self.head_yaw = LM.approach(self.head_yaw, LM.clamp(err * 0.6, -0.6, 0.6), 2.0, dt)
        return dict(fwd=fwd, turn=turn, sprint=self.sprint, head_yaw=self.head_yaw, head_pitch=0.0,
                    turret_yaw=-self.head_yaw * 0.3, turret_pitch=0.0)


class Patrol(Behaviour):
    """Walk from point to point on the navmesh, stop and scan the area at each one."""

    def __init__(self, navmesh, points, mode='LOOP', wait=3.0, speed=1.0, sprint=False, laps=1,
                 seed=0, scan=True, start=None):
        self.nav = navmesh
        self.points = list(points)
        self.mode = mode
        self.wait = wait
        self.speed, self.sprint = speed, sprint
        self.laps = laps
        self.rng = random.Random(seed)
        self.scan = scan
        self.idx = 0
        self.dir = 1
        self.visited = 0
        self.state = "PLAN"
        self.timer = 0.0
        self.pf = None
        self.head_yaw = 0.0
        self.head_pitch = 0.0
        self.scan_phase = 0.0
        self.failed = []
        if mode == 'WANDER' and not self.points:
            self.points = nav.wander_targets(navmesh, self.rng, n=8)

    def _next_target(self):
        if self.mode == 'WANDER':
            return self.nav.random_point(self.rng)
        return self.points[self.idx]

    def _advance(self):
        n = len(self.points)
        self.visited += 1
        if self.mode == 'PINGPONG' and n > 1:
            if not 0 <= self.idx + self.dir < n:
                self.dir = -self.dir
            self.idx += self.dir
        elif n:
            self.idx = (self.idx + 1) % n

    def update(self, loco, dt):
        cmd = dict(fwd=0.0, turn=0.0, sprint=False, head_yaw=self.head_yaw, head_pitch=self.head_pitch,
                   turret_yaw=0.0, turret_pitch=0.0)
        total = self.laps * max(1, len(self.points)) if self.mode != 'GOTO' else 1
        if self.visited >= total:
            self.done = True
            self.status = "Patrouille terminée"
            return self._settle(cmd, dt)
        if self.state == "PLAN":
            goal = self._next_target()
            path = self.nav.find_path((loco.x, loco.y, loco.z), goal) if self.nav else [(loco.x, loco.y, loco.z), goal]
            if not path or len(path) < 2:
                self.failed.append(goal)
                self._advance()
                return self._settle(cmd, dt)
            self.pf = nav.PathFollower(path)
            self.state = "GO"
            self.status = f"Vers le point {self.idx + 1}"
        if self.state == "GO":
            fwd, turn, done = self.pf.command(loco.x, loco.y, loco.yaw, self.speed)
            tx, ty, _ = self.pf.point_at(self.pf.s + 5.0)
            err = LM.wrap(LM.heading_of(tx - loco.x, ty - loco.y) - loco.yaw)
            self.head_yaw = LM.approach(self.head_yaw, LM.clamp(err * 0.6, -0.6, 0.6), 2.0, dt)
            self.head_pitch = LM.approach(self.head_pitch, 0.0, 2.0, dt)
            cmd.update(fwd=fwd, turn=turn, sprint=self.sprint, head_yaw=self.head_yaw, head_pitch=self.head_pitch,
                       turret_yaw=-self.head_yaw * 0.3)
            if done:
                self.state = "WAIT"
                self.timer = 0.0
                self.scan_phase = self.rng.uniform(0, math.tau)
                self.status = f"Observation au point {self.idx + 1}"
            return cmd
        if self.state == "WAIT":
            self.timer += dt
            if self.scan:
                u = self.timer / max(self.wait, 0.1)
                env = math.sin(math.pi * min(1.0, u)) ** 0.5
                self.head_yaw = LM.approach(self.head_yaw, 0.9 * env * math.sin(self.scan_phase + self.timer * 1.1), 3.0, dt)
                self.head_pitch = LM.approach(self.head_pitch, -0.08 * env, 2.0, dt)
                cmd.update(turret_yaw=0.35 * env * math.sin(self.timer * 1.7 + 1.0),
                           turret_pitch=0.15 * env * math.sin(self.timer * 0.9))
            cmd.update(head_yaw=self.head_yaw, head_pitch=self.head_pitch)
            if self.timer >= self.wait:
                self._advance()
                self.state = "PLAN"
            return cmd
        return cmd

    def _settle(self, cmd, dt):
        self.head_yaw = LM.approach(self.head_yaw, 0.0, 2.0, dt)
        self.head_pitch = LM.approach(self.head_pitch, 0.0, 2.0, dt)
        cmd.update(head_yaw=self.head_yaw, head_pitch=self.head_pitch)
        return cmd


# =========================================================================== bake

def bake(context, rig, behaviour, max_frames, ground=None, key_step=1, simplify=True, settle=True, start=None):
    """Simulate frame by frame from the current frame and key the rig. Returns (f0, f1, removed)."""
    scene = context.scene
    s = scene.robot_anim
    fps = scene.render.fps / scene.render.fps_base
    dt = 1.0 / fps
    f0 = scene.frame_current
    scene.frame_set(f0)
    pose, loco = make_locomotor(rig, s, ground)
    if start is not None:
        loco.teleport(*start)
    animdata.ensure_action(rig)
    channels = {b: R.CHANNELS[b] for b in R.LOCO_BONES + [x for sd in R.SIDES for x in R.TURRET_BONES[sd] if x in R.CHANNELS]}
    animdata.clear_range(rig, list(channels), f0 + 1, f0 + max_frames + int(3 * fps))
    cache = animdata.FCurveCache(rig)
    tur = {sd: [0.0, 0.0] for sd in R.SIDES}
    k = 0
    after_done = 0
    frame = f0
    while k <= max_frames:
        cmd = behaviour.update(loco, dt) if not behaviour.done else dict(fwd=0, turn=0, sprint=False, head_yaw=0.0,
                                                                          head_pitch=0.0, turret_yaw=0.0, turret_pitch=0.0)
        out = loco.step(cmd["fwd"], cmd.get("side", 0.0), cmd["turn"], dt, sprint=cmd["sprint"])
        LM.apply_output(pose, loco, out, torso_yaw=cmd["head_yaw"], torso_pitch=cmd["head_pitch"])
        for sd in R.SIDES:
            tur[sd][0] = LM.approach(tur[sd][0], cmd["turret_yaw"], 4.0, dt)
            tur[sd][1] = LM.approach(tur[sd][1], cmd["turret_pitch"], 4.0, dt)
            pose.set_turret(sd, tur[sd][0], tur[sd][1] + out.turret_sway, 0.0)
        frame = f0 + k
        if k % key_step == 0:
            animdata.key_bones(cache, rig, frame, channels)
        if behaviour.done and settle:
            after_done += 1
            if not loco.stepping() and after_done > fps * 1.0:
                animdata.key_bones(cache, rig, frame, channels)
                break
        k += 1
    cache.finish()
    removed = 0
    if simplify:
        removed = animdata.simplify(list(cache.touched), tol_loc=0.0015, tol_rot=0.0025)
    scene.frame_end = max(scene.frame_end, frame)
    scene.frame_set(f0)
    return f0, frame, removed, loco


# =========================================================================== navmesh builder

def build_navmesh(context, ground_objs, obstacles, cell=0.5, radius=1.6, max_slope=30.0, clearance=4.5):
    """Grid navmesh on the ground objects, minus the obstacles inflated by the robot radius."""
    dg = context.evaluated_depsgraph_get()
    gv, gf = [], []
    for o in ground_objs:
        v, f = world_mesh(o, dg)
        b = len(gv)
        gv += v
        gf += [tuple(i + b for i in face) for face in f]
    if not gf:
        return None, "Aucune surface de sol"
    gtree = BVHTree.FromPolygons([Vector(p) for p in gv], gf)
    otree = None
    if obstacles:
        ov, of = [], []
        for o in obstacles:
            v, f = world_mesh(o, dg)
            b = len(ov)
            ov += v
            of += [tuple(i + b for i in face) for face in f]
        if of:
            otree = BVHTree.FromPolygons([Vector(p) for p in ov], of)
    arr = np.array(gv)
    x0, y0 = arr[:, 0].min(), arr[:, 1].min()
    x1, y1 = arr[:, 0].max(), arr[:, 1].max()
    top = arr[:, 2].max() + 50.0
    nx, ny = max(1, int((x1 - x0) / cell)), max(1, int((y1 - y0) / cell))
    if nx * ny > 250000:
        return None, f"Grille trop fine ({nx}×{ny}) : augmentez la taille des cellules"
    cos_max = math.cos(math.radians(max_slope))
    walk = {}
    for i in range(nx):
        for j in range(ny):
            x, y = x0 + (i + 0.5) * cell, y0 + (j + 0.5) * cell
            loc, nrm, _idx, _d = gtree.ray_cast(Vector((x, y, top)), Vector((0, 0, -1)), 1000.0)
            if loc is None or nrm.z < cos_max:
                continue
            if otree is not None:
                blocked = False
                for h in (0.3, 1.2, 2.4, clearance):
                    hit = otree.find_nearest(Vector((x, y, loc.z + h)), radius)
                    if hit[0] is not None:
                        blocked = True
                        break
                if blocked:
                    continue
            walk[i, j] = loc.z
    if not walk:
        return None, "Aucune cellule praticable"
    # keep the largest connected region
    seen, best = set(), []
    for c in walk:
        if c in seen:
            continue
        comp, st = [], [c]
        seen.add(c)
        while st:
            a = st.pop()
            comp.append(a)
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                b = (a[0] + d[0], a[1] + d[1])
                if b in walk and b not in seen:
                    seen.add(b)
                    st.append(b)
        if len(comp) > len(best):
            best = comp
    keep = set(best)
    vid, verts, faces = {}, [], []

    def corner(ci, cj):
        if (ci, cj) not in vid:
            zs = [walk[c] for c in ((ci - 1, cj - 1), (ci, cj - 1), (ci - 1, cj), (ci, cj)) if c in keep]
            vid[ci, cj] = len(verts)
            verts.append((x0 + ci * cell, y0 + cj * cell, sum(zs) / len(zs) + 0.02))
        return vid[ci, cj]
    for (i, j) in sorted(keep):
        faces.append((corner(i, j), corner(i + 1, j), corner(i + 1, j + 1), corner(i, j + 1)))
    me = bpy.data.meshes.get(NAV_NAME)
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)
    me = bpy.data.meshes.new(NAV_NAME)
    me.from_pydata(verts, [], faces)
    obj = bpy.data.objects.get(NAV_NAME)
    if obj is None:
        obj = bpy.data.objects.new(NAV_NAME, me)
        context.scene.collection.objects.link(obj)
    else:
        old = obj.data
        obj.data = me
        if old.users == 0:
            bpy.data.meshes.remove(old)
    obj.display_type = 'WIRE'
    obj.hide_render = True
    obj.show_in_front = True
    obj.color = (0.1, 0.9, 0.4, 1)
    obj["robot_navmesh"] = True
    return obj, f"Navmesh : {len(faces)} cellules ({len(faces) * cell * cell:.0f} m²)"


# =========================================================================== operators

def _rig(context):
    return R.find_rig(context)


class ROBOT_OT_path_create(Operator):
    """Créer un chemin (courbe) devant le robot, à modifier puis à suivre"""
    bl_idname = "robot.path_create"
    bl_label = "Créer un chemin"
    bl_options = {'REGISTER', 'UNDO'}

    shape: bpy.props.EnumProperty(name="Forme", items=[
        ('S', "Virage en S", ""), ('LOOP', "Boucle", "Circuit fermé"), ('LINE', "Ligne droite", "")], default='S')
    length: bpy.props.FloatProperty(name="Longueur", default=30.0, min=4.0, max=500.0, unit='LENGTH')

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None

    def execute(self, context):
        rig = _rig(context)
        pose = R.RigPose(rig)
        pos, yaw = pose.get_root()
        fx_, fy_ = LM.forward(yaw)
        lx, ly = math.cos(yaw), math.sin(yaw)
        L = self.length
        if self.shape == 'LINE':
            local = [(0, 0), (0, L)]
        elif self.shape == 'S':
            local = [(0, 0), (0, L * 0.25), (L * 0.18, L * 0.5), (-L * 0.12, L * 0.75), (0, L)]
        else:
            r = L / (2 * math.pi)
            local = [(r * (1 - math.cos(a)), r * math.sin(a)) for a in [i * math.tau / 8 for i in range(8)]]
        cu = bpy.data.curves.new("Chemin_Robot", 'CURVE')
        cu.dimensions = '3D'
        sp = cu.splines.new('BEZIER')
        sp.bezier_points.add(len(local) - 1)
        for bp, (a, f) in zip(sp.bezier_points, local):
            x = pos.x + lx * -a + fx_ * f
            y = pos.y + ly * -a + fy_ * f
            bp.co = rig.matrix_world @ Vector((x, y, pos.z + 0.05))
            bp.handle_left_type = bp.handle_right_type = 'AUTO'
        sp.use_cyclic_u = self.shape == 'LOOP'
        obj = bpy.data.objects.new("Chemin_Robot", cu)
        context.scene.collection.objects.link(obj)
        context.scene.robot_anim.path_object = obj
        context.scene.robot_anim.path_loop = self.shape == 'LOOP'
        self.report({'INFO'}, "Chemin créé : modifiez ses points (Tab) puis « Suivre le chemin »")
        return {'FINISHED'}


class ROBOT_OT_path_follow(Operator):
    """Faire marcher le robot le long du chemin et enregistrer les clés à partir de l'image courante"""
    bl_idname = "robot.path_follow"
    bl_label = "Suivre le chemin"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        s = context.scene.robot_anim
        return _rig(context) is not None and s.path_object is not None and s.path_object.type == 'CURVE'

    def execute(self, context):
        rig = _rig(context)
        s = context.scene.robot_anim
        pts = curve_points(rig, s.path_object)
        if len(pts) < 2:
            self.report({'ERROR'}, "Chemin vide")
            return {'CANCELLED'}
        start = None
        if s.path_start_on_path:
            p0, p1 = Vector(pts[0]), Vector(pts[min(3, len(pts) - 1)])
            start = ((p0.x, p0.y, p0.z), LM.heading_of(p1.x - p0.x, p1.y - p0.y))
        beh = FollowPath(pts, loop=s.path_loop, speed=s.auto_speed, sprint=s.auto_sprint, laps=s.path_laps)
        ground = ground_callback(rig, s.nav_object or s.ground_object)
        fps = context.scene.render.fps / context.scene.render.fps_base
        f0, f1, removed, loco = bake(context, rig, beh, int(s.auto_max_seconds * fps), ground=ground,
                                     key_step=s.walk_key_step, simplify=s.auto_simplify, start=start)
        self.report({'INFO'}, f"Chemin suivi : images {f0}–{f1}, {loco.distance:.1f} m parcourus, "
                              f"{sum(f.steps for f in loco.feet.values())} pas")
        return {'FINISHED'}


class ROBOT_OT_nav_build(Operator):
    """Construire un navmesh (zone de marche) sur le sol, en évitant les obstacles"""
    bl_idname = "robot.nav_build"
    bl_label = "Créer le navmesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.robot_anim.ground_object is not None

    def execute(self, context):
        s = context.scene.robot_anim
        obstacles = []
        if s.obstacles is not None:
            obstacles = [o for o in s.obstacles.all_objects if o.type == 'MESH']
        obj, msg = build_navmesh(context, [s.ground_object], obstacles, cell=s.nav_cell, radius=s.nav_radius,
                                 max_slope=s.nav_slope)
        if obj is None:
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}
        s.nav_object = obj
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class ROBOT_OT_patrol_add(Operator):
    """Ajouter un point de patrouille à la position du curseur 3D"""
    bl_idname = "robot.patrol_add"
    bl_label = "Ajouter un point"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        col = bpy.data.collections.get(PATROL_COLLECTION)
        if col is None:
            col = bpy.data.collections.new(PATROL_COLLECTION)
            context.scene.collection.children.link(col)
        n = len(col.objects) + 1
        e = bpy.data.objects.new(f"P{n:02d}", None)
        e.empty_display_type = 'CONE'
        e.empty_display_size = 0.8
        e.rotation_euler = (math.pi, 0, 0)
        e.location = context.scene.cursor.location
        col.objects.link(e)
        self.report({'INFO'}, f"Point de patrouille P{n:02d} ajouté")
        return {'FINISHED'}


class ROBOT_OT_patrol_clear(Operator):
    """Supprimer tous les points de patrouille"""
    bl_idname = "robot.patrol_clear"
    bl_label = "Effacer les points"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        col = bpy.data.collections.get(PATROL_COLLECTION)
        n = 0
        if col:
            for o in list(col.objects):
                bpy.data.objects.remove(o)
                n += 1
        self.report({'INFO'}, f"{n} point(s) supprimé(s)")
        return {'FINISHED'}


class ROBOT_OT_ai_bake(Operator):
    """Mode IA : le robot patrouille sur le navmesh (ou va vers une cible) et l'animation est enregistrée"""
    bl_idname = "robot.ai_bake"
    bl_label = "Lancer l'IA"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None and context.scene.robot_anim.nav_object is not None

    def execute(self, context):
        rig = _rig(context)
        s = context.scene.robot_anim
        navm = navmesh_from_object(rig, s.nav_object)
        mode = s.ai_mode
        if mode == 'GOTO':
            if s.ai_target is None:
                self.report({'ERROR'}, "Choisissez une cible")
                return {'CANCELLED'}
            pts = [tuple(rig.matrix_world.inverted() @ s.ai_target.matrix_world.translation)]
        elif mode == 'WANDER':
            pts = []
        else:
            pts = patrol_points(rig, context.scene)
            if len(pts) < 2:
                self.report({'ERROR'}, "Ajoutez au moins 2 points de patrouille (curseur 3D + « Ajouter un point »)")
                return {'CANCELLED'}
        beh = Patrol(navm, pts, mode=mode, wait=s.ai_wait, speed=s.auto_speed, sprint=s.auto_sprint,
                     laps=s.ai_laps if mode != 'WANDER' else s.ai_laps * 4, seed=s.ai_seed, scan=s.ai_scan)
        ground = ground_callback(rig, s.nav_object)
        fps = context.scene.render.fps / context.scene.render.fps_base
        f0, f1, removed, loco = bake(context, rig, beh, int(s.auto_max_seconds * fps), ground=ground,
                                     key_step=s.walk_key_step, simplify=s.auto_simplify)
        extra = f" ({len(beh.failed)} point(s) inaccessible(s))" if beh.failed else ""
        self.report({'INFO'}, f"IA : images {f0}–{f1}, {loco.distance:.1f} m, {beh.visited} point(s) visités{extra}")
        return {'FINISHED'}


classes = (ROBOT_OT_path_create, ROBOT_OT_path_follow, ROBOT_OT_nav_build, ROBOT_OT_patrol_add,
           ROBOT_OT_patrol_clear, ROBOT_OT_ai_bake)

__all__ = ["Matrix"]
