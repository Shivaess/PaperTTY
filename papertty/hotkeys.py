"""Global Ctrl+Alt hotkeys read straight from the keyboard's evdev device.

The console keymap would normally turn these combos into escape sequences
(e.g. Ctrl+Alt+C -> ESC ^C), so on start they are mapped to VoidSymbol and
only PaperTTY sees them.
"""
import glob
import json
import os
import select
import struct
import subprocess
import threading
import time

EV_KEY = 1
KEY_LEFTCTRL, KEY_RIGHTCTRL = 29, 97
KEY_LEFTALT, KEY_RIGHTALT = 56, 100
CTRL_KEYS = (KEY_LEFTCTRL, KEY_RIGHTCTRL)
ALT_KEYS = (KEY_LEFTALT, KEY_RIGHTALT)
MODIFIERS = CTRL_KEYS + ALT_KEYS + (42, 54, 125, 126)  # + shifts, meta

# keycode -> action name
HOTKEYS = {
    103: 'bigger',   # Up
    108: 'smaller',  # Down
    46: 'clear',     # C
    19: 'rotate',    # R
    35: 'help',      # H
}

HELP_LINES = [
    'Ctrl+Alt+Up    bigger text',
    'Ctrl+Alt+Down  smaller text',
    'Ctrl+Alt+C     clear ghosting',
    'Ctrl+Alt+R     rotate',
    'Ctrl+Alt+H     help/status',
]

EVENT = struct.Struct('llHHi')

# font size and orientation chosen with hotkeys survive restarts
STATE_FILE = '/var/lib/papertty/hotkeys.json'


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(**state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        print('Could not save hotkey state: {}'.format(e))


def void_console_combos(tty='/dev/tty1'):
    """Stop the console from sending anything for our Ctrl+Alt combos"""
    keymap = ''.join('control {} keycode {} = VoidSymbol\n'.format(mod, code)
                     for code in HOTKEYS for mod in ('alt', 'altgr'))
    try:
        subprocess.run(['loadkeys', '-C', tty, '-'], input=keymap.encode(),
                       check=True, capture_output=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        print('Could not update console keymap, hotkeys will also reach the terminal: {}'.format(e))
        return False


class HotkeyListener(threading.Thread):
    RESCAN_S = 5

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.actions = []
        self.last_key_time = 0
        self.devices = {}  # path -> file
        self.ctrl = set()
        self.alt = set()

    def keyboard_names(self):
        names = []
        for path in self.devices:
            name = os.path.basename(os.path.realpath(path))
            try:
                with open('/sys/class/input/{}/device/name'.format(name)) as f:
                    names.append(f.read().strip())
            except OSError:
                names.append(path)
        return names

    def pop_actions(self):
        with self.lock:
            actions, self.actions = self.actions, []
        return actions

    def _rescan(self):
        for path in glob.glob('/dev/input/by-id/*-event-kbd'):
            if path not in self.devices:
                try:
                    self.devices[path] = open(path, 'rb', buffering=0)
                    print('Hotkeys: listening on {}'.format(path))
                except OSError as e:
                    print('Hotkeys: cannot open {}: {}'.format(path, e))

    def _on_key(self, code, value):
        for group, keys in ((self.ctrl, CTRL_KEYS), (self.alt, ALT_KEYS)):
            if code in keys:
                if value:
                    group.add(code)
                else:
                    group.discard(code)
                return
        if value != 1 or code in MODIFIERS:
            return
        with self.lock:
            self.last_key_time = time.monotonic()
            if self.ctrl and self.alt and code in HOTKEYS:
                self.actions.append(HOTKEYS[code])

    def run(self):
        next_scan = 0
        while True:
            if time.monotonic() >= next_scan:
                self._rescan()
                next_scan = time.monotonic() + self.RESCAN_S
            files = list(self.devices.values())
            if not files:
                time.sleep(self.RESCAN_S)
                continue
            ready, _, _ = select.select(files, [], [], self.RESCAN_S)
            for f in ready:
                try:
                    data = f.read(EVENT.size * 64)
                except OSError:
                    data = None
                if not data:
                    # keyboard unplugged
                    path = next(p for p, d in self.devices.items() if d is f)
                    del self.devices[path]
                    f.close()
                    self.ctrl.clear()
                    self.alt.clear()
                    continue
                for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, etype, code, value = EVENT.unpack_from(data, i)
                    if etype == EV_KEY:
                        self._on_key(code, value)
