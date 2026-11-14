"""Cancel / undo for the robot's modes (path, AI, pilot recording).

Before a mode writes keys, a ``Snapshot`` copies the actions of the rig and of the
muzzle flashes and remembers the pose, the frame range and the existing objects.
``restore()`` puts everything back and deletes the effects (rockets, smoke) created
since. Backup actions have no user: they are never saved in the .blend file.
"""
import bpy

from . import rig as R

_LAYERED = hasattr(bpy.types, "ActionSlot")
LAST = {"snap": None}


def _targets(rig):
    out = [("OBJECT", rig.name)]
    for side in R.SIDES:
        o = bpy.data.objects.get(f"FX-flash.{side}")
        if o is not None:
            out.append(("OBJECT", o.name))
            lo = bpy.data.objects.get(o.get("light", ""))
            if lo is not None and lo.data is not None:
                out.append(("LIGHT", lo.data.name))
    return out


def _lookup(kind, name):
    return (bpy.data.objects if kind == "OBJECT" else bpy.data.lights).get(name)


def _pose_of(rig):
    data = {}
    for pb in rig.pose.bones:
        data[pb.name] = (tuple(pb.location), tuple(pb.rotation_quaternion), tuple(pb.rotation_euler),
                         tuple(pb.rotation_axis_angle), tuple(pb.scale))
    return data


class Snapshot:
    def __init__(self, rig, scene, label):
        self.label = label
        self.rig_name = rig.name
        self.scene_name = scene.name
        self.frame = scene.frame_current
        self.frame_end = scene.frame_end
        self.objects = {o.name for o in bpy.data.objects}
        self.pose = _pose_of(rig)
        self.entries = []
        for kind, name in _targets(rig):
            idb = _lookup(kind, name)
            ad = idb.animation_data
            act = ad.action if ad else None
            if act is None:
                self.entries.append((kind, name, None, "", None, False))
                continue
            cp = act.copy()
            cp.use_fake_user = False
            cp.name = act.name + ".annuler"
            slot = ad.action_slot.identifier if _LAYERED and ad.action_slot is not None else ""
            self.entries.append((kind, name, cp.name, slot, act.name, act.use_fake_user))

    # ------------------------------------------------------------------ restore
    def restore(self):
        """Put the animation, pose and frame range back. Returns False if the backup is gone."""
        ok = True
        for kind, name, backup, slot, orig_name, fake in self.entries:
            idb = _lookup(kind, name)
            if idb is None:
                continue
            ad = idb.animation_data or idb.animation_data_create()
            current = ad.action
            if backup is None:
                ad.action = None
            else:
                act = bpy.data.actions.get(backup)
                if act is None:
                    ok = False
                    continue
                ad.action = act
                if _LAYERED and slot:
                    for s in act.slots:
                        if s.identifier == slot:
                            ad.action_slot = s
                            break
                act.use_fake_user = fake
            if current is not None and current != ad.action:
                old_name = current.name
                if current.users <= (1 if current.use_fake_user else 0):
                    bpy.data.actions.remove(current)
                if backup is not None and orig_name == old_name and ad.action is not None:
                    ad.action.name = orig_name
        # effects created since the snapshot (rockets, smoke, explosions, new flashes)
        for o in [o for o in bpy.data.objects if o.name not in self.objects and o.get("robot_fx")]:
            data = o.data
            bpy.data.objects.remove(o)
            if data is not None and data.users == 0:
                try:
                    if isinstance(data, bpy.types.Light):
                        bpy.data.lights.remove(data)
                    elif isinstance(data, bpy.types.Mesh) and data.name.startswith("FX-fumee"):
                        bpy.data.meshes.remove(data)
                except (ReferenceError, RuntimeError):
                    pass
        rig = bpy.data.objects.get(self.rig_name)
        if rig is not None:
            for bname, (loc, q, e, aa, sc) in self.pose.items():
                pb = rig.pose.bones.get(bname)
                if pb is not None:
                    pb.location, pb.rotation_quaternion, pb.rotation_euler = loc, q, e
                    pb.rotation_axis_angle, pb.scale = aa, sc
        scene = bpy.data.scenes.get(self.scene_name)
        if scene is not None:
            scene.frame_end = self.frame_end
            scene.frame_set(self.frame)
        self.discard()
        return ok

    def discard(self):
        for _k, _n, backup, *_rest in self.entries:
            act = bpy.data.actions.get(backup) if backup else None
            if act is not None and act.users == 0:
                bpy.data.actions.remove(act)
        self.entries = []


# ---------------------------------------------------------------------- one-level undo
def begin(rig, scene, label):
    return Snapshot(rig, scene, label)


def commit(snap):
    """The mode finished: its snapshot becomes the « last action » that can be undone."""
    old = LAST["snap"]
    if old is not None and old is not snap:
        old.discard()
    LAST["snap"] = snap


def cancel(snap):
    if LAST["snap"] is snap:
        LAST["snap"] = None
    return snap.restore()


def last_label():
    s = LAST["snap"]
    return s.label if s is not None else ""


def undo_last():
    snap = LAST["snap"]
    LAST["snap"] = None
    if snap is None:
        return None, False
    return snap.label, snap.restore()


def forget():
    if LAST["snap"] is not None:
        LAST["snap"].discard()
    LAST["snap"] = None


class ROBOT_OT_undo_last(bpy.types.Operator):
    """Annuler la dernière action du robot (chemin, IA ou prise au pilotage) et remettre l'animation d'avant"""
    bl_idname = "robot.undo_last"
    bl_label = "Annuler la dernière action"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return LAST["snap"] is not None

    def execute(self, context):
        label, ok = undo_last()
        if label is None:
            return {'CANCELLED'}
        if ok:
            self.report({'INFO'}, f"« {label} » annulé : animation précédente restaurée")
        else:
            self.report({'WARNING'}, f"« {label} » annulé, mais la sauvegarde avait été purgée")
        return {'FINISHED'}


classes = (ROBOT_OT_undo_last,)
