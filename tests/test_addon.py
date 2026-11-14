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
from robot_mech_animator import (animdata, autopilot, fx, gait, gamepad, history, locomotion as LM, nav, paint,  # noqa: E402
                                 pilot, rig as R)

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
LIM = R.limits(rig)


def wpos(bone, tail=False):
    pb = P[bone]
    return W @ (pb.tail if tail else pb.head)


def upd():
    bpy.context.view_layer.update()


def clear_anim():
    if rig.animation_data:
        rig.animation_data.action = None
    R.RigPose(rig).reset()
    upd()


@section
def test_rig_detect():
    check("rig detected", rig is not None and rig.name == "RIG_Robot")
    check("rig version 2", rig.data.get("robot_rig") == R.RIG_ID)
    check("no missing bones", not R.missing_bones(rig), str(R.missing_bones(rig)))
    check("meshes parented", all(o.parent == rig for o in scene.objects if o.name.startswith("GEO-")))
    for n in ("GEO-shoulder.L", "GEO-shoulder_hub.R", "GEO-toe_flap.L", "GEO-piston_rod.R"):
        check(f"part {n} exists and is rigged", n in bpy.data.objects and bpy.data.objects[n].parent == rig)
    check("collision-free limits stored on the rig", all(("lim_" + k) in rig.data for k in R.DEFAULT_LIMITS), str(LIM))


@section
def test_gatling_axis():
    """The gatling must spin around its own barrels: 60 deg turn maps the mesh onto itself."""
    clear_anim()
    g = bpy.data.objects["GEO-gatling.L"]
    co0 = [g.matrix_world @ v.co for v in g.data.vertices]
    P["gatling.L"].rotation_euler = (0, math.radians(60), 0)
    upd()
    from mathutils.kdtree import KDTree
    kd = KDTree(len(co0))
    for i, c in enumerate(co0):
        kd.insert(c, i)
    kd.balance()
    err = max(kd.find(g.matrix_world @ v.co)[2] for v in list(g.data.vertices)[::5] if (g.matrix_world @ v.co).y < -1.4)
    check("gatling spins on its barrel axis (60 deg symmetry)", err < 0.02, f"max {err * 1000:.1f} mm")
    P["gatling.L"].rotation_euler = (0, 0, 0)


@section
def test_turret_directions():
    clear_anim()
    pose = R.RigPose(rig)
    base = (wpos("turret.L", True) - wpos("turret.L")).normalized()
    check("turret faces forward (-Y)", base.y < -0.99, str(base))
    pose.set_turret("L", yaw=math.radians(20))
    upd()
    d = (wpos("turret.L", True) - wpos("turret.L")).normalized()
    check("yaw>0 aims to the robot's right (-X)", d.x < -0.3, str(d))
    pose.set_turret("L", pitch=math.radians(35))
    upd()
    d = (wpos("turret.L", True) - wpos("turret.L")).normalized()
    check("pitch>0 aims up (gun + shoulder)", d.z > 0.5, str(d))
    gun = -P["turret.L"].rotation_euler.x
    sh = -P["shoulder.L"].rotation_euler.x
    check("pitch goes to the gun first, then the shoulder",
          abs(gun - math.radians(LIM["turret_up"])) < 1e-4 and abs(gun + sh - math.radians(35)) < 1e-4,
          f"gun {math.degrees(gun):.1f} shoulder {math.degrees(sh):.1f}")
    pose.set_turret("L", recoil=0.2)
    upd()
    check("recoil pushes back (+Y)", wpos("turret.L").y > P["turret.L"].bone.head_local.y + 0.15)
    pose.set_turret("L", yaw=math.radians(90))
    check("yaw clamped (inner limit L)", abs(P["turret.L"].rotation_euler.z - math.radians(LIM["turret_yaw_in"])) < 1e-5)
    pose.set_turret("R", yaw=math.radians(90))
    check("yaw clamped (outer limit R)", abs(P["turret.R"].rotation_euler.z - math.radians(LIM["turret_yaw_out"])) < 1e-5)
    pose.set_torso(yaw=math.radians(200))
    check("head clamped", abs(P["torso"].rotation_euler.y - math.radians(LIM["torso_yaw"])) < 1e-5)
    pose.reset()


