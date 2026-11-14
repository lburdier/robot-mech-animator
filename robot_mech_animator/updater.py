# SPDX-License-Identifier: GPL-3.0-or-later
"""Mise à jour automatique depuis les releases GitHub.

* La vérification se fait dans un thread (aucun gel de l'interface), au démarrage
  de Blender au plus une fois par intervalle choisi, ou à la demande.
* L'accès réseau respecte « Autoriser l'accès en ligne » de Blender
  (``bpy.app.online_access``) : sans lui, rien n'est téléchargé.
* Le zip téléchargé est vérifié (taille, empreinte SHA-256 publiée par GitHub,
  identifiant et version du manifeste) puis réinstallé dans le même dépôt
  d'extensions que la version en cours, qui est remplacée et réactivée.
"""
import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.request
import zipfile

import bpy
from bpy.props import BoolProperty
from bpy.types import Operator

try:
    import tomllib
except ImportError:  # pragma: no cover - Blender 5 embarque Python 3.11+
    tomllib = None

REPO = "lburdier/robot-mech-animator"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
EXT_ID = "robot_mech_animator"
USER_AGENT = "robot-mech-animator-updater"
TIMEOUT = 15.0

ADDON = __package__  # « bl_ext.user_default.robot_mech_animator »

# État partagé (le thread n'écrit que ici ; l'interface ne fait que lire).
STATE = {
    "busy": False,       # vérification ou téléchargement en cours
    "checked": False,    # au moins une vérification terminée
    "available": None,   # dict de la release plus récente, sinon None
    "latest": "",        # version la plus récente vue sur GitHub
    "error": "",
    "message": "",
    "installed": "",     # version installée pendant cette session
}
_thread = None


# --------------------------------------------------------------------------- versions
def manifest_of(text):
    if tomllib is not None:
        return tomllib.loads(text)
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith(("#", "[")):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def current_version():
    path = os.path.join(os.path.dirname(__file__), "blender_manifest.toml")
    with open(path, encoding="utf-8") as fh:
        return manifest_of(fh.read()).get("version", "0.0.0")


def parse_version(v):
    v = str(v).strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for p in v.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(remote, local):
    return parse_version(remote) > parse_version(local)


# --------------------------------------------------------------------------- persistance
def _state_file():
    try:
        d = bpy.utils.extension_path_user(ADDON, create=True)
    except Exception:
        d = os.path.join(tempfile.gettempdir(), EXT_ID)
        os.makedirs(d, exist_ok=True)
    return os.path.join(d, "update_state.json")


