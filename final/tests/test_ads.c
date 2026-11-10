/* Host test of the bit-banged ads1220.c against a pin-level model of the chip (SPI mode 1, command set from the datasheet). */
#include <stdio.h>
#include <string.h>
#include "zephyr/drivers/gpio.h"
#include "../firmware_pedals/src/ads1220.h"

enum { P_CS = 24, P_SCK = 17, P_MOSI = 20, P_MISO = 22 };
enum mode { M_OK, M_FF, M_00 };
static enum mode mode;
static int cs_lvl_logical, sck, mosi, miso_out;
static int bitn, bytek, in_byte;
static uint8_t rxb[8], outb;
static uint8_t regs[4]; static int started, resets;
static int32_t next_sample;
static int errors;          /* protocol violations seen by the model */
static int fail_pin = -1;
static int fail_get_pin = -1;
static int fail_config_pin = -1;
static int force_miso_idle_low;
static int sample_edge_errors;
static int mosi_change_high_errors;
static int cs_low_frames;
static int bytes_in_frame;

static uint8_t out_for(int k)
{
	if (mode == M_FF) return 0xFF;
	if (mode == M_00) return 0x00;
	if (k == 0) return started ? (next_sample >> 16) & 0xFF : 0;
	uint8_t cmd = rxb[0];
	if (cmd == 0x00 && started) return k == 1 ? (next_sample >> 8) & 0xFF : next_sample & 0xFF;
	if ((cmd & 0xF0) == 0x20) { int reg = (cmd >> 2) & 3, cnt = (cmd & 3) + 1; return (k - 1 < cnt) ? regs[reg + k - 1] : 0; }
	return 0;
}
static void end_frame(void)
{
	if (bytes_in_frame == 0) return;
	if (bitn != 0) errors++;                         /* partial byte */
	uint8_t cmd = rxb[0];
	if (mode == M_OK) {
		if (cmd == 0x06) { memset(regs, 0, 4); started = 0; resets++; }
		else if (cmd == 0x08) started = 1;
		else if ((cmd & 0xF0) == 0x40) { int reg = (cmd >> 2) & 3, cnt = (cmd & 3) + 1; if (bytes_in_frame != cnt + 1) errors++; for (int i = 0; i < cnt; i++) regs[reg + i] = rxb[1 + i]; }
	}
	bytes_in_frame = 0;
}
bool gpio_is_ready_dt(const struct gpio_dt_spec *s) { return s->port && s->port->ready; }
int gpio_pin_configure_dt(const struct gpio_dt_spec *s, int f)
{
	if (s->pin == P_MISO && !(f & GPIO_PULL_UP)) errors++;
	return s->pin == fail_config_pin ? -EIO : 0;
}
int gpio_pin_set_dt(const struct gpio_dt_spec *s, int v)
{
	if (s->pin == fail_pin) return -EIO;
	switch (s->pin) {
	case P_CS:
		if (v && !cs_lvl_logical) { bitn = 0; bytek = 0; bytes_in_frame = 0; memset(rxb, 0, sizeof rxb); outb = out_for(0); cs_low_frames++; if (sck) errors++; }
		if (!v && cs_lvl_logical) end_frame();
		cs_lvl_logical = v; break;
	case P_MOSI:
		if (cs_lvl_logical && sck && v != mosi) mosi_change_high_errors++;
		mosi = v;
		break;
	case P_SCK:
		if (v == sck) break;
		if (!cs_lvl_logical) { sck = v; break; }
		if (v) { miso_out = (outb >> (7 - bitn)) & 1; }          /* rising: chip launches next DOUT bit */
		else {                                                    /* falling: chip latches DIN */
			in_byte = (in_byte << 1) | mosi; bitn++;
			if (bitn == 8) { rxb[bytek] = in_byte & 0xFF; bytek++; bytes_in_frame++; bitn = 0; in_byte = 0; outb = out_for(bytek); }
		}
		sck = v; break;
	}
	return 0;
}
int gpio_pin_get_dt(const struct gpio_dt_spec *s)
{
	if (s->pin == fail_get_pin) return -EIO;
	if (s->pin == P_MISO) {
		/* MISO is intentionally sampled once with CS inactive to verify its pull-up. */
		if (sck) sample_edge_errors++;
		return cs_lvl_logical ? miso_out : (force_miso_idle_low ? 0 : 1);
	}
	return 0;
}

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)