@section
def test_pistons_and_flap():
    clear_anim()
    pose = R.RigPose(rig)
    upd()
    rod0 = (P["piston_rod.L"].head - P["piston_anchor.L"].head).length
    dir0 = (P["piston_rod.L"].tail - P["piston_rod.L"].head).normalized()
    pose.set_body((0, 0, -0.5))
    upd()
    rod1 = (P["piston_rod.L"].head - P["piston_anchor.L"].head).length
    dir1 = (P["piston_rod.L"].tail - P["piston_rod.L"].head).normalized()
    track = dir1.angle((P["piston_anchor.L"].head - P["piston_rod.L"].head).normalized())
    check("hydraulic piston slides when the knee bends", abs(rod1 - rod0) > 0.05, f"{rod0:.3f} -> {rod1:.3f} m")
    check("piston rod points at its anchor", track < 1e-3 and dir0.angle(dir1) > 0.01, f"{track:.5f}")
    tip0 = W @ P["toe_flap.L"].tail
    pose.set_flap("L", math.radians(-40))
    upd()
    tip1 = W @ P["toe_flap.L"].tail
    check("foot flap folds back (negative angle)", tip1.y > tip0.y + 0.1, f"{tip0.y:.2f} -> {tip1.y:.2f}")
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
    clear_anim()
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
    flaps = []
    for f in range(1, 1 + s.walk_cycle_frames, 2):
        scene.frame_set(f)
        flaps.append(P["toe_flap.L"].rotation_euler.x)
    check("walk animates the foot flap", max(flaps) - min(flaps) > math.radians(20),
          f"{math.degrees(min(flaps)):.0f}..{math.degrees(max(flaps)):.0f} deg")
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
    clear_anim()
    f = 1
    yl = {sd: R.yaw_range(LIM, sd) for sd in R.SIDES}
    for kind, _l, _d in addon.presets.TURRET_PRESETS:
        sides = ('L', 'R', 'BOTH') if kind != 'ROCKETS' else ('BOTH',)
        for side in sides:
            s.turret_preset, s.turret_side = kind, side
            scene.frame_set(f)
            res = bpy.ops.robot.turret_preset(kind='SETTINGS')
            ok = res == {'FINISHED'}
            worst = 0.0
            for fr in range(f, f + s.turret_duration + 1, 3):
                scene.frame_set(fr)
                for sd in R.SIDES:
                    e = P[R.turret(sd)].rotation_euler
                    lo, hi = yl[sd]
                    worst = max(worst, math.degrees(e.z - hi), math.degrees(lo - e.z),
                                math.degrees(-e.x) - LIM["turret_up"], math.degrees(e.x) - LIM["turret_down"])
            check(f"turret preset {kind}/{side}", ok and worst < 0.5, f"limit overshoot {worst:.2f} deg")
            f += s.turret_duration + 10
    rockets = [o for o in bpy.data.objects if o.name.startswith("FX-roquette.")]
    check("rocket salvo created rockets", len(rockets) == 2 * s.rocket_count, f"{len(rockets)}")
    smoke = [o for o in bpy.data.objects if o.name.startswith("FX-fumee.")]
    check("each rocket has a smoke trail (geometry nodes)", len(smoke) == len(rockets)
          and all(m.type == 'NODES' for o in smoke for m in o.modifiers))
    flash = bpy.data.objects.get("FX-flash.L")
    check("burst keyed muzzle flashes", flash is not None and flash.animation_data is not None
          and any(fc.data_path == "scale" for fc in animdata.all_fcurves(flash)))
    s.turret_preset, s.turret_side = 'BURST', 'L'
    scene.frame_set(f)
    a0 = P["gatling.L"].rotation_euler.y
    bpy.ops.robot.turret_preset(kind='SETTINGS')
    scene.frame_set(f + s.turret_duration)
    spun = P["gatling.L"].rotation_euler.y - a0
    check("burst spins the gatling", spun > 2 * math.pi * 3, f"{spun / (2 * math.pi):.1f} turns")


@section
def test_rocket_flight():
    bpy.ops.mesh.primitive_plane_add(size=600, location=(0, 0, 0))
    ground = bpy.context.active_object
    ground.name = "TEST_Sol"
    clear_anim()
    pose = R.RigPose(rig)
    pose.set_turret("L", pitch=math.radians(-8))
    upd()
    o, d = pose.bone_world(R.rocket_port("L", 1))
    check("rocket port points forward", d.y < -0.9, str(d))
    res = fx.launch_rocket(scene, rig, "L", 1, 50, max_range=400, seed=3)
    check("rocket hits the ground and explodes", res["impact"] is not None and "blast" in res, str(res["impact"]))
    scene.frame_set(50)
    p0 = res["rocket"].matrix_world.translation.copy()
    scene.frame_set(58)
    p1 = res["rocket"].matrix_world.translation.copy()
    check("rocket flies away from the robot", (p1 - p0).length > 3.0 and (p1 - p0).normalized().dot(d) > 0.8,
          f"{(p1 - p0).length:.1f} m in 8 frames")
    scene.frame_set(49)
    check("rocket hidden before launch", res["rocket"].hide_render)
    pts = res["smoke"].data
    check("smoke trail has puffs along the path", len(pts.vertices) > 100, f"{len(pts.vertices)} puffs")
    n = fx.clear_fx(scene)
    check("clear effects", n > 0 and not any(o.name.startswith("FX-roquette") for o in bpy.data.objects), f"{n} removed")
    bpy.data.objects.remove(ground)


