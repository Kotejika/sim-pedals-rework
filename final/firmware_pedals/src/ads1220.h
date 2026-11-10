#ifndef ADS1220_H
#define ADS1220_H
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <stdint.h>

/* Software (bit-banged) SPI mode 1 on plain GPIO: no Zephyr SPI driver, no pinctrl, nothing to fail but wiring. */
struct ads1220 {
	struct gpio_dt_spec cs, sck, mosi, miso;
};

/* what the last init attempt saw; goes into the radio packet when the chip is not OK */
enum ads_err { ADS_OK = 0, ADS_ERR_DEV = 1, ADS_ERR_REGS = 2, ADS_ERR_DRDY = 3, ADS_ERR_SPI = 4 };
struct ads_diag {
	uint8_t err;
	uint8_t regs[4];      /* REG0..REG3 as read back */
	uint8_t tries;
};
extern struct ads_diag ads_diag;

int  ads1220_init(struct ads1220 *a);          /* reset + configure + START (continuous 1000 SPS) */
int  ads1220_read(struct ads1220 *a, int32_t *out);   /* call after DRDY: signed 24-bit code */
void ads1220_powerdown(struct ads1220 *a);
#endif
