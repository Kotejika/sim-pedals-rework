/*
 * Shared protocol definitions: pedals <-> hub (ESB radio) and hub <-> PC (HID config).
 * Python mirror: pc_app/protocol.py  (keep both in sync, tests/ checks it)
 */
#ifndef PEDAL_PROTOCOL_H
#define PEDAL_PROTOCOL_H

#include <stdint.h>

#define PP_VERSION 1

/* ---------------- Radio (ESB) ---------------- */
#define PP_RF_CHANNEL      80        /* 2400 + 80 = 2480 MHz, above Wi-Fi ch.11 */
#define PP_BASE_ADDR_0     {0xA7, 0x3C, 0x5E, 0x91}   /* pipe 0 (unused, must be set) */
#define PP_BASE_ADDR_1     {0x4B, 0x7D, 0xE2, 0x19}   /* pipes 1..7 */
#define PP_PREFIXES        {0xE7, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7}

/* Device type == ESB pipe number (1..7). One device of each type per hub. */
enum pp_dev_type {
	PP_DEV_PEDALS  = 1,   /* clutch, throttle, brake */
	PP_DEV_SHIFTER = 2,   /* reserved */
	PP_DEV_BUTTONS = 3,   /* reserved */
	PP_DEV_HANDBRAKE = 4, /* reserved */
	PP_DEV_MAX_TYPE = 7,
};

#define PP_MAX_AXES_PER_DEV 6

#define PP_PKT_DATA  0xA1  /* device -> hub */
#define PP_PKT_ACK   0xB1  /* hub -> device (ACK payload) */

/* flags in pp_data.flags */
#define PP_FLAG_LOW_BATT   (1u << 0)
#define PP_FLAG_SENSOR_ERR (1u << 1)   /* ADS1220 / ADC not answering */
#define PP_FLAG_SLEEPING   (1u << 2)   /* last packet before System OFF */
#define PP_FLAG_DIAG_EXT   (1u << 3)   /* PP_DIAG_LEN bytes follow the real axes */

/* ADS1220 diagnostics are metadata, never additional game-controller axes. */
#define PP_DIAG_LEN 7
struct __attribute__((packed)) pp_diag {
	uint8_t error;          /* 0 OK, 1 GPIO/device, 2 register mismatch, 3 DRDY, 4 SPI */
	uint8_t drdy_active;    /* 1 = active; for ACTIVE_LOW this means physical LOW */
	uint8_t tries;          /* saturating number of init attempts */
	uint8_t regs[4];        /* ADS1220 register 0..3 readback */
};

/* pedals axis order */
#define PP_PEDAL_CLUTCH   0   /* 14-bit, 0..16383 raw (SAADC, 16x oversampled) */
#define PP_PEDAL_THROTTLE 1   /* 14-bit, 0..16383 */
#define PP_PEDAL_BRAKE    2   /* 24-bit signed ADS1220 code, gain 128 */

struct __attribute__((packed)) pp_data {
	uint8_t  type;        /* PP_PKT_DATA */
	uint8_t  seq;
	uint8_t  dev_type;
	uint8_t  flags;
	uint32_t uid;         /* FICR DEVICEID[0], LE */
	uint8_t  batt_20mv;   /* battery voltage in units of 20 mV (0 = unknown) */
	uint8_t  n_axes;
	uint8_t  axes[PP_MAX_AXES_PER_DEV * 3];   /* int24 little endian each */
};
#define PP_DATA_HDR_LEN 10
#define PP_DATA_LEN(n)  (PP_DATA_HDR_LEN + 3 * (n))
#define PP_DATA_DIAG_LEN(n) (PP_DATA_LEN(n) + PP_DIAG_LEN)

enum pp_ack_cmd {
	PP_CMD_NONE = 0,
	PP_CMD_SLEEP = 1,          /* go to System OFF now */
	PP_CMD_SET_IDLE = 2,       /* p0 = idle minutes (0 = never sleep) */
};

struct __attribute__((packed)) pp_ack {
	uint8_t type;   /* PP_PKT_ACK */
	uint8_t cmd;
	uint8_t p0;
	uint8_t p1;
};