class FakeClock:
    t = 1000.0


def run(ctrl, pad, seconds, dt=1 / 60):
    for _ in range(int(seconds / dt)):
        ctrl.step(pad, dt)


@section
def test_pilot_locomotion():
    s = scene.robot_anim
    clear_anim()
    ctrl = pilot.PilotController(rig, s)
    start = Vector(ctrl.root_pos)
    run(ctrl, gamepad.PadState(ly=1.0, connected=True), 4)
    moved = start - ctrl.root_pos
    check("pilot: left stick walks forward (-Y)", moved.y > 3.0 and abs(moved.x) < 0.05, str(moved))
    nsteps = sum(f.steps for f in ctrl.loco.feet.values())
    check("pilot: legs step while walking", nsteps >= 4, f"{nsteps} steps")
    # IK follows the planted feet in the evaluated rig
    upd()
    err = max((wpos(R.foot(sd)) - wpos("DEF-foot." + sd)).length for sd in R.SIDES)
    check("pilot: IK reaches the feet", err < 0.01, f"{err:.4f}")
    run(ctrl, gamepad.PadState(), 3)
    check("pilot: stops when stick released", abs(ctrl.speed) < 0.02 and not ctrl.loco.stepping(), f"{ctrl.speed:.3f}")
    # 360 degree turn on the spot: the legs must step, the feet must stay under the body
    y0, p0 = ctrl.root_yaw, Vector(ctrl.root_pos)
    steps0 = sum(f.steps for f in ctrl.loco.feet.values())
    run(ctrl, gamepad.PadState(lx=-1.0), 360 / s.pilot_turn_speed + 0.6)
    turned = math.degrees(ctrl.root_yaw - y0)
    check("pilot: turns 360 deg on the spot", turned > 355 and (Vector(ctrl.root_pos) - p0).length < 0.05,
          f"{turned:.0f} deg")
    check("pilot: legs step to turn", sum(f.steps for f in ctrl.loco.feet.values()) - steps0 >= 8)
    worst = max(math.dist((f.x, f.y), ctrl.loco.home(f.side)) for f in ctrl.loco.feet.values())
    check("pilot: feet stay under the body while turning", worst < 0.8, f"{worst:.2f} m")
    # sprint with L3
    run(ctrl, gamepad.PadState(), 2)
    ctrl.step(gamepad.PadState(ly=1.0, buttons=frozenset({"l3"})), 1 / 60)
    run(ctrl, gamepad.PadState(ly=1.0), 3)
    check("pilot: L3 sprints", ctrl.sprint and ctrl.speed > s.loco_walk_speed * 1.6, f"{ctrl.speed:.2f} m/s")
    run(ctrl, gamepad.PadState(), 3)
    check("pilot: sprint ends when stopping", not ctrl.sprint)
    # strafe
    p0 = Vector(ctrl.root_pos)
    yaw = ctrl.root_yaw
    run(ctrl, gamepad.PadState(lx=1.0, buttons=frozenset({"square"})), 2)
    dpos = Vector(ctrl.root_pos) - p0
    right = Vector((-math.cos(yaw), -math.sin(yaw), 0))
    check("pilot: square + stick strafes", dpos.dot(right) > 0.5 and abs(ctrl.root_yaw - yaw) < 0.01, str(dpos))