def load_saved():
    try:
        with open(_state_file(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_saved(**kw):
    data = load_saved()
    data.update(kw)
    try:
        with open(_state_file(), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass


# --------------------------------------------------------------------------- préférences
def prefs():
    addon = bpy.context.preferences.addons.get(ADDON)
    return addon.preferences if addon else None


def online_allowed():
    return bool(getattr(bpy.app, "online_access", True))


def api_url():
    # Surcharge pour les tests (URL file:// vers un faux JSON de release).
    return os.environ.get("ROBOT_MECH_UPDATE_URL") or API_URL


def install_repo():
    """Dépôt d'extensions qui contient l'addon, et s'il est géré par Blender en ligne."""
    parts = ADDON.split(".")
    if len(parts) != 3 or parts[0] != "bl_ext":
        return None, False
    for repo in bpy.context.preferences.extensions.repos:
        if repo.module == parts[1]:
            return repo, bool(repo.use_remote_url)
    return None, False


# --------------------------------------------------------------------------- réseau
def _get(url, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def fetch_latest(url=None):
    """Lit la dernière release ; renvoie un dict (version, zip, taille, empreinte, notes)."""
    data = json.loads(_get(url or api_url()).decode("utf-8"))
    tag = data.get("tag_name") or ""
    asset = None
    for a in data.get("assets", []):
        name = a.get("name", "")
        if name.startswith(EXT_ID) and name.endswith(".zip"):
            asset = a
            break
    if asset is None:
        raise RuntimeError(f"La release {tag} ne contient pas de zip {EXT_ID}")
    digest = asset.get("digest") or ""
    return {
        "version": tag.lstrip("vV"),
        "tag": tag,
        "name": data.get("name") or tag,
        "notes": data.get("body") or "",
        "page": data.get("html_url") or RELEASES_URL,
        "zip_url": asset["browser_download_url"],
        "zip_name": asset["name"],
        "size": int(asset.get("size") or 0),
        "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else "",
    }


def download(info, dest_dir=None):
    """Télécharge et vérifie le zip ; renvoie son chemin."""
    raw = _get(info["zip_url"], accept="application/octet-stream")
    if info.get("size") and len(raw) != info["size"]:
        raise RuntimeError(f"Taille inattendue ({len(raw)} au lieu de {info['size']} octets)")
    if info.get("sha256") and hashlib.sha256(raw).hexdigest() != info["sha256"].lower():
        raise RuntimeError("Empreinte SHA-256 différente de celle publiée : fichier rejeté")
    dest_dir = dest_dir or tempfile.mkdtemp(prefix="robot_mech_update_")
    path = os.path.join(dest_dir, os.path.basename(info["zip_name"]))
    with open(path, "wb") as fh:
        fh.write(raw)
    verify_zip(path, info["version"])
    return path


def verify_zip(path, version):
    with zipfile.ZipFile(path) as zf:
        if zf.testzip() is not None:
            raise RuntimeError("Archive corrompue")
        names = zf.namelist()
        if "blender_manifest.toml" not in names or "__init__.py" not in names:
            raise RuntimeError("Ce zip n'est pas une extension Blender valide")
        man = manifest_of(zf.read("blender_manifest.toml").decode("utf-8"))
    if man.get("id") != EXT_ID:
        raise RuntimeError(f"Identifiant inattendu : {man.get('id')}")
    if parse_version(man.get("version", "0")) != parse_version(version):
        raise RuntimeError(f"Version du zip {man.get('version')} ≠ release {version}")


# --------------------------------------------------------------------------- thread
def _worker(then_install):
    try:
        info = fetch_latest()
        STATE["latest"] = info["version"]
        local = current_version()
        if is_newer(info["version"], local):
            STATE["available"] = info
            STATE["message"] = f"Version {info['version']} disponible (installée : {local})"
            if then_install:
                info["path"] = download(info)
        else:
            STATE["available"] = None
            STATE["message"] = f"À jour (version {local})"
        STATE["error"] = ""
    except Exception as exc:  # réseau coupé, quota GitHub, zip invalide…
        STATE["error"] = f"{type(exc).__name__}: {exc}"
        STATE["message"] = ""
    finally:
        STATE["checked"] = True
        STATE["busy"] = False


def start_check(then_install=False):
    """Lance une vérification en arrière-plan. Renvoie False si impossible."""
    global _thread
    if STATE["busy"]:
        return False
    if not online_allowed():
        STATE["error"] = "Accès en ligne désactivé dans Blender (Préférences › Système › Réseau)"
        return False
    STATE.update(busy=True, error="", message="Recherche d'une mise à jour…")
    save_saved(last_check=time.time())
    _thread = threading.Thread(target=_worker, args=(then_install,), daemon=True)
    _thread.start()
    bpy.app.timers.register(_poll, first_interval=0.5)
    return True


def _redraw():
    wm = getattr(bpy.context, "window_manager", None)
    for win in (wm.windows if wm else ()):
        for area in win.screen.areas:
            if area.type in {'VIEW_3D', 'PREFERENCES'}:
                area.tag_redraw()


def _poll():
    if STATE["busy"]:
        return 0.5
    _redraw()
    info = STATE["available"]
    if info and info.get("path") and info["version"] not in load_saved().get("skipped", []):
        bpy.app.timers.register(lambda: _install_now(info["path"], info["version"]) and None,
                                first_interval=0.1)
    return None


# --------------------------------------------------------------------------- installation
def _window_override():
    wm = bpy.context.window_manager
    if wm and wm.windows:
        win = wm.windows[0]
        return {"window": win, "screen": win.screen}
    return {}


def install_zip(path):
    """Installe le zip dans le dépôt de l'addon (remplace et réactive la version en cours)."""
    repo, remote = install_repo()
    if remote:
        raise RuntimeError("Addon installé depuis un dépôt en ligne : "
                           "mettez-le à jour dans Préférences › Extensions")
    kw = dict(filepath=path, repo=repo.module if repo else "user_default", enable_on_install=True)
    op = bpy.ops.extensions.package_install_files
    if "overwrite" in op.get_rna_type().properties.keys():
        kw["overwrite"] = True
    if bpy.context.window is None and not bpy.app.background:
        with bpy.context.temp_override(**_window_override()):
            return op(**kw)
    return op(**kw)


def _install_now(path, version):
    try:
        res = install_zip(path)
        if 'FINISHED' in res:
            STATE.update(installed=version, available=None,
                         message=f"Mise à jour {version} installée")
            save_saved(installed=version)
        else:
            STATE["error"] = f"Installation annulée ({res})"
    except Exception as exc:
        STATE["error"] = f"Installation impossible : {exc}"
    _redraw()
    return True


def _startup_check():
    p = prefs()
    if p is None or not p.update_auto_check or bpy.app.background or not online_allowed():
        return None
    last = float(load_saved().get("last_check", 0.0))
    if time.time() - last >= p.update_interval_hours * 3600.0:
        start_check(then_install=p.update_auto_install)
    return None


# --------------------------------------------------------------------------- opérateurs
class ROBOT_OT_update_check(Operator):
    bl_idname = "robot.update_check"
    bl_label = "Vérifier les mises à jour"
    bl_description = "Cherche une nouvelle version de l'addon sur GitHub"

    def execute(self, context):
        if not start_check(then_install=False):
            self.report({'WARNING'}, STATE["error"] or "Vérification déjà en cours")
            return {'CANCELLED'}
        return {'FINISHED'}


class ROBOT_OT_update_install(Operator):
    bl_idname = "robot.update_install"
    bl_label = "Installer la mise à jour"
    bl_description = "Télécharge la nouvelle version, la vérifie et remplace l'addon installé"

    @classmethod
    def poll(cls, context):
        return STATE["available"] is not None and not STATE["busy"]

    def execute(self, context):
        info = STATE["available"]
        if not online_allowed():
            self.report({'ERROR'}, "Accès en ligne désactivé dans Blender")
            return {'CANCELLED'}
        try:
            path = info.get("path") or download(info)
        except Exception as exc:
            self.report({'ERROR'}, f"Téléchargement impossible : {exc}")
            return {'CANCELLED'}
        version = info["version"]
        # L'installation recharge ce module : on la lance après la fin de l'opérateur.
        bpy.app.timers.register(lambda: _install_now(path, version) and None, first_interval=0.05)
        self.report({'INFO'}, f"Installation de la version {version}…")
        return {'FINISHED'}


class ROBOT_OT_update_skip(Operator):
    bl_idname = "robot.update_skip"
    bl_label = "Ignorer cette version"
    bl_description = "Ne plus proposer cette version (une version plus récente sera proposée)"

    def execute(self, context):
        info = STATE["available"]
        if info:
            skipped = load_saved().get("skipped", [])
            save_saved(skipped=sorted(set(skipped) | {info["version"]}))
            STATE["available"] = None
            STATE["message"] = f"Version {info['version']} ignorée"
        return {'FINISHED'}


class ROBOT_OT_update_notes(Operator):
    bl_idname = "robot.update_notes"
    bl_label = "Notes de version"
    bl_description = "Ouvre la page de la release sur GitHub"
    page: bpy.props.StringProperty(default=RELEASES_URL, options={'SKIP_SAVE'})

    def execute(self, context):
        info = STATE["available"]
        bpy.ops.wm.url_open(url=(info or {}).get("page") or self.page)
        return {'FINISHED'}


def visible_update():
    """Release à afficher (None si aucune, ou si la version a été ignorée)."""
    info = STATE["available"]
    if info and info["version"] in load_saved().get("skipped", []):
        return None
    return info


def draw_box(layout, compact=False):
    """Bloc d'interface partagé par le panneau Robot et les préférences."""
    info = visible_update()
    if compact and not (info or STATE["busy"] or STATE["installed"]):
        return
    box = layout.box()
    if STATE["installed"]:
        box.label(text=f"Version {STATE['installed']} installée", icon='CHECKMARK')
        return
    if STATE["busy"]:
        box.label(text=STATE["message"] or "Recherche…", icon='SORTTIME')
        return
    if info:
        box.label(text=f"Mise à jour {info['version']} disponible", icon='IMPORT')
        row = box.row(align=True)
        row.operator("robot.update_install", icon='IMPORT', text="Installer")
        row.operator("robot.update_notes", icon='URL', text="Notes")
        row.operator("robot.update_skip", icon='X', text="Ignorer")
        return
    if compact:
        return
    row = box.row()
    row.label(text=f"Version installée : {current_version()}", icon='PACKAGE')
    row.operator("robot.update_check", icon='FILE_REFRESH', text="Vérifier")
    if STATE["error"]:
        box.label(text=STATE["error"][:110], icon='ERROR')
    elif STATE["message"]:
        box.label(text=STATE["message"], icon='INFO')
    if not online_allowed():
        box.label(text="Accès en ligne désactivé (Préférences › Système › Réseau)", icon='UNLINKED')
    repo, remote = install_repo()
    if remote:
        box.label(text="Installé depuis un dépôt en ligne : Blender gère les mises à jour", icon='INFO')


classes = (ROBOT_OT_update_check, ROBOT_OT_update_install, ROBOT_OT_update_skip, ROBOT_OT_update_notes)


def register():
    bpy.app.timers.register(_startup_check, first_interval=4.0, persistent=True)


def unregister():
    for fn in (_startup_check, _poll):
        if bpy.app.timers.is_registered(fn):
            try:
                bpy.app.timers.unregister(fn)
            except ValueError:
                pass
