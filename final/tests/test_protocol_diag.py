import struct
import sys
from pathlib import Path

PC = Path(__file__).resolve().parents[1] / "pc_app"
sys.path.insert(0, str(PC))
import protocol as P

frame = bytearray(P.REPORT_LEN)
frame[0] = P.CMD_LIVE
frame[2] = 1
pos = 3
struct.pack_into("<BBBBHB", frame, pos, P.DEV_PEDALS, 0, P.PP_FLAG_SENSOR_ERR | P.PP_FLAG_DIAG_EXT, 1, 400, 3)
pos += 7
for raw, out in ((101, 1000), (202, 2000), (303, 3000)):
    struct.pack_into("<iH", frame, pos, raw, out)
    pos += 6
frame[pos:pos + P.PP_DIAG_LEN] = bytes((2, 1, 32, 0xFF, 0xFF, 0xFF, 0xFF))
parsed = P.parse_live(bytes(frame))
assert parsed is not None and len(parsed) == 1
dev = parsed[0]
assert dev.raw == [101, 202, 303]
assert len(dev.raw) == 3, "diagnostic bytes leaked into the game axes"
assert dev.diag == {"error": 2, "drdy_active": True, "tries": 32, "regs": (255, 255, 255, 255)}
assert dev.sensor_err and dev.has_diag

# A diagnostic extension is legal only for the three-axis pedal node.
bad = bytearray(frame)
struct.pack_into("<BBBBHB", bad, 3, P.DEV_PEDALS, 0, P.PP_FLAG_SENSOR_ERR | P.PP_FLAG_DIAG_EXT, 1, 400, 6)
assert P.parse_live(bytes(bad)) is None
print("live protocol + ADS1220 diagnostic extension tests OK")
