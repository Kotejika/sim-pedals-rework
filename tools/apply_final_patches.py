from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_final_patches.py <extracted-source-root>")
root = Path(sys.argv[1]).resolve()
if not (root / "firmware_pedals/src/main.c").is_file():
    raise SystemExit(f"not a Sim Pedals source root: {root}")

# Shared protocol: diagnostic bytes are metadata, never fake game-controller axes.
p=root/'common/pedal_protocol.h'; s=p.read_text()
s=s.replace('#define PP_FLAG_SLEEPING   (1u << 2)   /* last packet before System OFF */', '''#define PP_FLAG_SLEEPING   (1u << 2)   /* last packet before System OFF */
#define PP_FLAG_DIAG_EXT   (1u << 3)   /* PP_DIAG_LEN bytes follow the real axes */

/* ADS1220 diagnostics are metadata, never additional game-controller axes. */
#define PP_DIAG_LEN 7
struct __attribute__((packed)) pp_diag {
	uint8_t error;          /* 0 OK, 1 GPIO/device, 2 register mismatch, 3 DRDY, 4 SPI */
	uint8_t drdy_active;    /* 1 = active; for ACTIVE_LOW this means physical LOW */
	uint8_t tries;          /* saturating number of init attempts */
	uint8_t regs[4];        /* ADS1220 register 0..3 readback */
};''')
s=s.replace('#define PP_DATA_LEN(n)  (PP_DATA_HDR_LEN + 3 * (n))', '''#define PP_DATA_LEN(n)  (PP_DATA_HDR_LEN + 3 * (n))
#define PP_DATA_DIAG_LEN(n) (PP_DATA_LEN(n) + PP_DIAG_LEN)''')
p.write_text(s)

# Pedal firmware: exactly three playable axes; append diagnostics after them.
p=root/'firmware_pedals/src/main.c'; s=p.read_text()
s=s.replace('bool low = gpio_pin_get_dt(&g_drdy) > 0;     /* level: data waiting */',
            'bool drdy_active = gpio_pin_get_dt(&g_drdy) > 0; /* ACTIVE_LOW: active means physical LOW */')
s=s.replace('if (got || low) {','if (got || drdy_active) {')
start=s.index('\t\tpkt->seq = seq++;')
end=s.index('\n\n\t\tif (esb_tx_full())',start)
new='''\t\tpkt->seq = seq++;
\t\tpkt->flags = (ads_ok ? 0 : (PP_FLAG_SENSOR_ERR | PP_FLAG_DIAG_EXT)) |
\t\t\t     ((batt && batt * 20 < CONFIG_PEDALS_LOW_BATT_MV) ? PP_FLAG_LOW_BATT : 0);
\t\tpkt->batt_20mv = batt;
\t\tpkt->n_axes = 3; /* clutch, throttle, brake only */
\t\tpp_put_i24(&pkt->axes[0], c);
\t\tpp_put_i24(&pkt->axes[3], t);
\t\tpp_put_i24(&pkt->axes[6], brake);
\t\tif (!ads_ok) {
\t\t\tstruct pp_diag *diag = (struct pp_diag *)&pkt->axes[9];
\t\t\tdiag->error = ads_diag.err;
\t\t\tdiag->drdy_active = drdy_active ? 1 : 0;
\t\t\tdiag->tries = ads_diag.tries;
\t\t\tmemcpy(diag->regs, ads_diag.regs, sizeof(diag->regs));
\t\t}
\t\ttx.length = ads_ok ? PP_DATA_LEN(pkt->n_axes) : PP_DATA_DIAG_LEN(pkt->n_axes);'''
s=s[:start]+new+s[end:]
p.write_text(s)

# ADS1220 GPIO-SPI: Mode 1, deterministic edges, checked GPIO results.
p=root/'firmware_pedals/src/ads1220.c'
p.write_text(r'''/*
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
''')

# Hub state and receive validation / storage.
p=root/'firmware_hub/src/hub.h'; s=p.read_text()
s=s.replace('uint8_t  batt_20mv, flags, n_axes;', 'uint8_t  batt_20mv, flags, n_axes;\n\tstruct pp_diag diag;')
p.write_text(s)

