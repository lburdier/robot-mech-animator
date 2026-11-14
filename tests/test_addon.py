"""End-to-end tests, run inside Blender:

    blender -b --factory-startup --disable-autoexec build/Robot_Rig.blend --python tests/test_addon.py
"""
import math
import os
import sys
import traceback

import bpy
from mathutils import Vector

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
import robot_mech_animator as addon  # noqa: E402
from robot_mech_animator import animdata, gait, gamepad, pilot, rig as R  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"[{'OK' if cond else 'FAIL'}] {name} {detail}")


def section(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check(fn.__name__, False, "exception")
    return fn


addon.register()
scene = bpy.context.scene
rig = R.find_rig(bpy.context)
P = rig.pose.bones
W = rig.matrix_world


def wpos(bone, tail=False):
    pb = P[bone]
    return W @ (pb.tail if tail else pb.head)


@section
def test_rig_detect():
    check("rig detected", rig is not None and rig.name == "RIG_Robot")
    check("no missing bones", not R.missing_bones(rig), str(R.missing_bones(rig)))
    check("meshes parented", all(o.parent == rig for o in scene.objects if o.name.startswith("GEO-")))


@section
def test_turret_directions():
    pose = R.RigPose(rig)
    pose.reset()
    bpy.context.view_layer.update()
    base = (wpos("turret.L", True) - wpos("turret.L")).normalized()
    check("turret faces forward (-Y)", base.y < -0.99, str(base))
    pose.set_turret("L", yaw=math.radians(20))
    bpy.context.view_layer.update()
    d = (wpos("turret.L", True) - wpos("turret.L")).normalized()
    check("yaw>0 aims to the robot's right (-X)", d.x < -0.3, str(d))
    pose.set_turret("L", pitch=math.radians(25))
    bpy.context.view_layer.update()
    d = (wpos("turret.L", True) - wpos("turret.L")).normalized()
    check("pitch>0 aims up", d.z > 0.35, str(d))
    pose.set_turret("L", recoil=0.2)
    bpy.context.view_layer.update()
    check("recoil pushes back (+Y)", wpos("turret.L").y > P["turret.L"].bone.head_local.y + 0.15)
    pose.set_turret("L", yaw=math.radians(90))
    check("yaw clamped to limit", abs(P["turret.L"].rotation_euler.z - math.radians(R.TURRET_YAW)) < 1e-5)
    pose.reset()


def walk_metrics(f0, f1, step=1):
    """Max IK reach error, lowest sole and max stance slip for both feet."""
    reach, lowest, slip = 0.0, 1e9, 0.0
    anchors = {}
    for f in range(f0, f1 + 1, step):
        scene.frame_set(f)
        for side in R.SIDES:
            target = wpos(R.foot(side))
            ankle = wpos("DEF-foot." + side)
            reach = max(reach, (target - ankle).length)
            sole = min((o.matrix_world @ Vector(c)).z for o in (bpy.data.objects["GEO-foot." + side],)
                       for c in o.bound_box)
            lowest = min(lowest, sole)
            planted = sole < 0.01
            if planted:
                a = anchors.setdefault(side, target.copy())
                slip = max(slip, (target.xy - a.xy).length)
            else:
                anchors.pop(side, None)
    return reach, lowest, slip


@section
def test_walk_cycle():
    s = scene.robot_anim
    s.walk_mode = 'CYCLE'
    scene.frame_set(1)
    res = bpy.ops.robot.walk_preset()
    check("walk preset (cycle) runs", res == {'FINISHED'})
    act = rig.animation_data.action
    check("cycle action created", act and act.name.startswith("Robot_Marche_Cycle"))
    fcs = animdata.all_fcurves(rig)
    check("cycle has fcurves", len(fcs) > 30, f"{len(fcs)} fcurves")
    check("every fcurve cycles", all(any(m.type == 'CYCLES' for m in fc.modifiers) for fc in fcs))
    reach, lowest, slip = walk_metrics(1, 1 + 3 * s.walk_cycle_frames)
    check("IK reaches the foot target", reach < 0.01, f"max {reach:.4f} m")
    check("soles never go under the ground", lowest > -0.02, f"lowest {lowest:.4f} m")
    check("planted feet do not slide", slip < 0.06, f"max slip {slip:.4f} m")
    scene.frame_set(1)
    p0 = wpos(R.ROOT)
    scene.frame_set(1 + 2 * s.walk_cycle_frames)
    p1 = wpos(R.ROOT)
    expect = 2 * gait.GaitParams(stride=s.walk_stride, duty=s.walk_duty).travel_per_cycle
    check("root travels forward over 2 cycles", abs((p0.y - p1.y) - expect) < 0.05, f"{p0.y - p1.y:.3f} vs {expect:.3f}")


@section
def test_walk_bake():
    s = scene.robot_anim
    s.walk_mode = 'BAKE'
    s.walk_cycles = 3
    scene.frame_set(400)
    res = bpy.ops.robot.walk_preset()
    check("walk preset (bake) runs", res == {'FINISHED'})
    n = s.walk_cycle_frames * 3
    reach, lowest, slip = walk_metrics(400, 400 + n, 2)
    check("bake: IK reach", reach < 0.01, f"{reach:.4f}")
    check("bake: soles above ground", lowest > -0.02, f"{lowest:.4f}")
    scene.frame_set(400)
    a = (wpos("foot_ik.L").z, wpos("foot_ik.R").z)
    scene.frame_set(400 + n)
    b = (wpos("foot_ik.L").z, wpos("foot_ik.R").z)
    rest = P["foot_ik.L"].bone.head_local.z
    check("bake starts and ends at rest", max(abs(v - rest) for v in a + b) < 0.01, f"{a} {b}")


@section
def test_turret_presets():
    s = scene.robot_anim
    s.walk_mode = 'CYCLE'
    rig.animation_data.action = None
    f = 1
    for kind, _l, _d in addon.presets.TURRET_PRESETS:
        for side in ('L', 'R', 'BOTH'):
            s.turret_preset, s.turret_side = kind, side
            scene.frame_set(f)
            res = bpy.ops.robot.turret_preset(kind='SETTINGS')
            ok = res == {'FINISHED'}
            worst = 0.0
            for fr in range(f, f + s.turret_duration + 1, 3):
                scene.frame_set(fr)
                for sd in R.SIDES:
                    e = P[R.turret(sd)].rotation_euler
                    worst = max(worst, math.degrees(abs(e.z)) - R.TURRET_YAW,
                                math.degrees(-e.x) - R.TURRET_PITCH_UP, math.degrees(e.x) - R.TURRET_PITCH_DOWN)
            check(f"turret preset {kind}/{side}", ok and worst < 0.5, f"limit overshoot {worst:.2f} deg")
            f += s.turret_duration + 10
    s.turret_preset, s.turret_side = 'BURST', 'L'
    scene.frame_set(f)
    a0 = P["gatling.L"].rotation_euler.y
    bpy.ops.robot.turret_preset(kind='SETTINGS')
    scene.frame_set(f + s.turret_duration)
    spun = P["gatling.L"].rotation_euler.y - a0
    check("burst spins the gatling", spun > 2 * math.pi * 3, f"{spun / (2 * math.pi):.1f} turns")


class FakeClock:
    t = 1000.0


@section
def test_pilot_and_recording():
    s = scene.robot_anim
    rig.animation_data.action = None
    R.RigPose(rig).reset()
    from robot_mech_animator.presets import gait_params
    ctrl = pilot.PilotController(rig, s, gait_params(s))
    dt = 1 / 60
    pad = gamepad.PadState(ly=1.0, connected=True)
    start = Vector(ctrl.root_pos)
    for _ in range(int(4 / dt)):
        ctrl.step(pad, dt)
    moved = start - ctrl.root_pos
    check("pilot: left stick walks forward (-Y)", moved.y > 2.0 and abs(moved.x) < 0.05, str(moved))
    check("pilot: walk amplitude ramps up", ctrl.amp > 0.9, f"{ctrl.amp:.2f}")
    for _ in range(int(3 / dt)):
        ctrl.step(gamepad.PadState(), dt)
    check("pilot: stops when stick released", ctrl.amp < 0.05, f"amp {ctrl.amp:.3f}")
    y0 = ctrl.root_yaw
    for _ in range(int(1 / dt)):
        ctrl.step(gamepad.PadState(lx=-1.0), dt)
    check("pilot: stick left turns left", ctrl.root_yaw - y0 > math.radians(30), f"{math.degrees(ctrl.root_yaw - y0):.1f}")
    ctrl.step(gamepad.PadState(buttons=frozenset({"l1"})), dt)
    ctrl.step(gamepad.PadState(), dt)
    check("pilot: L1 selects the left turret", ctrl.selected == {"L"})
    for _ in range(int(1 / dt)):
        ctrl.step(gamepad.PadState(rx=1.0, ry=1.0), dt)
    tl, tr = ctrl.turrets["L"], ctrl.turrets["R"]
    check("pilot: right stick aims selected turret only",
          tl["yaw"] > math.radians(R.TURRET_YAW) - 1e-3 and tl["pitch"] > math.radians(30) and abs(tr["yaw"]) < 1e-6,
          f"L yaw {math.degrees(tl['yaw']):.1f} pitch {math.degrees(tl['pitch']):.1f}")
    check("pilot: aim stays inside limits", tl["yaw"] <= math.radians(R.TURRET_YAW) + 1e-6)
    for _ in range(int(1.5 / dt)):
        ctrl.step(gamepad.PadState(r2=1.0), dt)
    check("pilot: R2 spins the gatling", tl["rps"] > 0.8 * s.turret_spin_rps, f"{tl['rps']:.2f} rps")
    ev = ctrl.step(gamepad.PadState(buttons=frozenset({"cross"})), dt)
    check("pilot: cross toggles recording", "toggle_record" in ev)
    ev = ctrl.step(gamepad.PadState(buttons=frozenset({"options"})), dt)
    check("pilot: options quits", "quit" in ev)

    # --- recording with a fake clock
    real_now = gamepad.now
    gamepad.now = lambda: FakeClock.t
    try:
        scene.frame_set(1000)
        rec = pilot.Recorder(rig, scene, s)
        frames = []
        for i in range(int(3 / dt)):
            FakeClock.t += dt
            ctrl.step(gamepad.PadState(ly=0.8, rx=0.3, r2=1.0 if i > 60 else 0.0), dt)
            fr = rec.tick()
            if fr is not None:
                frames.append(fr)
        f0, f1, removed = rec.stop()
    finally:
        gamepad.now = real_now
    fps = scene.render.fps
    check("recorder: one pose per frame, real time", abs(len(frames) - 3 * fps) <= 2 and f0 == 1000,
          f"{len(frames)} poses, frames {f0}-{f1}")
    fcs = animdata.all_fcurves(rig)
    check("recorder: fcurves written", len(fcs) >= 30, f"{len(fcs)}")
    check("recorder: simplification removed keys", removed > 0, f"{removed} removed")
    scene.frame_set(f1)
    root_y = P[R.ROOT].location.y
    scene.frame_set(f0)
    check("recorder: playback moves the robot", root_y < P[R.ROOT].location.y - 0.5, f"{root_y:.2f}")


@section
def test_gamepad_backends():
    devs = gamepad.WinMMPad.list_devices() if sys.platform == "win32" else []
    pad, msg = gamepad.open_pad("AUTO")
    check("gamepad: open_pad never raises", isinstance(msg, str), f"{msg} / winmm devices: {devs}")
    if pad:
        st = pad.read()
        check("gamepad: read() works", isinstance(st, gamepad.PadState), st.raw)
    kb = gamepad.KeyboardPad()

    class Ev:
        def __init__(self, t, v):
            self.type, self.value = t, v
    kb.event(Ev("UP_ARROW", 'PRESS'))
    kb.event(Ev("SPACE", 'PRESS'))
    st = kb.read()
    check("keyboard pad", st.ly == 1.0 and st.r2 == 1.0)
    st = gamepad.apply_deadzone(gamepad.PadState(lx=0.05, ly=0.6), 0.12)
    check("deadzone", st.lx == 0.0 and 0.5 < st.ly < 0.6)


class StubLayout:
    """Checks that every property / operator referenced by the panels exists."""
    errors = []

    def __init__(self):
        self.alert = False
        self.active = True

    def __getattr__(self, name):
        def anything(*a, **k):
            return StubLayout()
        return anything

    def prop(self, data, name, **k):
        if not name.startswith('["') and name not in data.bl_rna.properties:
            StubLayout.errors.append(f"{data!r}.{name}")
        return StubLayout()

    def operator(self, idname, **k):
        mod, op = idname.split(".")
        if not hasattr(getattr(bpy.ops, mod), op):
            StubLayout.errors.append(idname)

        class Props:
            pass
        return Props()

    def label(self, **k):
        return None


@section
def test_ui_panels():
    from robot_mech_animator import ui

    class Ctx:
        pass
    ctx = Ctx()
    ctx.scene = scene
    ctx.active_object = rig
    ctx.mode = 'OBJECT'
    for cls in ui.classes:
        # bpy panels cannot be instantiated outside a draw call: use a proxy "self"
        panel = type("P", (), {"layout": StubLayout(), "ROWS": getattr(cls, "ROWS", ())})()
        cls.draw(panel, ctx)
        # draw again with the pilot running / monitor on to cover every branch
        pilot.STATUS.update(running=True, recording=True, monitor=True, monitor_text="a\nb")
        cls.draw(panel, ctx)
        pilot.STATUS.update(running=False, recording=False, monitor=False, monitor_text="")
    check("UI panels reference valid props/operators", not StubLayout.errors, str(StubLayout.errors))
    prefs = addon.props.RobotAnimPreferences
    p = type("P", (), {"layout": StubLayout()})()
    for k in ("backend", "device_index", "deadzone", "custom_mapping"):
        if k not in prefs.__annotations__:
            StubLayout.errors.append(k)
    check("preferences fields", not StubLayout.errors)


fails = [r for r in RESULTS if not r[1]]
print(f"\n==== {len(RESULTS) - len(fails)}/{len(RESULTS)} checks passed ====")
for n, _ok, d in fails:
    print("FAILED:", n, d)
addon.unregister()
sys.exit(1 if fails else 0)
