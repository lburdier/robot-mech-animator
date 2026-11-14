"""Sidebar panels (3D View > N > Robot)."""
import math

import bpy
from bpy.types import Panel

from . import rig as R
from . import updater
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
        updater.draw_box(lay, compact=True)
        s = context.scene.robot_anim
        lay.prop(s, "rig")
        rig = _rig_or_warn(lay, context)
        if rig is None:
            return
        miss = R.missing_bones(rig)
        if miss:
            lay.label(text="Rig ancien : reconstruisez-le (tools/build_rig.py)", icon='ERROR')
            lay.label(text=f"Os manquants : {', '.join(miss[:4])}…")
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
    bl_label = "Tête et tourelles"
    bl_parent_id = "ROBOT_PT_main"

    def draw(self, context):
        lay = self.layout
        rig = R.find_rig(context)
        if rig is None:
            return
        pbs = rig.pose.bones
        lim = R.limits(rig)
        t = pbs.get(R.TORSO)
        if t:
            col = lay.column(align=True)
            col.label(text=f"Tête / torse (±{lim['torso_yaw']:.0f}°)", icon='USER')
            row = col.row(align=True)
            row.prop(t, "rotation_euler", index=1, text="Tourner")
            row.prop(t, "rotation_euler", index=0, text="Incliner")
        col = lay.column(align=True)
        col.label(text="Tourelles : épaule (X), canon (X hausse, Z cap)", icon='ORIENTATION_GIMBAL')
        for side, label in (("L", "Gauche"), ("R", "Droite")):
            box = col.box()
            box.label(text=label)
            sh = pbs.get(R.shoulder(side))
            tu = pbs.get(R.turret(side))
            row = box.row(align=True)
            if sh:
                row.prop(sh, "rotation_euler", index=0, text="Épaule")
            if tu:
                row = box.row(align=True)
                row.prop(tu, "rotation_euler", index=0, text="Hausse")
                row.prop(tu, "rotation_euler", index=2, text="Cap")
                row.prop(tu, "location", index=1, text="Recul")
            g = pbs.get(R.gatling(side))
            if g:
                box.prop(g, "rotation_euler", index=1, text="Rotation gatling")
        lay.label(text=f"Limites sans collision : cap {lim['turret_yaw_in']:.0f}° int. / {lim['turret_yaw_out']:.0f}° ext., "
                       f"hausse +{lim['turret_up'] + lim['shoulder_up']:.0f}° / −{lim['turret_down'] + lim['shoulder_down']:.0f}°",
                  icon='INFO')
        if "turret_track" in rig:
            lay.prop(rig, '["turret_track"]', text="Suivi de la cible aim_target", slider=True)

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
        if k == 'ROCKETS':
            col.prop(s, "rocket_count")
            col.prop(s, "rocket_spacing")
        if k in ('BURST', 'SCAN', 'ROCKETS'):
            col.prop(s, "turret_seed")
        box.operator("robot.turret_preset", icon='PLAY').kind = 'SETTINGS'


class ROBOT_PT_fx(_Base, Panel):
    bl_label = "Effets de tir"
    bl_parent_id = "ROBOT_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        lay = self.layout
        s = context.scene.robot_anim
        lay.prop(s, "fx_flash")
        col = lay.column(align=True)
        col.label(text="Roquettes", icon='FORCE_FORCE')
        col.prop(s, "rocket_range")
        col.prop(s, "rocket_smoke")
        col.prop(s, "rocket_use_target")
        col.prop(s, "rocket_interval")
        row = lay.row(align=True)
        op = row.operator("robot.turret_preset", text="Salve de roquettes", icon='PLAY')
        op.kind = 'ROCKETS'
        row.operator("robot.fx_clear", icon='TRASH')
        lay.label(text="La fumée est volumétrique : visible en rendu (EEVEE / Cycles)", icon='INFO')


class ROBOT_PT_walk(_Base, Panel):
    bl_label = "Marche"
    bl_parent_id = "ROBOT_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

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
                  "walk_foot_pitch", "walk_toe_flex", "walk_flap"):
            col.prop(s, p)
        row = box.row(align=True)
        row.prop(s, "walk_turret_sway_on", text="")
        row.prop(s, "walk_turret_sway")
        lay.operator("robot.walk_preset", icon='ANIM')