@section
def test_pilot_head_turrets_fire():
    s = scene.robot_anim
    clear_anim()
    ctrl = pilot.PilotController(rig, s)
    run(ctrl, gamepad.PadState(buttons=frozenset({"left"})), 2)
    check("pilot: D-pad turns the head", ctrl.head_yaw > math.radians(40), f"{math.degrees(ctrl.head_yaw):.0f}")
    run(ctrl, gamepad.PadState(rx=1.0, buttons=frozenset({"l1"})), 3)
    check("pilot: L1 + right stick turns the head, not the turrets",
          ctrl.head_yaw < -math.radians(40) and abs(ctrl.turrets["L"]["yaw"]) < 1e-6, f"{math.degrees(ctrl.head_yaw):.0f}")
    ctrl.step(gamepad.PadState(buttons=frozenset({"triangle"})), 1 / 60)
    ctrl.step(gamepad.PadState(), 1 / 60)
    check("pilot: triangle selects the left turret", ctrl.selected == {"L"})
    run(ctrl, gamepad.PadState(rx=1.0, ry=1.0), 2)
    tl, tr = ctrl.turrets["L"], ctrl.turrets["R"]
    lo, hi = R.yaw_range(LIM, "L")
    check("pilot: right stick aims the selected turret only", tl["yaw"] > hi - 1e-3 and abs(tr["yaw"]) < 1e-6,
          f"L yaw {math.degrees(tl['yaw']):.1f}")
    check("pilot: aim stays inside limits", tl["yaw"] <= hi + 1e-6 and tl["pitch"] <= R.pitch_range(LIM)[1] + 1e-6)
    run(ctrl, gamepad.PadState(r2=1.0), 1.5)
    check("pilot: R2 spins the gatling", tl["rps"] > 0.8 * s.turret_spin_rps, f"{tl['rps']:.2f} rps")
    flashes = 0
    for _ in range(60):
        ctrl.step(gamepad.PadState(r2=1.0), 1 / 60)
        flashes += tl["flash"] > 0
    check("pilot: R2 makes muzzle flashes", 20 < flashes < 60, f"{flashes}/60 frames")
    _ev, rockets = ctrl.step(gamepad.PadState(buttons=frozenset({"r1"})), 1 / 60)
    check("pilot: R1 fires a rocket", rockets == [("L", 1)], str(rockets))
    n = 0
    for _ in range(120):
        _ev, r = ctrl.step(gamepad.PadState(buttons=frozenset({"r1"})), 1 / 60)
        n += len(r)
    check("pilot: holding R1 fires a salvo", 4 <= n <= 7, f"{n} rockets in 2 s")
    ev, _r = ctrl.step(gamepad.PadState(buttons=frozenset({"cross"})), 1 / 60)
    check("pilot: cross toggles recording", "toggle_record" in ev)
    ev, _r = ctrl.step(gamepad.PadState(buttons=frozenset({"options"})), 1 / 60)
    check("pilot: options quits", "quit" in ev)


@section
def test_recording():
    s = scene.robot_anim
    clear_anim()
    ctrl = pilot.PilotController(rig, s)
    dt = 1 / 60
    real_now = gamepad.now
    gamepad.now = lambda: FakeClock.t
    try:
        scene.frame_set(1000)
        rec = pilot.Recorder(rig, scene, s)
        frames = []
        for i in range(int(3 / dt)):
            FakeClock.t += dt
            ctrl.step(gamepad.PadState(ly=0.8, lx=0.3, r2=1.0 if i > 60 else 0.0), dt)
            fr = rec.tick(ctrl)
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
    flash = bpy.data.objects.get("FX-flash.L")
    keyed = [kp.co.x for fc in animdata.all_fcurves(flash) if fc.data_path == "scale" for kp in fc.keyframe_points
             if 1000 <= kp.co.x <= f1] if flash else []
    check("recorder: muzzle flashes keyed", len(keyed) > 10, f"{len(keyed)} keys")
    scene.frame_set(f1)
    root_y = P[R.ROOT].location.y
    scene.frame_set(f0)
    check("recorder: playback moves the robot", root_y < P[R.ROOT].location.y - 0.5, f"{root_y:.2f}")


