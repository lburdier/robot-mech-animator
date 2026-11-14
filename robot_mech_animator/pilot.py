"""Live piloting of the robot with a PS4 controller or the keyboard + real-time keyframe recording.

DualShock 4                         Keyboard (AZERTY / QWERTY)
  Left stick   walk / turn            Z/W S   forward / back, Q/A D turn
  L3 (click)   sprint                 Shift   sprint (hold)
  Square (hold) + left stick: strafe  Alt / E + Q/D : strafe
  Right stick  aim the turret(s)      arrows or I J K L
  L1 (hold) + right stick: head       U O (turn) Y H (tilt) the head
  D-pad        head turn / tilt       numpad 4 6 8 2
  R2           gatling fire           Space
  R1           rocket (hold = salvo)  F
  L2           crouch                 Ctrl / C
  Triangle     turret L / R / both    T
  R3           re-centre turrets      V
  Cross        start / stop recording R
  Circle       rest pose              Backspace
  Options / PS quit                   Esc
"""
import math
import random

import bpy
from bpy.types import Operator
from mathutils import Vector

from . import animdata, fx, gamepad, locomotion as LM, rig as R

STATUS = {"running": False, "recording": False, "text": "", "pad": "", "frame": 0, "keys": 0, "selected": "L+R",
          "monitor": False, "monitor_text": ""}

_approach = LM.approach


def loco_params(s):
    """Locomotion parameters from the scene settings."""
    p = LM.LocoParams()
    p.walk_speed = s.loco_walk_speed
    p.sprint_speed = s.loco_sprint_speed
    p.turn_rate = math.radians(s.pilot_turn_speed)
    p.step_height = s.walk_step_height
    p.crouch = s.walk_crouch
    p.foot_back = s.walk_foot_back
    p.bob, p.sway, p.roll, p.lean = s.walk_bob, s.walk_sway, s.walk_roll, s.walk_lean
    p.torso_twist = s.walk_torso_twist
    p.foot_pitch, p.toe_flex = s.walk_foot_pitch, s.walk_toe_flex
    p.flap_swing = s.walk_flap
    fps = s.id_data.render.fps / s.id_data.render.fps_base
    n = max(8, s.walk_cycle_frames)
    p.swing_time = max(0.25, n / fps * (1.0 - s.walk_duty))
    p.sprint_swing_time = max(0.2, p.swing_time * 0.65)
    return p


def make_locomotor(rig, s, ground=None):
    pose = R.RigPose(rig)
    pos, yaw = pose.get_root()
    rest = {side: tuple(pose.foot_rest(side))[:2] for side in R.SIDES}
    return pose, LM.Locomotor(loco_params(s), rest, pos=tuple(pos), yaw=yaw, ground=ground)


