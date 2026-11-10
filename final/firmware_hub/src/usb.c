/*
 * USB side of the hub:
 *   interface 0: HID joystick, 8 x 16-bit axes + 32 buttons (static descriptor, 20-byte report)
 *   interface 1: vendor HID (usage page 0xFF00), 64-byte in/out reports, config + live data
 */
#include <string.h>
#include <zephyr/device.h>
#include <zephyr/usb/usbd.h>
#include <zephyr/usb/class/usbd_hid.h>
#include <zephyr/sys/reboot.h>
#include <zephyr/drivers/hwinfo.h>
#include <nrf.h>
#include "hub.h"
#include "usb_init.h"

/* ------------------------------------------------------------- descriptors */
static const uint8_t pad_desc[] = {
	0x05, 0x01,             /* Usage Page (Generic Desktop) */
	0x09, 0x04,             /* Usage (Joystick) */
	0xA1, 0x01,             /* Collection (Application) */
	0x09, 0x30, 0x09, 0x31, 0x09, 0x32, 0x09, 0x33,   /* X Y Z Rx */
	0x09, 0x34, 0x09, 0x35, 0x09, 0x36, 0x09, 0x37,   /* Ry Rz Slider Dial */
	0x15, 0x00,             /* Logical Min 0 */
	0x27, 0xFF, 0xFF, 0x00, 0x00,   /* Logical Max 65535 */
	0x75, 0x10,             /* Report Size 16 */
	0x95, 0x08,             /* Report Count 8 */
	0x81, 0x02,             /* Input (Data,Var,Abs) */
	0x05, 0x09,             /* Usage Page (Button) */
	0x19, 0x01, 0x29, 0x20, /* Buttons 1..32 */
	0x15, 0x00, 0x25, 0x01,
	0x75, 0x01, 0x95, 0x20,
	0x81, 0x02,
	0xC0,
};
#define PAD_REPORT_LEN 20

static const uint8_t cfg_desc[] = {
	0x06, 0x00, 0xFF,       /* Usage Page (Vendor 0xFF00) */
	0x09, 0x01,             /* Usage (1) */
	0xA1, 0x01,             /* Collection (Application) */
	0x09, 0x02, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x40, 0x81, 0x02,  /* Input 64 */
	0x09, 0x03, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x40, 0x91, 0x02,  /* Output 64 */
	0xC0,
};

static const struct device *pad_dev = DEVICE_DT_GET(DT_NODELABEL(hid_pad));
static const struct device *cfg_dev = DEVICE_DT_GET(DT_NODELABEL(hid_cfg));

/* ------------------------------------------------------------- joystick */
static uint8_t pad_report[PAD_REPORT_LEN] __aligned(4);
static K_SEM_DEFINE(pad_done, 0, 1);
static volatile bool pad_ready;
static uint32_t g_buttons;

static void pad_iface_ready(const struct device *d, const bool ready)
{
	pad_ready = ready;
	if (ready) {
		k_sem_give(&pad_done);
	}
}
static void pad_in_done(const struct device *d, const uint8_t *r)
{
	k_sem_give(&pad_done);
}
static int pad_get_report(const struct device *d, const uint8_t t, const uint8_t id,
			  const uint16_t len, uint8_t *const buf)
{
	if (t != 1 /* input */ || len < PAD_REPORT_LEN) {
		return -ENOTSUP;
	}
	memcpy(buf, pad_report, PAD_REPORT_LEN);
	return PAD_REPORT_LEN;
}
static const struct hid_device_ops pad_ops = {
	.iface_ready = pad_iface_ready,
	.get_report = pad_get_report,
	.input_report_done = pad_in_done,
};

static void pad_thread(void *a, void *b, void *c)
{
	for (;;) {
		k_sem_take(&pad_done, K_MSEC(20));
		if (!pad_ready) {
			continue;
		}
		uint16_t ax[PP_HID_AXES];

		hub_outputs(ax, k_uptime_get());
		for (int i = 0; i < PP_HID_AXES; i++) {
			pad_report[2 * i] = ax[i] & 0xFF;
			pad_report[2 * i + 1] = ax[i] >> 8;
		}
		memcpy(&pad_report[16], &g_buttons, 4);
		if (hid_device_submit_report(pad_dev, PAD_REPORT_LEN, pad_report)) {
			k_msleep(1);
			k_sem_give(&pad_done);   /* retry */
		}
	}
}
K_THREAD_DEFINE(pad_tid, 2048, pad_thread, NULL, NULL, NULL, 5, 0, 0);

/* ------------------------------------------------------------- config channel */
struct frame { uint8_t d[PP_CFG_REPORT_LEN]; };
K_MSGQ_DEFINE(rx_q, sizeof(struct frame), 8, 4);
K_MSGQ_DEFINE(tx_q, sizeof(struct frame), 8, 4);
static K_SEM_DEFINE(cfg_tx_done, 1, 1);
static volatile bool cfg_ready, stream_on;
static uint8_t live_ctr;

