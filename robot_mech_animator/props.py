"""Scene settings and add-on preferences."""
import math

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty)
from bpy.types import AddonPreferences, PropertyGroup

from . import rig as R
from .paint import SCHEME_ITEMS
from .presets import TURRET_PRESETS

DEG = math.radians


def _rig_poll(self, obj):
    return R.is_robot_rig(obj)


class RobotAnimSettings(PropertyGroup):
    rig: PointerProperty(name="Rig", type=bpy.types.Object, poll=_rig_poll,
                         description="Armature du robot (détectée automatiquement si vide)")

    # ---------------------------------------------------------------- walk
    walk_mode: EnumProperty(name="Mode", items=[
        ('CYCLE', "Cycle en boucle", "Un cycle dans une nouvelle action, répété à l'infini (modificateurs Cycles)"),
        ('BAKE', "Clés sur la timeline", "Plusieurs pas insérés à partir de la frame courante, avec démarrage et arrêt")],
        default='CYCLE')
    walk_cycle_frames: IntProperty(name="Images par cycle", default=40, min=8, max=400,
                                   description="Durée de deux pas (gauche + droite)")
    walk_cycles: IntProperty(name="Nombre de cycles", default=4, min=1, max=100)
    walk_stride: FloatProperty(name="Longueur de pas", default=1.1, min=0.0, max=3.0, unit='LENGTH')
    walk_step_height: FloatProperty(name="Hauteur de pas", default=0.35, min=0.0, max=1.5, unit='LENGTH')
    walk_duty: FloatProperty(name="Appui au sol", default=0.6, min=0.5, max=0.85, subtype='FACTOR',
                             description="Part du cycle pendant laquelle un pied est posé")
    walk_bob: FloatProperty(name="Rebond du corps", default=0.06, min=0.0, max=0.5, unit='LENGTH')
    walk_sway: FloatProperty(name="Balancement latéral", default=0.07, min=0.0, max=0.5, unit='LENGTH')
    walk_roll: FloatProperty(name="Roulis", default=DEG(3), min=0, max=DEG(20), subtype='ANGLE')
    walk_lean: FloatProperty(name="Inclinaison avant", default=DEG(3), min=DEG(-15), max=DEG(20), subtype='ANGLE')
    walk_hip_yaw: FloatProperty(name="Rotation du bassin", default=DEG(2), min=0, max=DEG(20), subtype='ANGLE')
    walk_torso_twist: FloatProperty(name="Contre-rotation du torse", default=DEG(4), min=0, max=DEG(30), subtype='ANGLE')
    walk_turret_sway: FloatProperty(name="Balancement des tourelles", default=DEG(2.5), min=0, max=DEG(15), subtype='ANGLE')
    walk_turret_sway_on: BoolProperty(name="Animer les tourelles", default=True,
                                      description="Ajouter un léger balancement des tourelles pendant la marche")
    walk_foot_pitch: FloatProperty(name="Bascule du pied", default=DEG(14), min=0, max=DEG(45), subtype='ANGLE')
    walk_toe_flex: FloatProperty(name="Flexion des orteils", default=DEG(20), min=0, max=DEG(45), subtype='ANGLE')
    walk_flap: FloatProperty(name="Volet avant du pied", default=DEG(32), min=0, max=DEG(55), subtype='ANGLE',
                             description="La plaque mécanique à l'avant du pied se replie en l'air et rebondit à l'appui")
    walk_crouch: FloatProperty(name="Flexion des jambes", default=0.18, min=0.0, max=1.0, unit='LENGTH',
                               description="Abaisse le corps pendant la marche pour que les pieds restent à portée de l'IK")
    walk_foot_back: FloatProperty(name="Recentrage des pas", default=0.25, min=-0.5, max=0.8, unit='LENGTH',
                                  description="Recule le centre de la foulée sous les hanches")
    walk_in_place: BoolProperty(name="Sur place", default=False, description="Ne pas faire avancer le contrôleur root")
    walk_backward: BoolProperty(name="Marche arrière", default=False)
    walk_ease: BoolProperty(name="Démarrage / arrêt progressifs", default=True)
    walk_key_step: IntProperty(name="Clé toutes les", default=2, min=1, max=10, description="Espacement des clés (images)")

    # ---------------------------------------------------------------- turrets
    turret_side: EnumProperty(name="Tourelle", items=[('L', "Gauche", ""), ('R', "Droite", ""), ('BOTH', "Les deux", "")],
                              default='BOTH')
    turret_preset: EnumProperty(name="Preset", items=TURRET_PRESETS, default='SWEEP')
    turret_duration: IntProperty(name="Durée (images)", default=96, min=4, max=2000)
    turret_amplitude: FloatProperty(name="Amplitude (°)", default=16.0, min=0.0, max=34.0)
    turret_cycles: IntProperty(name="Répétitions", default=2, min=1, max=20)
    turret_aim_yaw: FloatProperty(name="Cap cible (°)", default=12.0, min=-34.0, max=34.0,
                                  description="Positif = vers la droite du robot (limité par le rig)")
    turret_aim_pitch: FloatProperty(name="Hausse cible (°)", default=18.0, min=-55.0, max=44.0,
                                    description="Positif = vers le haut (épaule + canon, limité par le rig)")
    turret_spin_rps: FloatProperty(name="Rotation gatling (tours/s)", default=6.0, min=0.0, max=50.0)
    turret_seed: IntProperty(name="Graine aléatoire", default=7, min=0)

    # ---------------------------------------------------------------- effects
    fx_flash: BoolProperty(name="Flashs de tir", default=True, description="Flash et lumière à la bouche des gatlings")
    rocket_count: IntProperty(name="Roquettes par tourelle", default=2, min=1, max=20)
    rocket_spacing: IntProperty(name="Intervalle (images)", default=6, min=1, max=200)
    rocket_range: FloatProperty(name="Portée", default=120.0, min=10.0, max=2000.0, unit='LENGTH')
    rocket_smoke: FloatProperty(name="Densité de fumée", default=1.0, min=0.2, max=3.0)
    rocket_use_target: BoolProperty(name="Viser aim_target", default=False,
                                    description="Les roquettes se dirigent vers le contrôleur aim_target")
    rocket_interval: FloatProperty(name="Cadence (R1 maintenu)", default=0.35, min=0.1, max=3.0, unit='TIME_ABSOLUTE')

    # ---------------------------------------------------------------- pilot
    pilot_walk_speed: FloatProperty(name="Sensibilité du stick", default=1.0, min=0.1, max=2.0, subtype='FACTOR')
    loco_walk_speed: FloatProperty(name="Vitesse de marche", default=1.15, min=0.2, max=3.0, unit='VELOCITY')
    loco_sprint_speed: FloatProperty(name="Vitesse de sprint", default=2.5, min=0.5, max=5.0, unit='VELOCITY')
    pilot_turn_speed: FloatProperty(name="Vitesse de rotation (°/s)", default=55.0, min=1.0, max=180.0)
    pilot_aim_speed: FloatProperty(name="Vitesse de visée (°/s)", default=70.0, min=1.0, max=500.0)
    pilot_torso_speed: FloatProperty(name="Vitesse de la tête (°/s)", default=50.0, min=1.0, max=360.0)
    pilot_aim_mode: EnumProperty(name="Visée", items=[
        ('RATE', "Vitesse", "Le stick fait tourner la tourelle ; elle reste en place quand on le lâche"),
        ('ABSOLUTE', "Absolue", "La position du stick donne directement l'angle de la tourelle")], default='RATE')
    pilot_invert_y: BoolProperty(name="Inverser l'axe vertical", default=False)
    pilot_crouch_depth: FloatProperty(name="Accroupi max (L2)", default=0.5, min=0.0, max=1.5, unit='LENGTH')
    pilot_key_every: IntProperty(name="Clé toutes les", default=1, min=1, max=10, description="Enregistrer une pose toutes les N images")
    pilot_time_scale: FloatProperty(name="Vitesse d'enregistrement", default=1.0, min=0.1, max=4.0,
                                    description="1 = temps réel, 0.5 = enregistre au ralenti (l'animation sera plus rapide)")
    pilot_simplify: BoolProperty(name="Simplifier les clés", default=True,
                                 description="Supprimer les clés redondantes à la fin de l'enregistrement")
    pilot_simplify_tol: FloatProperty(name="Tolérance", default=0.002, min=0.0, max=0.1, precision=4)
    pilot_extend_range: BoolProperty(name="Étendre la plage de la scène", default=True)
    pilot_record_on_start: BoolProperty(name="Enregistrer dès le lancement", default=False)

    # ---------------------------------------------------------------- autopilot
    path_object: PointerProperty(name="Chemin", type=bpy.types.Object, poll=lambda self, o: o.type == 'CURVE')
    path_loop: BoolProperty(name="Boucler", default=False)
    path_laps: IntProperty(name="Tours", default=1, min=1, max=50)
    path_start_on_path: BoolProperty(name="Partir du début du chemin", default=True)
    auto_speed: FloatProperty(name="Allure", default=1.0, min=0.1, max=1.0, subtype='FACTOR')
    auto_sprint: BoolProperty(name="Sprint", default=False)
    auto_max_seconds: FloatProperty(name="Durée max (s)", default=90.0, min=1.0, max=3600.0)
    auto_simplify: BoolProperty(name="Simplifier les clés", default=True)
    ground_object: PointerProperty(name="Sol", type=bpy.types.Object, poll=lambda self, o: o.type == 'MESH',
                                   description="Surface sur laquelle le robot marche (relief)")
    obstacles: PointerProperty(name="Obstacles", type=bpy.types.Collection,
                               description="Collection d'objets à contourner")
    nav_object: PointerProperty(name="Navmesh", type=bpy.types.Object, poll=lambda self, o: o.type == 'MESH')
    nav_cell: FloatProperty(name="Taille des cellules", default=0.75, min=0.1, max=5.0, unit='LENGTH')
    nav_radius: FloatProperty(name="Rayon du robot", default=1.8, min=0.2, max=6.0, unit='LENGTH')
    nav_slope: FloatProperty(name="Pente max (°)", default=30.0, min=1.0, max=60.0)
    ai_mode: EnumProperty(name="Comportement", items=[
        ('LOOP', "Patrouille en boucle", "P01 → P02 → … → P01"),
        ('PINGPONG', "Aller-retour", "P01 → … → dernier point → … → P01"),
        ('WANDER', "Errance", "Points aléatoires sur le navmesh"),
        ('GOTO', "Aller à la cible", "Rejoindre un objet par le plus court chemin")], default='LOOP')
    ai_target: PointerProperty(name="Cible", type=bpy.types.Object)
    ai_wait: FloatProperty(name="Pause à chaque point (s)", default=3.0, min=0.0, max=60.0)
    ai_laps: IntProperty(name="Tours", default=1, min=1, max=50)
    ai_scan: BoolProperty(name="Observer (tête et tourelles)", default=True)
    ai_seed: IntProperty(name="Graine", default=1, min=0)

    # ---------------------------------------------------------------- paint
    paint_scheme: EnumProperty(name="Camouflage", items=SCHEME_ITEMS, default='WOODLAND')
    paint_strength: FloatProperty(name="Couvrance", default=0.9, min=0.0, max=1.0, subtype='FACTOR')
    paint_wear: FloatProperty(name="Usure", default=0.35, min=0.0, max=1.0, subtype='FACTOR')
    paint_scale: FloatProperty(name="Taille des taches", default=1.0, min=0.1, max=10.0)
    paint_seed: FloatProperty(name="Variation", default=0.0, min=0.0, max=100.0)


