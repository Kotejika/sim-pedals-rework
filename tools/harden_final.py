from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: harden_final.py <source-root>")
root = Path(sys.argv[1]).resolve()

# Avoid reading stale bytes past the actual ESB payload length.
p = root / "firmware_hub/src/radio.c"
s = p.read_text()
old = '''\tbool has_diag = (m->flags & PP_FLAG_DIAG_EXT) != 0;
\tsize_t expected_len = has_diag ? PP_DATA_DIAG_LEN(m->n_axes) : PP_DATA_LEN(m->n_axes);
\tif (p->length < PP_DATA_HDR_LEN || m->type != PP_PKT_DATA || t < 1 || t > PP_DEV_MAX_TYPE ||
\t    m->dev_type != t || m->n_axes == 0 || m->n_axes > PP_MAX_AXES_PER_DEV ||
\t    (has_diag && (t != PP_DEV_PEDALS || m->n_axes != 3)) || p->length < expected_len) {'''
new = '''\t/* The ESB buffer has a fixed allocation; only p->length bytes are valid. */
\tif (p->length < PP_DATA_HDR_LEN) {
\t\tg_stats.rx_bad++;
\t\tg_stats.bad_len = p->length;
\t\tg_stats.bad_b0 = p->length ? p->data[0] : 0;
\t\treturn;
\t}
\tif (m->type != PP_PKT_DATA || t < 1 || t > PP_DEV_MAX_TYPE || m->dev_type != t ||
\t    m->n_axes == 0 || m->n_axes > PP_MAX_AXES_PER_DEV) {
\t\tg_stats.rx_bad++;
\t\tg_stats.bad_len = p->length;
\t\tg_stats.bad_b0 = p->data[0];
\t\treturn;
\t}
\tbool has_diag = (m->flags & PP_FLAG_DIAG_EXT) != 0;
\tsize_t expected_len = has_diag ? PP_DATA_DIAG_LEN(m->n_axes) : PP_DATA_LEN(m->n_axes);
\tif (p->length != expected_len ||
\t    (has_diag && (t != PP_DEV_PEDALS || m->n_axes != 3)) ||
\t    (has_diag && !(m->flags & PP_FLAG_SENSOR_ERR))) {'''
if old not in s:
    raise SystemExit("Expected radio validation block not found")
p.write_text(s.replace(old, new))

# Compile-time assertions for radio packet bounds.
p = root / "common/pedal_protocol.h"
s = p.read_text()
anchor = '_Static_assert(sizeof(struct pp_data) == PP_DATA_HDR_LEN + 3 * PP_MAX_AXES_PER_DEV, "pp_data size");'
if anchor in s and 'sizeof(struct pp_diag)' not in s:
    s = s.replace(anchor, '''_Static_assert(sizeof(struct pp_diag) == PP_DIAG_LEN, "pp_diag size");
_Static_assert(sizeof(struct pp_data) == PP_DATA_HDR_LEN + 3 * PP_MAX_AXES_PER_DEV, "pp_data size");
_Static_assert(PP_DATA_DIAG_LEN(3) <= 32, "ESB diagnostic packet exceeds 32-byte payload");''')
p.write_text(s)

# Python parser must mirror the C wire contract, including the reserved stats tail.
p = root / "pc_app/protocol.py"
s = p.read_text()
s = s.replace('''        if n > 6 or pos + 6 * n > 52:
            return None
        raw, out = [], []''','''        if t < 1 or t > 7 or n == 0 or n > 6 or pos + 6 * n > 52:
            return None
        has_diag = bool(flags & PP_FLAG_DIAG_EXT)
        if has_diag and (t != DEV_PEDALS or n != 3 or not (flags & PP_FLAG_SENSOR_ERR)):
            return None
        raw, out = [], []''')
s = s.replace('''        if flags & PP_FLAG_DIAG_EXT:
            if pos + PP_DIAG_LEN > 52:''','''        if has_diag:
            if pos + PP_DIAG_LEN > 52:''')
p.write_text(s)

