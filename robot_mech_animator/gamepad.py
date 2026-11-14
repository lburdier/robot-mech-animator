"""Gamepad input (PS4 DualShock 4 first) without any third-party dependency on Windows.

Backends
  * WINMM  – Windows multimedia joystick API (DirectInput HID). Reads a DualShock 4
             plugged in USB or paired in Bluetooth with no extra driver.
  * XINPUT – Xbox API. Use it with DS4Windows / Steam Input (DS4 seen as an Xbox pad).
  * PYGAME – optional, cross-platform (Linux / macOS) if pygame is installed in
             Blender's Python.
Every backend returns a normalised :class:`PadState`.
"""
import ctypes
import sys
import time
from dataclasses import dataclass, field

BUTTONS = ("cross", "circle", "square", "triangle", "l1", "r1", "l2", "r2", "share", "options",
           "l3", "r3", "ps", "touchpad", "up", "down", "left", "right")

SONY_VID = 0x054C
SONY_PIDS = {0x05C4: "DualShock 4 (v1)", 0x09CC: "DualShock 4 (v2)", 0x0BA0: "DualShock 4 (adaptateur sans fil)",
             0x0CE6: "DualSense", 0x0DF2: "DualSense Edge"}


@dataclass
class PadState:
    lx: float = 0.0     # left stick, right = +1
    ly: float = 0.0     # left stick, up = +1
    rx: float = 0.0
    ry: float = 0.0
    l2: float = 0.0     # 0..1
    r2: float = 0.0
    buttons: frozenset = field(default_factory=frozenset)
    connected: bool = False
    name: str = ""
    raw: str = ""
    # extra axes only produced by the keyboard
    hx: float = 0.0     # head (torso) yaw, right = +1
    hy: float = 0.0     # head pitch, up = +1
    sx: float = 0.0     # strafe, right = +1
    sprint: bool = False

    def merged(self, other):
        """Combine two states (gamepad + keyboard)."""
        def pick(a, b):
            return a if abs(a) >= abs(b) else b
        return PadState(pick(self.lx, other.lx), pick(self.ly, other.ly), pick(self.rx, other.rx),
                        pick(self.ry, other.ry), max(self.l2, other.l2), max(self.r2, other.r2),
                        self.buttons | other.buttons, self.connected or other.connected,
                        self.name or other.name, self.raw or other.raw,
                        pick(self.hx, other.hx), pick(self.hy, other.hy), pick(self.sx, other.sx),
                        self.sprint or other.sprint)


def deadzone(v, dz):
    if abs(v) < dz:
        return 0.0
    s = 1.0 if v > 0 else -1.0
    return s * min(1.0, (abs(v) - dz) / (1.0 - dz))


def apply_deadzone(st, dz):
    st.lx, st.ly, st.rx, st.ry = (deadzone(v, dz) for v in (st.lx, st.ly, st.rx, st.ry))
    st.l2 = 0.0 if st.l2 < 0.04 else st.l2
    st.r2 = 0.0 if st.r2 < 0.04 else st.r2
    return st


# =========================================================================== WinMM

WINMM_AXES = ("X", "Y", "Z", "R", "U", "V")
# DualShock 4 seen through DirectInput: LX=X LY=Y RX=Z RY=Rz(R) L2=Rx(U) R2=Ry(V)
DS4_WINMM_AXES = {"lx": "X", "ly": "Y", "rx": "Z", "ry": "R", "l2": "U", "r2": "V"}
DS4_WINMM_BUTTONS = {"square": 0, "cross": 1, "circle": 2, "triangle": 3, "l1": 4, "r1": 5, "l2": 6, "r2": 7,
                     "share": 8, "options": 9, "l3": 10, "r3": 11, "ps": 12, "touchpad": 13}

if sys.platform == "win32":
    from ctypes import wintypes

    class JOYINFOEX(ctypes.Structure):
        _fields_ = [(n, wintypes.DWORD) for n in (
            "dwSize", "dwFlags", "dwXpos", "dwYpos", "dwZpos", "dwRpos", "dwUpos", "dwVpos",
            "dwButtons", "dwButtonNumber", "dwPOV", "dwReserved1", "dwReserved2")]

    class JOYCAPSW(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("szPname", wintypes.WCHAR * 32)] + \
                   [(n, wintypes.UINT) for n in (
                       "wXmin", "wXmax", "wYmin", "wYmax", "wZmin", "wZmax", "wNumButtons", "wPeriodMin",
                       "wPeriodMax", "wRmin", "wRmax", "wUmin", "wUmax", "wVmin", "wVmax", "wCaps",
                       "wMaxAxes", "wNumAxes", "wMaxButtons")] + \
                   [("szRegKey", wintypes.WCHAR * 32), ("szOEMVxD", wintypes.WCHAR * 260)]

    class XINPUT_GAMEPAD(ctypes.Structure):
        _fields_ = [("wButtons", wintypes.WORD), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                    ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                    ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]

    class XINPUT_STATE(ctypes.Structure):
        _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", XINPUT_GAMEPAD)]