int main(void)
{
	struct device gp = {1}, gp_bad = {0};
	struct ads1220 a = {.cs = {&gp, P_CS, 0}, .sck = {&gp, P_SCK, 0}, .mosi = {&gp, P_MOSI, 0}, .miso = {&gp, P_MISO, 0}};

	mode = M_OK;
	force_miso_idle_low = 1;
	CHECK(ads1220_init(&a) == -EIO && ads_diag.err == ADS_ERR_MISO_LOW_IDLE);
	CHECK(ads_diag.miso_idle_high == 0 && cs_lvl_logical == 0);
	force_miso_idle_low = 0;
	CHECK(ads1220_init(&a) == 0);
	CHECK(ads_diag.miso_idle_high == 1);
	CHECK(started == 1 && resets == 1);
	CHECK(regs[0] == 0x0E && regs[1] == 0xC4 && regs[2] == 0xC0 && regs[3] == 0x00);
	CHECK(ads_diag.err == ADS_OK && ads_diag.regs[0] == 0x0E && ads_diag.regs[1] == 0xC4 && ads_diag.regs[2] == 0xC0);
	CHECK(errors == 0 && sample_edge_errors == 0 && mosi_change_high_errors == 0 && cs_lvl_logical == 0 && sck == 0);

	int32_t v = 0;
	next_sample = 0x123456; CHECK(ads1220_read(&a, &v) == 0 && v == 0x123456);
	next_sample = 0xFFFFFE; CHECK(ads1220_read(&a, &v) == 0 && v == -2);
	next_sample = 0x800000; CHECK(ads1220_read(&a, &v) == 0 && v == -8388608);
	next_sample = 0x7FFFFF; CHECK(ads1220_read(&a, &v) == 0 && v == 8388607);
	next_sample = 0x00A5C3; CHECK(ads1220_read(&a, &v) == 0 && v == 0xA5C3);
	CHECK(errors == 0 && sample_edge_errors == 0 && mosi_change_high_errors == 0);

	mode = M_FF; started = 0;
	CHECK(ads1220_init(&a) != 0 && ads_diag.err == ADS_ERR_REGS && ads_diag.regs[0] == 0xFF && !started);
	mode = M_00;
	CHECK(ads1220_init(&a) != 0 && ads_diag.err == ADS_ERR_REGS && ads_diag.regs[0] == 0x00 && !started);

	mode = M_OK; a.sck.port = &gp_bad;
	CHECK(ads1220_init(&a) != 0 && ads_diag.err == ADS_ERR_DEV);
	a.sck.port = &gp;
	a.cs.port = &gp_bad;
	CHECK(ads1220_init(&a) != 0 && ads_diag.err == ADS_ERR_DEV);
	a.cs.port = &gp;
	CHECK(ads1220_init(&a) == 0 && started);
	CHECK(errors == 0 && sample_edge_errors == 0 && mosi_change_high_errors == 0);

	/* Each GPIO failure must surface and leave CS inactive. */
	fail_pin = P_MOSI;
	CHECK(ads1220_read(&a, &v) == -EIO && cs_lvl_logical == 0);
	fail_pin = -1;
	fail_get_pin = P_MISO;
	CHECK(ads1220_read(&a, &v) == -EIO && cs_lvl_logical == 0);
	fail_get_pin = -1;
	fail_config_pin = P_CS;
	CHECK(ads1220_init(&a) == -EIO && ads_diag.err == ADS_ERR_DEV);
	fail_config_pin = -1;
	CHECK(ads1220_read(&a, NULL) == -EINVAL && cs_lvl_logical == 0);

	ads1220_powerdown(&a);
	CHECK(errors == 0 && sample_edge_errors == 0 && mosi_change_high_errors == 0 && cs_lvl_logical == 0 && sck == 0);

	printf(fails ? "ADS tests FAILED\n" : "ADS1220 bit-bang driver tests OK\n");
	return fails != 0;
}