# Strict ESB record validation; diagnostic extension does not count as axes.
p=root/'firmware_hub/src/radio.c'; s=p.read_text()
old='''\tif (p->length < PP_DATA_HDR_LEN || m->type != PP_PKT_DATA || t < 1 || t > PP_DEV_MAX_TYPE ||
\t    m->dev_type != t || m->n_axes == 0 || m->n_axes > PP_MAX_AXES_PER_DEV ||
\t    p->length < PP_DATA_LEN(m->n_axes)) {'''
new='''\t/* p->data has a fixed buffer size; only p->length bytes are valid. */
\tif (p->length < PP_DATA_HDR_LEN) {
\t\tg_stats.rx_bad++;
\t\tg_stats.bad_len = p->length;
\t\tg_stats.bad_b0 = p->length ? p->data[0] : 0;
\t\treturn;
\t}
\tif (m->type != PP_PKT_DATA || t < 1 || t > PP_DEV_MAX_TYPE ||
\t    m->dev_type != t || m->n_axes == 0 || m->n_axes > PP_MAX_AXES_PER_DEV) {
\t\tg_stats.rx_bad++;
\t\tg_stats.bad_len = p->length;
\t\tg_stats.bad_b0 = p->data[0];
\t\treturn;
\t}
\tbool has_diag = (m->flags & PP_FLAG_DIAG_EXT) != 0;
\tsize_t expected_len = has_diag ? PP_DATA_DIAG_LEN(m->n_axes) : PP_DATA_LEN(m->n_axes);
\tif (p->length != expected_len ||
\t    (has_diag && (t != PP_DEV_PEDALS || m->n_axes != 3)) ||
\t    (has_diag && !(m->flags & PP_FLAG_SENSOR_ERR))) {
\t\tg_stats.rx_bad++;
\t\tg_stats.bad_len = p->length;
\t\tg_stats.bad_b0 = p->data[0];
\t\treturn;
\t}'''
if old not in s: raise SystemExit('radio validation not found')
s=s.replace(old,new)
s=s.replace('''\td->flags = m->flags;
\td->batt_20mv = m->batt_20mv;
\td->n_axes = m->n_axes;''','''\td->flags = m->flags;
\td->batt_20mv = m->batt_20mv;
\td->n_axes = m->n_axes;
\tif (has_diag) {
\t\tmemcpy(&d->diag, &m->axes[m->n_axes * 3], sizeof(d->diag));
\t} else {
\t\tmemset(&d->diag, 0, sizeof(d->diag));
\t}''')
s = s.replace('old', 'old') if False else s

# The replacement above changes the validation predicate. Remove the original
# validation body that follows it; otherwise the old error body is duplicated.
tail = """\n\t\tg_stats.rx_bad++;\n\t\tg_stats.bad_len = p->length;\n\t\tg_stats.bad_b0 = p->data[0];\n\t\treturn;\n\t}\n\n\tint64_t now = k_uptime_get();"""
if tail not in s:
    raise SystemExit("duplicate ESB validation tail not found; review the source version")
s = s.replace(tail, "\n\tint64_t now = k_uptime_get();", 1)

p.write_text(s)

# USB live frame carries a fixed-size ADS diagnostic record after three true axes.
p=root/'firmware_hub/src/usb.c'; s=p.read_text()
s=s.replace('''\t\tint n = d->n_axes;

\t\tif (pos + 7 + 6 * n > 52) {''','''\t\tint n = d->n_axes;
\t\tbool has_diag = (d->flags & PP_FLAG_DIAG_EXT) != 0;
\t\tint extra = has_diag ? PP_DIAG_LEN : 0;

\t\tif (pos + 7 + 6 * n + extra > 52) {''')
s=s.replace('''\t\tfor (int i = 0; i < n; i++) {
\t\t\tmemcpy(&f.d[pos], &d->raw[i], 4);
\t\t\tpos += 4;
\t\t\tf.d[pos++] = d->out[i] & 0xFF;
\t\t\tf.d[pos++] = d->out[i] >> 8;
\t\t}
\t\tnd++;''','''\t\tfor (int i = 0; i < n; i++) {
\t\t\tmemcpy(&f.d[pos], &d->raw[i], 4);
\t\t\tpos += 4;
\t\t\tf.d[pos++] = d->out[i] & 0xFF;
\t\t\tf.d[pos++] = d->out[i] >> 8;
\t\t}
\t\tif (has_diag) {
\t\t\tmemcpy(&f.d[pos], &d->diag, PP_DIAG_LEN);
\t\t\tpos += PP_DIAG_LEN;
\t\t}
\t\tnd++;''')
p.write_text(s)

