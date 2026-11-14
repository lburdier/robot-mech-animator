# SPDX-License-Identifier: GPL-3.0-or-later
"""Robot Mech Animator – presets de marche, presets de tourelles et pilotage à la manette PS4."""

bl_info = {
    "name": "Robot Mech Animator",
    "author": "lburdier",
    "version": (1, 3, 0),
    "blender": (5, 0, 0),
    "location": "Vue 3D > Barre latérale (N) > Robot",
    "description": "Anime le robot mech : marche à pieds plantés, pilotage manette PS4 / clavier, chemin et IA navmesh, tirs et roquettes, peinture militaire",
    "category": "Animation",
}

import bpy

from . import autopilot, decals, history, paint, pilot, presets, props, ui, updater

_modules = (presets, paint, decals, history, props, pilot, autopilot, updater, ui)


@bpy.app.handlers.persistent
def _on_load_pre(*_args):
    """A new file is opened: forget the undo snapshot of the previous one."""
    history.forget()
    autopilot.BAKE.update(running=False, cancel=False, label="", progress=0.0)


def register():
    for m in _modules:
        for c in m.classes:
            bpy.utils.register_class(c)
    bpy.types.Scene.robot_anim = bpy.props.PointerProperty(type=props.RobotAnimSettings)
    updater.register()
    bpy.app.handlers.load_pre.append(_on_load_pre)


def unregister():
    if pilot.STATUS.get("running"):
        pilot.STATUS["running"] = False
    pilot.STATUS["monitor"] = False
    updater.unregister()
    if _on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load_pre)
    history.forget()
    if hasattr(bpy.types.Scene, "robot_anim"):
        del bpy.types.Scene.robot_anim
    for m in reversed(_modules):
        for c in reversed(m.classes):
            try:
                bpy.utils.unregister_class(c)
            except RuntimeError:  # déjà retirée (deux copies de l'addon actives)
                pass
