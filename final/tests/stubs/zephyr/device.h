#pragma once
#include <zephyr/kernel.h>
struct device { int ready; };
static inline bool device_is_ready(const struct device *d) { return d && d->ready; }
