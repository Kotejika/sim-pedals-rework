"""Hub <-> PC protocol (mirror of common/pedal_protocol.h)."""
import struct
from dataclasses import dataclass, field

PP_VERSION = 1
VID, PID = 0x1209, 0x5E00
USAGE_PAGE = 0xFF00
REPORT_LEN = 64

CMD_GET_INFO, CMD_LIVE = 0x01, 0x10
CMD_GET_AXIS, CMD_SET_AXIS, CMD_SAVE, CMD_DEFAULTS = 0x20, 0x21, 0x22, 0x23
CMD_TARE, CMD_STREAM, CMD_DEV_CMD, CMD_BOOTLOADER = 0x30, 0x31, 0x40, 0x50
DEVCMD_NONE, DEVCMD_SLEEP, DEVCMD_SET_IDLE = 0, 1, 2

SLOT_AUTO, SLOT_OFF = 0xFE, 0xFF
HID_AXES = 8
CURVE_PTS = 11
AXF_INVERT = 1
PP_FLAG_LOW_BATT = 1 << 0
PP_FLAG_SENSOR_ERR = 1 << 1
PP_FLAG_SLEEPING = 1 << 2
PP_FLAG_DIAG_EXT = 1 << 3
PP_DIAG_LEN = 7

DEV_PEDALS = 1
PEDAL_AXES = ["Clutch", "Throttle", "Brake"]
HID_AXIS_NAMES = ["X", "Y", "Z", "Rx", "Ry", "Rz", "Slider", "Dial"]

_CFG_FMT = "<BBBBiiHH%dHH" % CURVE_PTS
CFG_LEN = struct.calcsize(_CFG_FMT)
assert CFG_LEN == 40


@dataclass
class AxisCfg:
    slot: int = SLOT_AUTO
    flags: int = 0
    filter: int = 0
    cal_min: int = 0
    cal_max: int = 1000000
    dz_lo: int = 0
    dz_hi: int = 1000
    curve: list = field(default_factory=lambda: [i * 100 for i in range(CURVE_PTS)])
    full_scale_dkg: int = 0

    def pack(self) -> bytes:
        return struct.pack(_CFG_FMT, self.slot, self.flags, self.filter, 0, self.cal_min,
                           self.cal_max, self.dz_lo, self.dz_hi, *self.curve, self.full_scale_dkg)

    @staticmethod
    def unpack(b: bytes) -> "AxisCfg":
        v = struct.unpack(_CFG_FMT, bytes(b[:CFG_LEN]))
        return AxisCfg(v[0], v[1], v[2], v[4], v[5], v[6], v[7], list(v[8:8 + CURVE_PTS]),
                       v[8 + CURVE_PTS])


@dataclass
class DevLive:
    dev_type: int
    batt_mv: int        # 0 = unknown
    flags: int
    alive: bool
    pps: int
    raw: list
    out: list           # 0..65535 as sent to the PC game port
    diag: object = None # ADS1220 diagnostic metadata; never an axis

    @property
    def low_batt(self): return bool(self.flags & PP_FLAG_LOW_BATT)
    @property
    def sensor_err(self): return bool(self.flags & PP_FLAG_SENSOR_ERR)
    @property
    def has_diag(self): return bool(self.flags & PP_FLAG_DIAG_EXT)


def parse_live(frame: bytes):
    """Parse one 64-byte Hub live frame, rejecting truncated device records."""
    if len(frame) != REPORT_LEN or frame[0] != CMD_LIVE:
        return None
    nd, pos, res = frame[2], 3, []
    # Stats occupy [52:64]; device records may not overlap that reserved tail.
    for _ in range(nd):
        if pos + 7 > 52:
            return None
        t, batt, flags, alive, pps, n = struct.unpack_from("<BBBBHB", frame, pos)
        pos += 7
        if t < 1 or t > 7 or n == 0 or n > 6 or pos + 6 * n > 52:
            return None
        has_diag = bool(flags & PP_FLAG_DIAG_EXT)
        if has_diag and (t != DEV_PEDALS or n != 3 or not (flags & PP_FLAG_SENSOR_ERR)):
            return None
        raw, out = [], []
        for _ in range(n):
            r, o = struct.unpack_from("<iH", frame, pos)
            pos += 6
            raw.append(r)
            out.append(o)
        diag = None
        if has_diag:
            if pos + PP_DIAG_LEN > 52:
                return None
            error, drdy_active, tries = struct.unpack_from("<BBB", frame, pos)
            regs = tuple(frame[pos + 3:pos + 7])
            diag = {"error": error, "drdy_active": bool(drdy_active),
                    "tries": tries, "regs": regs}
            pos += PP_DIAG_LEN
        res.append(DevLive(t, batt * 20, flags, bool(alive), pps, raw, out, diag))
    return res


def request(cmd: int, token: int, payload: bytes = b"") -> bytes:
    f = bytes([cmd, token & 0xFF]) + payload
    return f + bytes(REPORT_LEN - len(f))


def parse_stats(frame: bytes):
    """hub radio counters from the tail of a live frame"""
    rx_ok, tx_ok, rx_bad, bad_len, bad_b0 = struct.unpack_from("<IIHBB", frame, 52)
    return {"rx_ok": rx_ok, "tx_ok": tx_ok, "rx_bad": rx_bad, "bad_len": bad_len, "bad_b0": bad_b0}