# Python parser mirror.
p=root/'pc_app/protocol.py'; s=p.read_text()
s=s.replace('AXF_INVERT = 1', 'AXF_INVERT = 1\nPP_FLAG_LOW_BATT = 1 << 0\nPP_FLAG_SENSOR_ERR = 1 << 1\nPP_FLAG_SLEEPING = 1 << 2\nPP_FLAG_DIAG_EXT = 1 << 3\nPP_DIAG_LEN = 7')
s=s.replace('''    out: list           # 0..65535 as sent to the PC game port

    @property
    def low_batt(self): return bool(self.flags & 1)
    @property
    def sensor_err(self): return bool(self.flags & 2)''','''    out: list           # 0..65535 as sent to the PC game port
    diag: object = None # ADS1220 diagnostic metadata; never an axis

    @property
    def low_batt(self): return bool(self.flags & PP_FLAG_LOW_BATT)
    @property
    def sensor_err(self): return bool(self.flags & PP_FLAG_SENSOR_ERR)
    @property
    def has_diag(self): return bool(self.flags & PP_FLAG_DIAG_EXT)''')
start=s.index('def parse_live(frame: bytes):'); end=s.index('\n\ndef request(',start)
s=s[:start]+'''def parse_live(frame: bytes):
    """Parse one 64-byte Hub live frame, rejecting truncated device records."""
    if len(frame) != REPORT_LEN or frame[0] != CMD_LIVE:
        return None
    nd, pos, res = frame[2], 3, []
    # Stats occupy [52:64]; device records may not overlap that reserved tail.
    for _ in range(nd):
        if pos + 7 > 52:
            return None
        t, batt, flags, alive, pps, n = struct.unpack_from("<BBBBHB", frame, pos)
        pos += 7
        if t < 1 or t > 7 or n == 0 or n > 6 or pos + 6 * n > 52:
            return None
        has_diag = bool(flags & PP_FLAG_DIAG_EXT)
        if has_diag and (t != DEV_PEDALS or n != 3 or not (flags & PP_FLAG_SENSOR_ERR)):
            return None
        raw, out = [], []
        for _ in range(n):
            r, o = struct.unpack_from("<iH", frame, pos)
            pos += 6
            raw.append(r)
            out.append(o)
        diag = None
        if has_diag:
            if pos + PP_DIAG_LEN > 52:
                return None
            error, drdy_active, tries = struct.unpack_from("<BBB", frame, pos)
            regs = tuple(frame[pos + 3:pos + 7])
            diag = {"error": error, "drdy_active": bool(drdy_active),
                    "tries": tries, "regs": regs}
            pos += PP_DIAG_LEN
        res.append(DevLive(t, batt * 20, flags, bool(alive), pps, raw, out, diag))
    return res
'''+s[end:]
p.write_text(s)