static void cfg_iface_ready(const struct device *d, const bool ready)
{
	cfg_ready = ready;
	if (!ready) {
		stream_on = false;
	}
}
static void cfg_in_done(const struct device *d, const uint8_t *r)
{
	k_sem_give(&cfg_tx_done);
}
static void cfg_out(const struct device *d, const uint16_t len, const uint8_t *const buf)
{
	struct frame f = {0};

	memcpy(f.d, buf, MIN(len, PP_CFG_REPORT_LEN));
	k_msgq_put(&rx_q, &f, K_NO_WAIT);
}
static int cfg_set_report(const struct device *d, const uint8_t t, const uint8_t id,
			  const uint16_t len, const uint8_t *const buf)
{
	if (t == 2 /* output */) {
		cfg_out(d, len, buf);
		return 0;
	}
	return -ENOTSUP;
}
static int cfg_get_report(const struct device *d, const uint8_t t, const uint8_t id,
			  const uint16_t len, uint8_t *const buf)
{
	return -ENOTSUP;
}
static const struct hid_device_ops cfg_ops = {
	.iface_ready = cfg_iface_ready,
	.get_report = cfg_get_report,
	.set_report = cfg_set_report,
	.input_report_done = cfg_in_done,
	.output_report = cfg_out,
};

static void tx_thread(void *a, void *b, void *c)
{
	static struct frame f __aligned(4);

	for (;;) {
		k_msgq_get(&tx_q, &f, K_FOREVER);
		if (!cfg_ready) {
			continue;
		}
		if (k_sem_take(&cfg_tx_done, K_MSEC(100)) != 0) {
			k_sem_give(&cfg_tx_done);      /* host not reading: drop, don't wedge */
			continue;
		}
		if (hid_device_submit_report(cfg_dev, PP_CFG_REPORT_LEN, f.d)) {
			k_sem_give(&cfg_tx_done);
		}
	}
}
K_THREAD_DEFINE(tx_tid, 2048, tx_thread, NULL, NULL, NULL, 6, 0, 0);

static void reply(const uint8_t *req, uint8_t status, const void *data, size_t n)
{
	struct frame f = {0};

	f.d[0] = req[0];
	f.d[1] = req[1];
	f.d[2] = status;
	if (data && n) {
		memcpy(&f.d[3], data, MIN(n, PP_CFG_REPORT_LEN - 3));
	}
	k_msgq_put(&tx_q, &f, K_MSEC(20));
}

static void handle(const uint8_t *r)
{
	uint8_t cmd = r[0];
	uint8_t t = r[2], a = r[3];

	switch (cmd) {
	case PP_CMD_GET_INFO: {
		uint8_t info[16] = {PP_VERSION, FW_MAJOR, FW_MINOR, PP_HID_AXES, 0, g_idle_min};
		uint32_t id = NRF_FICR->DEVICEID[0];

		for (int i = 1; i < N_TYPES; i++) {
			if (g_dev[i].seen) {
				info[4] |= BIT(i);
			}
		}
		memcpy(&info[6], &id, 4);
		reply(r, PP_ST_OK, info, sizeof(info));
		break;
	}
	case PP_CMD_GET_AXIS: {
		if (t < 1 || t > PP_DEV_MAX_TYPE || a >= PP_MAX_AXES_PER_DEV) {
			reply(r, PP_ST_BADARG, NULL, 0);
			break;
		}
		struct pp_axis_cfg c;
		k_spinlock_key_t k = k_spin_lock(&g_lock);
		c = g_cfg[t][a];
		k_spin_unlock(&g_lock, k);
		reply(r, PP_ST_OK, &c, sizeof(c));
		break;
	}
	case PP_CMD_SET_AXIS: {
		struct pp_axis_cfg c;

		if (t < 1 || t > PP_DEV_MAX_TYPE || a >= PP_MAX_AXES_PER_DEV) {
			reply(r, PP_ST_BADARG, NULL, 0);
			break;
		}
		memcpy(&c, &r[4], sizeof(c));
		axis_cfg_validate(&c);
		k_spinlock_key_t k = k_spin_lock(&g_lock);
		g_cfg[t][a] = c;
		g_dev[t].st[a].primed = 0;
		assign_slots_locked(t);
		k_spin_unlock(&g_lock, k);
		reply(r, PP_ST_OK, NULL, 0);
		break;
	}
	case PP_CMD_SAVE:
		reply(r, store_save() ? PP_ST_UNKNOWN : PP_ST_OK, NULL, 0);
		break;
	case PP_CMD_DEFAULTS: {
		k_spinlock_key_t k = k_spin_lock(&g_lock);
		defaults_all();
		k_spin_unlock(&g_lock, k);
		reply(r, PP_ST_OK, NULL, 0);
		break;
	}
	case PP_CMD_TARE: {
		if (t < 1 || t > PP_DEV_MAX_TYPE || a >= PP_MAX_AXES_PER_DEV || !g_dev[t].alive) {
			reply(r, PP_ST_NODEV, NULL, 0);
			break;
		}
		k_spinlock_key_t k = k_spin_lock(&g_lock);
		g_cfg[t][a].cal_min = g_dev[t].filt[a];
		k_spin_unlock(&g_lock, k);
		reply(r, PP_ST_OK, NULL, 0);
		break;
	}
	case PP_CMD_STREAM:
		stream_on = r[2] != 0;
		reply(r, PP_ST_OK, NULL, 0);
		break;
	case PP_CMD_DEV_CMD: {         /* payload: dev_type, ack_cmd, p0 */
		if (t < 1 || t > PP_DEV_MAX_TYPE) {
			reply(r, PP_ST_BADARG, NULL, 0);
			break;
		}
		k_spinlock_key_t k = k_spin_lock(&g_lock);
		g_dev[t].pend_cmd = r[3];
		g_dev[t].pend_p0 = r[4];
		if (r[3] == PP_CMD_SET_IDLE) {
			g_idle_min = r[4];
		}
		k_spin_unlock(&g_lock, k);
		reply(r, PP_ST_OK, NULL, 0);
		break;
	}
	case PP_CMD_BOOTLOADER:
		reply(r, PP_ST_OK, NULL, 0);
		k_msleep(100);
		NRF_POWER->GPREGRET = 0x57;          /* Adafruit UF2 bootloader magic */
		sys_reboot(SYS_REBOOT_COLD);
		break;
	default:
		reply(r, PP_ST_UNKNOWN, NULL, 0);
		break;
	}
}

