"""Bit-exact Python mirror of common/axis_proc.c (used for the curve preview / simulator)."""
from protocol import AXF_INVERT, CURVE_PTS


def cdiv(a: int, b: int) -> int:
    """C-style integer division (truncate toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


class AxisState:
    def __init__(self):
        self.filt = 0
        self.primed = False


def axis_process(c, s: AxisState, raw: int):
    """-> (out16, filtered_raw)"""
    x = raw * 256
    if c.filter == 0 or not s.primed:
        s.filt = x
        s.primed = True
    else:
        s.filt += cdiv(x - s.filt, 1 << c.filter)
    f = s.filt >> 8

    span = c.cal_max - c.cal_min
    n = 0 if span == 0 else cdiv((f - c.cal_min) * 1000000, span)
    n = max(0, min(1000000, n))
    if c.flags & AXF_INVERT:
        n = 1000000 - n

    lo, hi = c.dz_lo * 1000, c.dz_hi * 1000
    if hi <= lo:
        n = 1000000 if n >= hi else 0
    elif n <= lo:
        n = 0
    elif n >= hi:
        n = 1000000
    else:
        n = cdiv((n - lo) * 1000000, hi - lo)

    seg = n // 100000
    frac = n - seg * 100000
    if seg >= CURVE_PTS - 1:
        y = c.curve[CURVE_PTS - 1] * 1000
    else:
        y0, y1 = c.curve[seg], c.curve[seg + 1]
        y = cdiv(y0 * 100000 + (y1 - y0) * frac, 100)
    y = max(0, min(1000000, y))
    return (y * 65535 + 500000) // 1000000, f