class PilotController:
    """Pure logic: gamepad state + dt -> robot pose. No Blender UI involved."""

    def __init__(self, rig, settings, ground=None):
        self.rig = rig
        self.s = settings
        self.pose, self.loco = make_locomotor(rig, settings, ground)
        self.lim = self.pose.lim
        ty, tp = self.pose.get_torso()
        self.head_yaw, self.head_pitch = ty, tp
        self.turrets = {}
        for side in R.SIDES:
            yaw_t, pitch_t, _rec = self.pose.get_turret(side)
            self.turrets[side] = {"yaw": yaw_t, "pitch": pitch_t, "recoil": 0.0,
                                  "spin": self.pose.pb[R.gatling(side)].rotation_euler.y, "rps": 0.0, "flash": 0.0}
        self.selected = {"L", "R"}
        self.sprint = False
        self.idle_time = 0.0
        self.prev_buttons = frozenset()
        self.shot_clock = 0.0
        self.rocket_clock = 0.0
        self.rocket_count = 0
        self.rng = random.Random(1)
        self.out = None

    # convenience for tests / UI
    @property
    def root_pos(self):
        return Vector((self.loco.x, self.loco.y, self.loco.z))

    @property
    def root_yaw(self):
        return self.loco.yaw

    @property
    def speed(self):
        return self.loco.v_fwd

    # ------------------------------------------------------------------ input
    def step(self, pad, dt):
        """Advance the simulation. Returns (events, rockets) where rockets is a list of (side, port)."""
        s = self.s
        dt = max(0.0, min(dt, 0.1))
        events = set()
        rockets = []
        pressed = pad.buttons - self.prev_buttons
        self.prev_buttons = pad.buttons

        if "cross" in pressed:
            events.add("toggle_record")
        if "options" in pressed or "ps" in pressed:
            events.add("quit")
        if "circle" in pressed:
            self.reset()
        if "r3" in pressed:
            for t in self.turrets.values():
                t["yaw"] = t["pitch"] = 0.0
        if "triangle" in pressed:
            order = [{"L", "R"}, {"L"}, {"R"}]
            i = next((k for k, o in enumerate(order) if o == self.selected), 0)
            self.selected = order[(i + 1) % 3]

        # ---- locomotion ---------------------------------------------------
        if "l3" in pressed:
            self.sprint = not self.sprint
        if abs(pad.ly) < 0.2:
            self.idle_time += dt
            if self.idle_time > 0.6:
                self.sprint = False          # sprint stops with the robot
        else:
            self.idle_time = 0.0
        strafe_btn = "square" in pad.buttons
        fwd = pad.ly * s.pilot_walk_speed
        side = -(pad.sx or (pad.lx if strafe_btn else 0.0))     # locomotor: side > 0 = robot's left
        turn = 0.0 if strafe_btn else -pad.lx
        sprint = self.sprint or pad.sprint
        self.out = self.loco.step(fwd, side, turn, dt, sprint=sprint, crouch=pad.l2 * s.pilot_crouch_depth)

        # ---- head (torso) -------------------------------------------------
        head_mode = "l1" in pad.buttons
        hx = pad.hx + (1.0 if "right" in pad.buttons else 0.0) - (1.0 if "left" in pad.buttons else 0.0)
        hy = pad.hy + (1.0 if "up" in pad.buttons else 0.0) - (1.0 if "down" in pad.buttons else 0.0)
        if head_mode:
            hx += pad.rx
            hy += pad.ry * (-1.0 if s.pilot_invert_y else 1.0)
        rate = math.radians(s.pilot_torso_speed)
        ylim = math.radians(self.lim["torso_yaw"])
        plim = math.radians(R.TORSO_PITCH)
        # yaw > 0 turns left -> stick right turns right
        self.head_yaw = R.clamp(self.head_yaw - hx * rate * dt, -ylim, ylim)
        self.head_pitch = R.clamp(self.head_pitch - hy * rate * 0.5 * dt, -plim, plim)

        # ---- turrets --------------------------------------------------------
        aim = math.radians(s.pilot_aim_speed)
        inv = -1.0 if s.pilot_invert_y else 1.0
        p_lo, p_hi = R.pitch_range(self.lim)
        fire = pad.r2
        self.shot_clock += dt
        for sd in R.SIDES:
            t = self.turrets[sd]
            y_lo, y_hi = R.yaw_range(self.lim, sd)
            active = sd in self.selected
            if active and not head_mode:
                if s.pilot_aim_mode == 'ABSOLUTE':
                    t["yaw"] = _approach(t["yaw"], (pad.rx * (y_hi if pad.rx > 0 else -y_lo)), 10.0, dt)
                    ty = pad.ry * inv
                    t["pitch"] = _approach(t["pitch"], ty * (p_hi if ty > 0 else -p_lo), 10.0, dt)
                else:
                    t["yaw"] = R.clamp(t["yaw"] + pad.rx * aim * dt, y_lo, y_hi)
                    t["pitch"] = R.clamp(t["pitch"] + pad.ry * inv * aim * dt, p_lo, p_hi)
            target_rps = s.turret_spin_rps * fire if active else 0.0
            t["rps"] = _approach(t["rps"], target_rps, 3.0 if target_rps > t["rps"] else 1.5, dt)
            t["spin"] += 2.0 * math.pi * t["rps"] * dt
            firing = active and fire > 0.3 and t["rps"] > 0.6 * s.turret_spin_rps * fire
            kick = 0.0
            if firing:
                kick = (0.03 + 0.03 * fire) * (0.5 + 0.5 * math.cos(self.shot_clock * 2 * math.pi * 12))
            t["recoil"] = _approach(t["recoil"], kick, 30.0, dt)
            t["flash"] = (0.6 + 0.6 * fire) if firing and self.rng.random() < 0.75 else 0.0

        # ---- rockets --------------------------------------------------------
        self.rocket_clock -= dt
        if "r1" in pressed or ("r1" in pad.buttons and self.rocket_clock <= 0.0):
            sides = sorted(self.selected)
            sd = sides[self.rocket_count % len(sides)]
            port = 1 + (self.rocket_count // len(sides)) % 2
            rockets.append((sd, port))
            self.rocket_count += 1
            self.rocket_clock = s.rocket_interval
            self.turrets[sd]["recoil"] = max(self.turrets[sd]["recoil"], 0.12)
        self.apply()
        return events, rockets

    def reset(self):
        self.head_yaw = self.head_pitch = 0.0
        self.sprint = False
        for t in self.turrets.values():
            t.update(yaw=0.0, pitch=0.0, recoil=0.0, rps=0.0)

    # ------------------------------------------------------------------ output
    def apply(self):
        if self.out is None:
            self.out = self.loco._pose()
        LM.apply_output(self.pose, self.loco, self.out, torso_yaw=self.head_yaw, torso_pitch=self.head_pitch)
        sway = self.out.turret_sway
        for side, t in self.turrets.items():
            self.pose.set_turret(side, t["yaw"], t["pitch"] + sway, t["recoil"])
            self.pose.set_gatling(side, t["spin"])


class Recorder:
    """Writes the pilot's controller values as keyframes in real time."""

    def __init__(self, rig, scene, settings):
        self.rig = rig
        self.scene = scene
        self.s = settings
        self.cache = animdata.FCurveCache(rig)
        self.start_frame = scene.frame_current
        self.t0 = gamepad.now()
        self.last = None
        self.count = 0
        self.fps = scene.render.fps / scene.render.fps_base
        self.channels = dict(R.CHANNELS)
        self.flash_prev = {s: 0.0 for s in R.SIDES}
        self.rng = random.Random(3)

    def frame_now(self):
        elapsed = (gamepad.now() - self.t0) * self.s.pilot_time_scale
        return self.start_frame + int(elapsed * self.fps)

    def tick(self, ctrl=None):
        """Key the current pose if the timeline moved to a new frame. Returns the frame or None."""
        frame = self.frame_now()
        step = max(1, self.s.pilot_key_every)
        if self.last is not None and frame < self.last + step:
            return None
        animdata.key_bones(self.cache, self.rig, frame, self.channels)
        if ctrl is not None and self.s.fx_flash:
            for side, t in ctrl.turrets.items():
                if t["flash"] > 0 or self.flash_prev[side] > 0:
                    obj = fx.ensure_muzzle_flash(self.rig, side, self.scene)
                    fx.key_flash(obj, frame, t["flash"], self.rng)
                self.flash_prev[side] = t["flash"]
        self.count += 1
        self.last = frame
        return frame

    def stop(self):
        self.cache.finish()
        removed = 0
        if self.s.pilot_simplify:
            fcs = [fc for fc in self.cache.touched]
            removed = animdata.simplify(fcs, tol_loc=self.s.pilot_simplify_tol, tol_rot=self.s.pilot_simplify_tol * 1.75)
        if self.last is not None and self.s.pilot_extend_range:
            self.scene.frame_end = max(self.scene.frame_end, self.last + 60)
        return self.start_frame, self.last, removed


def read_prefs(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def open_from_prefs(context):
    p = read_prefs(context)
    backend = p.backend if p else "AUTO"
    index = p.device_index if p else -1
    mapping = buttons = None
    if p and p.custom_mapping:
        mapping = {k: getattr(p, "axis_" + k) for k in ("lx", "ly", "rx", "ry", "l2", "r2")}
    return gamepad.open_pad(backend, index, mapping, buttons)


def deadzone_from_prefs(context):
    p = read_prefs(context)
    return p.deadzone if p else 0.12


def ground_function(context, rig):
    """Ground height callback (armature space) from the navmesh or the ground object, or None."""
    from . import autopilot
    s = context.scene.robot_anim
    return autopilot.ground_callback(rig, s.nav_object or s.ground_object)


class ROBOT_OT_pilot(Operator):
    """Piloter le robot en direct avec une manette PS4 ou le clavier, et enregistrer des clés"""
    bl_idname = "robot.pilot"
    bl_label = "Piloter le robot"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return R.find_rig(context) is not None and not STATUS["running"]

    def invoke(self, context, event):
        rig = R.find_rig(context)
        missing = R.missing_bones(rig)
        if missing:
            self.report({'ERROR'}, "Rig incomplet (reconstruisez-le avec tools/build_rig.py), os manquants : " + ", ".join(missing))
            return {'CANCELLED'}
        s = context.scene.robot_anim
        self.pad, msg = open_from_prefs(context)
        self.kb = gamepad.KeyboardPad()
        self.ctrl = PilotController(rig, s, ground=ground_function(context, rig))
        self.rec = None
        self.dz = deadzone_from_prefs(context)
        self.t_last = gamepad.now()
        self.flash_rng = random.Random(5)
        self.warned_rocket = False
        STATUS.update(running=True, recording=False, pad=msg, keys=0, text="Pilotage actif")
        wm = context.window_manager
        self.timer = wm.event_timer_add(1.0 / 60.0, window=context.window)
        wm.modal_handler_add(self)
        if s.pilot_record_on_start:
            self.start_recording(context)
        self.report({'INFO'}, f"Pilotage : {msg}. Croix / R = enregistrer, Options / Échap = quitter")
        return {'RUNNING_MODAL'}

    # -------------------------------------------------------------- recording
    def start_recording(self, context):
        self.rec = Recorder(self.ctrl.rig, context.scene, context.scene.robot_anim)
        STATUS.update(recording=True, keys=0)

    def stop_recording(self, context):
        if not self.rec:
            return
        f0, f1, removed = self.rec.stop()
        n = self.rec.count
        self.rec = None
        STATUS.update(recording=False)
        if f1 is not None:
            self.report({'INFO'}, f"Enregistré : images {f0}–{f1} ({n} poses, {removed} clés simplifiées)")

    def fire_rockets(self, context, rockets):
        s = context.scene.robot_anim
        rig = self.ctrl.rig
        if not self.rec:
            if not self.warned_rocket:
                self.report({'INFO'}, "Roquettes : lancez l'enregistrement (Croix / R) pour les voir partir dans la timeline")
                self.warned_rocket = True
            return
        context.view_layer.update()
        target = None
        if s.rocket_use_target:
            target = rig.matrix_world @ rig.pose.bones[R.AIM].head
        for side, port in rockets:
            fx.launch_rocket(context.scene, rig, side, port, context.scene.frame_current, target=target,
                             max_range=s.rocket_range, seed=self.ctrl.rocket_count, smoke_density=s.rocket_smoke)

    # -------------------------------------------------------------- loop
    def modal(self, context, event):
        if event.type == 'ESC' and event.value == 'PRESS':
            return self.finish(context)
        if event.type != 'TIMER':
            if self.kb.event(event):
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}

        t = gamepad.now()
        dt, self.t_last = t - self.t_last, t
        state = self.pad.read() if self.pad else gamepad.PadState()
        state = gamepad.apply_deadzone(state, self.dz).merged(self.kb.read())
        events, rockets = self.ctrl.step(state, dt)
        if "toggle_record" in events:
            if self.rec:
                self.stop_recording(context)
            else:
                self.start_recording(context)
        if self.rec:
            frame = self.rec.frame_now()
            if frame != context.scene.frame_current:
                context.scene.frame_current = frame
                self.ctrl.apply()       # frame change re-evaluates the action: re-apply the live pose
            if self.rec.tick(self.ctrl) is not None:
                STATUS["frame"] = frame
                STATUS["keys"] = self.rec.count
        elif context.scene.robot_anim.fx_flash:
            for side, tr in self.ctrl.turrets.items():
                if tr["flash"] > 0 or bpy.data.objects.get(f"FX-flash.{side}"):
                    obj = fx.ensure_muzzle_flash(self.ctrl.rig, side, context.scene)
                    fx.flash_state(obj, tr["flash"], self.flash_rng)
        if rockets:
            self.fire_rockets(context, rockets)
        sel = "+".join(sorted(self.ctrl.selected))
        STATUS.update(selected=sel, text=(
            f"{'● REC' if self.rec else '○'}  tourelle(s) {sel}  vitesse {self.ctrl.speed:+.2f} m/s"
            f"{'  SPRINT' if self.ctrl.loco.sprint > 0.5 else ''}"),
            pad=(state.name or STATUS["pad"]))
        if "quit" in events:
            return self.finish(context)
        for area in context.screen.areas if context.screen else []:
            if area.type in {'VIEW_3D', 'DOPESHEET_EDITOR', 'GRAPH_EDITOR', 'TIMELINE'}:
                area.tag_redraw()
        return {'RUNNING_MODAL'}

    def finish(self, context):
        self.stop_recording(context)
        context.window_manager.event_timer_remove(self.timer)
        if self.pad:
            self.pad.close()
        for side in R.SIDES:
            obj = bpy.data.objects.get(f"FX-flash.{side}")
            if obj is not None and not (obj.animation_data and obj.animation_data.action):
                fx.flash_state(obj, 0.0, self.flash_rng)
        STATUS.update(running=False, recording=False, text="")
        return {'FINISHED'}

    def cancel(self, context):
        self.finish(context)


class ROBOT_OT_pad_monitor(Operator):
    """Afficher en direct les valeurs de la manette (pour vérifier le mappage)"""
    bl_idname = "robot.pad_monitor"
    bl_label = "Tester la manette"

    def invoke(self, context, event):
        if STATUS["monitor"]:
            STATUS["monitor"] = False
            return {'FINISHED'}
        self.pad, msg = open_from_prefs(context)
        STATUS.update(monitor=True, monitor_text=msg, pad=msg)
        if not self.pad:
            STATUS["monitor"] = False
            self.report({'WARNING'}, msg)
            return {'CANCELLED'}
        self.dz = deadzone_from_prefs(context)
        self.timer = context.window_manager.event_timer_add(1.0 / 20.0, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if not STATUS["monitor"] or (event.type == 'ESC' and event.value == 'PRESS'):
            context.window_manager.event_timer_remove(self.timer)
            STATUS["monitor"] = False
            return {'FINISHED'}
        if event.type == 'TIMER':
            st = gamepad.apply_deadzone(self.pad.read(), self.dz)
            STATUS["monitor_text"] = (
                f"{st.name} {'connectée' if st.connected else 'DÉCONNECTÉE'}\n"
                f"G ({st.lx:+.2f}, {st.ly:+.2f})  D ({st.rx:+.2f}, {st.ry:+.2f})  L2 {st.l2:.2f}  R2 {st.r2:.2f}\n"
                f"Boutons : {' '.join(sorted(st.buttons)) or '—'}\n{st.raw}")
            for area in context.screen.areas if context.screen else []:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return {'PASS_THROUGH'}


class ROBOT_OT_detect_pads(Operator):
    """Lister les manettes détectées"""
    bl_idname = "robot.detect_pads"
    bl_label = "Détecter les manettes"

    def execute(self, context):
        devs = gamepad.WinMMPad.list_devices()
        pad, msg = open_from_prefs(context)
        if pad:
            pad.close()
        txt = ", ".join(f"#{i} {n}" for i, n, _s in devs) or "aucune (WinMM)"
        STATUS["pad"] = msg
        self.report({'INFO'}, f"Manettes : {txt} — utilisée : {msg}")
        return {'FINISHED'}


classes = (ROBOT_OT_pilot, ROBOT_OT_pad_monitor, ROBOT_OT_detect_pads)
