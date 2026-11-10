"""Hub access: real (hidapi) and simulated."""
import math
import threading
import time
from collections import deque

import protocol as P
from processing import AxisState, axis_process


class HubError(Exception):
    pass


class RealHub:
    """Talks to the hub over its vendor HID interface (pip install hidapi)."""

    def __init__(self):
        import hid  # imported lazily so the app starts in simulator mode without it
        self._hid = hid
        self.dev = None
        self.live = deque(maxlen=64)
        self._resp = {}
        self._lock = threading.Lock()
        self._tok = 0
        self._run = False
        self.stats = None

    @staticmethod
    def find():
        import hid
        for d in hid.enumerate(P.VID, P.PID):
            if d.get("usage_page") == P.USAGE_PAGE:
                return d
        return None

    def open(self):
        info = self.find()
        if not info:
            raise HubError("Hub not found (is it plugged in? is the config interface visible?)")
        self.dev = self._hid.device()
        self.dev.open_path(info["path"])
        self.dev.set_nonblocking(0)
        self._run = True
        threading.Thread(target=self._reader, daemon=True).start()

    def close(self):
        self._run = False
        time.sleep(0.05)
        if self.dev:
            self.dev.close()
            self.dev = None

    def _reader(self):
        while self._run:
            try:
                d = self.dev.read(P.REPORT_LEN, 200)
            except Exception:
                self._run = False
                break
            if not d:
                continue
            d = bytes(d)
            if d[0] == P.CMD_LIVE:
                devs = P.parse_live(d)
                if devs is not None:
                    self.stats = P.parse_stats(d)
                    self.live.append(devs)
            else:
                with self._lock:
                    self._resp[(d[0], d[1])] = d

    def connected(self):
        return self._run and self.dev is not None

    def call(self, cmd, payload=b"", timeout=1.0):
        with self._lock:
            self._tok = (self._tok + 1) & 0xFF
            tok = self._tok
        self.dev.write(b"\x00" + P.request(cmd, tok, payload))     # leading 0 = no report ID
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self._lock:
                r = self._resp.pop((cmd, tok), None)
            if r:
                if r[2] != 0:
                    raise HubError("hub returned status %d for cmd 0x%02X" % (r[2], cmd))
                return r[3:]
            time.sleep(0.002)
        raise HubError("hub did not answer cmd 0x%02X" % cmd)

    # -- high level
    def info(self):
        r = self.call(P.CMD_GET_INFO)
        return {"proto": r[0], "fw": "%d.%d" % (r[1], r[2]), "axes": r[3], "seen_mask": r[4],
                "idle_min": r[5], "uid": int.from_bytes(r[6:10], "little")}

    def get_axis(self, t, a):
        return P.AxisCfg.unpack(self.call(P.CMD_GET_AXIS, bytes([t, a])))

    def set_axis(self, t, a, cfg):
        self.call(P.CMD_SET_AXIS, bytes([t, a]) + cfg.pack())

    def save(self): self.call(P.CMD_SAVE, timeout=3.0)
    def defaults(self): self.call(P.CMD_DEFAULTS)
    def tare(self, t, a): self.call(P.CMD_TARE, bytes([t, a]))
    def stream(self, on): self.call(P.CMD_STREAM, bytes([1 if on else 0]))
    def dev_cmd(self, t, cmd, p0=0): self.call(P.CMD_DEV_CMD, bytes([t, cmd, p0]))

    def bootloader(self):
        try:
            self.call(P.CMD_BOOTLOADER)
        except HubError:
            pass

    def poll_live(self):
        """-> list of DevLive (possibly empty) or None when no new frame arrived"""
        last = None
        while self.live:
            last = self.live.popleft()
        return last


class SimHub:
    """Fake hub with fake pedals, runs the same processing code. For trying the UI without hardware."""
    sim = True

    def __init__(self):
        self.cfg = {a: P.AxisCfg() for a in range(3)}
        self.cfg[0].cal_min, self.cfg[0].cal_max, self.cfg[0].slot = 4000, 12000, 0
        self.cfg[1].cal_min, self.cfg[1].cal_max, self.cfg[1].slot = 4000, 12000, 1
        self.cfg[2].cal_min, self.cfg[2].cal_max, self.cfg[2].slot = 0, 1000000, 2
        self.cfg[2].full_scale_dkg = 1000
        self.st = [AxisState() for _ in range(3)]
        self.t0 = time.time()
        self.idle = 20
        self.last_raw = [0, 0, 0]
        self._on = True

    def open(self): pass
    def close(self): pass
    def connected(self): return True

    def info(self):
        return {"proto": 1, "fw": "sim", "axes": 8, "seen_mask": 2, "idle_min": self.idle, "uid": 0}

    def get_axis(self, t, a): return self.cfg[a]
    def set_axis(self, t, a, cfg): self.cfg[a] = cfg; self.st[a] = AxisState()
    def save(self): pass
    def defaults(self): pass
    def tare(self, t, a): self.cfg[a].cal_min = self.last_raw[a]
    def stream(self, on): pass
    def dev_cmd(self, t, cmd, p0=0):
        if cmd == P.DEVCMD_SET_IDLE:
            self.idle = p0
    def bootloader(self): pass

    def poll_live(self):
        t = time.time() - self.t0
        raw = [int(8000 + 3500 * (math.sin(t * 0.9) + 1) / 2 * 2 - 3500),
               int(4000 + 8000 * max(0.0, math.sin(t * 0.5))),
               int(max(0.0, math.sin(t * 0.7 + 1.0)) ** 2 * 900000 + 2500 * math.sin(t * 40))]
        self.last_raw = raw
        out = []
        for a in range(3):
            o, _ = axis_process(self.cfg[a], self.st[a], raw[a])
            out.append(o)
        return [P.DevLive(1, 3900, 0, True, 1000, raw, out)]
