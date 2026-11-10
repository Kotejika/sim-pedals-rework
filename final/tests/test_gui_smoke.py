"""Qt offscreen smoke test for the complete desktop UI and wallpaper."""
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
