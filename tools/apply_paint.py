"""Apply the add-on's military paint to a rigged robot file.

    blender -b Robot_Rig.blend --python tools/apply_paint.py -- WOODLAND out.blend
"""
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import robot_mech_animator as addon  # noqa: E402
from robot_mech_animator import paint, rig as R  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
scheme = args[0] if args else 'WOODLAND'
out = args[1] if len(args) > 1 else None
rig = R.find_rig(bpy.context)
mats = paint.robot_materials(rig)
for m in mats:
    paint.apply(m, scheme)
print(f"[apply_paint] {scheme} on {[m.name for m in mats]}")
if out:
    bpy.ops.wm.save_as_mainfile(filepath=out, compress=True)
    print("[apply_paint] saved", out)
