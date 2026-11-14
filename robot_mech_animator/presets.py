"""Animation presets: walk cycle and turret behaviours."""
import math
import random

import bpy
from bpy.types import Operator

from . import animdata, fx, gait, rig as R

DEG = math.radians


def gait_params(s):
    return gait.GaitParams(
        stride=s.walk_stride, step_height=s.walk_step_height, duty=s.walk_duty,
        bob=s.walk_bob, sway=s.walk_sway, roll=s.walk_roll, lean=s.walk_lean,
        hip_yaw=s.walk_hip_yaw, torso_twist=s.walk_torso_twist,
        turret_sway=s.walk_turret_sway, foot_pitch=s.walk_foot_pitch, toe_flex=s.walk_toe_flex,
        crouch=s.walk_crouch, foot_back=s.walk_foot_back, flap=s.walk_flap,
    )


def _smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _poll_rig(context):
    return R.find_rig(context) is not None


def _channels(bones):
    return {b: R.CHANNELS[b] for b in bones}


# =========================================================================== walk

def bake_walk(context, rig, s):
    """Create the walk animation. Returns (first_frame, last_frame, action)."""
    scene = context.scene
    params = gait_params(s)
    n = max(8, s.walk_cycle_frames)
    step = max(1, s.walk_key_step)
    cyclic = s.walk_mode == 'CYCLE'
    f0 = scene.frame_current
    cycles = 1 if cyclic else max(1, s.walk_cycles)
    total = n * cycles
    direction = -1.0 if s.walk_backward else 1.0

    pose = R.RigPose(rig)
    scene.frame_set(f0)
    start_pos, start_yaw = pose.get_root()
    if cyclic:
        act = animdata.ensure_action(rig, name="Robot_Marche_Cycle", new=True)
    else:
        act = animdata.ensure_action(rig)
        animdata.clear_range(rig, R.WALK_BONES, f0, f0 + total)
    cache = animdata.FCurveCache(rig)

    bones = [b for b in R.WALK_BONES if b != R.ROOT]
    if not s.walk_turret_sway_on:
        bones = [b for b in bones if not b.startswith("turret")]
    channels = _channels(bones)
    heading = (math.sin(start_yaw), -math.cos(start_yaw))
    dist = 0.0
    speed = params.travel_per_cycle / n * direction        # metres per frame at full amplitude
    ease = (not cyclic) and s.walk_ease
    for k in range(total + 1):
        frame = f0 + k
        phase = (k / n) % 1.0
        amp = 1.0
        if ease:
            ramp = n * 0.5
            amp = _smooth(k / ramp) * _smooth((total - k) / ramp)
        if k > 0:
            dist += speed * amp
        if k % step and k != total:
            continue
        data = gait.sample(phase, params, amp=amp, direction=direction)
        gait.apply(pose, data, turrets=s.walk_turret_sway_on)
        animdata.key_bones(cache, rig, frame, channels)
        if not s.walk_in_place:
            pos = (start_pos.x + heading[0] * dist, start_pos.y + heading[1] * dist, start_pos.z)
            pose.set_root(pos, start_yaw)
            animdata.key_bones(cache, rig, frame, {R.ROOT: R.CHANNELS[R.ROOT]})
    cache.finish()
    for fc in animdata.bone_fcurves(rig, [R.ROOT]):
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
        fc.update()
    if cyclic:
        for fc in animdata.bone_fcurves(rig, R.WALK_BONES):
            is_root_loc = fc.data_path.startswith(f'pose.bones["{R.ROOT}"].location')
            animdata.add_cycles(fc, offset=is_root_loc)
            fc.update()
    scene.frame_set(f0)
    return f0, f0 + total, act


class ROBOT_OT_walk_preset(Operator):
    """Créer un cycle de marche procédural pour le robot"""
    bl_idname = "robot.walk_preset"
    bl_label = "Créer la marche"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_rig(context)

    def execute(self, context):
        rig = R.find_rig(context)
        f0, f1, act = bake_walk(context, rig, context.scene.robot_anim)
        if context.scene.robot_anim.walk_mode == 'CYCLE':
            context.scene.frame_end = max(context.scene.frame_end, f0 + 4 * (f1 - f0))
            self.report({'INFO'}, f"Cycle de marche « {act.name} » créé ({f1 - f0} images, boucle infinie)")
        else:
            context.scene.frame_end = max(context.scene.frame_end, f1)
            self.report({'INFO'}, f"Marche créée de l'image {f0} à {f1}")
        return {'FINISHED'}


