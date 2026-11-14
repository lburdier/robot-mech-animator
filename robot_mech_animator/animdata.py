"""F-Curve helpers compatible with Blender 4.2 -> 5.x (legacy and slotted/layered actions)."""
import bpy

try:  # Blender 4.4+
    from bpy_extras import anim_utils as _anim_utils
except ImportError:  # pragma: no cover
    _anim_utils = None

_LAYERED = hasattr(bpy.types, "ActionSlot")


def ensure_action(obj, name=None, new=False):
    """Return the action assigned to ``obj`` (created if needed)."""
    ad = obj.animation_data or obj.animation_data_create()
    if ad.action is None or new:
        if ad.action is not None:
            ad.action.use_fake_user = True   # never lose the previous animation
        ad.action = bpy.data.actions.new(name or f"{obj.name}_Action")
        ad.action.use_fake_user = True
    act = ad.action
    if _LAYERED and ad.action_slot is None:
        slot = None
        if _anim_utils and hasattr(_anim_utils, "action_get_first_suitable_slot"):
            slot = _anim_utils.action_get_first_suitable_slot(act, 'OBJECT')
        if slot is None:
            slot = act.slots.new('OBJECT', obj.name)
        ad.action_slot = slot
    return act


def _fcurve_collection(obj, create):
    ad = obj.animation_data
    if ad is None or ad.action is None:
        return None
    act = ad.action
    if _LAYERED:
        if ad.action_slot is None:
            return None
        if create:
            cb = _anim_utils.action_ensure_channelbag_for_slot(act, ad.action_slot)
        else:
            cb = _anim_utils.action_get_channelbag_for_slot(act, ad.action_slot)
        return cb.fcurves if cb else None
    return act.fcurves


def all_fcurves(obj):
    col = _fcurve_collection(obj, False)
    return list(col) if col is not None else []


class FCurveCache:
    """Fast access/creation of F-Curves of one object's current action."""

    def __init__(self, obj):
        self.obj = obj
        self.cache = {}
        ensure_action(obj)
        self.col = _fcurve_collection(obj, True)
        for fc in self.col:
            self.cache[(fc.data_path, fc.array_index)] = fc
        self.touched = set()

    def get(self, data_path, index, group=""):
        key = (data_path, index)
        fc = self.cache.get(key)
        if fc is None:
            if _LAYERED:
                fc = self.col.new(data_path, index=index, group_name=group)
            else:
                fc = self.col.new(data_path, index=index, action_group=group)
            self.cache[key] = fc
        return fc

    def key(self, data_path, index, frame, value, group=""):
        fc = self.get(data_path, index, group)
        # NB: 'REPLACE' means "only replace existing keys" -> never use it here.
        # A plain insert already overwrites a key sitting on the same frame.
        fc.keyframe_points.insert(frame, value, options={'FAST'})
        self.touched.add(fc)
        return fc

    def finish(self, interpolation=None):
        for fc in self.touched:
            if interpolation:
                for kp in fc.keyframe_points:
                    kp.interpolation = interpolation
            fc.update()


def key_bones(cache, rig, frame, channels):
    """Insert keys for ``{bone: ((data_path, indices), ...)}`` using current pose values."""
    pbs = rig.pose.bones
    for bname, specs in channels.items():
        pb = pbs.get(bname)
        if pb is None:
            continue
        for prop, idxs in specs:
            vals = getattr(pb, prop)
            path = f'pose.bones["{bname}"].{prop}'
            for i in idxs:
                cache.key(path, i, frame, vals[i], bname)


def bone_fcurves(obj, bone_names):
    prefixes = tuple(f'pose.bones["{b}"].' for b in bone_names)
    return [fc for fc in all_fcurves(obj) if fc.data_path.startswith(prefixes)]


def clear_range(obj, bone_names, f0, f1):
    """Delete keys of the given bones inside [f0, f1]."""
    for fc in bone_fcurves(obj, bone_names):
        pts = [kp for kp in fc.keyframe_points if f0 - 1e-4 <= kp.co.x <= f1 + 1e-4]
        for kp in reversed(pts):
            fc.keyframe_points.remove(kp, fast=True)
        fc.update()


def add_cycles(fc, offset=False):
    for m in list(fc.modifiers):
        if m.type == 'CYCLES':
            fc.modifiers.remove(m)
    m = fc.modifiers.new('CYCLES')
    if offset:
        m.mode_before = m.mode_after = 'REPEAT_OFFSET'
    return m


# --------------------------------------------------------------------------- simplify

def _rdp(pts, tol):
    """Ramer-Douglas-Peucker on [(x, y), ...]; returns indices to keep."""
    n = len(pts)
    if n < 3:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        (xa, ya), (xb, yb) = pts[a], pts[b]
        dmax, idx = -1.0, -1
        span = xb - xa or 1e-9
        for i in range(a + 1, b):
            x, y = pts[i]
            d = abs(y - (ya + (yb - ya) * (x - xa) / span))
            if d > dmax:
                dmax, idx = d, i
        if dmax > tol and idx > 0:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [i for i, k in enumerate(keep) if k]


def simplify_fcurve(fc, tol):
    kps = fc.keyframe_points
    n = len(kps)
    if n < 3:
        return 0
    pts = [(kp.co.x, kp.co.y) for kp in kps]
    keep = _rdp(pts, tol)
    if len(keep) == n:
        return 0
    kept = [pts[i] for i in keep]
    for kp in reversed(list(kps)):
        kps.remove(kp, fast=True)
    kps.add(len(kept))
    flat = [c for p in kept for c in p]
    kps.foreach_set("co", flat)
    # RDP guarantees the error bound for *linear* interpolation between the kept keys
    for kp in kps:
        kp.interpolation = 'LINEAR'
    fc.update()
    return n - len(kept)


def simplify(fcurves, tol_loc=0.002, tol_rot=0.0035):
    removed = 0
    for fc in fcurves:
        tol = tol_rot if "rotation" in fc.data_path else tol_loc
        removed += simplify_fcurve(fc, tol)
    return removed
