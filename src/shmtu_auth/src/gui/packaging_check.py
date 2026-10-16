"""在 Windows 构建机上检查真正打包后的程序，不连接校园网。"""

import io
import sys
from pathlib import Path
from unittest.mock import patch


def run(output_directory):
    import ddddocr
    from PIL import Image
    from PySide6.QtGui import QIcon, QRawFont
    from PySide6.QtWidgets import QApplication

    from shmtu_auth.src.config.project_directory import get_directory_base_path
    from shmtu_auth.src.datatype.shmtu.auth.auth_user import UserItem
    from shmtu_auth.src.gui.common.config import cfg
    from shmtu_auth.src.gui.view.interface import settings_interface
    from shmtu_auth.src.gui.view.main_window import MainWindow
    from shmtu_auth.src.gui.view.system_tray import SystemTray

    assert getattr(sys, "frozen", False), "This check must run inside the packaged exe"
    assert Path(get_directory_base_path()) == Path(sys.executable).parent
    assert not (Path(get_directory_base_path()) / "data" / "user_list.pickle").exists(), \
        "Run the packaging check only in a fresh folder without saved accounts"
    # 检查 OCR 的原生库和模型确实打包，而不只是能 import。
    engine = ddddocr.DdddOcr(show_ad=False)
    picture = io.BytesIO()
    Image.new("RGB", (100, 40), "white").save(picture, format="PNG")
    engine.classification(picture.getvalue())

    cfg.auth_auto_start_work_thread.value = False
    cfg.check_update_at_start_up.value = False
    app = QApplication([])
    assert QRawFont.fromFont(app.font()).supportsCharacter("A"), "Windows fonts are unavailable"
    with patch.object(settings_interface, "get_github_branches", return_value=["main"]), \
            patch.object(SystemTray, "start_initial_network_probe"):
        window = MainWindow()
    try:
        window.show()
        app.processEvents()
        assert not QIcon(":/gui/Logo128").isNull(), "Qt image resources are missing"
        assert not window.home_interface.banner.banner.isNull(), "Homepage banner is missing"
        accounts = window.user_list_interface
        accounts.user_list.append(UserItem(user_id="packaging-test", password="test-only"))
        accounts.save_status()
        accounts.user_list.clear()
        accounts.read_status()
        assert len(accounts.user_list) == 1
        assert accounts.user_list[0].password == "test-only"
        assert window.grab().save(str(output_directory / "gui-smoke.png"))
    finally:
        window.force_quit()
        app.processEvents()
    return {"ok": True, "checks": ["gui", "windows-fonts", "qt-resources", "ocr-model", "account-persistence", "exe-data-directory"]}
