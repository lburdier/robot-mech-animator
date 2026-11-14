"""Rig description + helpers to pose the mech robot rig from code.

All controller transforms written by the add-on go through :class:`RigPose`, so the
walk preset, the turret presets, the gamepad pilot and the autopilot share exactly
the same maths.
"""
import math

import bpy
from mathutils import Euler, Matrix, Vector

RIG_ID = "MECH_ROBOT_V2"
SIDES = ("L", "R")

ROOT = "root"
BODY = "body"
TORSO = "torso"          # upper body = the robot's "head" (cockpit + weapons)
AIM = "aim_target"


def shoulder(side):
    return f"shoulder.{side}"


def turret(side):
    return f"turret.{side}"


def gatling(side):
    return f"gatling.{side}"


def foot(side):
    return f"foot_ik.{side}"


def pole(side):
    return f"knee_pole.{side}"


def toe_front(side):
    return f"toe_front.{side}"


def toe_back(side):
    return f"toe_back.{side}"


def toe_flap(side):
    return f"toe_flap.{side}"


def muzzle(side):
    return f"muzzle.{side}"


def rocket_port(side, i):
    return f"rocket_port_{i}.{side}"


CONTROLLERS = [ROOT, BODY, TORSO, AIM] + [
    f(s) for s in SIDES for f in (shoulder, turret, gatling, foot, pole, toe_front, toe_back, toe_flap)
]
FX_BONES = [b for s in SIDES for b in (muzzle(s), rocket_port(s, 1), rocket_port(s, 2))]

# channels written by the add-on (bone, data_path, indices)
CHANNELS = {
    ROOT: (("location", (0, 1, 2)), ("rotation_euler", (2,))),
    BODY: (("location", (0, 1, 2)), ("rotation_euler", (0, 1, 2))),
    TORSO: (("rotation_euler", (0, 1, 2)),),
}
for _s in SIDES:
    CHANNELS[foot(_s)] = (("location", (0, 1, 2)), ("rotation_euler", (0, 1, 2)))
    CHANNELS[toe_front(_s)] = (("rotation_euler", (0,)),)
    CHANNELS[toe_back(_s)] = (("rotation_euler", (0,)),)
    CHANNELS[toe_flap(_s)] = (("rotation_euler", (0,)),)
    CHANNELS[shoulder(_s)] = (("rotation_euler", (0,)),)
    CHANNELS[turret(_s)] = (("location", (1,)), ("rotation_euler", (0, 2)))
    CHANNELS[gatling(_s)] = (("rotation_euler", (1,)),)

WALK_BONES = [ROOT, BODY, TORSO] + [f(s) for s in SIDES for f in (foot, toe_front, toe_back, toe_flap, turret)]
LEG_BONES = [BODY] + [f(s) for s in SIDES for f in (foot, pole, toe_front, toe_back, toe_flap)]
TURRET_BONES = {s: [shoulder(s), turret(s), gatling(s)] for s in SIDES}
LOCO_BONES = [ROOT, BODY, TORSO] + [f(s) for s in SIDES for f in (foot, toe_front, toe_back, toe_flap)]

# default limits (degrees) - the rig stores the collision-free values it was built with
DEFAULT_LIMITS = dict(turret_yaw_in=34.0, turret_yaw_out=18.0, turret_up=20.0, turret_down=35.0,
                      shoulder_up=24.0, shoulder_down=20.0, torso_yaw=58.0)
TORSO_PITCH = 12.0
TURRET_RECOIL = 0.3        # local -Y
FLAP_MIN, FLAP_MAX = -55.0, 40.0


# --------------------------------------------------------------------------- lookup

def is_robot_rig(obj):
    return (obj is not None and obj.type == 'ARMATURE'
            and str(obj.data.get("robot_rig", "")).startswith("MECH_ROBOT"))


def find_rig(context):
    obj = getattr(context, "active_object", None)
    if is_robot_rig(obj):
        return obj
    scene = getattr(context, "scene", None)
    picked = getattr(getattr(scene, "robot_anim", None), "rig", None)
    if is_robot_rig(picked):
        return picked
    for o in (scene.objects if scene else bpy.data.objects):
        if is_robot_rig(o):
            return o
    return None


def missing_bones(rig):
    return [n for n in CONTROLLERS + FX_BONES if n not in rig.pose.bones]


def limits(rig):
    """Limits in degrees, read from the rig (written by tools/build_rig.py)."""
    out = dict(DEFAULT_LIMITS)
    for k in out:
        v = rig.data.get("lim_" + k)
        if v is not None:
            out[k] = float(v)
    return out


def yaw_range(lim, side):
    """(min, max) turret yaw in radians; yaw > 0 aims to the robot's right."""
    inner, outer = math.radians(lim["turret_yaw_in"]), math.radians(lim["turret_yaw_out"])
    return (-outer, inner) if side == "L" else (-inner, outer)


def pitch_range(lim):
    """(min, max) total pitch (shoulder + gun) in radians; pitch > 0 aims up."""
    return (-math.radians(lim["turret_down"] + lim["shoulder_down"]),
            math.radians(lim["turret_up"] + lim["shoulder_up"]))


# --------------------------------------------------------------------------- posing

