"""End-to-end test of the self-updater in a throw-away user profile.

Run:  BLENDER_USER_RESOURCES=<tmp>/profile blender -b --factory-startup --online-mode \
          --python tests/test_updater.py -- <installed.zip> <newer.zip>
"""
import hashlib
import importlib
import json
import os
import sys
import tempfile
import zipfile

import addon_utils
import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
OLD_ZIP, NEW_ZIP = argv[0], argv[1]
FAILS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


def module():
    name = next(a for a in bpy.context.preferences.addons.keys() if a.endswith("robot_mech_animator"))
    return name, importlib.import_module(name + ".updater")


def file_url(p):
    return "file:///" + os.path.abspath(p).replace("\\", "/")


def fake_release(zip_path, version, digest=None, size=None):
    raw = open(zip_path, "rb").read()
    rel = {
        "tag_name": "v" + version, "name": "v" + version, "body": "notes",
        "html_url": "https://github.com/lburdier/robot-mech-animator/releases/tag/v" + version,
        "assets": [{"name": os.path.basename(zip_path), "browser_download_url": file_url(zip_path),
                    "size": len(raw) if size is None else size,
                    "digest": "sha256:" + (digest or hashlib.sha256(raw).hexdigest())}],
    }
    p = os.path.join(tempfile.mkdtemp(), "release.json")
    json.dump(rel, open(p, "w"))
    return file_url(p)


bpy.ops.extensions.package_install_files(filepath=OLD_ZIP, repo="user_default", enable_on_install=True)
name, up = module()
check("old version installed", up.current_version() == "1.2.0", up.current_version())
p = bpy.context.preferences.addons[name].preferences
check("prefs", p.update_auto_check and not p.update_auto_install and p.update_interval_hours == 24)
check("operators", all(hasattr(bpy.ops.robot, o) for o in
                       ("update_check", "update_install", "update_skip", "update_notes")))
check("repo detected", up.install_repo()[0] is not None and not up.install_repo()[1])
check("version compare", up.is_newer("1.10.0", "1.9.9") and not up.is_newer("v1.2.0", "1.2.0")
      and up.is_newer("2.0", "1.99.99"))

# --- real GitHub API (read-only)
if bpy.app.online_access:
    try:
        live = up.fetch_latest(up.API_URL)
        check("github latest readable", bool(live["version"]) and live["zip_url"].endswith(".zip"),
              f"{live['version']} {live['zip_name']} sha={bool(live['sha256'])}")
    except Exception as exc:
        check("github latest readable", False, repr(exc))

# --- tampered downloads are rejected
info = up.fetch_latest(fake_release(NEW_ZIP, "1.2.1", digest="0" * 64))
try:
    up.download(info)
    check("bad sha rejected", False)
except RuntimeError as exc:
    check("bad sha rejected", "SHA-256" in str(exc))
info = up.fetch_latest(fake_release(NEW_ZIP, "1.3.0"))
try:
    up.download(info)
    check("version mismatch rejected", False)
except RuntimeError as exc:
    check("version mismatch rejected", "Version" in str(exc), str(exc))
bad = os.path.join(tempfile.mkdtemp(), "robot_mech_animator-9.zip")
with zipfile.ZipFile(bad, "w") as zf:
    zf.writestr("blender_manifest.toml", 'id = "evil"\nversion = "9.0.0"\n')
    zf.writestr("__init__.py", "")
try:
    up.verify_zip(bad, "9.0.0")
    check("foreign id rejected", False)
except RuntimeError as exc:
    check("foreign id rejected", "Identifiant" in str(exc))

# --- no update when versions equal
os.environ["ROBOT_MECH_UPDATE_URL"] = fake_release(OLD_ZIP, "1.2.0")
up._worker(False)
check("up to date", up.STATE["available"] is None and not up.STATE["error"], up.STATE["message"])

# --- skip
os.environ["ROBOT_MECH_UPDATE_URL"] = fake_release(NEW_ZIP, "1.2.1")
up._worker(False)
check("update found", up.STATE["available"] and up.STATE["available"]["version"] == "1.2.1", up.STATE["error"])
bpy.ops.robot.update_skip()
check("skip hides it", up.visible_update() is None and "1.2.1" in up.load_saved()["skipped"])
up.save_saved(skipped=[])

# --- full update (auto-install path: download in the worker, install on the main thread)
up._worker(True)
info = up.STATE["available"]
check("downloaded + verified", info and os.path.isfile(info.get("path", "")), up.STATE["error"])
up._install_now(info["path"], info["version"])
check("install reported", up.STATE["installed"] == "1.2.1", up.STATE["error"])

name2, up2 = module()
check("same repo", name2 == name, name2)
check("enabled after update", name2 in bpy.context.preferences.addons.keys())
check("new code loaded", up2.current_version() == "1.2.1", up2.current_version())
mods = [m for m in addon_utils.modules() if m.__name__ == name2]
check("addon_utils sees 1.2.1", mods and tuple(mods[0].bl_info.get("version", ())) >= (1, 2, 0))
check("operators still registered", hasattr(bpy.ops.robot, "update_check") and hasattr(bpy.ops.robot, "walk_preset"))
check("scene props", hasattr(bpy.context.scene, "robot_anim"))
check("state persisted", up2.load_saved().get("installed") == "1.2.1")
print("RESULT", "OK" if not FAILS else f"{len(FAILS)} FAIL: {FAILS}")
