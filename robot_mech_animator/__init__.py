# SPDX-License-Identifier: GPL-3.0-or-later
"""Robot Mech Animator – presets de marche, presets de tourelles et pilotage à la manette PS4."""

bl_info = {
    "name": "Robot Mech Animator",
    "author": "lburdier",
    "version": (1, 0, 0),
    "blender": (5, 0, 0),
    "location": "Vue 3D > Barre latérale (N) > Robot",
    "description": "Anime le robot mech : cycle de marche, presets de tourelles, pilotage manette PS4 avec enregistrement de clés",
    "category": "Animation",
}

import bpy

from . import pilot, presets, props, ui

_modules = (presets, props, pilot, ui)


def register():
    for m in _modules:
        for c in m.classes:
            bpy.utils.register_class(c)
    bpy.types.Scene.robot_anim = bpy.props.PointerProperty(type=props.RobotAnimSettings)


def unregister():
    if pilot.STATUS.get("running"):
        pilot.STATUS["running"] = False
    pilot.STATUS["monitor"] = False
    del bpy.types.Scene.robot_anim
    for m in reversed(_modules):
        for c in reversed(m.classes):
            bpy.utils.unregister_class(c)
