"""Apply the add-on's military PBR paint (and the markings) to a rigged robot file.

    blender -b Robot_Rig.blend --python tools/apply_paint.py -- WOODLAND out.blend [--no-decals]
"""
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import robot_mech_animator as addon  # noqa: E402
from robot_mech_animator import rig as R  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
flags = {a for a in args if a.startswith("--")}
args = [a for a in args if not a.startswith("--")]
scheme = args[0] if args else 'WOODLAND'
out = args[1] if len(args) > 1 else None
addon.register()
rig = R.find_rig(bpy.context)
s = bpy.context.scene.robot_anim
s.rig = rig
s.paint_scheme = scheme
print("[apply_paint]", bpy.ops.robot.military_paint())
if "--no-decals" not in flags:
    print("[apply_paint] decals", bpy.ops.robot.decals())
if out:
    bpy.ops.wm.save_as_mainfile(filepath=out, compress=True)
    print("[apply_paint] saved", out)
addon.unregister()
