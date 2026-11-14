"""Procedural walk cycle for the mech (pure maths, no bpy).

The same generator is used to bake the walk preset and to animate the robot live
when it is driven with the gamepad, so recorded walks look like the preset.

Conventions (root space): the robot faces -Y, +X is the robot's left, +Z is up.
"""
import math
from dataclasses import dataclass


@dataclass
class GaitParams:
    stride: float = 1.1          # length of one step (m)
    step_height: float = 0.35    # foot lift (m)
    duty: float = 0.6            # fraction of the cycle a foot is on the ground
    bob: float = 0.06            # body vertical bounce (m)
    sway: float = 0.07           # body lateral shift over the support foot (m)
    roll: float = math.radians(3.0)
    lean: float = math.radians(3.0)
    hip_yaw: float = math.radians(2.0)
    torso_twist: float = math.radians(4.0)
    turret_sway: float = math.radians(2.5)
    foot_pitch: float = math.radians(14.0)
    toe_flex: float = math.radians(20.0)
    # the rest pose has almost straight legs (150 deg): bend them while walking and
    # centre the stride under the hips, otherwise the front foot is out of IK reach
    crouch: float = 0.18         # body lowered while walking (m)
    foot_back: float = 0.25      # stride centre moved back under the hips (m)

    @property
    def travel_per_cycle(self):
        """Distance the root travels during one full cycle (two steps)."""
        return self.stride / max(self.duty, 0.05)


def _smooth(t):
    return t * t * (3.0 - 2.0 * t)


def foot_curve(q, p: GaitParams):
    """Foot offset for a local phase q in [0, 1).

    Returns (dy, dz, toe_up, toe_curl, back_lift). dy is along the walking direction
    (negative = in front of the rest position).
    """
    q %= 1.0
    s_len = p.stride
    if q < p.duty:                                   # stance: foot slides back in root space
        u = q / p.duty
        return (-s_len / 2 + s_len * u, 0.0, 0.0, 0.0, 0.0)
    s = (q - p.duty) / (1.0 - p.duty)                # swing
    dy = s_len / 2 - s_len * _smooth(s)
    dz = p.step_height * math.sin(math.pi * s) ** 1.2
    toe_up = p.foot_pitch * math.sin(2.0 * math.pi * s) * -0.6 + p.foot_pitch * 0.4 * math.sin(math.pi * s)
    curl = p.toe_flex * math.sin(math.pi * s)
    back = p.toe_flex * 0.6 * math.sin(math.pi * min(1.0, s * 1.6))
    return dy, dz, toe_up, curl, back


def sample(phase, p: GaitParams, amp=1.0, direction=1.0):
    """Full-body pose for a cycle phase in [0, 1).

    ``amp`` (0..1) scales the whole motion (used to start/stop smoothly) and
    ``direction`` is +1 to walk forward, -1 to walk backward.
    Returns a dict with root-space offsets/angles (radians).
    """
    out = {}
    d = 1.0 if direction >= 0 else -1.0
    for side, off in (("L", 0.0), ("R", 0.5)):
        dy, dz, toe_up, curl, back = foot_curve(phase + off, p)
        # walking forward = towards -Y, so the stance slide goes to +Y (dy > 0 at the back)
        out["foot." + side] = ((0.0, (dy * d + p.foot_back) * amp, dz * amp), toe_up * amp, curl * amp, back * amp)
    duty_mid = p.duty / 2.0
    c = math.cos(2.0 * math.pi * (phase - duty_mid))        # +1 when the left foot carries the weight
    bob = -p.bob * (0.5 + 0.5 * math.cos(4.0 * math.pi * phase))
    out["body"] = (
        (p.sway * c * amp, 0.0, (bob - p.crouch) * amp),
        p.lean * amp * d,                                      # pitch (lean into the walk)
        p.roll * c * amp,                                      # roll towards the support foot
        p.hip_yaw * math.sin(2.0 * math.pi * phase) * amp,     # yaw
    )
    out["torso_yaw"] = -p.torso_twist * math.sin(2.0 * math.pi * phase) * amp
    out["turret_pitch"] = p.turret_sway * math.sin(4.0 * math.pi * phase - 0.8) * amp
    return out


def apply(pose, data, extra_crouch=0.0, torso_yaw=None, turrets=True):
    """Write a :func:`sample` result to a :class:`rig.RigPose`."""
    for side in ("L", "R"):
        off, toe_up, curl, back = data["foot." + side]
        pose.set_foot(side, off, toe_up=toe_up)
        pose.set_toes(side, front_curl=-curl * 0.5 + max(0.0, toe_up) * 0.3, back_lift=back)
    (bx, by, bz), pitch, roll, yaw = data["body"]
    pose.set_body((bx, by, bz - extra_crouch), pitch=pitch, roll=roll, yaw=yaw)
    pose.set_torso(yaw=data["torso_yaw"] if torso_yaw is None else torso_yaw + data["torso_yaw"])
    if turrets:
        for side in ("L", "R"):
            pose.set_turret(side, pitch=data["turret_pitch"])
