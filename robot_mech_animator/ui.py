"""Sidebar panels (3D View > N > Robot)."""
import math

import bpy
from bpy.types import Panel

from . import rig as R
from .pilot import STATUS


class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Robot"


def _rig_or_warn(layout, context):
    rig = R.find_rig(context)
    if rig is None:
        layout.label(text="Aucun rig de robot trouvé", icon='ERROR')
        layout.label(text="(armature avec la propriété robot_rig)")
    return rig


class ROBOT_PT_main(_Base, Panel):
    bl_label = "Robot – Rig"

    def draw(self, context):
        lay = self.layout
        s = context.scene.robot_anim
        lay.prop(s, "rig")
        rig = _rig_or_warn(lay, context)
        if rig is None:
            return
        miss = R.missing_bones(rig)
        if miss:
            lay.label(text=f"Os manquants : {', '.join(miss)}", icon='ERROR')
        lay.label(text=f"Rig : {rig.name}", icon='ARMATURE_DATA')
        row = lay.row(align=True)
        for g, label in (('BODY', "Corps"), ('TURRETS', "Tourelles"), ('LEGS', "Jambes"), ('ALL', "Tout")):
            row.operator("robot.select_group", text=label).group = g
        row = lay.row(align=True)
        row.operator("robot.reset_pose", icon='LOOP_BACK').target = 'ALL'
        row.operator("robot.key_pose", icon='KEYFRAME_HLT')
        box = lay.box()
        box.label(text="Calques d'os", icon='GROUP_BONE')
        row = box.row(align=True)
        for bc in rig.data.collections_all:
            row.prop(bc, "is_visible", text=bc.name, toggle=True)


class ROBOT_PT_turrets(_Base, Panel):
    bl_label = "Tourelles"
    bl_parent_id = "ROBOT_PT_main"

    def draw(self, context):
        lay = self.layout
        rig = R.find_rig(context)
        if rig is None:
            return
        pbs = rig.pose.bones
        col = lay.column(align=True)
        col.label(text="Contrôleurs (X = hausse, Z = cap)", icon='ORIENTATION_GIMBAL')
        for side, label in (("L", "Gauche"), ("R", "Droite")):
            row = col.row(align=True)
            row.label(text=label)
            t = pbs.get(R.turret(side))
            if t:
                row.prop(t, "rotation_euler", index=0, text="Hausse")
                row.prop(t, "rotation_euler", index=2, text="Cap")
                row.prop(t, "location", index=1, text="Recul")
            g = pbs.get(R.gatling(side))
            if g:
                col.prop(g, "rotation_euler", index=1, text=f"Gatling {label.lower()}")
        if "turret_track" in rig:
            lay.prop(rig, '["turret_track"]', text="Suivi de la cible aim_target", slider=True)
        t = pbs.get(R.TORSO)
        if t:
            lay.prop(t, "rotation_euler", index=1, text="Rotation du torse")

        s = context.scene.robot_anim
        box = lay.box()
        box.label(text="Presets", icon='PRESET')
        box.prop(s, "turret_preset")
        box.prop(s, "turret_side", expand=True)
        col = box.column(align=True)
        col.prop(s, "turret_duration")
        k = s.turret_preset
        if k in ('SWEEP', 'SCAN'):
            col.prop(s, "turret_amplitude")
            col.prop(s, "turret_cycles")
        if k == 'AIM':
            col.prop(s, "turret_aim_yaw")
            col.prop(s, "turret_aim_pitch")
        if k == 'BURST':
            col.prop(s, "turret_spin_rps")
        if k in ('BURST', 'SCAN'):
            col.prop(s, "turret_seed")
        box.operator("robot.turret_preset", icon='PLAY').kind = 'SETTINGS'


