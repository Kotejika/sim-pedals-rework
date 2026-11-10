#include "axis_proc.h"

void axis_defaults(struct pp_axis_cfg *c, int32_t cal_min, int32_t cal_max)
{
	c->slot = PP_SLOT_AUTO;
	c->flags = 0;
	c->filter = 0;
	c->reserved = 0;
	c->cal_min = cal_min;
	c->cal_max = cal_max;
	c->dz_lo = 0;
	c->dz_hi = 1000;
	for (int i = 0; i < PP_CURVE_PTS; i++) {
		c->curve[i] = (uint16_t)(i * 100);
	}
	c->full_scale_dkg = 0;
}

int axis_cfg_validate(struct pp_axis_cfg *c)
{
	int bad = 0;
	if (c->slot >= PP_HID_AXES && c->slot != PP_SLOT_AUTO && c->slot != PP_SLOT_OFF) {
		c->slot = PP_SLOT_OFF; bad = 1;
	}
	if (c->filter > 8) { c->filter = 8; bad = 1; }
	if (c->dz_lo > 1000) { c->dz_lo = 1000; bad = 1; }
	if (c->dz_hi > 1000) { c->dz_hi = 1000; bad = 1; }
	for (int i = 0; i < PP_CURVE_PTS; i++) {
		if (c->curve[i] > 1000) { c->curve[i] = 1000; bad = 1; }
	}
	return bad ? -1 : 0;
}

uint16_t axis_process(const struct pp_axis_cfg *c, struct axis_state *s, int32_t raw,
		      int32_t *filt_raw)
{
	int64_t x = (int64_t)raw * 256;

	if (c->filter == 0 || !s->primed) {
		s->filt = x;
		s->primed = 1;
	} else {
		s->filt += (x - s->filt) / ((int64_t)1 << c->filter);
	}
	int32_t f = (int32_t)(s->filt >> 8);
	if (filt_raw) {
		*filt_raw = f;
	}

	int64_t span = (int64_t)c->cal_max - (int64_t)c->cal_min;
	int64_t n;                       /* ppm 0..1e6 */
	if (span == 0) {
		n = 0;
	} else {
		n = ((int64_t)f - c->cal_min) * 1000000 / span;
	}
	if (n < 0) n = 0;
	if (n > 1000000) n = 1000000;
	if (c->flags & PP_AXF_INVERT) {
		n = 1000000 - n;
	}

	/* deadzone (permille -> ppm) */
	int64_t lo = (int64_t)c->dz_lo * 1000, hi = (int64_t)c->dz_hi * 1000;
	if (hi <= lo) {
		n = (n >= hi) ? 1000000 : 0;
	} else if (n <= lo) {
		n = 0;
	} else if (n >= hi) {
		n = 1000000;
	} else {
		n = (n - lo) * 1000000 / (hi - lo);
	}

	/* curve: piecewise linear, 10 segments of 100000 ppm, values in permille */
	int64_t seg = n / 100000;
	int64_t frac = n - seg * 100000;
	int64_t y;
	if (seg >= PP_CURVE_PTS - 1) {
		y = (int64_t)c->curve[PP_CURVE_PTS - 1] * 1000;
	} else {
		int64_t y0 = c->curve[seg], y1 = c->curve[seg + 1];
		y = (y0 * 100000 + (y1 - y0) * frac) / 100;   /* permille*1000 = ppm */
	}
	if (y < 0) y = 0;
	if (y > 1000000) y = 1000000;
	return (uint16_t)((y * 65535 + 500000) / 1000000);
}