# GUI: custom Sachi-inspired room wallpaper, truthful ADS diagnostics/save/profile handling.
p=root/'pc_app/app.py'; s=p.read_text()
s=s.replace('from PySide6.QtCore import Qt, QTimer', 'from pathlib import Path\n\nfrom PySide6.QtCore import Qt, QTimer, QRectF')
s=s.replace('''from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QProgressBar, QPushButton, QSlider, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)''','''from PySide6.QtGui import QColor, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QProgressBar, QPushButton, QSlider, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)''')
style='''APP_STYLE = r"""
QMainWindow, QWidget { color: #F1F4FA; font-family: "Segoe UI"; font-size: 10pt; }
QLabel#Brand { color: #E7E6FF; font-size: 13pt; font-weight: 700; letter-spacing: 2px; }
QLabel#Status { color: #D9E4F4; background: rgba(12,18,30,218); border: 1px solid rgba(151,177,220,85); border-radius: 9px; padding: 8px 12px; }
QGroupBox { background: rgba(13,20,34,222); border: 1px solid rgba(160,183,220,90); border-radius: 12px; margin-top: 13px; padding: 12px 10px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 13px; padding: 0 6px; color: #D9C8FF; }
QTabWidget::pane { background: rgba(13,20,34,190); border: 1px solid rgba(160,183,220,95); border-radius: 12px; top: -1px; }
QTabBar::tab { background: rgba(24,34,52,235); color: #C8D4E6; border: 1px solid rgba(160,183,220,70); border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; padding: 9px 18px; margin-right: 4px; }
QTabBar::tab:selected { background: rgba(95,79,150,240); color: white; }
QPushButton { background: rgba(38,52,75,245); color: #F2F5FB; border: 1px solid rgba(166,189,225,115); border-radius: 8px; padding: 7px 12px; min-height: 18px; }
QPushButton:hover { background: rgba(66,83,117,250); border-color: #BFAEFF; }
QPushButton:pressed { background: rgba(91,72,138,250); }
QPushButton#PrimaryButton { background: #765CC2; border-color: #A58DEB; font-weight: 700; }
QPushButton#PrimaryButton:hover { background: #8A70D4; }
QCheckBox { spacing: 8px; color: #E5EAF5; }
QComboBox, QSpinBox, QDoubleSpinBox { background: rgba(17,27,43,245); color: #F4F6FB; border: 1px solid rgba(158,180,216,115); border-radius: 7px; padding: 5px 8px; min-height: 22px; }
QComboBox QAbstractItemView { background: #1B2639; color: #F4F6FB; selection-background-color: #765CC2; }
QSlider::groove:horizontal { background: rgba(143,160,192,105); height: 5px; border-radius: 2px; }
QSlider::handle:horizontal { background: #C7B3FF; border: 1px solid #F2ECFF; width: 15px; margin: -6px 0; border-radius: 7px; }
QProgressBar { background: rgba(7,12,22,220); border: 1px solid rgba(160,183,220,100); border-radius: 7px; height: 16px; text-align: center; color: white; }
QProgressBar::chunk { background: #8B74D6; border-radius: 6px; }
QLabel { background: transparent; }
"""

APP_DIR = Path(__file__).resolve().parent
WALLPAPER_PATH = APP_DIR / "assets" / "sachi-room.svg"


class WallpaperWidget(QWidget):
    """Original, fully clothed Sachi-inspired fan-art background; not an official image."""
    def __init__(self, image_path, parent=None):
        super().__init__(parent)
        self._renderer = QSvgRenderer(str(image_path)) if image_path.exists() else None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111827"))
        if self._renderer and self._renderer.isValid():
            source = self._renderer.viewBoxF()
            if source.isEmpty():
                source = QRectF(0, 0, 1920, 1080)
            scale = max(self.width() / source.width(), self.height() / source.height())
            w, h = source.width() * scale, source.height() * scale
            target = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
            self._renderer.render(painter, target)
        painter.end()
'''
s=s.replace('SLOT_ITEMS = [("Auto (next free)", P.SLOT_AUTO)',style+'\n\nSLOT_ITEMS = [("Auto (next free)", P.SLOT_AUTO)')
s=s.replace('''        self.setWindowTitle("Sim Pedals")
        self.resize(980, 760)''','''        self.setWindowTitle("Sim Pedals • Precision Control")
        self.resize(1180, 820)
        self.setMinimumSize(920, 680)
        self.setStyleSheet(APP_STYLE)''')
s=s.replace('''        c = QWidget()
        self.setCentralWidget(c)
        v = QVBoxLayout(c)''','''        c = WallpaperWidget(WALLPAPER_PATH)
        self.setCentralWidget(c)
        v = QVBoxLayout(c)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(12)''')
