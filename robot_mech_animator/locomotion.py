"""Planted-feet locomotion for the mech (pure maths, no bpy).

Unlike the cyclic walk preset, the feet here live in *world* (armature) space:
a foot stays exactly where it landed until the controller decides to step again.
Steps are triggered when a foot gets too far (or too twisted) from where it
should be, so the robot can walk, strafe, sprint and turn on the spot over 360
degrees - the legs always move to follow the body instead of sliding.

Conventions: armature space, +Z up, the robot faces -Y when yaw = 0, yaw > 0
turns to the robot's left (counter-clockwise seen from above).
"""
import math
from dataclasses import dataclass, field

TAU = 2.0 * math.pi


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def smooth(t):
    t = clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def wrap(a):
    return (a + math.pi) % TAU - math.pi


def approach(cur, target, rate, dt):
    """Frame-rate independent exponential smoothing."""
    return cur + (target - cur) * (1.0 - math.exp(-rate * dt))


def rot2(yaw, x, y):
    c, s = math.cos(yaw), math.sin(yaw)
    return x * c - y * s, x * s + y * c


def forward(yaw):
    return math.sin(yaw), -math.cos(yaw)


def heading_of(dx, dy):
    """Yaw that makes the robot face the direction (dx, dy)."""
    return math.atan2(dx, -dy)


@dataclass
class LocoParams:
    walk_speed: float = 1.15            # m/s, stick fully forward
    sprint_speed: float = 2.5           # m/s with sprint (L3 / Shift)
    back_speed: float = 0.7
    strafe_speed: float = 0.6
    turn_rate: float = math.radians(55.0)
    accel: float = 1.8                  # m/s^2
    swing_time: float = 0.62            # s, one step while walking
    sprint_swing_time: float = 0.40
    double_support: float = 0.08        # s, both feet down between two steps
    step_height: float = 0.32
    sprint_step_height: float = 0.42
    max_reach: float = 0.78             # m, horizontal distance foot <-> its home
    sprint_reach: float = 0.92
    foot_back: float = 0.25             # home of the feet moved back under the hips
    crouch: float = 0.18                # body lowered while walking
    sprint_crouch: float = 0.34
    idle_crouch: float = 0.08
    bob: float = 0.06
    sway: float = 0.07
    roll: float = math.radians(3.0)
    lean: float = math.radians(3.0)
    sprint_lean: float = math.radians(8.0)
    torso_twist: float = math.radians(3.0)
    foot_pitch: float = math.radians(14.0)
    toe_flex: float = math.radians(20.0)
    flap_swing: float = math.radians(32.0)   # front flap folds back while the foot is in the air
    flap_kick: float = 5.0                   # rad/s impulse when the foot lands
    turret_sway: float = math.radians(2.5)
    step_threshold: float = 0.07        # m, step when a foot is further than this (moving)
    idle_threshold: float = 0.12        # m, settle steps when standing
    yaw_threshold: float = math.radians(6.0)


@dataclass
class Foot:
    side: str
    x: float
    y: float
    z: float
    yaw: float
    planted: bool = True
    t: float = 0.0
    dur: float = 0.6
    height: float = 0.3
    sx: float = 0.0
    sy: float = 0.0
    sz: float = 0.0
    syaw: float = 0.0
    tx: float = 0.0
    ty: float = 0.0
    tz: float = 0.0
    tyaw: float = 0.0
    flap: float = 0.0
    flap_v: float = 0.0
    steps: int = 0


@dataclass
class LocoOutput:
    root: tuple = (0.0, 0.0, 0.0)
    yaw: float = 0.0
    body_offset: tuple = (0.0, 0.0, 0.0)      # root space
    body_pitch: float = 0.0
    body_roll: float = 0.0
    body_yaw: float = 0.0
    torso_twist: float = 0.0
    turret_sway: float = 0.0
    feet: dict = field(default_factory=dict)  # side -> (x, y, z, yaw, toe_up, curl, back, flap) world