# Host ADS test: reject MOSI changes while SCLK is high and inject GPIO failures.
p = root / "tests/test_ads.c"
s = p.read_text()
s = s.replace('static int errors;          /* protocol violations seen by the model */',
'''static int errors;          /* protocol violations seen by the model */
static int fail_pin = -1;
static int fail_get_pin = -1;
static int fail_config_pin = -1;
static int sample_edge_errors;
static int mosi_change_high_errors;''')
s = s.replace('''int gpio_pin_configure_dt(const struct gpio_dt_spec *s, int f) { (void)s; (void)f; return 0; }''',
'''int gpio_pin_configure_dt(const struct gpio_dt_spec *s, int f)
{
\t(void)f;
\treturn s->pin == fail_config_pin ? -EIO : 0;
}''')
s = s.replace('''int gpio_pin_set_dt(const struct gpio_dt_spec *s, int v)
{
\tswitch (s->pin) {''','''int gpio_pin_set_dt(const struct gpio_dt_spec *s, int v)
{
\tif (s->pin == fail_pin) return -EIO;
\tswitch (s->pin) {''')
s = s.replace('''\tcase P_MOSI: mosi = v; break;''','''\tcase P_MOSI:
\t\tif (cs_lvl_logical && sck && v != mosi) mosi_change_high_errors++;
\t\tmosi = v;
\t\tbreak;''')
s = s.replace('''int gpio_pin_get_dt(const struct gpio_dt_spec *s) { return s->pin == P_MISO ? (cs_lvl_logical ? miso_out : 1) : 0; }''',
'''int gpio_pin_get_dt(const struct gpio_dt_spec *s)
{
\tif (s->pin == fail_get_pin) return -EIO;
\tif (s->pin == P_MISO) {
\t\tif (!cs_lvl_logical || sck) sample_edge_errors++;
\t\treturn cs_lvl_logical ? miso_out : 1;
\t}
\treturn 0;
}''')
s = s.replace('CHECK(errors == 0 && cs_lvl_logical == 0 && sck == 0);',
              'CHECK(errors == 0 && sample_edge_errors == 0 && mosi_change_high_errors == 0 && cs_lvl_logical == 0 && sck == 0);')
s = s.replace('CHECK(errors == 0);', 'CHECK(errors == 0 && sample_edge_errors == 0 && mosi_change_high_errors == 0);')
s = s.replace('''\tCHECK(errors == 0 && sample_edge_errors == 0 && mosi_change_high_errors == 0);

\tads1220_powerdown(&a);''','''\tCHECK(errors == 0 && sample_edge_errors == 0 && mosi_change_high_errors == 0);

\t/* Each GPIO failure must surface and leave CS inactive. */
\tfail_pin = P_MOSI;
\tCHECK(ads1220_read(&a, &v) == -EIO && cs_lvl_logical == 0);
\tfail_pin = -1;
\tfail_get_pin = P_MISO;
\tCHECK(ads1220_read(&a, &v) == -EIO && cs_lvl_logical == 0);
\tfail_get_pin = -1;
\tfail_config_pin = P_CS;
\tCHECK(ads1220_init(&a) == -EIO && ads_diag.err == ADS_ERR_DEV);
\tfail_config_pin = -1;
\tCHECK(ads1220_read(&a, NULL) == -EINVAL && cs_lvl_logical == 0);

\tads1220_powerdown(&a);''')
p.write_text(s)

# The axes plots/panels use translucent backgrounds so the wallpaper remains visible.
p = root / "pc_app/widgets.py"
s = p.read_text()
s = s.replace('p.fillRect(self.rect(), pal.base())', 'p.fillRect(self.rect(), QColor(10, 16, 29, 174))')
s = s.replace('p.fillRect(self.rect(), self.palette().base())', 'p.fillRect(self.rect(), QColor(10, 16, 29, 174))')
if 'from PySide6.QtGui import QColor' not in s:
    s = s.replace('from PySide6.QtCore import', 'from PySide6.QtGui import QColor\nfrom PySide6.QtCore import', 1)
p.write_text(s)

# Wrap long status text in narrow window sizes.
p = root / "pc_app/app.py"
s = p.read_text()
s = s.replace('''        self.status.setObjectName("Status")
        self.sim_cb''','''        self.status.setObjectName("Status")
        self.status.setMinimumWidth(220)
        self.status.setWordWrap(True)
        self.sim_cb''')
p.write_text(s)

# Reproducible Qt offscreen smoke test.
test = root / "tests/test_gui_smoke.py"
test.write_text('''"""Qt offscreen smoke test for the complete desktop UI and wallpaper."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PC_APP = Path(__file__).resolve().parents[1] / "pc_app"
sys.path.insert(0, str(PC_APP))

from PySide6.QtWidgets import QApplication
from PySide6.QtSvg import QSvgRenderer
from app import Main, WALLPAPER_PATH


def main():
    assert WALLPAPER_PATH.is_file(), f"Missing wallpaper: {WALLPAPER_PATH}"
    renderer = QSvgRenderer(str(WALLPAPER_PATH))
    assert renderer.isValid(), "Wallpaper SVG does not parse"
    app = QApplication.instance() or QApplication([])
    window = Main(sim=True)
    assert len(window.axes) == 3
    assert window.windowTitle().startswith("Sim Pedals")
    window.connect_hub()
    assert window.hub is not None
    assert "simulator" in window.status.text().lower()
    window.tick()
    assert window.axes[0].bar.value() >= 0
    window.timer.stop()
    window.close()
    app.processEvents()
    app.quit()
    print("Qt GUI offscreen smoke test OK")


if __name__ == "__main__":
    main()
''', encoding="utf-8")
print(f"Hardened source tree: {root}")