@section
def test_keyboard():
    kb = gamepad.KeyboardPad()

    class Ev:
        def __init__(self, t, v):
            self.type, self.value = t, v
    for k in ("Z", "LEFT_SHIFT", "SPACE", "U", "UP_ARROW"):
        kb.event(Ev(k, 'PRESS'))
    st = kb.read()
    check("keyboard: Z forward, Shift sprint, Space fire, U head, arrows aim",
          st.ly == 1.0 and st.sprint and st.r2 == 1.0 and st.hx == -1.0 and st.ry == 1.0)
    kb = gamepad.KeyboardPad()
    for k in ("W", "A"):
        kb.event(Ev(k, 'PRESS'))
    st = kb.read()
    check("keyboard: QWERTY W/A", st.ly == 1.0 and st.lx == -1.0)
    kb.event(Ev("LEFT_ALT", 'PRESS'))
    st = kb.read()
    check("keyboard: Alt turns Q/A/D into strafe", st.lx == 0.0 and st.sx == -1.0)
    kb = gamepad.KeyboardPad()
    kb.event(Ev("F", 'PRESS'))
    kb.event(Ev("R", 'PRESS'))
    st = kb.read()
    check("keyboard: F rocket, R record", {"r1", "cross"} <= st.buttons)
    st = gamepad.apply_deadzone(gamepad.PadState(lx=0.05, ly=0.6), 0.12)
    check("deadzone", st.lx == 0.0 and 0.5 < st.ly < 0.6)
    pad, msg = gamepad.open_pad("AUTO")
    check("gamepad: open_pad never raises", isinstance(msg, str), msg)
    if pad:
        check("gamepad: read() works", isinstance(pad.read(), gamepad.PadState))


@section
def test_path_follow():
    s = scene.robot_anim
    clear_anim()
    scene.frame_set(2000)
    res = bpy.ops.robot.path_create(shape='S', length=30.0)
    check("path: create", res == {'FINISHED'} and s.path_object is not None)
    s.path_loop = False
    s.auto_max_seconds = 80
    res = bpy.ops.robot.path_follow()
    check("path: follow runs", res == {'FINISHED'})
    pts = autopilot.curve_points(rig, s.path_object)
    scene.frame_set(scene.frame_end)
    end = wpos(R.ROOT)
    d = math.dist(end.xy, pts[-1][:2])
    check("path: robot reaches the end of the path", d < 1.5, f"{d:.2f} m from the end")
    # max distance from the path while walking
    worst = 0.0
    for f in range(2000, scene.frame_end, 6):
        scene.frame_set(f)
        p = wpos(R.ROOT)
        worst = max(worst, min(math.dist(p.xy, q[:2]) for q in pts))
    check("path: robot stays on the path", worst < 1.2, f"max {worst:.2f} m")
    reach, lowest, _slip = walk_metrics(2000, scene.frame_end, 3)
    check("path: IK reach / soles", reach < 0.01 and lowest > -0.03, f"{reach:.4f} / {lowest:.3f}")
    bpy.data.objects.remove(s.path_object)


@section
def test_navmesh_ai():
    s = scene.robot_anim
    clear_anim()
    bpy.ops.mesh.primitive_plane_add(size=60, location=(0, -20, 0))
    ground = bpy.context.active_object
    ground.name = "TEST_Sol"
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -16, 1.5), scale=(8, 1.5, 3))
    wall = bpy.context.active_object
    wall.name = "TEST_Mur"
    obs = bpy.data.collections.new("TEST_Obstacles")
    scene.collection.children.link(obs)
    obs.objects.link(wall)
    s.ground_object, s.obstacles = ground, obs
    s.nav_cell, s.nav_radius = 1.0, 1.8
    res = bpy.ops.robot.nav_build()
    check("navmesh: build", res == {'FINISHED'} and s.nav_object is not None,
          f"{len(s.nav_object.data.polygons) if s.nav_object else 0} cells")
    navm = autopilot.navmesh_from_object(rig, s.nav_object)
    check("navmesh: wall is not walkable", navm.locate(0, -16) is None and navm.locate(0, -5) is not None)
    path = navm.find_path((0, 0, 0), (0, -30, 0))
    check("navmesh: path goes around the wall (straightened)", path is not None and max(abs(p[0]) for p in path) > 4.5
          and len(path) <= 6, f"{len(path or [])} points: {[tuple(round(c, 1) for c in p[:2]) for p in path or []]}")
    bpy.ops.robot.patrol_clear()
    for loc in ((0, -2, 0), (-12, -30, 0), (12, -30, 0)):
        scene.cursor.location = loc
        bpy.ops.robot.patrol_add()
    s.ai_mode, s.ai_wait, s.ai_laps, s.auto_max_seconds = 'LOOP', 1.0, 1, 200
    scene.frame_set(3000)
    res = bpy.ops.robot.ai_bake()
    check("AI patrol: runs", res == {'FINISHED'})
    pts = autopilot.patrol_points(rig, scene)
    visited = set()
    inside_wall = 0
    for f in range(3000, scene.frame_end, 4):
        scene.frame_set(f)
        p = wpos(R.ROOT)
        for i, q in enumerate(pts):
            if math.dist(p.xy, q[:2]) < 1.5:
                visited.add(i)
        if abs(p.x) < 4 + 0.3 and abs(p.y + 16) < 0.75 + 0.3:
            inside_wall += 1
    check("AI patrol: visits every point", visited == {0, 1, 2}, str(sorted(visited)))
    check("AI patrol: never walks through the wall", inside_wall == 0, f"{inside_wall} frames")
    s.ai_mode = 'GOTO'
    s.ai_target = bpy.data.objects["P02"]
    scene.frame_set(scene.frame_end + 10)
    f0 = scene.frame_current
    res = bpy.ops.robot.ai_bake()
    scene.frame_set(scene.frame_end)
    d = math.dist(wpos(R.ROOT).xy, pts[1][:2])
    check("AI go-to target", res == {'FINISHED'} and d < 1.5, f"{d:.2f} m (from frame {f0})")
    bpy.ops.robot.patrol_clear()
    for o in (ground, wall, s.nav_object):
        bpy.data.objects.remove(o)
    bpy.data.collections.remove(obs)


