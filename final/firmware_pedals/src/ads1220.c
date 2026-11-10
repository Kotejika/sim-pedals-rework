/*
 * ADS1220 GPIO-SPI driver for a 4-wire load cell (NA151 etc.).
 * SPI mode 1: SCLK idles LOW, DIN is latched on falling edges, DOUT shifts
 * on rising edges. DIN is set while SCLK is low; DOUT is sampled after fall.
 * Every GPIO call is checked and the bus is returned to idle on failure.
 */
#include <errno.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>
#include "ads1220.h"

#define CMD_RESET  0x06
#define CMD_START  0x08
#define CMD_PWRDWN 0x02
#define CMD_RREG(r, n) (0x20 | ((r) << 2) | ((n) - 1))
#define CMD_WREG(r, n) (0x40 | ((r) << 2) | ((n) - 1))
#define REG0_VAL 0x0E /* AIN0-AIN1, gain 128, PGA enabled */
#define REG1_VAL 0xC4 /* 1000 SPS, normal mode, continuous conversion */
#define REG2_VAL 0xC0 /* AVDD/AVSS reference */
#define REG3_VAL 0x00 /* no IDAC routing, dedicated DRDY mode */
#define HALF_US 2     /* moderate timing margin; GPIO call overhead lowers the real SCLK further */

struct ads_diag ads_diag;

static int sw_byte(struct ads1220 *a, uint8_t out, uint8_t *in)
{
	uint8_t value = 0;
	for (int i = 7; i >= 0; --i) {
		int ret = gpio_pin_set_dt(&a->mosi, (out >> i) & 1);
		if (ret < 0) return ret;
		k_busy_wait(HALF_US);
		ret = gpio_pin_set_dt(&a->sck, 1);
		if (ret < 0) return ret;
		k_busy_wait(HALF_US);
		ret = gpio_pin_set_dt(&a->sck, 0);
		if (ret < 0) return ret;
		k_busy_wait(HALF_US);
		/* DOUT has already shifted on rising edge and remains stable through falling edge. */
		ret = gpio_pin_get_dt(&a->miso);
		if (ret < 0) return ret;
		value = (uint8_t)((value << 1) | (ret ? 1U : 0U));
	}
	/* A short margin between bytes; ADS1220 specifies a maximum SPI timeout, not a 4-tCLK minimum. */
	k_busy_wait(2);
	*in = value;
	return 0;
}

static int xfer(struct ads1220 *a, const uint8_t *tx, uint8_t *rx, size_t n)
{
	int ret = gpio_pin_set_dt(&a->sck, 0);
	if (ret < 0) return ret;
	ret = gpio_pin_set_dt(&a->mosi, 0);
	if (ret < 0) return ret;
	ret = gpio_pin_set_dt(&a->cs, 1); /* logical active = physical LOW (GPIO_ACTIVE_LOW) */
	if (ret < 0) {
		(void)gpio_pin_set_dt(&a->cs, 0);
		return ret;
	}
	k_busy_wait(2); /* CS setup margin, greater than the TI 50-ns minimum */

	for (size_t i = 0; i < n; ++i) {
		uint8_t value = 0;
		ret = sw_byte(a, tx[i], &value);
		if (ret < 0) break;
		if (rx != NULL) rx[i] = value;
	}

	/* Always deassert CS and restore idle clock/data even after transfer failure. */
	int sck_ret = gpio_pin_set_dt(&a->sck, 0);
	int mosi_ret = gpio_pin_set_dt(&a->mosi, 0);
	int cs_ret = gpio_pin_set_dt(&a->cs, 0); /* logical inactive = physical HIGH */
	if (ret == 0 && sck_ret < 0) ret = sck_ret;
	if (ret == 0 && mosi_ret < 0) ret = mosi_ret;
	if (ret == 0 && cs_ret < 0) ret = cs_ret;
	k_busy_wait(2); /* CS hold margin, greater than the TI 25-ns minimum */
	return ret;
}

int ads1220_init(struct ads1220 *a)
{
	const uint8_t reset = CMD_RESET, start = CMD_START;
	const uint8_t write_regs[5] = {CMD_WREG(0, 4), REG0_VAL, REG1_VAL, REG2_VAL, REG3_VAL};
	const uint8_t read_regs[5] = {CMD_RREG(0, 4), 0, 0, 0, 0};
	uint8_t back[5] = {0};
	int ret;

	if (ads_diag.tries < UINT8_MAX) ads_diag.tries++;
	ads_diag.err = ADS_ERR_DEV;
	memset(ads_diag.regs, 0, sizeof(ads_diag.regs));
	if (!gpio_is_ready_dt(&a->cs) || !gpio_is_ready_dt(&a->sck) ||
	    !gpio_is_ready_dt(&a->mosi) || !gpio_is_ready_dt(&a->miso)) return -ENODEV;

	ret = gpio_pin_configure_dt(&a->cs, GPIO_OUTPUT_INACTIVE);
	if (ret < 0) return ret;
	ret = gpio_pin_configure_dt(&a->sck, GPIO_OUTPUT_INACTIVE);
	if (ret < 0) return ret;
	ret = gpio_pin_configure_dt(&a->mosi, GPIO_OUTPUT_INACTIVE);
	if (ret < 0) return ret;
	ret = gpio_pin_configure_dt(&a->miso, GPIO_INPUT);
	if (ret < 0) return ret;

	/* Datasheet power-up requirement is 2^14 master clocks; rail was enabled first. */
	k_msleep(5);
	ret = xfer(a, &reset, NULL, 1);
	if (ret < 0) { ads_diag.err = ADS_ERR_SPI; return ret; }
	k_msleep(2);
	ret = xfer(a, write_regs, NULL, sizeof(write_regs));
	if (ret < 0) { ads_diag.err = ADS_ERR_SPI; return ret; }
	k_busy_wait(20);
	ret = xfer(a, read_regs, back, sizeof(read_regs));
	if (ret < 0) { ads_diag.err = ADS_ERR_SPI; return ret; }

	for (int i = 0; i < 4; ++i) ads_diag.regs[i] = back[i + 1];
	if (back[1] != REG0_VAL || back[2] != REG1_VAL ||
	    back[3] != REG2_VAL || back[4] != REG3_VAL) {
		ads_diag.err = ADS_ERR_REGS;
		return -EIO;
	}
	ret = xfer(a, &start, NULL, 1);
	if (ret < 0) { ads_diag.err = ADS_ERR_SPI; return ret; }
	ads_diag.err = ADS_OK;
	return 0;
}

int ads1220_read(struct ads1220 *a, int32_t *out)
{
	const uint8_t tx[3] = {0, 0, 0};
	uint8_t rx[3] = {0, 0, 0};
	int ret;
	if (out == NULL) return -EINVAL;
	/* Continuous conversion: after DRDY falls, read data directly; no RDATA command. */
	ret = xfer(a, tx, rx, sizeof(tx));
	if (ret < 0) return ret;
	int32_t value = ((int32_t)rx[0] << 16) | ((int32_t)rx[1] << 8) | (int32_t)rx[2];
	if (value & 0x800000) value -= 0x1000000;
	*out = value;
	return 0;
}

void ads1220_powerdown(struct ads1220 *a)
{
	const uint8_t cmd = CMD_PWRDWN;
	(void)xfer(a, &cmd, NULL, 1);
}
