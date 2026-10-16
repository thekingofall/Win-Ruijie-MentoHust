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
        checks = {"version": APP_VERSION, "gui_initialized": True,
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