# =========================================================================== turrets

def _sides(s):
    return ["L", "R"] if s.turret_side == 'BOTH' else [s.turret_side]


def rocket_frames(s, n, side):
    """Frame offsets of the rockets fired by one turret in the ROCKETS preset."""
    sides = _sides(s)
    per = max(1, s.rocket_count)
    out = []
    k = 0
    for i in range(per * len(sides)):
        if sides[i % len(sides)] == side:
            f = i * s.rocket_spacing
            if f <= n:
                out.append(f)
        k += 1
    return out


def turret_track(kind, s, side, base, n, fps, rng):
    """Yield (frame_offset, yaw, pitch, recoil, spin_angle) for one turret."""
    yaw0, pitch0, _rec0, spin0 = base
    amp = DEG(s.turret_amplitude)
    out = []
    if kind == 'SWEEP':
        k = max(1, s.turret_cycles)
        for i in range(n + 1):
            t = i / n
            env = _smooth(t / 0.12) * _smooth((1 - t) / 0.12)
            yaw = yaw0 * (1 - env) + amp * math.sin(2 * math.pi * k * t) * env
            pitch = pitch0 * (1 - env) + (DEG(4) + DEG(3) * math.sin(4 * math.pi * k * t)) * env
            out.append((i, yaw, pitch, 0.0, spin0))
    elif kind == 'AIM':
        ty, tp = DEG(s.turret_aim_yaw), DEG(s.turret_aim_pitch)
        a_end, r_start = 0.3, 0.8
        for i in range(n + 1):
            t = i / n
            if t <= a_end:                       # move with a small overshoot
                u = t / a_end
                e = 1 - math.exp(-6 * u) * math.cos(7.5 * u)
                e = e / (1 - math.exp(-6) * math.cos(7.5))
                w = e
            elif t < r_start:
                w = 1.0
            else:
                w = 1 - _smooth((t - r_start) / (1 - r_start))
            jit = 0.0 if not (a_end < t < r_start) else DEG(0.25) * math.sin(i * 1.7 + (0 if side == "L" else 2))
            out.append((i, yaw0 + (ty - yaw0) * w + jit, pitch0 + (tp - pitch0) * w, 0.0, spin0))
    elif kind == 'BURST':
        rps = s.turret_spin_rps
        angle = spin0
        for i in range(n + 1):
            t = i / n
            spin = rps * _smooth(t / 0.15) * _smooth((1 - t) / 0.22)
            if i:
                angle += 2 * math.pi * spin / fps
            firing = spin > 0.7 * rps
            rec = (0.035 + 0.025 * rng.random()) if firing and i % 2 == 0 else (0.01 if firing else 0.0)
            kick = DEG(0.8) * rng.random() if firing else 0.0
            jy = DEG(0.5) * (rng.random() - 0.5) if firing else 0.0
            out.append((i, yaw0 + jy, pitch0 + kick, rec, angle))
    elif kind == 'SHOT':
        for i in range(n + 1):
            if i <= 2:
                w = i / 2
            else:
                w = math.exp(-(i - 2) / (n * 0.22))
            rec = 0.26 * w
            pitch = pitch0 + DEG(7) * w
            out.append((i, yaw0 + DEG(1.2) * w * (1 if side == "L" else -1), pitch, rec, spin0))
    elif kind == 'ROCKETS':
        shots = rocket_frames(s, n, side)
        for i in range(n + 1):
            rec = 0.0
            for f in shots:
                if 0 <= i - f < 10:
                    rec = max(rec, 0.14 * math.exp(-(i - f) / 3.0))
            out.append((i, yaw0, pitch0 + DEG(1.5) * rec / 0.14, rec, spin0))
    elif kind == 'SCAN':
        hops = max(2, s.turret_cycles * 2)
        targets = [(yaw0, pitch0)]
        for _ in range(hops):
            targets.append((rng.uniform(-amp, amp), rng.uniform(DEG(-6), DEG(16))))
        targets.append((yaw0, pitch0))
        seg = n / (len(targets) - 1)
        for i in range(n + 1):
            j = min(int(i / seg), len(targets) - 2)
            u = (i - j * seg) / seg
            move = _smooth(min(1.0, u / 0.45))          # move quickly then hold
            (ya, pa), (yb, pb) = targets[j], targets[j + 1]
            out.append((i, ya + (yb - ya) * move, pa + (pb - pa) * move, 0.0, spin0))
    return out


