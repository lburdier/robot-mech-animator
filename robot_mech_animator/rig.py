"""Rig description + helpers to pose the mech robot rig from code.

All controller transforms written by the add-on go through :class:`RigPose`, so the
walk preset, the turret presets and the gamepad pilot share exactly the same maths.
"""
import math

import bpy
from mathutils import Euler, Matrix, Vector

RIG_ID = "MECH_ROBOT_V1"
SIDES = ("L", "R")

ROOT = "root"
BODY = "body"
TORSO = "torso"
AIM = "aim_target"


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


CONTROLLERS = [ROOT, BODY, TORSO, AIM] + [
    f(s) for s in SIDES for f in (turret, gatling, foot, pole, toe_front, toe_back)
]

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
    CHANNELS[turret(_s)] = (("location", (1,)), ("rotation_euler", (0, 2)))
    CHANNELS[gatling(_s)] = (("rotation_euler", (1,)),)

WALK_BONES = [ROOT, BODY, TORSO] + [f(s) for s in SIDES for f in (foot, toe_front, toe_back, turret)]
TURRET_BONES = {s: [turret(s), gatling(s)] for s in SIDES}

# turret limits (degrees) – must match the Limit Rotation constraints of the rig
TURRET_PITCH_UP = 40.0     # local X negative
TURRET_PITCH_DOWN = 22.0   # local X positive
TURRET_YAW = 28.0          # local Z, positive = towards the robot's right
TURRET_RECOIL = 0.3        # local -Y
TORSO_YAW = 75.0


# --------------------------------------------------------------------------- lookup

def is_robot_rig(obj):
    return (obj is not None and obj.type == 'ARMATURE'
            and obj.data.get("robot_rig") == RIG_ID)


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
    return [n for n in CONTROLLERS if n not in rig.pose.bones]


# --------------------------------------------------------------------------- posing

class RigPose:
    """Write controller transforms in an axis-agnostic way.

    Positions/rotations for root children are expressed in *root space* (the
    armature space when ``root`` is at rest), with the robot facing -Y.
    """

    def __init__(self, rig):
        self.rig = rig
        self.pb = rig.pose.bones
        self.rest = {n: self.pb[n].bone.matrix_local.copy() for n in CONTROLLERS if n in self.pb}
        self.rest_inv = {n: m.inverted() for n, m in self.rest.items()}

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

    def set_torso(self, yaw=0.0, pitch=0.0, roll=0.0):
        """yaw>0 turns the torso to the robot's left."""
        self.pb[TORSO].rotation_euler = (pitch, yaw, roll)

    def set_turret(self, side, yaw=0.0, pitch=0.0, recoil=0.0):
        """yaw>0 aims to the robot's right, pitch>0 aims UP, recoil>=0 pushes back."""
        yaw = clamp(yaw, -math.radians(TURRET_YAW), math.radians(TURRET_YAW))
        pitch = clamp(pitch, -math.radians(TURRET_PITCH_DOWN), math.radians(TURRET_PITCH_UP))
        p = self.pb[turret(side)]
        p.rotation_euler = (-pitch, 0.0, yaw)
        p.location = (0.0, -clamp(recoil, 0.0, TURRET_RECOIL), 0.0)

    def set_gatling(self, side, angle):
        self.pb[gatling(side)].rotation_euler = (0.0, angle, 0.0)

    # -- read back --------------------------------------------------------------
    def get_turret(self, side):
        p = self.pb[turret(side)]
        return p.rotation_euler.z, -p.rotation_euler.x, -p.location.y

    def get_root(self):
        p = self.pb[ROOT]
        return Vector(p.location), p.rotation_euler.z

    def reset(self, bones=None):
        for n in bones or CONTROLLERS:
            p = self.pb.get(n)
            if p is None:
                continue
            p.location = (0, 0, 0)
            p.rotation_quaternion = (1, 0, 0, 0)
            p.rotation_euler = (0, 0, 0)
            p.scale = (1, 1, 1)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v
