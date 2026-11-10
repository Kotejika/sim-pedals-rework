#pragma once
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <stdbool.h>
static inline void k_msleep(int ms) { (void)ms; }
static inline void k_busy_wait(unsigned us) { (void)us; }