TURRET_PRESETS = [
    ('SWEEP', "Balayage", "Les tourelles balayent de gauche à droite"),
    ('AIM', "Visée", "Pointe vers une cible, tient la position puis revient"),
    ('BURST', "Rafale gatling", "Montée en régime des gatlings, tir avec recul et vibrations"),
    ('SHOT', "Tir lourd", "Un coup puissant : recul fort puis retour amorti"),
    ('SCAN', "Patrouille", "Regarde autour de lui de façon aléatoire"),
    ('ROCKETS', "Salve de roquettes", "Tire des roquettes (flamme, traînée de fumée, explosion à l'impact)"),
]


class ROBOT_OT_turret_preset(Operator):
    """Ajouter un preset d'animation de tourelle à la frame courante"""
    bl_idname = "robot.turret_preset"
    bl_label = "Appliquer le preset tourelle"
    bl_options = {'REGISTER', 'UNDO'}

    kind: bpy.props.EnumProperty(name="Preset", items=TURRET_PRESETS + [('SETTINGS', "Réglage", "")], default='SETTINGS')

    @classmethod
    def poll(cls, context):
        return _poll_rig(context)

    def execute(self, context):
        scene = context.scene
        s = scene.robot_anim
        kind = s.turret_preset if self.kind == 'SETTINGS' else self.kind
        rig = R.find_rig(context)
        pose = R.RigPose(rig)
        f0 = scene.frame_current
        n = max(4, s.turret_duration)
        fps = scene.render.fps / scene.render.fps_base
        scene.frame_set(f0)
        animdata.ensure_action(rig)
        sides = _sides(s)
        bases = {}
        for side in sides:
            yaw, pitch, rec = pose.get_turret(side)
            bases[side] = (yaw, pitch, rec, pose.pb[R.gatling(side)].rotation_euler.y)
        bones = [b for side in sides for b in R.TURRET_BONES[side]]
        animdata.clear_range(rig, bones, f0 + 1, f0 + n)
        cache = animdata.FCurveCache(rig)
        rng = random.Random(s.turret_seed)
        tracks = {side: turret_track(kind, s, side, bases[side], n, fps, rng) for side in sides}
        for i in range(n + 1):
            for side in sides:
                _, yaw, pitch, rec, ang = tracks[side][i]
                pose.set_turret(side, yaw, pitch, rec)
                pose.set_gatling(side, ang)
            animdata.key_bones(cache, rig, f0 + i, _channels(bones))
        cache.finish()
        for fc in animdata.bone_fcurves(rig, [R.gatling(sd) for sd in sides]):
            for kp in fc.keyframe_points:
                if f0 <= kp.co.x <= f0 + n:
                    kp.interpolation = 'LINEAR'
            fc.update()
        if kind in ('AIM', 'SWEEP', 'SCAN'):
            simp = animdata.bone_fcurves(rig, bones)
            animdata.simplify(simp, tol_loc=0.0005, tol_rot=0.0015)
        extra = ""
        if kind in ('BURST', 'SHOT') and s.fx_flash:
            for side in sides:
                if kind == 'BURST':
                    shots = {f0 + i for i, (_k, _y, _p, rec, _a) in enumerate(tracks[side]) if rec > 0.02}
                    fx.key_flash_track(rig, side, scene, shots, f0, f0 + n, seed=s.turret_seed + len(side), size=1.0)
                else:
                    fx.key_flash_track(rig, side, scene, {f0 + 1, f0 + 2}, f0, f0 + 6, seed=s.turret_seed, size=2.2)
            extra = ", flashs de tir"
        if kind == 'ROCKETS':
            made = 0
            target = None
            for side in sides:
                for j, off in enumerate(rocket_frames(s, n, side)):
                    scene.frame_set(f0 + off)
                    if s.rocket_use_target:
                        target = rig.matrix_world @ rig.pose.bones[R.AIM].head
                    fx.launch_rocket(scene, rig, side, 1 + j % 2, f0 + off, target=target, max_range=s.rocket_range,
                                     seed=s.turret_seed + made, smoke_density=s.rocket_smoke)
                    made += 1
            extra = f", {made} roquette(s)"
        scene.frame_end = max(scene.frame_end, f0 + n)
        scene.frame_set(f0)
        label = dict((k, l) for k, l, _d in TURRET_PRESETS)[kind]
        self.report({'INFO'}, f"Preset « {label} » ajouté ({', '.join(sides)}) images {f0}–{f0 + n}{extra}")
        return {'FINISHED'}


