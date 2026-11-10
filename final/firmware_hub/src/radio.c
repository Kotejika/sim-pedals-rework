#include "hub.h"
#if IS_ENABLED(CONFIG_ESB)
#include <zephyr/drivers/clock_control/nrf_clock_control.h>
#include <esb.h>
#include <string.h>

static uint8_t ack_queued[N_TYPES];
static uint16_t rx_since_ack[N_TYPES];

static void queue_ack(uint8_t t)
{
	struct esb_payload ack = {.pipe = t, .length = sizeof(struct pp_ack)};
	struct pp_ack *a = (struct pp_ack *)ack.data;
	struct dev_state *d = &g_dev[t];

	a->type = PP_PKT_ACK;
	a->cmd = d->pend_cmd;
	a->p0 = d->pend_p0;
	a->p1 = 0;
	if (esb_write_payload(&ack) == 0) {
		ack_queued[t] = 1;
		rx_since_ack[t] = 0;
		d->pend_cmd = PP_CMD_NONE;
	}
}

static void on_data(const struct esb_payload *p)
{
	const struct pp_data *m = (const struct pp_data *)p->data;
	uint8_t t = p->pipe;

	/* p->data has a fixed buffer size; only p->length bytes are valid. */
	if (p->length < PP_DATA_HDR_LEN) {
		g_stats.rx_bad++;
		g_stats.bad_len = p->length;
		g_stats.bad_b0 = p->length ? p->data[0] : 0;
		return;
	}
	if (m->type != PP_PKT_DATA || t < 1 || t > PP_DEV_MAX_TYPE ||
	    m->dev_type != t || m->n_axes == 0 || m->n_axes > PP_MAX_AXES_PER_DEV) {
		g_stats.rx_bad++;
		g_stats.bad_len = p->length;
		g_stats.bad_b0 = p->data[0];
		return;
	}
	bool has_diag = (m->flags & PP_FLAG_DIAG_EXT) != 0;
	size_t expected_len = has_diag ? PP_DATA_DIAG_LEN(m->n_axes) : PP_DATA_LEN(m->n_axes);
	if (p->length != expected_len ||
	    (has_diag && (t != PP_DEV_PEDALS || m->n_axes != 3)) ||
	    (has_diag && !(m->flags & PP_FLAG_SENSOR_ERR))) {
		g_stats.rx_bad++;
		g_stats.bad_len = p->length;
		g_stats.bad_b0 = p->data[0];
		return;
	}
		g_stats.rx_bad++;
		g_stats.bad_len = p->length;
		g_stats.bad_b0 = p->data[0];
		return;
	}

	int64_t now = k_uptime_get();
	k_spinlock_key_t k = k_spin_lock(&g_lock);
	struct dev_state *d = &g_dev[t];

	if (!d->alive) {                      /* device (re)appeared: push idle timeout to it */
		d->pend_cmd = PP_CMD_SET_IDLE;
		d->pend_p0 = g_idle_min;
	}
	d->alive = true;
	d->seen = true;
	d->last_rx_ms = now;
	d->uid = m->uid;
	d->flags = m->flags;
	d->batt_20mv = m->batt_20mv;
	d->n_axes = m->n_axes;
	if (has_diag) {
		memcpy(&d->diag, &m->axes[m->n_axes * 3], sizeof(d->diag));
	} else {
		memset(&d->diag, 0, sizeof(d->diag));
	}
	for (int i = 0; i < m->n_axes; i++) {
		int32_t raw = pp_get_i24(&m->axes[i * 3]);

		d->raw[i] = raw;
		d->out[i] = axis_process(&g_cfg[t][i], &d->st[i], raw, &d->filt[i]);
	}
	assign_slots_locked(t);
	d->pkt_cnt++;
	g_stats.rx_ok++;
	k_spin_unlock(&g_lock, k);

	if (ack_queued[t] && ++rx_since_ack[t] > 100) {   /* ACK never got out: reset FIFO */
		esb_flush_tx();
		memset(ack_queued, 0, sizeof(ack_queued));
	}
	if (IS_ENABLED(CONFIG_HUB_ACK_PAYLOAD) && !ack_queued[t]) {
		queue_ack(t);
	}
}

static void esb_handler(struct esb_evt const *ev)
{
	static struct esb_payload rx;

	switch (ev->evt_id) {
	case ESB_EVENT_TX_SUCCESS:             /* an ACK payload went out */
		g_stats.tx_ok++;
		for (int t = 1; t < N_TYPES; t++) {
			ack_queued[t] = 0;
		}
		break;
	case ESB_EVENT_RX_RECEIVED:
		while (esb_read_rx_payload(&rx) == 0) {
			on_data(&rx);
		}
		break;
	default:
		break;
	}
}

static int clocks_start(void)
{
	struct onoff_manager *mgr = z_nrf_clock_control_get_onoff(CLOCK_CONTROL_NRF_SUBSYS_HF);
	struct onoff_client cli;
	int res, err;

	if (!mgr) {
		return -ENXIO;
	}
	sys_notify_init_spinwait(&cli.notify);
	err = onoff_request(mgr, &cli);
	if (err < 0) {
		return err;
	}
	do {
		err = sys_notify_fetch_result(&cli.notify, &res);
	} while (err == -EAGAIN);
	return err ? err : res;
}

int radio_start(void)
{
	uint8_t b0[4] = PP_BASE_ADDR_0, b1[4] = PP_BASE_ADDR_1, pf[8] = PP_PREFIXES;
	struct esb_config c = ESB_DEFAULT_CONFIG;
	int err;

	if ((err = clocks_start())) {
		return err;
	}
	c.protocol = ESB_PROTOCOL_ESB_DPL;
	c.mode = ESB_MODE_PRX;
	c.event_handler = esb_handler;
	c.bitrate = ESB_BITRATE_2MBPS;
	c.crc = ESB_CRC_16BIT;
	c.selective_auto_ack = false;

	if ((err = esb_init(&c)) || (err = esb_set_base_address_0(b0)) ||
	    (err = esb_set_base_address_1(b1)) || (err = esb_set_prefixes(pf, 8)) ||
	    (err = esb_set_rf_channel(PP_RF_CHANNEL))) {
		return err;
	}
	return esb_start_rx();
}

#else
int radio_start(void) { return 0; }
#endif