JOY_RETURNALL = 0x000000FF
JOYERR_NOERROR = 0


class WinMMPad:
    backend = "WINMM"

    def __init__(self, joy_id=-1, mapping=None, buttons=None):
        if sys.platform != "win32":
            raise OSError("WinMM n'existe que sous Windows")
        self.dll = ctypes.WinDLL("winmm")
        self.dll.joyGetPosEx.argtypes = [wintypes.UINT, ctypes.POINTER(JOYINFOEX)]
        self.dll.joyGetPosEx.restype = wintypes.UINT
        self.dll.joyGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(JOYCAPSW), wintypes.UINT]
        self.dll.joyGetDevCapsW.restype = wintypes.UINT
        self.mapping = dict(DS4_WINMM_AXES, **(mapping or {}))
        self.bmap = dict(DS4_WINMM_BUTTONS, **(buttons or {}))
        self.joy_id = joy_id if joy_id >= 0 else self.find_first()
        if self.joy_id < 0:
            raise OSError("Aucune manette détectée (WinMM)")
        self.caps = JOYCAPSW()
        self.dll.joyGetDevCapsW(self.joy_id, ctypes.byref(self.caps), ctypes.sizeof(JOYCAPSW))
        c = self.caps
        self.ranges = {"X": (c.wXmin, c.wXmax), "Y": (c.wYmin, c.wYmax), "Z": (c.wZmin, c.wZmax),
                       "R": (c.wRmin, c.wRmax), "U": (c.wUmin, c.wUmax), "V": (c.wVmin, c.wVmax)}
        model = SONY_PIDS.get(c.wPid) if c.wMid == SONY_VID else None
        self.name = model or c.szPname or f"Manette {self.joy_id}"
        self.is_sony = c.wMid == SONY_VID
        self.info = JOYINFOEX()
        self.info.dwSize = ctypes.sizeof(JOYINFOEX)
        self.info.dwFlags = JOY_RETURNALL
        # Triggers: depending on the driver they rest at the minimum or at the middle of
        # their range. Track the lowest value seen and use it as the "released" position.
        self.trigger_rest = {}

    @staticmethod
    def list_devices():
        if sys.platform != "win32":
            return []
        dll = ctypes.WinDLL("winmm")
        dll.joyGetPosEx.argtypes = [wintypes.UINT, ctypes.POINTER(JOYINFOEX)]
        dll.joyGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(JOYCAPSW), wintypes.UINT]
        found = []
        info = JOYINFOEX()
        info.dwSize = ctypes.sizeof(JOYINFOEX)
        info.dwFlags = JOY_RETURNALL
        for i in range(16):
            if dll.joyGetPosEx(i, ctypes.byref(info)) == JOYERR_NOERROR:
                caps = JOYCAPSW()
                dll.joyGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(JOYCAPSW))
                model = SONY_PIDS.get(caps.wPid) if caps.wMid == SONY_VID else None
                found.append((i, model or caps.szPname or f"Manette {i}", caps.wMid == SONY_VID))
        return found

    @classmethod
    def find_first(cls):
        devs = cls.list_devices()
        sony = [d for d in devs if d[2]]
        return (sony or devs or [(-1,)])[0][0]

    def _axis(self, letter, raw):
        lo, hi = self.ranges[letter]
        if hi <= lo:
            return 0.0
        return (raw - lo) / (hi - lo) * 2.0 - 1.0

    def read(self):
        if self.dll.joyGetPosEx(self.joy_id, ctypes.byref(self.info)) != JOYERR_NOERROR:
            return PadState(connected=False, name=self.name)
        i = self.info
        raw = {"X": i.dwXpos, "Y": i.dwYpos, "Z": i.dwZpos, "R": i.dwRpos, "U": i.dwUpos, "V": i.dwVpos}
        ax = {k: self._axis(v, raw[v]) for k, v in self.mapping.items() if k not in ("l2", "r2")}
        for k in ("l2", "r2"):
            letter = self.mapping[k]
            lo, hi = self.ranges[letter]
            rest = min(self.trigger_rest.get(k, raw[letter]), raw[letter])
            self.trigger_rest[k] = rest
            ax[k] = 0.0 if hi <= rest else max(0.0, min(1.0, (raw[letter] - rest) / (hi - rest))) * 2.0 - 1.0
        btn = set()
        for name, bit in self.bmap.items():
            if i.dwButtons & (1 << bit):
                btn.add(name)
        pov = i.dwPOV
        if pov != 0xFFFF and pov <= 36000:
            ang = pov / 100.0
            if ang >= 315 or ang <= 45:
                btn.add("up")
            if 45 <= ang <= 135:
                btn.add("right")
            if 135 <= ang <= 225:
                btn.add("down")
            if 225 <= ang <= 315:
                btn.add("left")
        st = PadState(lx=ax["lx"], ly=-ax["ly"], rx=ax["rx"], ry=-ax["ry"],
                      l2=(ax["l2"] + 1.0) / 2.0, r2=(ax["r2"] + 1.0) / 2.0,
                      buttons=frozenset(btn), connected=True, name=self.name)
        st.raw = " ".join(f"{k}:{raw[k]}" for k in WINMM_AXES) + f" btn:{i.dwButtons:#06x} pov:{pov}"
        return st

    def close(self):
        pass