AXIS_ITEMS = [(a, a, "") for a in ("X", "Y", "Z", "R", "U", "V")]


class RobotAnimPreferences(AddonPreferences):
    bl_idname = __package__

    backend: EnumProperty(name="Pilote", items=[
        ('AUTO', "Automatique", "WinMM puis XInput puis pygame"),
        ('WINMM', "DirectInput / WinMM (DS4 natif)", "Manette PS4 branchée en USB ou en Bluetooth, sans logiciel"),
        ('XINPUT', "XInput (DS4Windows, Steam, Xbox)", "Quand la manette est émulée en manette Xbox"),
        ('PYGAME', "pygame (Linux / macOS)", "Nécessite pygame installé dans le Python de Blender"),
        ('KEYBOARD', "Clavier seulement", "")], default='AUTO')
    device_index: IntProperty(name="Index de la manette", default=-1, min=-1, max=15,
                              description="-1 = première manette trouvée (Sony en priorité)")
    deadzone: FloatProperty(name="Zone morte", default=0.12, min=0.0, max=0.5, subtype='FACTOR')
    custom_mapping: BoolProperty(name="Mappage d'axes personnalisé (WinMM)", default=False)
    axis_lx: EnumProperty(name="Stick G horizontal", items=AXIS_ITEMS, default='X')
    axis_ly: EnumProperty(name="Stick G vertical", items=AXIS_ITEMS, default='Y')
    axis_rx: EnumProperty(name="Stick D horizontal", items=AXIS_ITEMS, default='Z')
    axis_ry: EnumProperty(name="Stick D vertical", items=AXIS_ITEMS, default='R')
    axis_l2: EnumProperty(name="L2", items=AXIS_ITEMS, default='U')
    axis_r2: EnumProperty(name="R2", items=AXIS_ITEMS, default='V')
    update_auto_check: BoolProperty(
        name="Vérifier les mises à jour au démarrage", default=True,
        description="Interroge GitHub au lancement de Blender (si l'accès en ligne est autorisé)")
    update_auto_install: BoolProperty(
        name="Installer automatiquement", default=False,
        description="Télécharge, vérifie et installe la nouvelle version sans demander")
    update_interval_hours: IntProperty(
        name="Intervalle (heures)", default=24, min=0, max=24 * 30,
        description="Délai minimum entre deux vérifications automatiques (0 = à chaque démarrage)")

    def draw(self, context):
        lay = self.layout
        lay.prop(self, "backend")
        row = lay.row()
        row.prop(self, "device_index")
        row.prop(self, "deadzone")
        lay.prop(self, "custom_mapping")
        if self.custom_mapping:
            col = lay.column(align=True)
            for k in ("lx", "ly", "rx", "ry", "l2", "r2"):
                col.prop(self, "axis_" + k)
        lay.operator("robot.pad_monitor", icon='HIDE_OFF')
        from . import updater
        lay.separator()
        lay.label(text="Mises à jour", icon='FILE_REFRESH')
        col = lay.column()
        col.prop(self, "update_auto_check")
        sub = col.column()
        sub.active = self.update_auto_check
        sub.prop(self, "update_auto_install")
        sub.prop(self, "update_interval_hours")
        updater.draw_box(lay)


classes = (RobotAnimSettings, RobotAnimPreferences)
