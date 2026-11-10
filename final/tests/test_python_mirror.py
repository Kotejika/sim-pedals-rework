"""Checks: Python processing == C processing (golden vectors), AxisCfg layout, live frame parsing."""
import os, sys, struct
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "pc_app"))
import protocol as P
from processing import AxisState, axis_process

def main():
    rows = [tuple(map(int, l.split())) for l in open(os.path.join(here, "golden.txt"))]
    cfgs = {}
    bad = 0
    for fl, raw, ff, out in rows:
        if fl not in cfgs:
            c = P.AxisCfg(cal_min=100, cal_max=16000, filter=fl * 2, dz_lo=30, dz_hi=970,
                          curve=[i * i * 10 for i in range(P.CURVE_PTS)])
            if fl == 1:
                c.flags = P.AXF_INVERT
            cfgs[fl] = (c, AxisState())
        c, s = cfgs[fl]
        o, f = axis_process(c, s, raw)
        if (o, f) != (out, ff):
            bad += 1
            print("MISMATCH", fl, raw, "py", (o, f), "c", (out, ff))
    c = P.AxisCfg(slot=3, flags=1, filter=2, cal_min=-5, cal_max=77, dz_lo=1, dz_hi=999, full_scale_dkg=1234)
    assert P.AxisCfg.unpack(c.pack()) == c and len(c.pack()) == 40
    # live frame
    fr = bytearray(64); fr[0] = P.CMD_LIVE; fr[1] = 7; fr[2] = 1
    fr[3:10] = struct.pack("<BBBBHB", 1, 195, 0, 1, 998, 3)
    pos = 10
    for r, o in [(100, 1), (-2, 65535), (-8388608, 300)]:
        fr[pos:pos + 6] = struct.pack("<iH", r, o); pos += 6
    d = P.parse_live(bytes(fr))[0]
    assert d.batt_mv == 3900 and d.pps == 998 and d.raw == [100, -2, -8388608] and d.out == [1, 65535, 300]
    print("python mirror tests", "FAILED" if bad else "OK", "(%d golden rows)" % len(rows))
    return 1 if bad else 0

sys.exit(main())