class ROBOT_PT_pilot(_Base, Panel):
    bl_label = "Pilotage manette PS4 / clavier"
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
            box.label(text="Croix / R = enregistrer  •  Options / Échap = quitter")
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
        col.label(text="Déplacement")
        col.prop(s, "loco_walk_speed")
        col.prop(s, "loco_sprint_speed")
        col.prop(s, "pilot_turn_speed")
        col.prop(s, "pilot_walk_speed", slider=True)
        col.prop(s, "pilot_crouch_depth")
        col = lay.column(align=True)
        col.label(text="Visée")
        col.prop(s, "pilot_aim_speed")
        col.prop(s, "pilot_torso_speed")
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
        ("Stick gauche", "marcher / tourner (360°)", "Z/W S Q/A D"),
        ("L3 (clic)", "sprint", "Maj"),
        ("Carré + stick G", "pas de côté", "Alt/E + Q D"),
        ("Stick droit", "viser", "Flèches / IJKL"),
        ("L1 + stick D", "tourner la tête", "U O Y H"),
        ("Croix directionnelle", "tête (tourner / incliner)", "pavé 4 6 8 2"),
        ("R2", "tir gatling (flash)", "Espace"),
        ("R1", "roquette (maintenu = salve)", "F"),
        ("L2", "s'accroupir", "Ctrl / C"),
        ("Triangle", "tourelle G / D / les deux", "T"),
        ("R3", "recentrer les tourelles", "V"),
        ("Croix", "enregistrer on/off", "R"),
        ("Rond", "pose de repos", "Retour arrière"),
        ("Options / PS", "quitter", "Échap"),
    )

    def draw(self, context):
        col = self.layout.column(align=True)
        for pad, action, key in self.ROWS:
            row = col.row()
            row.label(text=pad)
            row.label(text=action)
            row.label(text=key)


class ROBOT_PT_auto(_Base, Panel):
    bl_label = "Chemin et IA (navmesh)"
    bl_parent_id = "ROBOT_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        lay = self.layout
        s = context.scene.robot_anim
        col = lay.column(align=True)
        col.prop(s, "auto_speed", slider=True)
        row = col.row(align=True)
        row.prop(s, "auto_sprint", toggle=True)
        row.prop(s, "auto_simplify", toggle=True)
        col.prop(s, "auto_max_seconds")

        box = lay.box()
        box.label(text="Chemin prédéfini", icon='CURVE_PATH')
        row = box.row(align=True)
        for shape, label in (('S', "En S"), ('LOOP', "Boucle"), ('LINE', "Ligne")):
            op = row.operator("robot.path_create", text=label)
            op.shape = shape
        box.prop(s, "path_object")
        row = box.row(align=True)
        row.prop(s, "path_loop", toggle=True)
        sub = row.row(align=True)
        sub.active = s.path_loop
        sub.prop(s, "path_laps")
        box.prop(s, "path_start_on_path")
        box.operator("robot.path_follow", icon='ANIM')

        box = lay.box()
        box.label(text="Navmesh", icon='MOD_TRIANGULATE')
        box.prop(s, "ground_object")
        box.prop(s, "obstacles")
        col = box.column(align=True)
        col.prop(s, "nav_cell")
        col.prop(s, "nav_radius")
        col.prop(s, "nav_slope")
        box.operator("robot.nav_build", icon='MESH_GRID')
        box.prop(s, "nav_object")

        box = lay.box()
        box.label(text="IA", icon='GHOST_ENABLED')
        box.prop(s, "ai_mode")
        if s.ai_mode == 'GOTO':
            box.prop(s, "ai_target")
        elif s.ai_mode != 'WANDER':
            row = box.row(align=True)
            row.operator("robot.patrol_add", icon='ADD')
            row.operator("robot.patrol_clear", icon='X', text="")
            col = bpy.data.collections.get("Patrouille_Robot")
            box.label(text=f"{len(col.objects) if col else 0} point(s) de patrouille (curseur 3D)")
        col = box.column(align=True)
        col.prop(s, "ai_wait")
        col.prop(s, "ai_laps")
        col.prop(s, "ai_scan")
        col.prop(s, "ai_seed")
        box.operator("robot.ai_bake", icon='PLAY')


class ROBOT_PT_paint(_Base, Panel):
    bl_label = "Peinture militaire"
    bl_parent_id = "ROBOT_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        lay = self.layout
        s = context.scene.robot_anim
        lay.prop(s, "paint_scheme")
        col = lay.column(align=True)
        col.prop(s, "paint_strength", slider=True)
        col.prop(s, "paint_wear", slider=True)
        col.prop(s, "paint_scale")
        col.prop(s, "paint_seed")
        row = lay.row(align=True)
        row.operator("robot.military_paint", icon='BRUSH_DATA', text="Appliquer").remove_paint = False
        row.operator("robot.military_paint", icon='X', text="Retirer").remove_paint = True


classes = (ROBOT_PT_main, ROBOT_PT_turrets, ROBOT_PT_walk, ROBOT_PT_pilot, ROBOT_PT_help, ROBOT_PT_auto,
           ROBOT_PT_fx, ROBOT_PT_paint)

__all__ = ["math"]
