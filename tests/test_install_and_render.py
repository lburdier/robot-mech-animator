"""Install the built extension zip in a throw-away user profile, enable it, render the walk."""
import bpy, sys, os, glob, addon_utils
zip_path = glob.glob(os.path.join(os.getcwd(), "dist", "*.zip"))[0]
out = os.path.join(os.getcwd(), "tools", "renders", "walk")
os.makedirs(out, exist_ok=True)
bpy.ops.extensions.package_install_files(filepath=zip_path, repo="user_default", enable_on_install=True)
mods = [m.__name__ for m in addon_utils.modules() if "robot_mech" in m.__name__]
enabled = [a for a in bpy.context.preferences.addons.keys() if "robot_mech" in a]
print("INSTALLED", mods, "ENABLED", enabled)
prefs = bpy.context.preferences.addons[enabled[0]].preferences
print("PREFS backend", prefs.backend, "deadzone", prefs.deadzone)
sc = bpy.context.scene
print("SCENE PROPS", hasattr(sc, "robot_anim"))
sc.frame_set(1)
print("WALK", bpy.ops.robot.walk_preset())
sc.robot_anim.turret_preset = 'SWEEP'
print("TURRET", bpy.ops.robot.turret_preset(kind='SETTINGS'))
sc.frame_set(120)
print("ROCKETS", bpy.ops.robot.turret_preset(kind='ROCKETS'))
print("PAINT", bpy.ops.robot.military_paint())
sc.frame_set(300)
print("PATH", bpy.ops.robot.path_create(shape='S'), bpy.ops.robot.path_follow())
sc.frame_set(1)
sc.render.engine = 'BLENDER_WORKBENCH'
sc.display.shading.color_type = 'MATERIAL'
sc.display.shading.light = 'STUDIO'
sc.render.resolution_x, sc.render.resolution_y = 520, 520
bpy.ops.mesh.primitive_plane_add(size=40, location=(0, -6, 0))
cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam")); sc.collection.objects.link(cam); sc.camera = cam
from mathutils import Vector
rig = bpy.data.objects["RIG_Robot"]
for f in (1, 6, 11, 16, 21, 26, 31, 36):
    sc.frame_set(f)
    root = rig.matrix_world @ rig.pose.bones["root"].head
    tgt = root + Vector((0, 0, 2.3))
    cam.location = root + Vector((9.5, -6.5, 3.2))
    cam.rotation_euler = (tgt - cam.location).to_track_quat('-Z', 'Y').to_euler()
    sc.render.filepath = os.path.join(out, f"walk_{f:03d}.png")
    bpy.ops.render.render(write_still=True)
print("RENDERED")
