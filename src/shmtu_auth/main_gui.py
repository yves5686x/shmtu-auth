import os
import sys

# 从快捷方式或其他目录启动时，设置和账号仍保存在 exe 旁边。
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(os.path.abspath(sys.executable)))

# Ensure the src directory is in the Python path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()

logger.info("Initializing libraries...")

if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        import json
        import traceback
        from pathlib import Path

        report = Path(sys.argv[sys.argv.index("--smoke-test") + 1])
        try:
            from shmtu_auth.src.gui.packaging_check import run

            result = run(report.parent)
        except Exception:
            result = {"ok": False, "error": traceback.format_exc()}
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(0 if result["ok"] else 1)

    from shmtu_auth.src.gui.gui_main_application import gui_main_application

    logger.info("GUI Main program is starting...")

    # Show GUI
    gui_main_application()