# =========================================================================== misc

class ROBOT_OT_reset_pose(Operator):
    """Remettre les contrôleurs du robot en position de repos"""
    bl_idname = "robot.reset_pose"
    bl_label = "Pose de repos"
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.EnumProperty(items=[('ALL', "Tout", ""), ('TURRETS', "Tourelles", ""), ('LEGS', "Jambes", "")], default='ALL')

    @classmethod
    def poll(cls, context):
        return _poll_rig(context)

    def execute(self, context):
        rig = R.find_rig(context)
        bones = {
            'ALL': [b for b in R.CONTROLLERS if b != R.ROOT],
            'TURRETS': [b for s in R.SIDES for b in R.TURRET_BONES[s]],
            'LEGS': R.LEG_BONES,
        }[self.target]
        R.RigPose(rig).reset(bones)
        return {'FINISHED'}


class ROBOT_OT_key_pose(Operator):
    """Insérer une clé sur tous les contrôleurs à la frame courante"""
    bl_idname = "robot.key_pose"
    bl_label = "Clé sur tous les contrôleurs"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_rig(context)

    def execute(self, context):
        rig = R.find_rig(context)
        cache = animdata.FCurveCache(rig)
        animdata.key_bones(cache, rig, context.scene.frame_current, R.CHANNELS)
        cache.finish()
        return {'FINISHED'}


class ROBOT_OT_select_group(Operator):
    """Sélectionner un groupe de contrôleurs (mode Pose)"""
    bl_idname = "robot.select_group"
    bl_label = "Sélectionner"
    bl_options = {'REGISTER', 'UNDO'}

    group: bpy.props.EnumProperty(items=[
        ('BODY', "Corps", ""), ('TURRETS', "Tourelles", ""), ('LEGS', "Jambes", ""), ('ALL', "Tout", "")])

    @classmethod
    def poll(cls, context):
        return _poll_rig(context)

    def execute(self, context):
        rig = R.find_rig(context)
        names = {
            'BODY': [R.ROOT, R.BODY, R.TORSO],
            'TURRETS': [R.AIM] + [b for s in R.SIDES for b in R.TURRET_BONES[s]],
            'LEGS': [f(s) for s in R.SIDES for f in (R.foot, R.pole, R.toe_front, R.toe_back, R.toe_flap)],
            'ALL': R.CONTROLLERS,
        }[self.group]
        if context.mode != 'POSE':
            for o in context.view_layer.objects:
                o.select_set(False)
            context.view_layer.objects.active = rig
            rig.select_set(True)
            bpy.ops.object.mode_set(mode='POSE')
        for b in rig.data.bones:
            sel = b.name in names
            if hasattr(b, "select"):
                b.select = sel
            pb = rig.pose.bones.get(b.name)
            if pb is not None and hasattr(pb, "select"):
                pb.select = sel
        if names:
            rig.data.bones.active = rig.data.bones[names[0]]
        return {'FINISHED'}


class ROBOT_OT_fx_clear(Operator):
    """Supprimer les roquettes, fumées et explosions générées"""
    bl_idname = "robot.fx_clear"
    bl_label = "Effacer les effets"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n = fx.clear_fx(context.scene)
        self.report({'INFO'}, f"{n} objet(s) d'effet supprimé(s)")
        return {'FINISHED'}


classes = (ROBOT_OT_fx_clear, ROBOT_OT_walk_preset, ROBOT_OT_turret_preset, ROBOT_OT_reset_pose, ROBOT_OT_key_pose, ROBOT_OT_select_group)
