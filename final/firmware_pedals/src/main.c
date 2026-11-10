/*
 * Sim pedals firmware (nRF52840, NCS 3.1.x / Zephyr)
 *   clutch, throttle : SS49E Hall sensors -> SAADC (14-bit, 16x oversampling)
 *   brake            : ADS1220 + load cell, 24-bit, 1000 SPS (drives the packet rate)
 *   link             : Nordic ESB PTX, 2 Mbps, one packet per ADS1220 sample (~1 kHz)
 *   power            : System OFF after idle timeout (default 20 min), wake by button / reset
 */
#include <stdlib.h>
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/clock_control/nrf_clock_control.h>
#include <zephyr/sys/poweroff.h>
#include <nrf.h>
#include <esb.h>
#include "pedal_protocol.h"
#include "ads1220.h"

#define ZU DT_PATH(zephyr_user)

static const struct gpio_dt_spec g_drdy  = GPIO_DT_SPEC_GET(ZU, drdy_gpios);
static const struct gpio_dt_spec g_wake  = GPIO_DT_SPEC_GET(ZU, wake_gpios);
static const struct gpio_dt_spec g_power = GPIO_DT_SPEC_GET(ZU, power_gpios);
static const struct gpio_dt_spec g_led   = GPIO_DT_SPEC_GET(ZU, led_gpios);

static const struct adc_dt_spec adc_clutch   = ADC_DT_SPEC_GET_BY_IDX(ZU, 0);
static const struct adc_dt_spec adc_throttle = ADC_DT_SPEC_GET_BY_IDX(ZU, 1);
static const struct adc_dt_spec adc_batt     = ADC_DT_SPEC_GET_BY_IDX(ZU, 2);

static struct ads1220 ads = {
	.cs = GPIO_DT_SPEC_GET(ZU, cs_gpios),
	.sck = GPIO_DT_SPEC_GET(ZU, sck_gpios),
	.mosi = GPIO_DT_SPEC_GET(ZU, mosi_gpios),
	.miso = GPIO_DT_SPEC_GET(ZU, miso_gpios),
};

static K_SEM_DEFINE(drdy_sem, 0, 1);
static struct gpio_callback drdy_cb;

static volatile uint32_t idle_minutes = CONFIG_PEDALS_IDLE_MINUTES;
static volatile bool sleep_requested;
static volatile int64_t last_ack_ms;

/* ---------------------------------------------------------------- power rail */
static void rail(bool on)
{
#if CONFIG_PEDALS_EXT_POWER_CTRL
	bool level = on ? IS_ENABLED(CONFIG_PEDALS_EXT_POWER_ACTIVE_HIGH)
			: !IS_ENABLED(CONFIG_PEDALS_EXT_POWER_ACTIVE_HIGH);
	gpio_pin_configure_dt(&g_power, GPIO_OUTPUT);
	gpio_pin_set_raw(g_power.port, g_power.pin, level ? 1 : 0);
#else
	ARG_UNUSED(on);
#endif
}

static void go_sleep(void)
{
	ads1220_powerdown(&ads);
	esb_disable();
	gpio_pin_set_dt(&g_led, 0);
	rail(false);

	/* wake on button press (also: the reset button always wakes the board) */
	while (gpio_pin_get_dt(&g_wake) > 0) {
		k_msleep(20);                 /* wait until released, else instant wake */
	}
	gpio_pin_configure_dt(&g_wake, GPIO_INPUT);
	gpio_pin_interrupt_configure_dt(&g_wake, GPIO_INT_LEVEL_ACTIVE);
	sys_poweroff();
}