# =========================================================================== XInput

XI_MAP = {0x0001: "up", 0x0002: "down", 0x0004: "left", 0x0008: "right", 0x0010: "options", 0x0020: "share",
          0x0040: "l3", 0x0080: "r3", 0x0100: "l1", 0x0200: "r1", 0x1000: "cross", 0x2000: "circle",
          0x4000: "square", 0x8000: "triangle"}


class XInputPad:
    backend = "XINPUT"

    def __init__(self, index=-1):
        if sys.platform != "win32":
            raise OSError("XInput n'existe que sous Windows")
        self.dll = None
        for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                self.dll = ctypes.WinDLL(name)
                break
            except OSError:
                continue
        if self.dll is None:
            raise OSError("XInput introuvable")
        self.dll.XInputGetState.argtypes = [wintypes.DWORD, ctypes.POINTER(XINPUT_STATE)]
        self.dll.XInputGetState.restype = wintypes.DWORD
        self.state = XINPUT_STATE()
        self.index = index if index >= 0 else self.find_first()
        if self.index < 0:
            raise OSError("Aucune manette XInput détectée")
        self.name = f"Manette XInput #{self.index + 1}"

    def find_first(self):
        for i in range(4):
            if self.dll.XInputGetState(i, ctypes.byref(self.state)) == 0:
                return i
        return -1

    def read(self):
        if self.dll.XInputGetState(self.index, ctypes.byref(self.state)) != 0:
            return PadState(connected=False, name=self.name)
        g = self.state.Gamepad
        btn = {n for bit, n in XI_MAP.items() if g.wButtons & bit}
        l2, r2 = g.bLeftTrigger / 255.0, g.bRightTrigger / 255.0
        if l2 > 0.5:
            btn.add("l2")
        if r2 > 0.5:
            btn.add("r2")
        st = PadState(lx=g.sThumbLX / 32767.0, ly=g.sThumbLY / 32767.0, rx=g.sThumbRX / 32767.0,
                      ry=g.sThumbRY / 32767.0, l2=l2, r2=r2, buttons=frozenset(btn), connected=True, name=self.name)
        st.raw = f"LX:{g.sThumbLX} LY:{g.sThumbLY} RX:{g.sThumbRX} RY:{g.sThumbRY} LT:{g.bLeftTrigger} RT:{g.bRightTrigger} btn:{g.wButtons:#06x}"
        return st

    def close(self):
        pass


# =========================================================================== pygame (optional)

PG_DS4_BUTTONS = {0: "cross", 1: "circle", 2: "square", 3: "triangle", 4: "share", 5: "ps", 6: "options",
                  7: "l3", 8: "r3", 9: "l1", 10: "r1", 11: "up", 12: "down", 13: "left", 14: "right", 15: "touchpad"}