static void rx_thread(void *a, void *b, void *c)
{
	struct frame f;
	int64_t last_save = 0;

	for (;;) {
		if (k_msgq_get(&rx_q, &f, K_MSEC(500)) == 0) {
			handle(f.d);
		}
		/* slots were auto-assigned by a new device: persist after things settle */
		if (g_dirty && k_uptime_get() - last_save > 3000) {
			last_save = k_uptime_get();
			store_save();
		}
	}
}
K_THREAD_DEFINE(rx_tid, 4096, rx_thread, NULL, NULL, NULL, 7, 0, 0);

/* live frame: [0]=PP_CMD_LIVE [1]=ctr [2]=ndev, per device:
 *   type u8, batt_20mv u8, flags u8, alive u8, pps u16, n u8, n x { raw i32, out u16 } */
void usb_cfg_enqueue_live(void)
{
	if (!stream_on || !cfg_ready || k_msgq_num_used_get(&tx_q) > 0) {
		return;
	}
	struct frame f = {0};
	int pos = 3, nd = 0;
	int64_t now = k_uptime_get();
	k_spinlock_key_t k = k_spin_lock(&g_lock);

	for (int t = 1; t < N_TYPES; t++) {
		struct dev_state *d = &g_dev[t];

		if (!d->seen) {
			continue;
		}
		int n = d->n_axes;
		bool has_diag = (d->flags & PP_FLAG_DIAG_EXT) != 0;
		int extra = has_diag ? PP_DIAG_LEN : 0;

		if (pos + 7 + 6 * n + extra > 52) {
			break;
		}
		f.d[pos++] = t;
		f.d[pos++] = d->batt_20mv;
		f.d[pos++] = d->flags;
		f.d[pos++] = (now - d->last_rx_ms) < LINK_TIMEOUT_MS;
		f.d[pos++] = d->pps & 0xFF;
		f.d[pos++] = d->pps >> 8;
		f.d[pos++] = n;
		for (int i = 0; i < n; i++) {
			memcpy(&f.d[pos], &d->raw[i], 4);
			pos += 4;
			f.d[pos++] = d->out[i] & 0xFF;
			f.d[pos++] = d->out[i] >> 8;
		}
		if (has_diag) {
			memcpy(&f.d[pos], &d->diag, PP_DIAG_LEN);
			pos += PP_DIAG_LEN;
		}
		nd++;
	}
	memcpy(&f.d[52], &g_stats.rx_ok, 4);
	memcpy(&f.d[56], &g_stats.tx_ok, 4);
	memcpy(&f.d[60], &g_stats.rx_bad, 2);
	f.d[62] = g_stats.bad_len;
	f.d[63] = g_stats.bad_b0;
	k_spin_unlock(&g_lock, k);
	f.d[0] = PP_CMD_LIVE;
	f.d[1] = live_ctr++;
	f.d[2] = nd;
	k_msgq_put(&tx_q, &f, K_NO_WAIT);
}

int usb_start(void)
{
	struct usbd_context *ctx;
	int err;

	if (!device_is_ready(pad_dev) || !device_is_ready(cfg_dev)) {
		return -ENODEV;
	}
	err = hid_device_register(pad_dev, pad_desc, sizeof(pad_desc), &pad_ops);
	if (err) {
		return err;
	}
	err = hid_device_register(cfg_dev, cfg_desc, sizeof(cfg_desc), &cfg_ops);
	if (err) {
		return err;
	}
	ctx = sample_usbd_init_device(NULL);
	if (!ctx) {
		return -ENODEV;
	}
	return usbd_enable(ctx);
}