class Locomotor:
    """Simulates the robot's legs. Feed it commands with :meth:`step`."""

    def __init__(self, params, foot_rest, pos=(0.0, 0.0, 0.0), yaw=0.0, ground=None):
        """``foot_rest``: {side: (x, y)} rest ankle position in root space.
        ``ground(x, y)`` returns the ground height or None (then the root height is used)."""
        self.p = params
        self.rest = {s: (float(v[0]), float(v[1])) for s, v in foot_rest.items()}
        self.ground_fn = ground
        self.x, self.y, self.z = float(pos[0]), float(pos[1]), float(pos[2])
        self.yaw = float(yaw)
        self.v_fwd = self.v_side = self.omega = 0.0
        self.sprint = 0.0
        self.move_amt = 0.0
        self.extra_crouch = 0.0
        self.ds_timer = 1.0
        self.last_side = "R"
        self.sway_x = 0.0
        self.distance = 0.0
        self.time = 0.0
        self.feet = {}
        for s in self.rest:
            hx, hy = self.home(s)
            self.feet[s] = Foot(s, hx, hy, self.ground(hx, hy), self.yaw)
        self.out = LocoOutput()

    # ------------------------------------------------------------------ helpers
    def ground(self, x, y):
        if self.ground_fn is not None:
            z = self.ground_fn(x, y)
            if z is not None:
                return z
        return self.z

    def home(self, side, x=None, y=None, yaw=None):
        """World position of a foot's home for a root pose."""
        x = self.x if x is None else x
        y = self.y if y is None else y
        yaw = self.yaw if yaw is None else yaw
        rx, ry = self.rest[side]
        lx, ly = rot2(yaw, rx, ry + self.p.foot_back)
        return x + lx, y + ly

    def velocity(self):
        fx, fy = forward(self.yaw)
        lx, ly = math.cos(self.yaw), math.sin(self.yaw)          # robot's left
        return fx * self.v_fwd + lx * self.v_side, fy * self.v_fwd + ly * self.v_side

    def swing_time(self):
        p = self.p
        return p.swing_time + (p.sprint_swing_time - p.swing_time) * self.sprint

    def reach(self):
        p = self.p
        return p.max_reach + (p.sprint_reach - p.max_reach) * self.sprint

    def landing(self, side, time_to_land):
        """Where a foot should land: under the predicted hips, ahead by half a stance."""
        vx, vy = self.velocity()
        stance = self.swing_time() + 2.0 * self.p.double_support
        lead = time_to_land + 0.5 * stance
        yaw_l = self.yaw + self.omega * lead
        px, py = self.x + vx * lead, self.y + vy * lead
        hx, hy = self.home(side, px, py, yaw_l)
        # never further than the leg can reach from the current hips
        cx, cy = self.home(side)
        dx, dy = hx - cx, hy - cy
        d = math.hypot(dx, dy)
        r = self.reach()
        if d > r:
            hx, hy = cx + dx * r / d, cy + dy * r / d
        return hx, hy, self.ground(hx, hy), yaw_l

    def foot_error(self, side):
        f = self.feet[side]
        hx, hy, _hz, hyaw = self.landing(side, 0.0)
        return math.hypot(f.x - hx, f.y - hy), abs(wrap(f.yaw - hyaw))

    def stepping(self):
        return any(not f.planted for f in self.feet.values())

    def teleport(self, pos, yaw):
        """Put the robot somewhere else, feet under it."""
        self.x, self.y, self.z = float(pos[0]), float(pos[1]), float(pos[2])
        self.yaw = float(yaw)
        self.v_fwd = self.v_side = self.omega = 0.0
        for s, f in self.feet.items():
            hx, hy = self.home(s)
            f.x, f.y, f.z, f.yaw, f.planted, f.t = hx, hy, self.ground(hx, hy), self.yaw, True, 0.0

    # ------------------------------------------------------------------ simulation
    def step(self, fwd=0.0, side=0.0, turn=0.0, dt=1.0 / 24.0, sprint=False, crouch=0.0):
        """Advance by ``dt`` seconds. fwd/side/turn are stick values in [-1, 1]
        (turn > 0 = turn left). Returns a :class:`LocoOutput`."""
        p = self.p
        dt = clamp(dt, 0.0, 0.1)
        self.time += dt
        fwd, side, turn = clamp(fwd, -1, 1), clamp(side, -1, 1), clamp(turn, -1, 1)

        # ---- commands -> body velocity
        want_sprint = sprint and fwd > 0.3
        self.sprint = approach(self.sprint, 1.0 if want_sprint else 0.0, 2.5, dt)
        top = p.walk_speed + (p.sprint_speed - p.walk_speed) * self.sprint
        t_fwd = fwd * (top if fwd > 0 else p.back_speed)
        t_side = side * p.strafe_speed
        acc = p.accel * (1.0 + self.sprint) * dt
        self.v_fwd += clamp(t_fwd - self.v_fwd, -acc, acc)
        self.v_side += clamp(t_side - self.v_side, -acc, acc)
        self.omega = approach(self.omega, turn * p.turn_rate * (1.0 - 0.3 * self.sprint), 6.0, dt)
        moving = abs(self.v_fwd) > 0.03 or abs(self.v_side) > 0.03 or abs(self.omega) > math.radians(3)
        self.move_amt = approach(self.move_amt, 1.0 if moving else 0.0, 3.0, dt)
        self.extra_crouch = approach(self.extra_crouch, crouch, 8.0, dt)

        # ---- integrate the root
        self.yaw += self.omega * dt
        vx, vy = self.velocity()
        self.x += vx * dt
        self.y += vy * dt
        self.distance += math.hypot(vx, vy) * dt
        if self.ground_fn is not None:
            gz = self.ground_fn(self.x, self.y)
            if gz is not None:
                self.z = approach(self.z, gz, 6.0, dt)

        # ---- legs
        for f in self.feet.values():
            if not f.planted:
                self._update_swing(f, dt)
        if not self.stepping():
            self.ds_timer += dt
            if self.ds_timer >= p.double_support:
                self._maybe_step(moving)
        self._update_flaps(dt)
        self._dt = dt
        return self._pose()

    def _maybe_step(self, moving):
        p = self.p
        thr = p.step_threshold if moving else p.idle_threshold
        errs = {s: self.foot_error(s) for s in self.feet}

        def needs(s):
            d, a = errs[s]
            return d > thr or a > p.yaw_threshold
        other = "L" if self.last_side == "R" else "R"
        cand = None
        if moving and needs(other):
            cand = other                         # keep alternating while walking
        else:
            worst = max(self.feet, key=lambda s: errs[s][0] + errs[s][1] * 1.5)
            if needs(worst):
                cand = worst
        if cand is not None:
            self._start_swing(self.feet[cand])

    def _start_swing(self, f):
        p = self.p
        f.planted = False
        f.t = 0.0
        f.dur = self.swing_time()
        f.height = p.step_height + (p.sprint_step_height - p.step_height) * self.sprint
        f.sx, f.sy, f.sz, f.syaw = f.x, f.y, f.z, f.yaw
        f.tx, f.ty, f.tz, f.tyaw = self.landing(f.side, f.dur)
        dist = math.hypot(f.tx - f.sx, f.ty - f.sy)
        if dist < 0.25:                          # small settle / turning step: lift less
            f.height *= 0.45 + 0.55 * dist / 0.25
        self.last_side = f.side
        self.ds_timer = 0.0

    def _update_swing(self, f, dt):
        f.t = min(1.0, f.t + dt / max(f.dur, 1e-3))
        if f.t < 0.7:                            # keep aiming while there is time to correct
            f.tx, f.ty, f.tz, f.tyaw = self.landing(f.side, f.dur * (1.0 - f.t))
        u = smooth(f.t)
        f.x = f.sx + (f.tx - f.sx) * u
        f.y = f.sy + (f.ty - f.sy) * u
        f.z = f.sz + (f.tz - f.sz) * u
        f.yaw = f.syaw + wrap(f.tyaw - f.syaw) * u
        if f.t >= 1.0:
            f.planted = True
            f.x, f.y, f.z, f.yaw = f.tx, f.ty, f.tz, f.tyaw
            f.steps += 1
            f.flap_v += self.p.flap_kick * (0.5 + 0.5 * self.move_amt)

    def _update_flaps(self, dt):
        p = self.p
        for f in self.feet.values():
            target = 0.0 if f.planted else -p.flap_swing * math.sin(math.pi * f.t)
            # under-damped spring: the flap bounces when the foot lands
            k, c = 160.0, 9.0
            sub = 4
            h = dt / sub
            for _ in range(sub):
                acc = k * (target - f.flap) - c * f.flap_v
                f.flap_v += acc * h
                f.flap += f.flap_v * h
            f.flap = clamp(f.flap, -math.radians(55), math.radians(40))

    # ------------------------------------------------------------------ output
    def _pose(self):
        p = self.p
        o = self.out
        o.root = (self.x, self.y, self.z)
        o.yaw = self.yaw
        swing = [f for f in self.feet.values() if not f.planted]
        s = math.sin(math.pi * swing[0].t) if swing else 0.0
        support = None
        if swing:
            support = "R" if swing[0].side == "L" else "L"
        sway_target = 0.0
        if support is not None:
            sway_target = (p.sway if support == "L" else -p.sway) * s * self.move_amt
        self.sway_x = approach(self.sway_x, sway_target, 10.0, getattr(self, '_dt', 1.0 / 24.0))
        crouch = p.idle_crouch + (p.crouch - p.idle_crouch + (p.sprint_crouch - p.crouch) * self.sprint) * self.move_amt
        bz = -crouch - p.bob * (1.0 - s) * self.move_amt - self.extra_crouch
        speed_frac = clamp(self.v_fwd / max(p.walk_speed, 1e-3), -1.0, 2.5)
        o.body_offset = (self.sway_x, 0.0, bz)
        o.body_pitch = p.lean * speed_frac * (1.0 - self.sprint) + p.sprint_lean * self.sprint
        roll_dir = 1.0 if support == "L" else -1.0 if support == "R" else 0.0
        o.body_roll = p.roll * roll_dir * s * self.move_amt
        o.body_yaw = 0.0
        swing_dir = 0.0
        if swing:
            swing_dir = 1.0 if swing[0].side == "L" else -1.0
        o.body_yaw = math.radians(2.0) * swing_dir * s * self.move_amt
        o.torso_twist = -p.torso_twist * swing_dir * s * self.move_amt
        o.turret_sway = p.turret_sway * (s - 0.5) * self.move_amt
        feet = {}
        for f in self.feet.values():
            lift = 0.0
            toe_up = curl = back = 0.0
            if not f.planted:
                w = f.t
                lift = f.height * math.sin(math.pi * w) ** 1.2
                k = clamp(math.hypot(f.tx - f.sx, f.ty - f.sy) / 0.6, 0.2, 1.0)
                toe_up = k * (p.foot_pitch * math.sin(TAU * w) * -0.6 + p.foot_pitch * 0.4 * math.sin(math.pi * w))
                curl = k * p.toe_flex * math.sin(math.pi * w)
                back = k * p.toe_flex * 0.6 * math.sin(math.pi * min(1.0, w * 1.6))
            feet[f.side] = (f.x, f.y, f.z + lift, f.yaw, toe_up, curl, back, f.flap)
        o.feet = feet
        return o

    # ------------------------------------------------------------------ to the rig
    def to_root_space(self, x, y, z):
        """World (armature) point -> root space of the current root pose."""
        lx, ly = rot2(-self.yaw, x - self.x, y - self.y)
        return lx, ly, z - self.z


def apply_output(pose, loco, out, torso_yaw=0.0, torso_pitch=0.0, rest_z=None):
    """Write a :class:`LocoOutput` to a ``rig.RigPose``."""
    pose.set_root(out.root, out.yaw)
    bx, by, bz = out.body_offset
    pose.set_body((bx, by, bz), pitch=out.body_pitch, roll=out.body_roll, yaw=out.body_yaw)
    pose.set_torso(yaw=torso_yaw + out.torso_twist, pitch=torso_pitch)
    for side, (x, y, z, fyaw, toe_up, curl, back, flap) in out.feet.items():
        lx, ly, lz = loco.to_root_space(x, y, z)
        rx, ry = loco.rest[side]
        pose.set_foot(side, (lx - rx, ly - ry, lz), toe_up=toe_up, yaw=wrap(fyaw - out.yaw))
        pose.set_toes(side, front_curl=-curl * 0.5 + max(0.0, toe_up) * 0.3, back_lift=back)
        pose.set_flap(side, flap)