@section
def test_military_paint():
    s = scene.robot_anim
    mats = paint.robot_materials(rig)
    check("paint: robot materials found", len(mats) >= 2, str([m.name for m in mats]))
    bsdf = paint._principled(mats[0])
    before = {k: (bsdf.inputs[k].links[0].from_socket if bsdf.inputs[k].links else None,
                  bsdf.inputs[k].default_value if not hasattr(bsdf.inputs[k].default_value, "__len__")
                  else tuple(bsdf.inputs[k].default_value))
              for k in ("Base Color", "Metallic", "Roughness", "Coat Weight")}
    for scheme, _l, _d in paint.SCHEME_ITEMS:
        s.paint_scheme = scheme
        res = bpy.ops.robot.military_paint(remove_paint=False)
    linked = bsdf.inputs["Base Color"].links[0].from_node
    check("paint: PBR group drives colour, metal, roughness, coat",
          res == {'FINISHED'} and linked.name == paint.TAG and linked.node_tree.name == paint.GROUP
          and all(bsdf.inputs[k].links and bsdf.inputs[k].links[0].from_node == linked
                  for k in ("Metallic", "Roughness", "Coat Weight")))
    check("paint: only one paint node after re-applying",
          sum(1 for n in mats[0].node_tree.nodes if n.name.startswith(paint.TAG)) == 1)
    parts = {n: bpy.data.objects[n].get(paint.PART_PROP) for n in ("GEO-turret.L", "GEO-gatling.R", "GEO-piston_rod.L",
                                                                   "GEO-shoulder.L", "GEO-torso")}
    check("paint: guns bare metal, piston rods chrome, pods painted",
          parts == {"GEO-turret.L": 1, "GEO-gatling.R": 1, "GEO-piston_rod.L": 2, "GEO-shoulder.L": 0, "GEO-torso": 0},
          str(parts))
    attr = bpy.data.objects["GEO-torso"].data.attributes.get(paint.VISOR_ATTR)
    n_visor = sum(1 for d in attr.data if d.value > 0.5) if attr else 0
    check("paint: visor found on the head", 20 < n_visor < 400, f"{n_visor} faces")
    s.paint_turret_metal = 'ALL'
    bpy.ops.robot.military_paint()
    check("paint: whole turrets in metal option", bpy.data.objects["GEO-shoulder.L"].get(paint.PART_PROP) == 1)
    s.paint_turret_metal = 'GUNS'
    res = bpy.ops.robot.military_paint(remove_paint=True)
    after = {k: (bsdf.inputs[k].links[0].from_socket if bsdf.inputs[k].links else None,
                 bsdf.inputs[k].default_value if not hasattr(bsdf.inputs[k].default_value, "__len__")
                 else tuple(bsdf.inputs[k].default_value))
             for k in before}
    check("paint: removal restores links and values exactly", after == before
          and not any(n.name.startswith(paint.TAG) for n in mats[0].node_tree.nodes))
    # legacy (v1.1/1.2) paint is removed too
    nt = mats[0].node_tree
    g = nt.nodes.new("ShaderNodeGroup")
    g.name = paint.TAG
    g.node_tree = bpy.data.node_groups.new(paint.LEGACY_GROUP, 'ShaderNodeTree')
    g.node_tree.interface.new_socket("C", in_out='INPUT', socket_type='NodeSocketColor')
    nt.links.new(before["Base Color"][0], g.inputs[0])
    check("paint: legacy paint layer removed", paint.remove(mats[0]) and paint.TAG not in nt.nodes)


