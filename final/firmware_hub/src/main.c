#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/fatal.h>
#include <zephyr/sys/reboot.h>
#include "hub.h"

/* ---- crash diagnostics (no serial available): last stage + fault reason survive a soft reset ---- */
struct dbg { uint32_t magic, stage, reason, pc; };
static __noinit struct dbg dbg;
#define DBG_MAGIC 0xD3B6C0DEu
#define STAGE(n) (dbg.stage = (n))

void k_sys_fatal_error_handler(unsigned int reason, const struct arch_esf *esf)
{
	dbg.magic = DBG_MAGIC;
	dbg.reason = reason + 1;
	dbg.pc = esf ? esf->basic.pc : 0;
	sys_reboot(SYS_REBOOT_COLD);
}

struct dev_state g_dev[N_TYPES];
bool g_dirty;
struct k_spinlock g_lock;
struct hub_stats g_stats;

static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(DT_PATH(zephyr_user), led_gpios);

/* Give every axis of a freshly seen device the next free HID axis (needs g_lock). */
void assign_slots_locked(uint8_t t)
{
	struct dev_state *d = &g_dev[t];

	for (int a = 0; a < d->n_axes && a < PP_MAX_AXES_PER_DEV; a++) {
		if (g_cfg[t][a].slot != PP_SLOT_AUTO) {
			continue;
		}
		uint8_t used = 0;

		for (int tt = 1; tt < N_TYPES; tt++) {
			for (int aa = 0; aa < PP_MAX_AXES_PER_DEV; aa++) {
				if (g_cfg[tt][aa].slot < PP_HID_AXES) {
					used |= BIT(g_cfg[tt][aa].slot);
				}
			}
		}
		for (int s = 0; s < PP_HID_AXES; s++) {
			if (!(used & BIT(s))) {
				g_cfg[t][a].slot = s;
				g_dirty = true;
				break;
			}
		}
		if (g_cfg[t][a].slot == PP_SLOT_AUTO) {
			g_cfg[t][a].slot = PP_SLOT_OFF;        /* out of HID axes */
		}
	}
}

void hub_outputs(uint16_t axes[PP_HID_AXES], int64_t now)
{
	k_spinlock_key_t k = k_spin_lock(&g_lock);

	for (int i = 0; i < PP_HID_AXES; i++) {
		axes[i] = 0;
	}
	for (int t = 1; t < N_TYPES; t++) {
		struct dev_state *d = &g_dev[t];
		bool ok = d->seen && (now - d->last_rx_ms) < LINK_TIMEOUT_MS;

		if (now - d->pps_t0 >= 1000) {          /* packets per second, also falls to 0 */
			d->pps = d->pkt_cnt;
			d->pkt_cnt = 0;
			d->pps_t0 = now;
		}

		if (!ok && d->alive) {
			d->alive = false;                   /* link lost -> re-push settings on return */
		}
		for (int a = 0; a < d->n_axes; a++) {
			uint8_t s = g_cfg[t][a].slot;

			if (s < PP_HID_AXES) {
				axes[s] = ok ? d->out[a] : 0;     /* failsafe: zero */
			}
		}
	}
	k_spin_unlock(&g_lock, k);
}

static void blinks(int n, int on_ms, int off_ms)
{
	for (int i = 0; i < n; i++) {
		gpio_pin_set_dt(&led, 1);
		k_msleep(on_ms);
		gpio_pin_set_dt(&led, 0);
		k_msleep(off_ms);
	}
}

static void blink_code(int n)
{
	/* n quick blinks, long pause, forever (USB keeps running in its own threads) */
	for (;;) {
		blinks(n, 150, 250);
		k_msleep(1500);
	}
}

/* After a crash: stage blinks (slow), pause, reason blinks (fast), 3 times, then try again. */
static void show_crash(void)
{
	int rc = dbg.reason < 6 ? (int)dbg.reason : 6 + MIN((int)dbg.reason - 17, 3);

	for (int r = 0; r < 3; r++) {
		blinks(dbg.stage, 500, 500);
		k_msleep(1500);
		blinks(rc, 120, 200);
		k_msleep(3000);
	}
	dbg.reason = 0;
}

static void diag_loop(int level)
{
	/* distinguishable patterns for the bring-up ladder */
	for (;;) {
		switch (level) {
		case 0: blinks(1, 500, 500); break;                       /* 1 Hz symmetric */
		case 1: blinks(1, 60, 1940); break;                       /* one blip per 2 s */
		default: blinks(2, 60, 200); k_msleep(1500); break;       /* double blip per 2 s */
		}
	}
}

int main(void)
{
	gpio_pin_configure_dt(&led, GPIO_OUTPUT_ACTIVE);   /* solid LED = app is running */
	if (dbg.magic != DBG_MAGIC) {
		dbg.magic = DBG_MAGIC;
		dbg.stage = 0;
		dbg.reason = 0;
	} else if (dbg.reason) {
		show_crash();
	}
	STAGE(1);
	defaults_all();
	if (CONFIG_HUB_DIAG_LEVEL == 0) {
		diag_loop(0);
	}

	/* USB first, so the PC sees the device even if flash/radio init has problems */
	STAGE(2);
	int err = usb_start();
	if (err) {
		blink_code(1);
	}
	STAGE(3);
	if (CONFIG_HUB_DIAG_LEVEL == 1) {
		diag_loop(1);
	}
	k_msleep(300);
	STAGE(4);
	store_load();                   /* settings from flash (defaults if empty) */
	if (CONFIG_HUB_DIAG_LEVEL == 2) {
		diag_loop(2);
	}

	STAGE(5);
	err = radio_start();
	if (err) {
		blink_code(2);
	}
	STAGE(6);
	for (;;) {                      /* slow heartbeat + live stream to the PC app (100 Hz) */
		for (int i = 0; i < 100; i++) {
			usb_cfg_enqueue_live();
			k_msleep(10);
		}
		gpio_pin_toggle_dt(&led);
	}
	return 0;
}