class ROBOT_PT_walk(_Base, Panel):
    bl_label = "Marche"
    bl_parent_id = "ROBOT_PT_main"

    def draw(self, context):
        lay = self.layout
        s = context.scene.robot_anim
        lay.prop(s, "walk_mode", expand=True)
        col = lay.column(align=True)
        col.prop(s, "walk_cycle_frames")
        if s.walk_mode == 'BAKE':
            col.prop(s, "walk_cycles")
            col.prop(s, "walk_ease")
        col.prop(s, "walk_key_step")
        col = lay.column(align=True)
        col.prop(s, "walk_stride")
        col.prop(s, "walk_step_height")
        col.prop(s, "walk_duty", slider=True)
        col.prop(s, "walk_crouch")
        col.prop(s, "walk_foot_back")
        row = lay.row(align=True)
        row.prop(s, "walk_in_place", toggle=True)
        row.prop(s, "walk_backward", toggle=True)
        box = lay.box()
        box.label(text="Style", icon='MOD_WAVE')
        col = box.column(align=True)
        for p in ("walk_bob", "walk_sway", "walk_roll", "walk_lean", "walk_hip_yaw", "walk_torso_twist",
                  "walk_foot_pitch", "walk_toe_flex"):
            col.prop(s, p)
        row = box.row(align=True)
        row.prop(s, "walk_turret_sway_on", text="")
        row.prop(s, "walk_turret_sway")
        lay.operator("robot.walk_preset", icon='ANIM')


class ROBOT_PT_pilot(_Base, Panel):
    bl_label = "Pilotage manette PS4"
    bl_parent_id = "ROBOT_PT_main"

    def draw(self, context):
        lay = self.layout
        s = context.scene.robot_anim
        if STATUS["running"]:
            box = lay.box()
            box.alert = STATUS["recording"]
            box.label(text=STATUS["text"], icon='REC' if STATUS["recording"] else 'PLAY')
            box.label(text=f"Manette : {STATUS['pad']}")
            if STATUS["recording"]:
                box.label(text=f"Image {STATUS['frame']} – {STATUS['keys']} poses enregistrées")
            box.label(text="Croix = enregistrer  •  Options / Échap = quitter")
        else:
            lay.operator("robot.pilot", icon='PLAY', text="Piloter le robot")
        row = lay.row(align=True)
        row.operator("robot.detect_pads", icon='VIEWZOOM')
        row.operator("robot.pad_monitor", icon='HIDE_OFF', text="Arrêter le test" if STATUS["monitor"] else "Tester la manette")
        if STATUS["monitor"] or STATUS["monitor_text"]:
            box = lay.box()
            for line in STATUS["monitor_text"].splitlines():
                box.label(text=line)
        elif STATUS["pad"] and not STATUS["running"]:
            lay.label(text=STATUS["pad"], icon='INFO')

        col = lay.column(align=True)
        col.label(text="Contrôle")
        col.prop(s, "pilot_walk_speed", slider=True)
        col.prop(s, "pilot_turn_speed")
        col.prop(s, "pilot_aim_speed")
        col.prop(s, "pilot_torso_speed")
        col.prop(s, "pilot_crouch_depth")
        lay.prop(s, "pilot_aim_mode", expand=True)
        lay.prop(s, "pilot_invert_y")
        col = lay.column(align=True)
        col.label(text="Enregistrement")
        col.prop(s, "pilot_key_every")
        col.prop(s, "pilot_time_scale")
        row = col.row(align=True)
        row.prop(s, "pilot_simplify")
        sub = row.row()
        sub.active = s.pilot_simplify
        sub.prop(s, "pilot_simplify_tol")
        col.prop(s, "pilot_extend_range")
        col.prop(s, "pilot_record_on_start")


class ROBOT_PT_help(_Base, Panel):
    bl_label = "Commandes"
    bl_parent_id = "ROBOT_PT_pilot"
    bl_options = {'DEFAULT_CLOSED'}

    ROWS = (
        ("Stick gauche", "marcher / tourner", "Flèches"),
        ("Stick droit", "viser", "I J K L"),
        ("R2", "tirer (gatling + recul)", "Espace"),
        ("L2", "s'accroupir", "C"),
        ("L1 / R1 / Triangle", "tourelle G / D / les deux", "T"),
        ("Croix pad ← →", "rotation du torse", ""),
        ("Croix pad ↑ ↓", "hauteur du corps", ""),
        ("Croix", "enregistrer on/off", "R"),
        ("Rond", "pose de repos", "Retour arrière"),
        ("Carré", "marche sur place", "G"),
        ("L3 / R3", "course / recentrer", "F / V"),
        ("Options / PS", "quitter", "Échap"),
    )

    def draw(self, context):
        col = self.layout.column(align=True)
        for pad, action, key in self.ROWS:
            row = col.row()
            row.label(text=pad)
            row.label(text=action)
            row.label(text=key)


classes = (ROBOT_PT_main, ROBOT_PT_turrets, ROBOT_PT_walk, ROBOT_PT_pilot, ROBOT_PT_help)
