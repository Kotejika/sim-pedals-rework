#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>
int main(void)
{
	const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);
	gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE);
	for (;;) {
		gpio_pin_toggle_dt(&led);
		k_msleep(500);
	}
}
