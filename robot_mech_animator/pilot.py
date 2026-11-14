"""Live piloting of the robot with a PS4 controller + real-time keyframe recording.

Default mapping (DualShock 4)
  Left stick ............ walk forward / backward, turn
  Right stick ........... aim the selected turret(s)
  R2 (analog) ........... fire: gatling spin + recoil
  L2 (analog) ........... crouch
  L1 / R1 / Triangle .... select left / right / both turrets
  D-pad left / right .... torso twist         D-pad up / down: body height
  Cross ................. start / stop recording keyframes
  Circle ................ reset the pose (stop walking)
  Square ................ toggle "walk in place"
  L3 .................... run (longer strides)       R3: re-centre turrets
  Options / PS / Esc .... quit the pilot mode
"""
import math

import bpy
from bpy.types import Operator
from mathutils import Vector

from . import animdata, gait, gamepad, rig as R

STATUS = {"running": False, "recording": False, "text": "", "pad": "", "frame": 0, "keys": 0, "selected": "L+R",
          "monitor": False, "monitor_text": ""}


def _approach(cur, target, rate, dt):
    """Exponential smoothing that does not depend on the frame rate."""
    k = 1.0 - math.exp(-rate * dt)
    return cur + (target - cur) * k


class PilotController:
    """Pure logic: gamepad state + dt -> robot pose. No Blender UI involved."""

    def __init__(self, rig, settings, params):
        self.rig = rig
        self.s = settings
        self.params = params
        self.pose = R.RigPose(rig)
        pos, yaw = self.pose.get_root()
        self.root_pos = Vector(pos)
        self.root_yaw = yaw
        self.phase = 0.0
        self.amp = 0.0
        self.direction = 1.0
        self.speed_cmd = 0.0
        self.crouch = 0.0
        self.height = 0.0
        self.torso_yaw = self.pose.pb[R.TORSO].rotation_euler.y
        self.turrets = {}
        for side in R.SIDES:
            yaw_t, pitch_t, _rec = self.pose.get_turret(side)
            self.turrets[side] = {"yaw": yaw_t, "pitch": pitch_t, "recoil": 0.0,
                                  "spin": self.pose.pb[R.gatling(side)].rotation_euler.y, "rps": 0.0}
        self.selected = {"L", "R"}
        self.in_place = False
        self.run = False
        self.prev_buttons = frozenset()
        self.shot_clock = 0.0
        self.distance = 0.0

    # ------------------------------------------------------------------ input
    def step(self, pad, dt):
        """Advance the simulation. Returns a set of events for the operator."""
        s = self.s
        dt = max(0.0, min(dt, 0.1))
        events = set()
        pressed = pad.buttons - self.prev_buttons
        self.prev_buttons = pad.buttons

        if "cross" in pressed:
            events.add("toggle_record")
        if "options" in pressed or "ps" in pressed:
            events.add("quit")
        if "circle" in pressed:
            self.reset()
        if "square" in pressed:
            self.in_place = not self.in_place
        if "l3" in pressed:
            self.run = not self.run
        if "r3" in pressed:
            for t in self.turrets.values():
                t["yaw"] = t["pitch"] = 0.0
        if "l1" in pad.buttons and "r1" in pad.buttons or "triangle" in pressed:
            self.selected = {"L", "R"}
        elif "l1" in pressed:
            self.selected = {"L"}
        elif "r1" in pressed:
            self.selected = {"R"}

        # ---- locomotion ---------------------------------------------------
        stick = pad.ly
        target_speed = stick * (1.5 if self.run else 1.0) * s.pilot_walk_speed
        self.speed_cmd = _approach(self.speed_cmd, target_speed, 4.0, dt)
        moving = abs(self.speed_cmd) > 0.02
        self.amp = _approach(self.amp, min(1.0, abs(self.speed_cmd)) if moving else 0.0, 5.0, dt)
        if moving:
            self.direction = 1.0 if self.speed_cmd > 0 else -1.0
        n = max(8, s.walk_cycle_frames)
        fps = s.id_data.render.fps / s.id_data.render.fps_base
        cadence = fps / n * (1.25 if self.run else 1.0)            # cycles per second
        if self.amp > 0.01:
            self.phase = (self.phase + cadence * dt) % 1.0
        elif self.amp <= 0.01:
            # settle on a double-support phase so the feet land flat
            self.phase = _approach(self.phase, round(self.phase * 2) / 2, 6.0, dt) % 1.0
        stride_scale = 1.35 if self.run else 1.0
        params = self.params
        if stride_scale != 1.0:
            params = gait.GaitParams(**{**params.__dict__, "stride": params.stride * stride_scale})
        v = params.travel_per_cycle * cadence * self.amp * self.direction
        turn = -pad.lx * math.radians(s.pilot_turn_speed)
        self.root_yaw += turn * dt
        if not self.in_place:
            fwd = Vector((math.sin(self.root_yaw), -math.cos(self.root_yaw), 0.0))
            self.root_pos += fwd * v * dt
            self.distance += abs(v) * dt

        # ---- body -----------------------------------------------------------
        self.crouch = _approach(self.crouch, pad.l2 * s.pilot_crouch_depth, 8.0, dt)
        if "up" in pad.buttons:
            self.height = min(0.25, self.height + 0.4 * dt)
        if "down" in pad.buttons:
            self.height = max(-0.6, self.height - 0.4 * dt)
        twist = (1.0 if "left" in pad.buttons else 0.0) - (1.0 if "right" in pad.buttons else 0.0)
        lim = math.radians(R.TORSO_YAW)
        self.torso_yaw = R.clamp(self.torso_yaw + twist * math.radians(s.pilot_torso_speed) * dt, -lim, lim)

        # ---- turrets --------------------------------------------------------
        aim = math.radians(s.pilot_aim_speed)
        inv = -1.0 if s.pilot_invert_y else 1.0
        ylim, up, down = math.radians(R.TURRET_YAW), math.radians(R.TURRET_PITCH_UP), math.radians(R.TURRET_PITCH_DOWN)
        fire = pad.r2
        self.shot_clock += dt
        for side in R.SIDES:
            t = self.turrets[side]
            active = side in self.selected
            if active:
                if s.pilot_aim_mode == 'ABSOLUTE':
                    t["yaw"] = _approach(t["yaw"], pad.rx * ylim, 10.0, dt)
                    ty = pad.ry * inv
                    t["pitch"] = _approach(t["pitch"], ty * (up if ty > 0 else down), 10.0, dt)
                else:
                    t["yaw"] = R.clamp(t["yaw"] + pad.rx * aim * dt, -ylim, ylim)
                    t["pitch"] = R.clamp(t["pitch"] + pad.ry * inv * aim * dt, -down, up)
            target_rps = s.turret_spin_rps * fire if active else 0.0
            t["rps"] = _approach(t["rps"], target_rps, 3.0 if target_rps > t["rps"] else 1.5, dt)
            t["spin"] += 2.0 * math.pi * t["rps"] * dt
            firing = active and fire > 0.3 and t["rps"] > 0.6 * s.turret_spin_rps * fire
            kick = 0.0
            if firing:
                # recoil pulse at ~12 Hz while firing
                kick = (0.03 + 0.03 * fire) * (0.5 + 0.5 * math.cos(self.shot_clock * 2 * math.pi * 12))
            t["recoil"] = _approach(t["recoil"], kick, 30.0, dt)
        self.apply()
        return events

    def reset(self):
        self.speed_cmd = 0.0
        self.crouch = 0.0
        self.height = 0.0
        self.torso_yaw = 0.0
        for t in self.turrets.values():
            t.update(yaw=0.0, pitch=0.0, recoil=0.0, rps=0.0)

    # ------------------------------------------------------------------ output
    def apply(self):
        data = gait.sample(self.phase, self.params, amp=self.amp, direction=self.direction)
        gait.apply(self.pose, data, extra_crouch=self.crouch - self.height, torso_yaw=self.torso_yaw, turrets=False)
        self.pose.set_root(self.root_pos, self.root_yaw)
        sway = data["turret_pitch"]
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
        # gatling channel recorded linearly for smooth spinning
        self.channels = dict(R.CHANNELS)

    def tick(self):
        """Key the current pose if the timeline moved to a new frame. Returns the frame or None."""
        elapsed = (gamepad.now() - self.t0) * self.s.pilot_time_scale
        frame = self.start_frame + int(elapsed * self.fps)
        step = max(1, self.s.pilot_key_every)
        if self.last is not None and frame < self.last + step:
            return None
        animdata.key_bones(self.cache, self.rig, frame, self.channels)
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
            self.scene.frame_end = max(self.scene.frame_end, self.last)
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


