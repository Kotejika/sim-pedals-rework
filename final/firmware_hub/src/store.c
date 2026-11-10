#include <string.h>
#include <zephyr/settings/settings.h>
#include "hub.h"

#define STORE_MAGIC 0x50454431u   /* "PED1" */

struct blob {
	uint32_t magic;
	struct pp_axis_cfg cfg[PP_MAX_AXES_PER_DEV];
};

struct pp_axis_cfg g_cfg[N_TYPES][PP_MAX_AXES_PER_DEV];
uint8_t g_idle_min = CONFIG_HUB_IDLE_MINUTES;

void defaults_all(void)
{
	for (int t = 0; t < N_TYPES; t++) {
		for (int a = 0; a < PP_MAX_AXES_PER_DEV; a++) {
			axis_defaults(&g_cfg[t][a], 0, 1000000);
		}
	}
	/* pedals: Hall axes are 14-bit (centered around ~7500 at 3.3 V), brake is ADS1220 24-bit */
	axis_defaults(&g_cfg[PP_DEV_PEDALS][PP_PEDAL_CLUTCH], 4000, 12000);
	axis_defaults(&g_cfg[PP_DEV_PEDALS][PP_PEDAL_THROTTLE], 4000, 12000);
	axis_defaults(&g_cfg[PP_DEV_PEDALS][PP_PEDAL_BRAKE], 0, 1000000);
	g_cfg[PP_DEV_PEDALS][PP_PEDAL_BRAKE].full_scale_dkg = 1000;   /* 100 kg, set by calibration */
	for (int a = 3; a < PP_MAX_AXES_PER_DEV; a++) {
		g_cfg[PP_DEV_PEDALS][a].slot = PP_SLOT_OFF;
	}
	g_idle_min = CONFIG_HUB_IDLE_MINUTES;
}

static int set_cb(const char *name, size_t len, settings_read_cb read_cb, void *cb_arg)
{
	if (name[0] == 'd' && name[1] >= '1' && name[1] <= '7' && name[2] == 0) {
		struct blob b;
		int t = name[1] - '0';

		if (len == sizeof(b) && read_cb(cb_arg, &b, sizeof(b)) == sizeof(b) &&
		    b.magic == STORE_MAGIC) {
			for (int a = 0; a < PP_MAX_AXES_PER_DEV; a++) {
				axis_cfg_validate(&b.cfg[a]);
			}
			memcpy(g_cfg[t], b.cfg, sizeof(b.cfg));
		}
		return 0;
	}
	if (!strcmp(name, "idle")) {
		uint8_t v;

		if (read_cb(cb_arg, &v, 1) == 1) {
			g_idle_min = v;
		}
	}
	return 0;
}

SETTINGS_STATIC_HANDLER_DEFINE(hub, "hub", NULL, set_cb, NULL, NULL);

int store_load(void)
{
	defaults_all();
	int err = settings_subsys_init();

	if (err) {
		return err;
	}
	return settings_load_subtree("hub");
}

int store_save(void)
{
	struct blob b;
	char key[] = "hub/dX";
	int err = 0;

	for (int t = 1; t <= PP_DEV_MAX_TYPE; t++) {
		k_spinlock_key_t k = k_spin_lock(&g_lock);

		b.magic = STORE_MAGIC;
		memcpy(b.cfg, g_cfg[t], sizeof(b.cfg));
		k_spin_unlock(&g_lock, k);
		key[5] = (char)('0' + t);
		err |= settings_save_one(key, &b, sizeof(b));
	}
	err |= settings_save_one("hub/idle", &g_idle_min, 1);
	g_dirty = false;
	return err;
}
