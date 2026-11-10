#include <stdio.h>
#include <string.h>
#include <assert.h>
#include "axis_proc.h"

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

int main(int argc, char **argv)
{
	struct pp_axis_cfg c; struct axis_state s = {0};
	axis_defaults(&c, 0, 16383);
	CHECK(axis_process(&c, &s, 0, 0) == 0);
	CHECK(axis_process(&c, &s, 16383, 0) == 65535);
	CHECK(axis_process(&c, &s, 99999, 0) == 65535);
	CHECK(axis_process(&c, &s, -5, 0) == 0);
	uint16_t mid = axis_process(&c, &s, 8192, 0);
	CHECK(mid >= 32767 && mid <= 32770);

	/* invert */
	c.flags = PP_AXF_INVERT;
	CHECK(axis_process(&c, &s, 0, 0) == 65535);
	CHECK(axis_process(&c, &s, 16383, 0) == 0);
	c.flags = 0;

	/* deadzone 5%..95% */
	c.dz_lo = 50; c.dz_hi = 950;
	CHECK(axis_process(&c, &s, 400, 0) == 0);
	CHECK(axis_process(&c, &s, 16000, 0) == 65535);
	c.dz_lo = 0; c.dz_hi = 1000;

	/* curve: x^2-ish */
	for (int i = 0; i < PP_CURVE_PTS; i++) c.curve[i] = (uint16_t)(i * i * 10);
	CHECK(axis_process(&c, &s, 16383, 0) == 65535);
	uint16_t q = axis_process(&c, &s, 8192, 0);  /* 50% -> 250 permille */
	CHECK(q > 16000 && q < 16800);
	axis_defaults(&c, 0, 16383);

	/* brake 24-bit negative cal_min */
	axis_defaults(&c, -1000, 2000000);
	CHECK(axis_process(&c, &s, -1000, 0) == 0);
	CHECK(axis_process(&c, &s, 2000000, 0) == 65535);
	/* span 0 must not crash */
	c.cal_max = c.cal_min;
	CHECK(axis_process(&c, &s, 5, 0) == 0);

	/* filter: step response converges, no overshoot */
	axis_defaults(&c, 0, 1000); c.filter = 3; memset(&s, 0, sizeof s);
	axis_process(&c, &s, 0, 0);
	int32_t f = 0; uint16_t o = 0;
	for (int i = 0; i < 100; i++) o = axis_process(&c, &s, 1000, &f);
	CHECK(f >= 990 && f <= 1000);
	CHECK(o >= 64900);

	/* validate */
	axis_defaults(&c, 0, 10); c.filter = 99; c.curve[3] = 5000; c.slot = 17;
	CHECK(axis_cfg_validate(&c) != 0 && c.filter == 8 && c.curve[3] == 1000 && c.slot == PP_SLOT_OFF);

	/* packet sizes / i24 */
	CHECK(sizeof(struct pp_axis_cfg) == 40);
	uint8_t b[3]; pp_put_i24(b, -2); CHECK(pp_get_i24(b) == -2);
	pp_put_i24(b, 8388607); CHECK(pp_get_i24(b) == 8388607);
	pp_put_i24(b, -8388608); CHECK(pp_get_i24(b) == -8388608);

	/* dump golden vectors for the Python mirror */
	if (argc > 1) {
		FILE *fp = fopen(argv[1], "w");
		struct pp_axis_cfg g; struct axis_state gs;
		int32_t raws[] = {0, 1, 100, 5000, 8192, 12000, 16383, 20000, -50};
		for (int fl = 0; fl < 3; fl++) {
			axis_defaults(&g, 100, 16000); g.filter = (uint8_t)(fl * 2); g.dz_lo = 30; g.dz_hi = 970;
			if (fl == 1) g.flags = PP_AXF_INVERT;
			for (int i = 0; i < PP_CURVE_PTS; i++) g.curve[i] = (uint16_t)(i * i * 10);
			memset(&gs, 0, sizeof gs);
			for (unsigned i = 0; i < sizeof raws / sizeof raws[0]; i++) {
				int32_t ff; uint16_t o2 = axis_process(&g, &gs, raws[i], &ff);
				fprintf(fp, "%d %d %d %d\n", fl, raws[i], ff, o2);
			}
		}
		fclose(fp);
	}
	printf(fails ? "FAILED\n" : "axis tests OK\n");
	return fails != 0;
}