class ROBOT_OT_pilot(Operator):
    """Piloter le robot en direct avec une manette PS4 (ou le clavier) et enregistrer des clés"""
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
            self.report({'ERROR'}, "Rig incomplet, os manquants : " + ", ".join(missing))
            return {'CANCELLED'}
        s = context.scene.robot_anim
        from .presets import gait_params
        self.pad, msg = open_from_prefs(context)
        self.kb = gamepad.KeyboardPad()
        self.ctrl = PilotController(rig, s, gait_params(s))
        self.rec = None
        self.dz = deadzone_from_prefs(context)
        self.t_last = gamepad.now()
        STATUS.update(running=True, recording=False, pad=msg, keys=0, text="Pilotage actif")
        wm = context.window_manager
        self.timer = wm.event_timer_add(1.0 / 60.0, window=context.window)
        wm.modal_handler_add(self)
        if s.pilot_record_on_start:
            self.start_recording(context)
        self.report({'INFO'}, f"Pilotage : {msg}. Croix = enregistrer, Options/Échap = quitter")
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
        events = self.ctrl.step(state, dt)
        if "toggle_record" in events:
            if self.rec:
                self.stop_recording(context)
            else:
                self.start_recording(context)
        if self.rec:
            frame = self.rec.tick()
            if frame is not None:
                context.scene.frame_current = frame
                self.ctrl.apply()       # frame change re-evaluates the action: re-apply the live pose
                STATUS["frame"] = frame
                STATUS["keys"] = self.rec.count
        sel = "+".join(sorted(self.ctrl.selected))
        STATUS.update(selected=sel, text=(
            f"{'● REC' if self.rec else '○'}  tourelle(s) {sel}  vitesse {self.ctrl.speed_cmd:+.2f}"
            f"{'  sur place' if self.ctrl.in_place else ''}{'  course' if self.ctrl.run else ''}"),
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