static inline int32_t pp_get_i24(const uint8_t *p)
{
	int32_t v = (int32_t)p[0] | ((int32_t)p[1] << 8) | ((int32_t)p[2] << 16);
	if (v & 0x800000) {
		v -= 0x1000000;
	}
	return v;
}

static inline void pp_put_i24(uint8_t *p, int32_t v)
{
	p[0] = (uint8_t)(v);
	p[1] = (uint8_t)(v >> 8);
	p[2] = (uint8_t)(v >> 16);
}

/* ---------------- USB: hub <-> PC (vendor HID, 64-byte reports) ---------------- */
#define PP_USB_VID 0x1209    /* pid.codes open-source VID. Replace if you ever sell this. */
#define PP_USB_PID_HUB 0x5E00   /* TEST PID range of pid.codes - change if it clashes */
#define PP_CFG_USAGE_PAGE 0xFF00
#define PP_CFG_REPORT_LEN 64

/* Request (PC -> hub): [0]=cmd [1]=token [2..63]=payload
 * Response (hub -> PC): [0]=cmd [1]=token [2]=status(0=ok) [3..63]=data
 * LIVE (hub -> PC unsolicited): [0]=PP_CMD_LIVE [1]=frame counter [2]=ndev, records... */
enum pp_cfg_cmd {
	PP_CMD_GET_INFO   = 0x01,
	PP_CMD_GET_AXIS   = 0x20,  /* payload: dev_type, axis        -> pp_axis_cfg */
	PP_CMD_SET_AXIS   = 0x21,  /* payload: dev_type, axis, pp_axis_cfg */
	PP_CMD_SAVE       = 0x22,  /* persist all to flash */
	PP_CMD_DEFAULTS   = 0x23,  /* factory defaults (not saved until SAVE) */
	PP_CMD_TARE       = 0x30,  /* payload: dev_type, axis: cal_min := current filtered raw */
	PP_CMD_STREAM     = 0x31,  /* payload: on(1)/off(0) */
	PP_CMD_DEV_CMD    = 0x40,  /* payload: dev_type, pp_ack_cmd, p0 */
	PP_CMD_BOOTLOADER = 0x50,  /* reboot into UF2 bootloader */
	PP_CMD_LIVE       = 0x10,
};

#define PP_ST_OK       0
#define PP_ST_BADARG   1
#define PP_ST_NODEV    2
#define PP_ST_UNKNOWN  0xFF

#define PP_SLOT_AUTO 0xFE   /* axis not yet assigned: hub picks the next free HID axis */
#define PP_SLOT_OFF  0xFF
#define PP_HID_AXES  8
#define PP_CURVE_PTS 11

#define PP_AXF_INVERT (1u << 0)

struct __attribute__((packed)) pp_axis_cfg {
	uint8_t  slot;         /* HID axis 0..7, PP_SLOT_AUTO, PP_SLOT_OFF */
	uint8_t  flags;        /* PP_AXF_* */
	uint8_t  filter;       /* 0 = off, k = IIR y += (x-y)/2^k, k<=8 */
	uint8_t  reserved;
	int32_t  cal_min;      /* raw value => 0 % */
	int32_t  cal_max;      /* raw value => 100 % */
	uint16_t dz_lo;        /* 0..1000 (0.1 %): below => 0 */
	uint16_t dz_hi;        /* 0..1000: above => 100 % (1000 = none) */
	uint16_t curve[PP_CURVE_PTS]; /* output permille at input 0,10,..100 % */
	uint16_t full_scale_dkg; /* brake: force at cal_max in 0.1 kg (info for the PC app) */
};
#define PP_AXIS_CFG_LEN 40

_Static_assert(sizeof(struct pp_axis_cfg) == PP_AXIS_CFG_LEN, "axis cfg size");
_Static_assert(sizeof(struct pp_diag) == PP_DIAG_LEN, "pp_diag size");
_Static_assert(sizeof(struct pp_data) == PP_DATA_HDR_LEN + 3 * PP_MAX_AXES_PER_DEV, "pp_data size");
_Static_assert(PP_DATA_DIAG_LEN(3) <= 32, "ESB diagnostic packet exceeds 32-byte payload");
_Static_assert(4 + PP_AXIS_CFG_LEN <= PP_CFG_REPORT_LEN, "cfg frame fits");

#endif
