/*
 * ADS1220 load-cell driver for nRF52840 Pro Micro.
 *
 * SPI mode 1: SCLK idles low, ADS1220 changes DOUT on rising edges and
 * latches DIN on falling edges. CS is active-low and is held for each full
 * command/data frame. The 1 us phase delays are conservative and within the
 * ADS1220 timing limits.
 */
#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>
#include <errno.h>
#include <limits.h>
#include "ads1220.h"

#define CMD_RESET  0x06U
#define CMD_START  0x08U
#define CMD_PWRDWN 0x02U
#define CMD_RREG(r, n) (0x20U | ((r) << 2) | ((n) - 1U))
#define CMD_WREG(r, n) (0x40U | ((r) << 2) | ((n) - 1U))

#define REG0_VAL 0x0EU /* AIN0-AIN1, gain 128, PGA enabled */
#define REG1_VAL 0xC4U /* 1000 SPS, normal mode, continuous conversion */
#define REG2_VAL 0xC0U /* AVDD reference, no 50/60 Hz notch */
#define REG3_VAL 0x00U /* No IDAC routing, dedicated DRDY mode */

#define HALF_US 1U
#define CS_SETUP_US 2U
#define CS_HOLD_US 2U

struct ads_diag ads_diag;

static int set_pin(const struct gpio_dt_spec *pin, int value)
{
    return gpio_pin_set_dt(pin, value);
}

static int sw_byte(struct ads1220 *a, uint8_t tx, uint8_t *rx)
{
    uint8_t value = 0U;

    for (int bit = 7; bit >= 0; --bit) {
        int ret = set_pin(&a->sck, 1);
        if (ret < 0) return ret;

        /* DOUT is updated by the ADC on the rising edge. */
        k_busy_wait(HALF_US);
        ret = gpio_pin_get_dt(&a->miso);
        if (ret < 0) return ret;
        value = (uint8_t)((value << 1) | (ret ? 1U : 0U));

        /* DIN is stable before the falling edge where the ADC samples it. */
        ret = set_pin(&a->mosi, (tx >> bit) & 1U);
        if (ret < 0) return ret;
        k_busy_wait(HALF_US);
        ret = set_pin(&a->sck, 0);
        if (ret < 0) return ret;
        k_busy_wait(HALF_US);
    }

    /* At least four modulator clocks between command bytes. */
    k_busy_wait(2U);
    *rx = value;
    return 0;
}

static int xfer(struct ads1220 *a, const uint8_t *tx, uint8_t *rx, size_t len)
{
    int ret;

    if (a == NULL || tx == NULL || len == 0U) return -EINVAL;

    ret = set_pin(&a->sck, 0);
    if (ret < 0) return ret;
    ret = set_pin(&a->mosi, 0);
    if (ret < 0) return ret;

    /* gpio_pin_set_dt takes logical levels: 1 asserts ACTIVE_LOW CS. */
    ret = set_pin(&a->cs, 1);
    if (ret < 0) {
        (void)set_pin(&a->cs, 0);
        return ret;
    }
    k_busy_wait(CS_SETUP_US);

    for (size_t i = 0U; i < len; ++i) {
        uint8_t value = 0U;
        ret = sw_byte(a, tx[i], &value);
        if (ret < 0) break;
        if (rx != NULL) rx[i] = value;
    }

    /* Leave the bus in its idle state and release CS even after a GPIO error. */
    {
        int low_ret = set_pin(&a->sck, 0);
        int data_ret = set_pin(&a->mosi, 0);
        k_busy_wait(CS_HOLD_US);
        int cs_ret = set_pin(&a->cs, 0);
        if (ret == 0 && low_ret < 0) ret = low_ret;
        if (ret == 0 && data_ret < 0) ret = data_ret;
        if (ret == 0 && cs_ret < 0) ret = cs_ret;
    }
    return ret;
}

static int configure_pins(struct ads1220 *a)
{
    int ret;

    if (a == NULL || !gpio_is_ready_dt(&a->cs) ||
        !gpio_is_ready_dt(&a->sck) || !gpio_is_ready_dt(&a->mosi) ||
        !gpio_is_ready_dt(&a->miso)) {
        return -ENODEV;
    }

    ret = gpio_pin_configure_dt(&a->cs, GPIO_OUTPUT_INACTIVE);
    if (ret < 0) return ret;
    ret = gpio_pin_configure_dt(&a->sck, GPIO_OUTPUT_INACTIVE);
    if (ret < 0) return ret;
    ret = gpio_pin_configure_dt(&a->mosi, GPIO_OUTPUT_INACTIVE);
    if (ret < 0) return ret;
    return gpio_pin_configure_dt(&a->miso, GPIO_INPUT);
}

int ads1220_init(struct ads1220 *a)
{
    const uint8_t reset = CMD_RESET;
    const uint8_t start = CMD_START;
    const uint8_t write_regs[5] = {
        CMD_WREG(0U, 4U), REG0_VAL, REG1_VAL, REG2_VAL, REG3_VAL
    };
    const uint8_t read_regs[5] = {CMD_RREG(0U, 4U), 0U, 0U, 0U, 0U};
    uint8_t back[5] = {0U};
    int ret;

    if (ads_diag.tries < UINT8_MAX) ads_diag.tries++;
    ads_diag.err = ADS_ERR_DEV;
    for (int i = 0; i < 4; ++i) ads_diag.regs[i] = 0U;

    ret = configure_pins(a);
    if (ret < 0) {
        ads_diag.err = ADS_ERR_DEV;
        return ret;
    }

    /* Caller enables sensor rail first. Wait for power and oscillator startup. */
    k_msleep(5);
    ret = xfer(a, &reset, NULL, 1U);
    if (ret < 0) {
        ads_diag.err = ADS_ERR_SPI;
        return ret;
    }
    /* RESET requires 50 us + 32 tCLK; 2 ms is deliberately conservative. */
    k_msleep(2);

    ret = xfer(a, write_regs, NULL, sizeof(write_regs));
    if (ret < 0) {
        ads_diag.err = ADS_ERR_SPI;
        return ret;
    }
    ret = xfer(a, read_regs, back, sizeof(read_regs));
    if (ret < 0) {
        ads_diag.err = ADS_ERR_SPI;
        return ret;
    }

    for (int i = 0; i < 4; ++i) ads_diag.regs[i] = back[i + 1];
    if (back[1] != REG0_VAL || back[2] != REG1_VAL ||
        back[3] != REG2_VAL || back[4] != REG3_VAL) {
        ads_diag.err = ADS_ERR_REGS;
        return -EIO;
    }

    ret = xfer(a, &start, NULL, 1U);
    if (ret < 0) {
        ads_diag.err = ADS_ERR_SPI;
        return ret;
    }

    ads_diag.err = ADS_OK;
    return 0;
}

int ads1220_read(struct ads1220 *a, int32_t *out)
{
    const uint8_t tx[3] = {0U, 0U, 0U};
    uint8_t rx[3] = {0U, 0U, 0U};
    int32_t value;
    int ret;

    if (out == NULL) return -EINVAL;
    ret = xfer(a, tx, rx, sizeof(tx));
    if (ret < 0) return ret;

    value = ((int32_t)rx[0] << 16) |
            ((int32_t)rx[1] << 8) |
            (int32_t)rx[2];
    if ((value & 0x800000) != 0) value -= 0x1000000;
    *out = value;
    return 0;
}

void ads1220_powerdown(struct ads1220 *a)
{
    const uint8_t cmd = CMD_PWRDWN;
    (void)xfer(a, &cmd, NULL, 1U);
}