s=s.replace('''        top = QHBoxLayout()
        self.status = QLabel("not connected")''','''        top = QHBoxLayout()
        top.setSpacing(10)
        brand = QLabel("SIM PEDALS  /  PRECISION CONTROL")
        brand.setObjectName("Brand")
        self.status = QLabel("not connected")
        self.status.setObjectName("Status")
        self.status.setMinimumWidth(220)
        self.status.setWordWrap(True)''')
s=s.replace('''        top.addWidget(conn)
        top.addWidget(self.sim_cb)
        top.addWidget(self.status, 1)''','''        top.addWidget(brand)
        top.addStretch(1)
        top.addWidget(conn)
        top.addWidget(self.sim_cb)
        top.addWidget(self.status, 1)''')
s=s.replace('''        save = QPushButton("Save to hub")
        save.clicked.connect(self.save_hub)''','''        save = QPushButton("Save to hub")
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self.save_hub)''')
old='''            if d.sensor_err:
                txt += " | ADS1220 ERROR"
                if len(d.raw) >= 6:
                    code, r01, r23 = d.raw[3], d.raw[4] & 0xFFFF, d.raw[5] & 0xFFFF
                    names = {1: "SPI device not ready", 2: "register readback mismatch",
                             3: "no DRDY (no conversions)", 4: "SPI transfer error"}
                    txt += " [%s; regs %02X %02X %02X %02X; DRDY pin %s; tries %d]" % (
                        names.get(code & 0xFF, "err %d" % (code & 0xFF)), r01 >> 8, r01 & 0xFF,
                        r23 >> 8, r23 & 0xFF, "low" if code & 0x100 else "high", (code >> 16) & 0xFF)
            self.status.setText(txt + stxt)'''
new='''            if d.sensor_err:
                txt += " | ADS1220 ERROR"
                if d.diag:
                    diag = d.diag
                    names = {1: "GPIO/device not ready", 2: "register readback mismatch",
                             3: "DRDY timeout", 4: "GPIO SPI transfer error"}
                    r0, r1, r2, r3 = diag["regs"]
                    txt += " [%s; regs %02X %02X %02X %02X; DRDY %s; tries %d]" % (
                        names.get(diag["error"], "error %d" % diag["error"]), r0, r1, r2, r3,
                        "LOW (active)" if diag["drdy_active"] else "HIGH (idle)", diag["tries"])
                else:
                    txt += " [diagnostic extension missing; check Hub/firmware pair]"
            self.status.setText(txt + stxt)'''
if old not in s: raise SystemExit('app diagnostic block not found')
s=s.replace(old,new)
old='''    def save_hub(self):
        if self.hub_call("save") is None and self.hub:
            self.status.setText("saved to hub flash")'''
new='''    def save_hub(self):
        if not self.hub:
            self.status.setText("not connected")
            return
        try:
            self.hub.save()
        except Exception as e:
            self.status.setText("save failed: %s" % e)
        else:
            self.status.setText("saved to hub flash")'''
if old not in s: raise SystemExit('save_hub block not found')
s=s.replace(old,new)
s=s.replace('''        if path:
            data = {"axes": [{**t.cfg.__dict__} for t in self.axes], "idle": self.idle.value()}
            json.dump(data, open(path, "w"), indent=1)''','''        if path:
            data = {"axes": [{**t.cfg.__dict__} for t in self.axes], "idle": self.idle.value()}
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except OSError as e:
                QMessageBox.critical(self, "Profile save failed", str(e))''')
s=s.replace('''        data = json.load(open(path))
        for t, d in zip(self.axes, data["axes"]):
            t.load(P.AxisCfg(**d))
            t.push()
        self.idle.setValue(data.get("idle", 20))''','''        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            axes = data["axes"]
            if not isinstance(axes, list) or len(axes) != 3:
                raise ValueError("Profile must contain exactly three pedal axes")
            configs = [P.AxisCfg(**item) for item in axes]
            for t, cfg in zip(self.axes, configs):
                t.load(cfg)
                t.push()
            self.idle.setValue(int(data.get("idle", 20)))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            QMessageBox.critical(self, "Profile load failed", str(e))''')
