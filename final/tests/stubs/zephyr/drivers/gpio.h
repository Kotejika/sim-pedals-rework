#pragma once
#include <zephyr/device.h>
struct gpio_dt_spec { const struct device *port; int pin; int flags; };
#define GPIO_OUTPUT_INACTIVE 1
#define GPIO_INPUT 2
bool gpio_is_ready_dt(const struct gpio_dt_spec *s);
int gpio_pin_configure_dt(const struct gpio_dt_spec *s, int f);
int gpio_pin_set_dt(const struct gpio_dt_spec *s, int v);
int gpio_pin_get_dt(const struct gpio_dt_spec *s);
