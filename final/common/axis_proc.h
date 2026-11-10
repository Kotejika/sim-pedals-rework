/* Axis processing on the hub: filter -> calibration -> invert -> deadzone -> curve -> 16 bit.
 * Pure C, no Zephyr dependencies (host-tested). */
#ifndef AXIS_PROC_H
#define AXIS_PROC_H
#include <stdint.h>
#include "pedal_protocol.h"

struct axis_state {
	int64_t filt;      /* IIR state, raw units << 8 */
	uint8_t primed;
};

void axis_defaults(struct pp_axis_cfg *c, int32_t cal_min, int32_t cal_max);
/* returns 0..65535, writes filtered raw to *filt_raw (may be NULL) */
uint16_t axis_process(const struct pp_axis_cfg *c, struct axis_state *s, int32_t raw,
		      int32_t *filt_raw);
/* sanitize a config received from the PC (returns 0 if ok) */
int axis_cfg_validate(struct pp_axis_cfg *c);
#endif
