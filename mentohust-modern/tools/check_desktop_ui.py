"""Offline widget/layout check. No profile reads, network calls, or authentication."""
import json
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path
from mentohust_modern.windows import enable_high_dpi_awareness, minimize_window
from mentohust_modern.gui import MentohustApp

enable_high_dpi_awareness()
app = MentohustApp(smoke_test=True)
app.update()
app.after(80,app.quit)
app.mainloop()
app.update()
checks = {"window_dip": [round(app.winfo_width()/app.ui_scale), round(app.winfo_height()/app.ui_scale)],
          "scale": app.ui_scale, "network_authentication": "not_started"}
checks["physical_pixels"] = [app.winfo_width(),app.winfo_height()]
checks["reference_footprint"] = checks["physical_pixels"] == [app.m(290),app.m(467)]
checks["integrated_title_strip"] = app.winfo_rooty() == app.winfo_y()
def contained(widget, window):
    window.update()
    x, y = widget.winfo_rootx()-window.winfo_rootx(), widget.winfo_rooty()-window.winfo_rooty()
    return x>=0 and y>=0 and x+widget.winfo_width()<=window.winfo_width() and y+widget.winfo_height()<=window.winfo_height()
checks["login_controls_fit"] = all(contained(w,app) for w in (app.username_entry, app.password_entry, app.connect_button))
app._open_settings()
app.update()
checks["settings_separate_window"] = app.settings_window is not None and app.settings_window != app
checks["adapter_in_settings"] = contained(app.settings_adapter,app.settings_window) and app.settings_adapter.winfo_viewable()
checks["startup_option_visible"] = contained(app.startup_check,app.settings_window) and app.startup_check.winfo_viewable()
checks["boot_switch_visible"] = contained(app.boot_check,app.settings_window) and app.boot_check.winfo_viewable()
checks["preview_cannot_change_boot"] = app.boot_check.instate(["disabled"])
checks["preview_cannot_change_startup"] = app.startup_check.instate(["disabled"])
app.settings_window.destroy()
app.form["username"].set("test-private-user")
app.form["password"].set("test-private-password")
app._log("test-private-user / test-private-password")
app._open_logs()
app.update()
checks["log_redaction"] = "test-private" not in app.logs.get("1.0","end")
checks["logs_separate_window"] = app.log_window != app
app.log_window.destroy()
app._log("Log window can be closed during authentication.")
app._render_state("寻找服务器")
checks["no_online_timer_before_success"] = app.online_since is None
app._render_state("认证成功")
checks["online_timer_after_success"] = app.online_since is not None
app._render_state("已停止")
checks["timer_cleared_on_disconnect"] = app.online_since is None
app.connect_button.configure(state="disabled")
checks["disabled_button"] = not app.connect_button.enabled
minimize_window(app.winfo_id())
app.update()
checks["native_minimize"] = app.state() == "iconic"
app.deiconify()
app.after(80,app.quit)
app.mainloop()
checks["restore_footprint"] = [app.winfo_width(),app.winfo_height()] == [app.m(290),app.m(467)]
# Exercise the real window callbacks with an inert authentication worker.
client = SimpleNamespace(stop=Mock(), thread=SimpleNamespace(is_alive=lambda: True))
app.client = client
app.tray = SimpleNamespace(available=True, update_title=Mock(), stop=Mock())
close_button = next(w for w in app.winfo_children() if isinstance(w,tk.Button) and w.cget("text")=="×")
close_button.invoke()
app.update()
checks["close_hides_and_preserves_session"] = app.state()=="withdrawn" and app.client is client and not app.exiting and not client.stop.called
app.events.put(("show",None))
app._drain()
app.update()
checks["tray_restores_window"] = app.state()=="normal"
app.tk.call(app.protocol("WM_DELETE_WINDOW"))
checks["system_close_hides"] = app.state()=="withdrawn" and not client.stop.called
app._show_window()
app._hide_after_connect=True
app.events.put(("state","认证成功"))
app._drain()
checks["authentication_waits_for_dhcp"] = app.state()=="normal"
app.events.put(("state","在线保持"))
app._drain()
checks["automatic_connection_hides"] = app.state()=="withdrawn" and not client.stop.called
app._show_window()
app.events.put(("state","在线保持"))
app._drain()
checks["heartbeat_does_not_rehide"] = app.state()=="normal"
app._hide_after_connect=True
app._show_window()
app.events.put(("state","在线保持"))
app._drain()
checks["explicit_show_cancels_auto_hide"] = app.state()=="normal"
app._hide_after_connect=True
app.events.put(("state","错误"))
app._drain()
checks["failed_connection_stays_visible"] = app.state()=="normal" and not app._hide_after_connect
app.smoke_test=False
app._save=Mock()
app.tray.available=False
close_button.invoke()
checks["missing_tray_keeps_window_accessible"] = app.state()=="normal" and not client.stop.called
app.tray.available=True
app.events.put(("exit",None))
app._drain()
checks["tray_exit_stops_worker"] = app.exiting and client.stop.call_count==1
client.thread.is_alive=lambda:False
app._drain()
checks["exit_removes_tray"] = app.tray.stop.call_count==1
root = Path(__file__).resolve().parents[2]
(root / "reports/desktop-ui-check.json").write_text(json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(checks, ensure_ascii=False))
assert all(value for value in checks.values()), checks