@section
def test_decals():
    s = scene.robot_anim
    clear_anim()
    s.decal_set = 'FULL'
    res = bpy.ops.robot.decals()
    dec = [o for o in bpy.data.objects if o.get("robot_decal")]
    check("decals: placed on the robot", res == {'FINISHED'} and len(dec) >= 18, f"{len(dec)} decals")
    ok_parent = all(o.parent == rig and o.parent_type == 'BONE' and o.parent_bone in rig.data.bones for o in dec)
    ok_wrap = all(o.modifiers and o.modifiers[0].type == 'SHRINKWRAP' and o.modifiers[0].target is not None for o in dec)
    check("decals: follow their bone and hug the surface", ok_parent and ok_wrap)
    img = bpy.data.images.get("Marquages_Robot_atlas")
    check("decals: atlas loaded and packed", img is not None and img.packed_file is not None and img.size[0] == 2048)
    # a decal stays on its part when the part moves
    d = next(o for o in dec if o.parent_bone == "turret.L")
    rel0 = bpy.data.objects["GEO-turret.L"].matrix_world.inverted() @ d.matrix_world.translation
    R.RigPose(rig).set_turret("L", math.radians(20), math.radians(10), 0.0)
    upd()
    rel1 = bpy.data.objects["GEO-turret.L"].matrix_world.inverted() @ d.matrix_world.translation
    check("decals: move with the turret", (rel1 - rel0).length < 1e-4, f"{(rel1 - rel0).length:.6f}")
    R.RigPose(rig).reset()
    upd()
    s.decal_set = 'LIGHT'
    bpy.ops.robot.decals()
    n_light = len([o for o in bpy.data.objects if o.get("robot_decal")])
    check("decals: light set replaces the full set", 4 <= n_light < len(dec), f"{n_light}")
    bpy.ops.robot.decals(remove=True)
    check("decals: removed", not any(o.get("robot_decal") for o in bpy.data.objects))


@section
def test_rocket_airburst_and_live():
    clear_anim()
    res = fx.launch_rocket(scene, rig, "R", 2, 300, max_range=50, seed=5)
    check("rocket: explodes at the end of its range without any target", res["impact"] is not None and "blast" in res,
          str(res["impact"]))
    o, d = R.RigPose(rig).bone_world(R.rocket_port("R", 2))
    imp = Vector(res["impact"])
    lateral = (imp - o - d * (imp - o).dot(d)).length
    check("rocket: flies straight (no drift)", lateral < 1.0, f"{lateral:.2f} m off the line")
    plan = fx.plan_rocket(scene, rig, "L", 1, max_range=50, seed=2)
    n0 = len(bpy.data.objects)
    live = fx.LiveRocket(scene, plan)
    pos = []
    for t in (0.0, 0.2, 0.4):
        live.update(t)
        pos.append(live.rocket.location.copy())
    births0 = list(live.births)
    live.update(1.0)
    b = [0.0] * len(births0)
    live.smoke.data.attributes["birth"].data.foreach_get("value", b)
    check("live rocket: flies without keyframes", (pos[2] - pos[0]).length > 5 and live.rocket.animation_data is None,
          f"{(pos[2] - pos[0]).length:.1f} m")
    check("live rocket: smoke ages in real time", abs((births0[0] - b[0]) - 1.0) < 1e-4)
    check("live rocket: explosion visible after impact", not live.blast.hide_viewport)
    alive = live.update(60.0)
    live.remove()
    check("live rocket: finishes and is removed", not alive and len(bpy.data.objects) == n0,
          f"{len(bpy.data.objects)} vs {n0}")
    fx.clear_fx(scene)


def _key_count():
    return sum(len(fc.keyframe_points) for fc in animdata.all_fcurves(rig))