/* ---------------------------------------------------------------- ESB */
static void esb_handler(struct esb_evt const *ev)
{
	static struct esb_payload rx;

	switch (ev->evt_id) {
	case ESB_EVENT_TX_FAILED:
		esb_flush_tx();      /* never retry stale data */
		break;
	case ESB_EVENT_RX_RECEIVED:
		while (esb_read_rx_payload(&rx) == 0) {
			if (rx.length >= sizeof(struct pp_ack) && rx.data[0] == PP_PKT_ACK) {
				const struct pp_ack *a = (const struct pp_ack *)rx.data;

				last_ack_ms = k_uptime_get();
				if (a->cmd == PP_CMD_SLEEP) {
					sleep_requested = true;
				} else if (a->cmd == PP_CMD_SET_IDLE) {
					idle_minutes = a->p0;
				}
			}
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

static int radio_init(void)
{
	uint8_t b0[4] = PP_BASE_ADDR_0, b1[4] = PP_BASE_ADDR_1, pf[8] = PP_PREFIXES;
	struct esb_config c = ESB_DEFAULT_CONFIG;
	int err;

	c.protocol = ESB_PROTOCOL_ESB_DPL;
	c.mode = ESB_MODE_PTX;
	c.event_handler = esb_handler;
	c.bitrate = ESB_BITRATE_2MBPS;
	c.crc = ESB_CRC_16BIT;
	c.tx_output_power = 4;
	c.retransmit_delay = 600;   /* must exceed packet + ACK airtime incl. radio ramp-up */
	c.retransmit_count = 1;
	c.tx_mode = ESB_TXMODE_AUTO;
	c.selective_auto_ack = false;

	if ((err = esb_init(&c)) || (err = esb_set_base_address_0(b0)) ||
	    (err = esb_set_base_address_1(b1)) || (err = esb_set_prefixes(pf, 8)) ||
	    (err = esb_set_rf_channel(PP_RF_CHANNEL))) {
		return err;
	}
	return 0;
}

/* ---------------------------------------------------------------- sensors */
static int read_adc14(const struct adc_dt_spec *s)
{
	int16_t buf = 0;
	struct adc_sequence seq = {.buffer = &buf, .buffer_size = sizeof(buf)};

	adc_sequence_init_dt(s, &seq);
	if (adc_read_dt(s, &seq)) {
		return -1;
	}
	return buf < 0 ? 0 : buf;
}

static uint8_t read_batt_20mv(void)
{
#if CONFIG_PEDALS_BATT_SENSE
	int16_t buf = 0;
	struct adc_sequence seq = {.buffer = &buf, .buffer_size = sizeof(buf)};
	int32_t mv;

	adc_sequence_init_dt(&adc_batt, &seq);
	if (adc_read_dt(&adc_batt, &seq)) {
		return 0;
	}
	mv = buf;
	if (adc_raw_to_millivolts_dt(&adc_batt, &mv)) {
		return 0;
	}
	mv *= 2;                         /* on-board 1:1 divider (verify with a multimeter) */
	if (mv < 2500) {
		return 0;                /* no battery connected: unknown, no LOW flag */
	}
	return (uint8_t)MIN(mv / 20, 255);
#else
	return 0;
#endif
}

static void drdy_isr(const struct device *d, struct gpio_callback *cb, uint32_t pins)
{
	k_sem_give(&drdy_sem);
}

/* LED: no link = 100 ms blip every 0.5 s; link OK = one 15 ms blip every 3 s;
 * link OK but ADS1220 not working = two blips every 3 s */
static void led_blip(bool linked, bool ads_ok, int64_t now)
{
	if (!linked) {
		gpio_pin_set_dt(&g_led, (now % 500) < 100);
		return;
	}
	int64_t p = now % 3000;

	gpio_pin_set_dt(&g_led, p < 15 || (!ads_ok && p >= 150 && p < 165));
}

int main(void)
{
	bool ads_ok = false;
	int32_t brake = 0;
	uint8_t seq = 0, batt = 0;
	int ref_c = 0, ref_t = 0;
	int32_t ref_b = 0;
	uint32_t n = 0, miss = 0;
	int64_t last_act = k_uptime_get(), last_ads_try = -1000;
	struct esb_payload tx = {.pipe = PP_DEV_PEDALS};
	struct pp_data *pkt = (struct pp_data *)tx.data;

	gpio_pin_configure_dt(&g_led, GPIO_OUTPUT_INACTIVE);
	gpio_pin_configure_dt(&g_wake, GPIO_INPUT);
	gpio_pin_configure_dt(&g_drdy, GPIO_INPUT);
	rail(true);
	k_msleep(50);                            /* sensors power up */

	adc_channel_setup_dt(&adc_clutch);
	adc_channel_setup_dt(&adc_throttle);
#if CONFIG_PEDALS_BATT_SENSE
	adc_channel_setup_dt(&adc_batt);
#endif

	if (clocks_start() || radio_init()) {
		/* radio failed: fast LED, nothing else to do */
		for (;;) {
			gpio_pin_toggle_dt(&g_led);
			k_msleep(80);
		}
	}

	gpio_init_callback(&drdy_cb, drdy_isr, BIT(g_drdy.pin));
	gpio_add_callback(g_drdy.port, &drdy_cb);
	gpio_pin_interrupt_configure_dt(&g_drdy, GPIO_INT_EDGE_TO_ACTIVE);

	ref_c = read_adc14(&adc_clutch);
	ref_t = read_adc14(&adc_throttle);
	batt = read_batt_20mv();

	pkt->type = PP_PKT_DATA;
	pkt->dev_type = PP_DEV_PEDALS;
	pkt->uid = NRF_FICR->DEVICEID[0];

	for (;;) {
		int64_t now = k_uptime_get();

		/* (re)initialise the ADS1220 every 500 ms while it is not OK */
		if (!ads_ok && now - last_ads_try >= 500) {
			last_ads_try = now;
			ads_ok = (ads1220_init(&ads) == 0);
			miss = 0;
		}

		/* The radio never waits for the sensor chip: at most 2 ms per loop. */
		bool got = k_sem_take(&drdy_sem, K_MSEC(2)) == 0;
		bool drdy_active = gpio_pin_get_dt(&g_drdy) > 0; /* ACTIVE_LOW: active means physical LOW */

		if (ads_ok) {
			if (got || drdy_active) {
				miss = 0;
				if (ads1220_read(&ads, &brake)) {
					ads_ok = false;
					ads_diag.err = ADS_ERR_SPI;
				}
			} else if (++miss > 20) {            /* ~40 ms without a conversion */
				ads_ok = false;
				ads_diag.err = ADS_ERR_DRDY;
			}
		}
		if (!ads_ok) {
			brake = 0;
		}

		int c = read_adc14(&adc_clutch);
		int t = read_adc14(&adc_throttle);

		if (c < 0) c = 0;
		if (t < 0) t = 0;

		if ((n++ % 1000) == 0) {
			batt = read_batt_20mv();
		}

		/* activity detection */
		if (abs(c - ref_c) > CONFIG_PEDALS_ACTIVITY_THR_ADC) { ref_c = c; last_act = now; }
		if (abs(t - ref_t) > CONFIG_PEDALS_ACTIVITY_THR_ADC) { ref_t = t; last_act = now; }
		if (ads_ok && abs(brake - ref_b) > CONFIG_PEDALS_ACTIVITY_THR_ADS) {
			ref_b = brake; last_act = now;
		}

		pkt->seq = seq++;
		pkt->flags = (ads_ok ? 0 : (PP_FLAG_SENSOR_ERR | PP_FLAG_DIAG_EXT)) |
			     ((batt && batt * 20 < CONFIG_PEDALS_LOW_BATT_MV) ? PP_FLAG_LOW_BATT : 0);
		pkt->batt_20mv = batt;
		pkt->n_axes = 3; /* clutch, throttle, brake only */
		pp_put_i24(&pkt->axes[0], c);
		pp_put_i24(&pkt->axes[3], t);
		pp_put_i24(&pkt->axes[6], brake);
		if (!ads_ok) {
			struct pp_diag *diag = (struct pp_diag *)&pkt->axes[9];
			diag->error = ads_diag.err;
			diag->drdy_active = drdy_active ? 1 : 0;
			diag->tries = ads_diag.tries;
			memcpy(diag->regs, ads_diag.regs, sizeof(diag->regs));
		}
		tx.length = ads_ok ? PP_DATA_LEN(pkt->n_axes) : PP_DATA_DIAG_LEN(pkt->n_axes);

		if (esb_tx_full()) {
			esb_flush_tx();
		}
		esb_write_payload(&tx);

		led_blip((now - last_ack_ms) < 300, ads_ok, now);

		if (sleep_requested ||
		    (idle_minutes && (now - last_act) > (int64_t)idle_minutes * 60000)) {
			go_sleep();
		}
	}
	return 0;
}
