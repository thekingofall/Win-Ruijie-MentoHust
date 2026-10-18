from __future__ import annotations
import json
import sys
from pathlib import Path
from mentohust_modern import APP_VERSION
from mentohust_modern.elevation import ensure_admin
from mentohust_modern.gui import MentohustApp
from mentohust_modern.logging_utils import configure_logging
from mentohust_modern.windows import enable_high_dpi_awareness, set_current_process_app_id

def main():
    if sys.argv[1:] == ["--boot-worker"]:
        from mentohust_modern.boot_worker import run
        run()
        return
    if sys.argv[1:] == ["--boot-preflight"]:
        from mentohust_modern.boot_worker import preflight
        preflight()
        return
    if len(sys.argv) == 3 and sys.argv[1] in ("--boot-enable", "--boot-disable", "--boot-status"):
        if ensure_admin():
            return
        from mentohust_modern.boot import set_boot_enabled, boot_status, read_status
        from mentohust_modern.config import MentohustConfig
        from mentohust_modern.app_paths import default_profile_path
        try:
            operation = sys.argv[1]
            if operation == "--boot-status":
                result = boot_status()
            else:
                config = MentohustConfig.load_json(default_profile_path()) if operation == "--boot-enable" else None
                result = set_boot_enabled(operation == "--boot-enable", config)
            report = {"ok": True, "version": APP_VERSION, "task": result, "connection": read_status()}
        except Exception as exc:
            report = {"ok": False, "version": APP_VERSION, "error": str(exc)}
        Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    if sys.argv[1:] == ["--preview"]:
        enable_high_dpi_awareness()
        app = MentohustApp(smoke_test=True)
        app.title("新锐捷 · 界面预览")
        app.status.set("界面预览")
        app.status_detail.set("可检查窗口操作；连接功能已停用")
        app.mainloop()
        return
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        from mentohust_modern.secrets import protect, unprotect
        from mentohust_modern.runtime_checks import npcap_installed
        app = MentohustApp(smoke_test=True)
        app.withdraw()
        app.update()
        app._open_settings()
        app.update()
        checks = {"version": APP_VERSION, "gui_initialized": True,
                  "boot_switch_present": app.boot_check.winfo_exists() == 1,
                  "boot_switch_safe_in_preview": app.boot_check.instate(["disabled"]),
                  "dpapi_roundtrip": unprotect(protect("offline-test")) == "offline-test",
                  "pcap_available": npcap_installed(), "network_authentication": "not_started"}
        app.destroy()
        Path(sys.argv[2]).write_text(json.dumps(checks, indent=2), encoding="utf-8")
        return
    if ensure_admin():
        return
    enable_high_dpi_awareness()
    set_current_process_app_id("XinRuijie.Client")
    configure_logging()
    MentohustApp().mainloop()

if __name__ == "__main__":
    main()