@section
def test_cancel_modes():
    s = scene.robot_anim
    clear_anim()
    scene.frame_set(1)
    bpy.ops.robot.walk_preset()
    n_before = _key_count()
    end_before = scene.frame_end
    # 1. path follow, then « undo last action »
    scene.frame_set(500)
    bpy.ops.robot.path_create(shape='LINE', length=12.0)
    s.auto_max_seconds = 20
    res = bpy.ops.robot.path_follow()
    n_after = _key_count()
    check("cancel: path follow wrote keys", res == {'FINISHED'} and n_after > n_before, f"{n_before} -> {n_after}")
    check("cancel: undo button offered", history.last_label() == "Suivi du chemin")
    res = bpy.ops.robot.undo_last()
    check("cancel: undo last action restores the animation", res == {'FINISHED'} and _key_count() == n_before
          and scene.frame_end == end_before, f"{_key_count()} keys")
    check("cancel: no backup action left behind", not any(a.name.endswith(".annuler") for a in bpy.data.actions))
    # 2. bake interrupted half-way (what Échap does in the modal operator)
    snap = history.begin(rig, scene, "Suivi du chemin")
    pts = autopilot.curve_points(rig, s.path_object)
    gen = autopilot.bake_steps(bpy.context, rig, autopilot.FollowPath(pts, speed=1.0), 400)
    for _ in range(10):
        next(gen)
    gen.close()
    check("cancel: interrupted bake had written keys", _key_count() > n_before)
    history.cancel(snap)
    check("cancel: interrupted bake fully reverted", _key_count() == n_before, f"{_key_count()}")
    bpy.data.objects.remove(s.path_object)
    # 3. pilot take thrown away (Share / Suppr / « Annuler la prise »)
    ctrl = pilot.PilotController(rig, s)
    real_now = gamepad.now
    gamepad.now = lambda: FakeClock.t
    try:
        scene.frame_set(800)
        snap = history.begin(rig, scene, "Prise au pilotage")
        rec = pilot.Recorder(rig, scene, s)
        for i in range(90):
            FakeClock.t += 1 / 60
            ctrl.step(gamepad.PadState(ly=0.8, r2=1.0), 1 / 60)
            rec.tick(ctrl)
        rocket = fx.launch_rocket(scene, rig, "L", 1, 810, max_range=40, seed=1)["rocket"].name
    finally:
        gamepad.now = real_now
    check("cancel: take recorded keys", _key_count() > n_before)
    history.cancel(snap)
    check("cancel: take thrown away, rockets of the take removed",
          _key_count() == n_before and rocket not in bpy.data.objects, f"{_key_count()}")
    ev, _r = ctrl.step(gamepad.PadState(buttons=frozenset({"share"})), 1 / 60)
    check("cancel: Share button asks to cancel the take", "cancel_take" in ev)

    class Ev:
        def __init__(self, t, v):
            self.type, self.value = t, v
    kb = gamepad.KeyboardPad()
    kb.event(Ev("DEL", 'PRESS'))
    check("cancel: Suppr key = Share", "share" in kb.read().buttons)
    check("cancel: operators registered", all(hasattr(bpy.ops.robot, n) for n in
                                                ("undo_last", "bake_cancel", "pilot_request")))
    clear_anim()


@section
def test_locomotion_pure():
    rest = {"L": (1.02, -0.111), "R": (-1.02, -0.111)}
    for name, cmd, secs, sprint in (("walk", (1, 0, 0), 6, False), ("sprint", (1, 0, 0), 6, True),
                                    ("back", (-1, 0, 0), 4, False), ("turn", (0, 0, 1), 7, False),
                                    ("walk+turn", (1, 0, 0.6), 8, False)):
        lo = LM.Locomotor(LM.LocoParams(), rest)
        worst = 0.0
        for _ in range(int(secs * 24)):
            out = lo.step(*cmd, dt=1 / 24, sprint=sprint)
            bz = out.body_offset[2]
            for sd, (x, y, z, *_r) in out.feet.items():
                lx, ly, lz = lo.to_root_space(x, y, z)
                hip = (1.02 if sd == "L" else -1.02, 0.58, 2.962 + bz)
                worst = max(worst, math.dist(hip, (lx, ly, 0.535 + lz)))
        check(f"locomotion {name}: legs can reach", worst < 2.55, f"hip-ankle max {worst:.3f} m")


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
    s = scene.robot_anim
    for kind, _l, _d in addon.presets.TURRET_PRESETS:
        s.turret_preset = kind
        for mode in ('LOOP', 'GOTO', 'WANDER'):
            s.ai_mode = mode
            for cls in ui.classes:
                panel = type("P", (), {"layout": StubLayout(), "ROWS": getattr(cls, "ROWS", ())})()
                cls.draw(panel, ctx)
                pilot.STATUS.update(running=True, recording=True, monitor=True, monitor_text="a\nb")
                autopilot.BAKE.update(running=True, label="Mode IA", progress=0.4)
                cls.draw(panel, ctx)
                pilot.STATUS.update(running=False, recording=False, monitor=False, monitor_text="")
                autopilot.BAKE.update(running=False, label="", progress=0.0)
    check("UI panels reference valid props/operators", not StubLayout.errors, str(StubLayout.errors[:5]))
    prefs = addon.props.RobotAnimPreferences
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