class RigPose:
    """Write controller transforms in an axis-agnostic way.

    Positions/rotations for root children are expressed in *root space* (the
    armature space when ``root`` is at rest), with the robot facing -Y.
    """

    def __init__(self, rig):
        self.rig = rig
        self.pb = rig.pose.bones
        self.rest = {n: self.pb[n].bone.matrix_local.copy() for n in CONTROLLERS + FX_BONES if n in self.pb}
        self.rest_inv = {n: m.inverted() for n, m in self.rest.items()}
        self.lim = limits(rig)

    # -- rest data used by the locomotion -------------------------------------------
    def foot_rest(self, side):
        """Rest position of the ankle (foot controller head) in root space."""
        return self.rest[foot(side)].to_translation()

    # -- root children (feet, body) --------------------------------------------
    def _set_root_child(self, name, offset, rot_world):
        rest = self.rest[name]
        head = rest.to_translation()
        d = Matrix.Translation(head + Vector(offset)) @ rot_world.to_matrix().to_4x4() @ rest.to_3x3().to_4x4()
        self.pb[name].matrix_basis = self.rest_inv[name] @ d

    def set_root(self, pos, yaw):
        p = self.pb[ROOT]
        p.location = (pos[0], pos[1], pos[2] if len(pos) > 2 else 0.0)
        p.rotation_euler = (0.0, 0.0, yaw)

    def set_body(self, offset=(0, 0, 0), pitch=0.0, roll=0.0, yaw=0.0):
        """pitch>0 leans forward, roll>0 leans to the robot's left, yaw>0 turns left."""
        self._set_root_child(BODY, offset, Euler((pitch, roll, yaw), 'YXZ'))

    def set_foot(self, side, offset=(0, 0, 0), toe_up=0.0, yaw=0.0):
        self._set_root_child(foot(side), offset, Euler((-toe_up, 0.0, yaw), 'XYZ'))

    def set_toes(self, side, front_curl=0.0, back_lift=0.0):
        self.pb[toe_front(side)].rotation_euler = (front_curl, 0.0, 0.0)
        self.pb[toe_back(side)].rotation_euler = (back_lift, 0.0, 0.0)

    def set_flap(self, side, angle):
        """Front foot flap. angle<0 folds it backwards, >0 tips it forwards."""
        a = clamp(angle, math.radians(FLAP_MIN), math.radians(FLAP_MAX))
        self.pb[toe_flap(side)].rotation_euler = (a, 0.0, 0.0)

    def set_torso(self, yaw=0.0, pitch=0.0, roll=0.0):
        """Upper body / head. yaw>0 turns to the robot's left, pitch>0 looks down."""
        ylim = math.radians(self.lim["torso_yaw"])
        plim = math.radians(TORSO_PITCH)
        self.pb[TORSO].rotation_euler = (clamp(pitch, -plim, plim), clamp(yaw, -ylim, ylim), roll)

    set_head = set_torso

    def set_turret(self, side, yaw=0.0, pitch=0.0, recoil=0.0):
        """yaw>0 aims to the robot's right, pitch>0 aims UP, recoil>=0 pushes back.

        The pitch is given to the gun first; what is left goes to the shoulder."""
        lo, hi = yaw_range(self.lim, side)
        yaw = clamp(yaw, lo, hi)
        g_lo, g_hi = -math.radians(self.lim["turret_down"]), math.radians(self.lim["turret_up"])
        gun_p = clamp(pitch, g_lo, g_hi)
        rest = pitch - gun_p
        sh_p = clamp(rest, -math.radians(self.lim["shoulder_down"]), math.radians(self.lim["shoulder_up"]))
        self.pb[shoulder(side)].rotation_euler = (-sh_p, 0.0, 0.0)
        p = self.pb[turret(side)]
        p.rotation_euler = (-gun_p, 0.0, yaw)
        p.location = (0.0, -clamp(recoil, 0.0, TURRET_RECOIL), 0.0)

    def set_gatling(self, side, angle):
        self.pb[gatling(side)].rotation_euler = (0.0, angle, 0.0)

    # -- read back --------------------------------------------------------------
    def get_turret(self, side):
        p = self.pb[turret(side)]
        sh = self.pb[shoulder(side)]
        return p.rotation_euler.z, -p.rotation_euler.x - sh.rotation_euler.x, -p.location.y

    def get_root(self):
        p = self.pb[ROOT]
        return Vector(p.location), p.rotation_euler.z

    def get_torso(self):
        r = self.pb[TORSO].rotation_euler
        return r.y, r.x

    def reset(self, bones=None):
        for n in bones or CONTROLLERS:
            p = self.pb.get(n)
            if p is None:
                continue
            p.location = (0, 0, 0)
            p.rotation_quaternion = (1, 0, 0, 0)
            p.rotation_euler = (0, 0, 0)
            p.scale = (1, 1, 1)

    # -- world helpers for the effects ---------------------------------------------
    def bone_world(self, name):
        """(head, direction) of a bone in world space, from the evaluated pose."""
        m = self.rig.matrix_world @ self.pb[name].matrix
        return m.to_translation(), (m.to_3x3() @ Vector((0, 1, 0))).normalized()


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v