class PygamePad:
    backend = "PYGAME"

    def __init__(self, index=-1):
        import pygame  # noqa: F401  (optional dependency)
        self.pg = pygame
        pygame.display.init()
        pygame.joystick.init()
        if pygame.joystick.get_count() == 0:
            raise OSError("Aucune manette détectée (pygame)")
        self.js = pygame.joystick.Joystick(max(0, index))
        self.js.init()
        self.name = self.js.get_name()

    def read(self):
        pg = self.pg
        pg.event.pump()
        js = self.js
        n_ax = js.get_numaxes()

        def ax(i):
            return js.get_axis(i) if i < n_ax else 0.0
        btn = {PG_DS4_BUTTONS[i] for i in range(min(js.get_numbuttons(), 16)) if js.get_button(i) and i in PG_DS4_BUTTONS}
        if js.get_numhats():
            hx, hy = js.get_hat(0)
            if hx < 0:
                btn.add("left")
            if hx > 0:
                btn.add("right")
            if hy > 0:
                btn.add("up")
            if hy < 0:
                btn.add("down")
        st = PadState(lx=ax(0), ly=-ax(1), rx=ax(2), ry=-ax(3), l2=(ax(4) + 1) / 2, r2=(ax(5) + 1) / 2,
                      buttons=frozenset(btn), connected=True, name=self.name)
        st.raw = " ".join(f"a{i}:{ax(i):+.2f}" for i in range(n_ax))
        return st

    def close(self):
        try:
            self.js.quit()
        except Exception:
            pass


# =========================================================================== factory

def open_pad(backend="AUTO", index=-1, mapping=None, buttons=None):
    """Open a pad. Returns (pad or None, message)."""
    order = {"AUTO": ("WINMM", "XINPUT", "PYGAME"), "WINMM": ("WINMM",), "XINPUT": ("XINPUT",),
             "PYGAME": ("PYGAME",), "KEYBOARD": ()}[backend]
    errors = []
    for b in order:
        try:
            if b == "WINMM":
                pad = WinMMPad(index, mapping, buttons)
            elif b == "XINPUT":
                pad = XInputPad(index)
            else:
                pad = PygamePad(index)
            return pad, f"{pad.name} ({b})"
        except Exception as exc:  # noqa: BLE001 - report every backend failure
            errors.append(f"{b}: {exc}")
    if backend == "KEYBOARD":
        return None, "Clavier uniquement"
    return None, "Aucune manette : " + " | ".join(errors)


class KeyboardPad:
    """Virtual pad driven by the keyboard (works alone or together with a pad).

    Both QWERTY (WASD) and AZERTY (ZQSD) layouts are accepted."""

    MOVE = {"Z": "fwd", "W": "fwd", "S": "back", "Q": "left", "A": "left", "D": "right"}
    KEYS = set(MOVE) | {
        "UP_ARROW", "DOWN_ARROW", "LEFT_ARROW", "RIGHT_ARROW", "I", "J", "K", "L",
        "U", "O", "Y", "H", "NUMPAD_4", "NUMPAD_6", "NUMPAD_8", "NUMPAD_2",
        "SPACE", "F", "LEFT_SHIFT", "RIGHT_SHIFT", "LEFT_CTRL", "C", "LEFT_ALT", "E",
        "R", "T", "BACK_SPACE", "V", "G"}

    def __init__(self):
        self.down = set()

    def event(self, ev):
        if ev.type in self.KEYS:
            if ev.value == 'PRESS':
                self.down.add(ev.type)
            elif ev.value == 'RELEASE':
                self.down.discard(ev.type)
            return True
        return False

    def read(self):
        d = self.down

        def axis(neg, pos):
            n = any(k in d for k in neg)
            p = any(k in d for k in pos)
            return (1.0 if p else 0.0) - (1.0 if n else 0.0)
        fwd = axis(("S",), ("Z", "W"))
        side = axis(("Q", "A"), ("D",))
        strafe_mode = "LEFT_ALT" in d or "E" in d
        btn = set()
        for k, b in (("R", "cross"), ("BACK_SPACE", "circle"), ("T", "triangle"), ("V", "r3"),
                     ("F", "r1"), ("G", "square")):
            if k in d:
                btn.add(b)
        return PadState(lx=0.0 if strafe_mode else side, ly=fwd,
                        rx=axis(("LEFT_ARROW", "J"), ("RIGHT_ARROW", "L")),
                        ry=axis(("DOWN_ARROW", "K"), ("UP_ARROW", "I")),
                        l2=1.0 if ("LEFT_CTRL" in d or "C" in d) else 0.0,
                        r2=1.0 if "SPACE" in d else 0.0, buttons=frozenset(btn), connected=bool(d),
                        hx=axis(("U", "NUMPAD_4"), ("O", "NUMPAD_6")), hy=axis(("H", "NUMPAD_2"), ("Y", "NUMPAD_8")),
                        sx=side if strafe_mode else 0.0, sprint=("LEFT_SHIFT" in d or "RIGHT_SHIFT" in d))


def now():
    return time.perf_counter()
