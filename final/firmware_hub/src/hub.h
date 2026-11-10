#ifndef HUB_H
#define HUB_H
#include <zephyr/kernel.h>
#include "pedal_protocol.h"
#include "axis_proc.h"

#define FW_MAJOR 1
#define FW_MINOR 0
#define N_TYPES  (PP_DEV_MAX_TYPE + 1)   /* index = dev_type (0 unused) */
#define LINK_TIMEOUT_MS 50               /* after this the device's axes fall to 0 */

struct dev_state {
	bool     seen;
	bool     alive;
	int64_t  last_rx_ms;
	uint32_t uid;
	uint8_t  batt_20mv, flags, n_axes;
	struct pp_diag diag;
	int32_t  raw[PP_MAX_AXES_PER_DEV];
	int32_t  filt[PP_MAX_AXES_PER_DEV];
	uint16_t out[PP_MAX_AXES_PER_DEV];
	struct axis_state st[PP_MAX_AXES_PER_DEV];
	uint16_t pps, pkt_cnt;
	int64_t  pps_t0;
	uint8_t  pend_cmd, pend_p0;      /* next ACK payload */
};

extern struct dev_state g_dev[N_TYPES];
extern struct pp_axis_cfg g_cfg[N_TYPES][PP_MAX_AXES_PER_DEV];
struct hub_stats { uint32_t rx_ok, tx_ok; uint16_t rx_bad; uint8_t bad_len, bad_b0; };
extern struct hub_stats g_stats;
extern uint8_t g_idle_min;
extern bool g_dirty;                 /* config changed by auto slot assignment */
extern struct k_spinlock g_lock;

void defaults_all(void);
int  store_load(void);
int  store_save(void);

int  radio_start(void);

void assign_slots_locked(uint8_t t);          /* needs g_lock */
void hub_outputs(uint16_t axes[PP_HID_AXES], int64_t now);

int  usb_start(void);
void usb_cfg_enqueue_live(void);
#endif
