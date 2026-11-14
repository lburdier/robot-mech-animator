"""Visual check of the rig: bones drawn as sticks, rest pose and a test pose."""
import bpy, sys, os, math
from mathutils import Vector, Matrix

out_dir = sys.argv[sys.argv.index("--") + 1]
os.makedirs(out_dir, exist_ok=True)
sc = bpy.context.scene
rig = bpy.data.objects["RIG_Robot"]
sc.render.engine = 'BLENDER_WORKBENCH'
sc.display.shading.color_type = 'RANDOM'
sc.render.resolution_x = sc.render.resolution_y = 900
sc.render.film_transparent = False

stick_mat_cols = {}


def stick(name, color):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(0, 0, 0), (0, 1, 0)], [(0, 1)], [])
    o = bpy.data.objects.new(name, me)
    sc.collection.objects.link(o)
    sk = o.modifiers.new("skin", 'SKIN')
    for v in o.data.skin_vertices[0].data:
        v.radius = (0.035, 0.035)
    o.show_in_front = True
    o.color = color
    return o


sticks = []
for pb in rig.pose.bones:
    col = (1, 0.2, 0.2, 1) if pb.name.endswith(".R") else (0.2, 0.4, 1, 1) if pb.name.endswith(".L") else (1, 0.9, 0.1, 1)
    o = stick("STK_" + pb.name, col)
    sticks.append((o, pb))


def sync_sticks():
    bpy.context.view_layer.update()
    for o, pb in sticks:
        h = rig.matrix_world @ pb.head
        t = rig.matrix_world @ pb.tail
        d = t - h
        o.matrix_world = Matrix.Translation(h) @ d.to_track_quat('Y', 'Z').to_matrix().to_4x4() @ Matrix.Scale(d.length, 4, (0, 1, 0))


cam_data = bpy.data.cameras.new("C")
cam = bpy.data.objects.new("C", cam_data)
sc.collection.objects.link(cam)
sc.camera = cam


def shoot(name, loc, target=Vector((0, 0, 2.4)), ortho=None):
    if ortho:
        cam_data.type = 'ORTHO'
        cam_data.ortho_scale = ortho
    else:
        cam_data.type = 'PERSP'
        cam_data.lens = 45
    cam.location = loc
    cam.rotation_euler = (target - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
    sc.render.filepath = os.path.join(out_dir, name + ".png")
    sync_sticks()
    bpy.ops.render.render(write_still=True)
    print("SHOT", name)


shoot("rest_persp", (-8, -10, 5))
shoot("rest_side", (12, 0.2, 2.4), ortho=7)
shoot("rest_front", (0, -12, 2.4), ortho=7)

P = rig.pose.bones
P["turret.L"].rotation_euler = (math.radians(-30), 0, math.radians(20))
P["turret.R"].rotation_euler = (math.radians(15), 0, math.radians(-20))
P["gatling.L"].rotation_euler = (0, math.radians(30), 0)
P["torso"].rotation_euler = (0, math.radians(25), 0)
P["foot_ik.L"].location = (0, 0.4, 0)          # local Y = forward
P["foot_ik.L"].location = (0, 0.5, 0.0)
P["foot_ik.R"].location = (0, -0.4, -0.35)     # local -Z = up (bone points forward)
P["foot_ik.R"].rotation_euler = (math.radians(-15), 0, 0)
P["body"].location = (0, 0, -0.25)
P["toe_front.L"].rotation_euler = (math.radians(25), 0, 0)
shoot("pose_persp", (-8, -10, 5))
shoot("pose_side", (12, 0.2, 2.4), ortho=7)
shoot("pose_front", (0, -12, 2.4), ortho=7)
for o, pb in sticks:
    o.hide_render = True
shoot("pose_persp_nobones", (-8, -10, 5))