p.write_text(s)

# Original, non-sexual, fully clothed fan-art inspired by Sachi; not an official manga frame.
asset=root/'pc_app/assets/sachi-room.svg'; asset.parent.mkdir(parents=True,exist_ok=True)
asset.write_text(r'''<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
<defs>
 <linearGradient id="wall" x2="0" y2="1"><stop stop-color="#f4dce7"/><stop offset="1" stop-color="#b7b0d3"/></linearGradient>
 <linearGradient id="window" x2="1" y2="1"><stop stop-color="#fdf7ed"/><stop offset=".55" stop-color="#b9d4dc"/><stop offset="1" stop-color="#e9b9bd"/></linearGradient>
 <linearGradient id="coat" x2="0" y2="1"><stop stop-color="#315c57"/><stop offset="1" stop-color="#153f42"/></linearGradient>
 <linearGradient id="hair" x2=".15" y2="1"><stop stop-color="#282536"/><stop offset="1" stop-color="#111723"/></linearGradient>
 <linearGradient id="skin" x2="0" y2="1"><stop stop-color="#ffe0d2"/><stop offset="1" stop-color="#dca9a5"/></linearGradient>
 <linearGradient id="shade" x2="1"><stop stop-color="#101727" stop-opacity=".93"/><stop offset=".52" stop-color="#101727" stop-opacity=".73"/><stop offset=".83" stop-color="#101727" stop-opacity=".15"/><stop offset="1" stop-color="#101727" stop-opacity=".05"/></linearGradient>
 <radialGradient id="glow"><stop stop-color="#fff7e9" stop-opacity=".95"/><stop offset="1" stop-color="#fff7e9" stop-opacity="0"/></radialGradient>
</defs>
<rect width="1920" height="1080" fill="url(#wall)"/><circle cx="1420" cy="210" r="420" fill="url(#glow)"/>
<rect x="1180" y="70" width="610" height="590" rx="15" fill="#725f75" opacity=".25"/><rect x="1200" y="90" width="570" height="550" rx="8" fill="url(#window)" stroke="#fff3e9" stroke-width="18"/>
<path d="M1485 100V630M1210 370H1760" stroke="#fff9f2" stroke-width="13" opacity=".9"/><path d="M1230 540 Q1360 430 1480 520 T1750 490 V630 H1230Z" fill="#b2b9c7" opacity=".45"/>
<path d="M1150 70 Q1200 260 1140 650 L1200 650 Q1240 280 1205 70Z" fill="#f6d5db"/><path d="M1770 70 Q1720 260 1780 650 L1840 650 Q1790 280 1820 70Z" fill="#f0cbd7"/>
<rect x="1300" y="710" width="430" height="32" rx="16" fill="#795b70" opacity=".25"/><path d="M1340 742 L1310 905 M1690 742 L1720 905" stroke="#795b70" stroke-width="18" opacity=".28"/><rect x="1260" y="910" width="500" height="30" rx="15" fill="#7c6074" opacity=".32"/>
<path d="M1090 785 Q1060 710 1100 645 Q1140 710 1120 785Z" fill="#71958b"/><path d="M1090 785 Q1015 720 1035 670 Q1090 710 1090 785Z" fill="#547e73"/><path d="M1110 785 Q1160 705 1190 690 Q1180 760 1110 785Z" fill="#88a99b"/><path d="M1040 790H1180L1160 900H1060Z" fill="#c18b86"/>
<!-- Original, fully clothed Sachi-inspired character -->
<path d="M1420 1060 Q1375 915 1370 825 Q1370 750 1445 715 Q1510 690 1580 720 Q1655 755 1665 850 L1715 1080Z" fill="url(#coat)"/><path d="M1390 790 Q1445 710 1505 735 L1550 845 L1460 1000Z" fill="#3d7067"/><path d="M1580 735 Q1640 725 1685 795 L1720 1000 L1595 900Z" fill="#244f4e"/>
<path d="M1417 747 Q1460 690 1512 716 Q1540 744 1565 790 Q1520 833 1470 810 Q1430 795 1417 747Z" fill="#e7e1dd"/><path d="M1560 788 Q1600 713 1650 745 Q1665 770 1655 810 Q1600 838 1560 788Z" fill="#f6eee7"/><path d="M1450 760 Q1490 805 1530 805 M1590 785 Q1618 764 1640 767" fill="none" stroke="#c6c1c7" stroke-width="10" stroke-linecap="round"/>
<path d="M1480 700 Q1495 650 1495 615 L1580 610 Q1570 680 1600 725 Q1555 760 1500 730Z" fill="url(#skin)"/><ellipse cx="1540" cy="520" rx="145" ry="184" fill="url(#skin)"/>
<path d="M1390 545 Q1360 355 1510 300 Q1680 280 1700 465 Q1730 610 1670 690 Q1660 600 1620 560 Q1590 510 1565 450 Q1510 520 1430 550 Q1415 640 1380 680 Q1365 610 1390 545Z" fill="url(#hair)"/><path d="M1400 425 Q1440 285 1570 308 Q1680 310 1690 430 Q1640 392 1580 396 Q1510 400 1450 445Z" fill="#323047"/><path d="M1410 430 Q1450 360 1490 352 Q1472 420 1475 478 Q1440 458 1425 510Z" fill="#191e2b"/><path d="M1490 360 Q1540 332 1570 348 Q1542 415 1542 486 Q1505 456 1490 360Z" fill="#25263a"/><path d="M1560 350 Q1615 354 1645 405 Q1602 420 1580 480 Q1568 418 1560 350Z" fill="#161c2b"/>
<rect x="1640" y="462" width="62" height="22" rx="8" transform="rotate(22 1640 462)" fill="#f3e5d5" opacity=".95"/><path d="M1650 457l15 24m-4 -30l15 24" stroke="#d1b7a8" stroke-width="3"/>
<path d="M1450 530 Q1485 497 1520 529 Q1485 559 1450 530Z" fill="#fff4ed"/><ellipse cx="1487" cy="530" rx="15" ry="21" fill="#81524d"/><ellipse cx="1491" cy="526" rx="6" ry="9" fill="#1d2635"/><path d="M1560 530 Q1595 497 1630 529 Q1595 559 1560 530Z" fill="#fff4ed"/><ellipse cx="1596" cy="530" rx="15" ry="21" fill="#81524d"/><ellipse cx="1600" cy="526" rx="6" ry="9" fill="#1d2635"/><path d="M1447 505 Q1483 482 1520 505 M1558 505 Q1595 482 1632 505" fill="none" stroke="#292638" stroke-width="10" stroke-linecap="round"/><path d="M1530 595 Q1545 605 1560 594" fill="none" stroke="#a66d6d" stroke-width="6" stroke-linecap="round"/><ellipse cx="1438" cy="570" rx="24" ry="12" fill="#d9858f" opacity=".3"/><ellipse cx="1642" cy="570" rx="24" ry="12" fill="#d9858f" opacity=".3"/>
<path d="M1400 500 Q1355 615 1405 735 M1680 450 Q1740 575 1698 730" fill="none" stroke="#1d2331" stroke-width="18" stroke-linecap="round"/><path d="M1430 365 Q1380 485 1410 600 M1650 350 Q1705 470 1670 590" fill="none" stroke="#454056" stroke-width="8" stroke-linecap="round"/>
<path d="M1000 410V610" stroke="#8b7183" stroke-width="12"/><path d="M948 430 Q1000 350 1052 430 L1035 465H965Z" fill="#f7d2a9"/><ellipse cx="1000" cy="610" rx="100" ry="18" fill="#8b7183" opacity=".3"/><circle cx="940" cy="860" r="8" fill="#f1c98e"/><circle cx="980" cy="880" r="7" fill="#d8909b"/><circle cx="1017" cy="850" r="8" fill="#c6acd9"/>
<rect width="1920" height="1080" fill="url(#shade)"/><rect width="1920" height="1080" fill="#0b1220" opacity=".12"/>
</svg>
''',encoding='utf-8')
print(f"Patched source tree: {root}")
