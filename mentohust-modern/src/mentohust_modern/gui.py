"""Compact Windows desktop client with separate settings and log windows."""
from __future__ import annotations
from collections import deque
import ipaddress
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from . import APP_VERSION
from .app_paths import APP_DIR_NAME, asset_path, default_profile_path, ensure_app_dirs
from .client import MentohustClient
from .config import MentohustConfig
from .interfaces import list_interfaces
from .logging_utils import get_logger
from .runtime_checks import validation_errors
from .tray import TrayController
from .startup import startup_status, set_startup
from .boot import boot_status, set_boot_enabled, read_status
from .windows import SessionEndNotifier, configure_compact_window, minimize_window, begin_window_drag

BG, INK, MUTED, BLUE, GREEN = "#F4F8F9", "#26363C", "#778593", "#4C91CC", "#38C779"


class ConnectButton(tk.Canvas):
    """Keyboard-accessible compact pill button with a native widget focus stop."""
    def __init__(self, parent, *, command, scale):
        self.command, self.scale = command, scale
        self.caption, self.color, self.enabled = "连接", GREEN, True
        super().__init__(parent, bg=BG, bd=0, highlightthickness=0, takefocus=True, cursor="hand2")
        self.bind("<Configure>", self._draw)
        self.bind("<FocusIn>", self._draw)
        self.bind("<FocusOut>", self._draw)
        self.bind("<ButtonRelease-1>", self._invoke)
        self.bind("<space>", self._invoke)
        self.bind("<Return>", self._invoke)
        self.bind("<Enter>", lambda e: self._draw(hover=True))
        self.bind("<Leave>", self._draw)

    def _invoke(self, event=None):
        if self.enabled:
            self.focus_set()
            self.command()
        return "break"

    def configure(self, cnf=None, **kw):
        if "text" in kw: self.caption = kw.pop("text")
        if "bg" in kw: self.color = kw.pop("bg")
        if "state" in kw: self.enabled = kw.pop("state") != "disabled"
        if cnf or kw: super().configure(cnf, **kw)
        self._draw()

    def _draw(self, event=None, *, hover=False):
        self.delete("all")
        w,h = self.winfo_width(), self.winfo_height()
        if w < 3 or h < 3: return
        color = ("#2DB86B" if hover and self.color == GREEN else self.color) if self.enabled else "#9ECDB2"
        r = h/2
        self.create_oval(0,0,h,h,fill=color,outline="")
        self.create_rectangle(r,0,w-r,h,fill=color,outline="")
        self.create_oval(w-h,0,w,h,fill=color,outline="")
        self.create_text(w/2,h/2,text=self.caption,fill="white",font=("Microsoft YaHei UI",-self.scale(16)))
        if self.focus_get() == self:
            self.create_rectangle(r,4,w-r,h-4,outline="white",dash=(1,2))

def adapter_caption(item):
    state = {"Up":"已插线", "Disconnected":"未插线", "Disabled":"已禁用"}.get(item.status, "待检查")
    return f"{item.name} · {state} · {item.mac[-5:]}"

class MentohustApp(tk.Tk):
    def __init__(self, *, smoke_test=False):
        super().__init__()
        self.title("新锐捷")
        self.configure(bg=BG)
        self.ui_scale = self.winfo_fpixels("1i") / 96
        # Match the installed legacy client's physical footprint at 150% DPI.
        self.compact_scale = max(1.0, self.ui_scale / 1.5)
        self.geometry(f"{self.m(290)}x{self.m(467)}")
        # Native styles remove the resize frame; let Win32 size the custom chrome.
        self.resizable(True, True)
        self.option_add("*Font", ("Microsoft YaHei UI", 9))
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        for name in ("TFrame", "TLabel", "TCheckbutton", "TLabelframe", "TLabelframe.Label"):
            style.configure(name, background=BG)
        style.configure("TLabel", foreground=INK)
        style.configure("TEntry", padding=self.p(5))
        style.configure("TCombobox", padding=self.p(4))
        style.configure("TNotebook.Tab", padding=(self.p(12), self.p(5)))
        self._icon = tk.PhotoImage(file=str(asset_path("app-icon.png")))
        self.iconphoto(True, self._icon)
        self.client = None
        self._boot = {"ready": smoke_test, "enabled": False, "legacy_enabled": False}
        self._boot_busy = False
        self._boot_query_serial = 0
        self._boot_message = "界面预览中的开机联网设置已停用。" if smoke_test else "正在读取开机联网设置…"
        self._next_boot_status = 0.0
        self.interfaces = []
        self.events = queue.Queue()
        self.exiting = False
        self._hide_after_connect = False
        self.smoke_test = smoke_test
        self.logger = get_logger("gui")
        self.profile_path = default_profile_path()
        self.form = {k: tk.StringVar(value=str(v)) for k,v in MentohustConfig().to_dict().items()
                     if k not in ("enable", "auto_connect", "remember_password")}
        self.remember, self.auto_connect = tk.BooleanVar(), tk.BooleanVar()
        self.interface_var = tk.StringVar()
        self.status = tk.StringVar(value="等待连接")
        self.status_detail = tk.StringVar(value="请选择校园网线连接的网卡")
        self.log_buffer = deque(maxlen=600)
        self.logs = self.log_window = self.settings_window = None
        self.online_since = None
        self.tray = self.session_end = None
        self._build()
        # Tk assigns wrapper styles while mapping; apply taskbar styles afterwards.
        self.bind("<Map>", lambda e: self.after(30, lambda: configure_compact_window(self.winfo_id(),self.m(290),self.m(467))) if e.widget == self else None)
        self.bind("<Alt-F4>", self._hide_to_tray)
        if not smoke_test:
            self._query_boot()
            ensure_app_dirs()
            try:
                if self.profile_path.exists():
                    self._apply(MentohustConfig.load_json(self.profile_path))
            except Exception:
                self._log("设置读取未完成，请重新填写账号和连接设置。")
            self.tray = TrayController(asset_path("app-icon.png"), app_name=APP_DIR_NAME,
                on_show=lambda: self.events.put(("show", None)), on_exit=lambda: self.events.put(("exit", None)))
            self.tray.start()
            self.session_end = SessionEndNotifier(is_connected=lambda: self.client is not None,
                on_session_end=lambda: self.client.stop(wait=False) if self.client else None)
            self.session_end.start()
            self._refresh()
        self.bind("<Return>", lambda e: self._primary_action() if e.widget.winfo_toplevel() == self else None)
        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self.after(100, self._drain)

    def p(self, value):
        return round(value * self.ui_scale)

    def m(self, value):
        return round(value * self.compact_scale)

    def _label(self, parent, text=None, **kwargs):
        return tk.Label(parent, text=text, bg=BG, fg=INK, **kwargs)

    def _link(self, parent, text, command):
        return tk.Button(parent, text=text, command=command, relief="flat", bd=0,
            fg=BLUE, bg=BG, activeforeground="#20588B", activebackground=BG,
            cursor="hand2", padx=0, pady=0, takefocus=True)

    def _build(self):
        m = self.m
        font = lambda size: ("Microsoft YaHei UI", -m(size))
        self.configure(highlightthickness=1, highlightbackground="#A7B8C4")
        hero = tk.Canvas(self, highlightthickness=0, bd=0)
        hero.place(x=0,y=0,width=m(290),height=m(189))
        self._header_background=tk.PhotoImage(width=m(290),height=m(189))
        for y in range(m(189)):
            t=y/m(189)
            rgb=tuple(round(a+(b-a)*t) for a,b in zip((83,151,211),(53,121,185)))
            self._header_background.put("#%02x%02x%02x"%rgb,to=(0,y,m(290),y+1))
        hero.create_image(0,0,image=self._header_background,anchor="nw")
        # Own subdued artwork and branding; proportions follow the installed skin.
        for coords,color in [((174,0,243,189),"#4B8FC9"),((204,25,277,189),"#4689C3"),((254,90,290,189),"#4283BE")]:
            x1,y1,x2,y2=coords
            hero.create_polygon(m(x1),m(y1),m(x2),m(y1),m(x2-27),m(y2),m(x1-27),m(y2),fill=color,outline="")
        hero.create_oval(m(10),m(9),m(28),m(27),outline="white",width=m(1))
        hero.create_line(m(14),m(20),m(18),m(20),m(21),m(15),m(25),m(15),fill="white",width=m(2))
        hero.create_text(m(34),m(16),text="新锐捷",anchor="w",fill="white",font=font(12))
        hero.create_text(m(34),m(29),text="CAMPUS NETWORK",anchor="w",fill="#DAEAF7",font=("Segoe UI",-m(6)))
        hero.create_text(m(40),m(91),text="校园网认证客户端",anchor="w",fill="white",font=font(23))
        hero.create_text(m(42),m(121),text="有线网络接入 · 新锐捷",anchor="w",fill="white",font=font(12))
        hero.create_text(m(42),m(140),text="使用校园网账号连接",anchor="w",fill="#E3EFF8",font=font(11))
        hero.create_text(m(276),m(178),text=f"V{APP_VERSION}",anchor="e",fill="#E3EFF8",font=("Segoe UI",-m(8)))
        self.hero=hero
        hero.bind("<ButtonPress-1>",self._begin_drag)
        self.menu=tk.Menu(self,tearoff=False)
        self.menu.add_command(label="网络设置…",command=self._open_settings)
        self.menu.add_command(label="连接日志…",command=self._open_logs)
        self.menu.add_separator()
        self.menu.add_command(label="关于新锐捷",command=self._about)
        self.menu.add_command(label="收起到托盘",command=self._hide_to_tray)
        def popup():
            try: self.menu.tk_popup(self.winfo_rootx()+m(177),self.winfo_rooty()+m(30))
            finally: self.menu.grab_release()
        for x,text,command in ((200,"▾",popup),(229,"−",lambda:minimize_window(self.winfo_id())),(258,"×",self._hide_to_tray)):
            btn=tk.Button(self,text=text,command=command,bg="#5296D1",fg="white",activebackground="#397FBA" if text!="×" else "#D95656",
                activeforeground="white",relief="flat",bd=0,highlightthickness=0,padx=0,pady=0,font=font(17),takefocus=False)
            btn.place(x=m(x),y=m(3),width=m(26),height=m(25))
        self._label(self,"连接网络",font=font(14),anchor="w").place(x=m(43),y=m(222),width=m(204),height=m(20))
        style=ttk.Style(self)
        style.configure("Compact.TCombobox",padding=0,arrowsize=m(15))
        self.username_entry=ttk.Combobox(self,textvariable=self.form["username"],font=font(13),style="Compact.TCombobox")
        self.username_entry.place(x=m(43),y=m(251),width=m(204),height=m(27))
        self.password_entry=tk.Entry(self,textvariable=self.form["password"],show="*",font=font(13),relief="sunken",bd=1,
            highlightthickness=0,bg="white",fg=INK,insertwidth=1)
        self.password_entry.place(x=m(43),y=m(286),width=m(204),height=m(27))
        # Inline hints disappear on focus without entering the saved credentials.
        for key,entry,label in (("username",self.username_entry,"用户名"),("password",self.password_entry,"密码")):
            hint=tk.Label(self,text=label,font=font(12),bg="white",fg="#97A1A6",anchor="w",bd=0)
            def update_hint(*args,k=key,e=entry,h=hint):
                if self.form[k].get() or self.focus_get()==e: h.place_forget()
                else: h.place(x=m(50),y=m(256 if k=="username" else 291),width=m(110),height=m(16))
            hint.bind("<Button-1>",lambda e,w=entry:w.focus_set())
            entry.bind("<FocusIn>",update_hint)
            entry.bind("<FocusOut>",update_hint)
            self.form[key].trace_add("write",update_hint)
            update_hint()
        for x,label,var in ((40,"记住密码",self.remember),(176,"自动连接",self.auto_connect)):
            check=tk.Checkbutton(self,text=label,variable=var,font=font(11),bg=BG,activebackground=BG,fg=INK,
                bd=0,highlightthickness=0,padx=0,pady=0,anchor="w",selectcolor="white")
            check.place(x=m(x),y=m(329),width=m(83),height=m(20))
        self.connect_button=ConnectButton(self,command=self._primary_action,scale=m)
        self.connect_button.place(x=m(43),y=m(373),width=m(204),height=m(38))
        self.status_dot=self._label(self,"●",font=font(9))
        self.status_dot.configure(fg="#98A6B2")
        self.status_dot.place(x=m(41),y=m(422),width=m(12),height=m(14))
        self._label(self,textvariable=self.status,font=font(10),anchor="w").place(x=m(56),y=m(422),width=m(190),height=m(14))
        self._label(self,textvariable=self.status_detail,font=font(10),anchor="w",wraplength=m(204)).place(x=m(43),y=m(438),width=m(210),height=m(25))
        # Persistent selection model; the visible adapter picker lives in settings.
        self.device_model=tk.Frame(self)
        self.adapter_combo=ttk.Combobox(self.device_model,textvariable=self.interface_var,state="readonly")
        self.refresh_button=ttk.Button(self.device_model,command=self._refresh)
        self.settings_adapter=None

    def _begin_drag(self,event):
        begin_window_drag(self.winfo_id(),event.x_root,event.y_root)
        return "break"

    def _tool_window(self,title,width,height):
        win=tk.Toplevel(self)
        win.title(title)
        win.configure(bg=BG)
        win.transient(self)
        win.geometry(f"{self.p(width)}x{self.p(height)}+{self.winfo_x()+self.p(30)}+{self.winfo_y()+self.p(45)}")
        win.minsize(self.p(width),self.p(height))
        return win

    def _open_settings(self):
        if not self.smoke_test and not self._boot_busy: self._query_boot()
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        win=self._tool_window("网络设置 · 新锐捷",500,570)
        self.settings_window=win
        draft={k:tk.StringVar(win,v.get()) for k,v in self.form.items()}
        adapter_row=ttk.Frame(win,padding=(self.p(14),self.p(14),self.p(14),0))
        adapter_row.pack(fill="x")
        ttk.Label(adapter_row,text="有线网卡").pack(side="left",padx=(0,self.p(10)))
        draft_adapter=tk.StringVar(win,self.interface_var.get())
        self.settings_adapter=ttk.Combobox(adapter_row,textvariable=draft_adapter,
            values=[adapter_caption(x) for x in self.interfaces],state="disabled" if self.client else "readonly")
        self.settings_adapter.pack(side="left",fill="x",expand=True)
        def choose_adapter(event):
            index=event.widget.current()
            if 0<=index<len(self.interfaces):
                item=self.interfaces[index]
                draft["interface_id"].set(item.guid)
                draft["interface_description"].set(item.description)
        self.settings_adapter.bind("<<ComboboxSelected>>",choose_adapter)
        ttk.Button(adapter_row,text="刷新",command=self._refresh,width=6).pack(side="right",padx=(self.p(8),0))
        tabs=ttk.Notebook(win)
        tabs.pack(fill="both",expand=True,padx=self.p(14),pady=self.p(14))
        general,network,auth,device=[ttk.Frame(tabs,padding=self.p(16)) for _ in range(4)]
        for panel,title in ((general,"常规"),(network,"网络"),(auth,"认证"),(device,"网卡信息")):
            tabs.add(panel,text=title)
            panel.columnconfigure(1,weight=1)
        self.boot_var=tk.BooleanVar(win,self._boot["enabled"])
        self.boot_info=tk.StringVar(win,self._boot_message)
        self.boot_check=ttk.Checkbutton(general,text="开机自动联网（无需登录 Windows）",
            variable=self.boot_var,command=self._toggle_boot,
            state="disabled" if self.smoke_test or not self._boot["ready"] or self._boot_busy else "normal")
        self.boot_check.grid(row=0,column=0,columnspan=2,sticky="w",pady=(0,self.p(8)))
        ttk.Label(general,text="开机后在后台认证，掉线后自动重试。\n退出窗口后继续联网；关闭此开关结束后台连接。",
            wraplength=self.p(400),foreground=MUTED).grid(row=1,column=0,columnspan=2,sticky="w")
        ttk.Label(general,textvariable=self.boot_info,wraplength=self.p(400)).grid(row=2,column=0,columnspan=2,sticky="w",pady=self.p(12))
        ttk.Separator(general).grid(row=3,column=0,columnspan=2,sticky="ew",pady=self.p(8))
        startup_var=tk.BooleanVar(win,False)
        startup_info=tk.StringVar(win,"正在读取 Windows 启动设置…")
        startup_check=ttk.Checkbutton(general,text="登录后显示新锐捷",variable=startup_var,state="disabled")
        startup_check.grid(row=4,column=0,columnspan=2,sticky="w",pady=(self.p(8),self.p(12)))
        ttk.Label(general,text="登录 Windows 后自动打开新锐捷。\n勾选主窗口的“自动连接”后，连接成功会收起到托盘。\n右键托盘图标可显示窗口或退出。",wraplength=self.p(400),foreground=MUTED).grid(row=5,column=0,columnspan=2,sticky="w")
        ttk.Label(general,textvariable=startup_info,wraplength=self.p(400)).grid(row=6,column=0,columnspan=2,sticky="w",pady=self.p(16))
        startup_events=queue.Queue()
        startup_state={"ready":False,"busy":False,"enabled":False}
        def startup_worker(operation):
            try: startup_events.put(("ok",startup_status() if operation is None else set_startup(operation)))
            except Exception as exc: startup_events.put(("error",str(exc)))
        def apply_startup():
            if not startup_state["ready"] or startup_state["busy"]: return
            startup_state["busy"]=True
            startup_check.configure(state="disabled")
            startup_apply.configure(state="disabled")
            startup_info.set("正在保存 Windows 启动设置…")
            threading.Thread(target=startup_worker,args=(startup_var.get(),),daemon=True).start()
        startup_apply=ttk.Button(general,text="应用启动设置",command=apply_startup,state="disabled")
        startup_apply.grid(row=7,column=0,columnspan=2,sticky="w")
        self.startup_check=startup_check
        def poll_startup():
            if not win.winfo_exists(): return
            try: kind,payload=startup_events.get_nowait()
            except queue.Empty: pass
            else:
                startup_state["busy"]=False
                if kind=="ok":
                    startup_state.update(ready=True,enabled=payload["enabled"])
                    startup_var.set(payload["enabled"])
                    startup_info.set("已开启：下次登录 Windows 时自动启动。" if payload["enabled"] else "已关闭开机自启动。")
                else:
                    startup_var.set(startup_state["enabled"])
                    startup_info.set(payload)
                startup_check.configure(state="normal" if startup_state["ready"] else "disabled")
                startup_apply.configure(state="normal" if startup_state["ready"] else "disabled")
            win.after(100,poll_startup)
        if self.smoke_test:
            startup_info.set("界面预览中的启动设置已停用。")
        else:
            threading.Thread(target=startup_worker,args=(None,),daemon=True).start()
            win.after(100,poll_startup)
        def field(panel,row,label,key):
            ttk.Label(panel,text=label).grid(row=row,column=0,sticky="w",padx=(0,self.p(14)),pady=self.p(6))
            ttk.Entry(panel,textvariable=draft[key]).grid(row=row,column=1,sticky="ew")
        dhcp_names=["使用现有地址","认证后续租并重新认证","认证成功后续租","认证前续租"]
        dhcp=tk.StringVar(win,dhcp_names[int(draft["dhcp_mode"].get())%4])
        ttk.Label(network,text="地址获取").grid(row=0,column=0,sticky="w",padx=(0,self.p(14)),pady=self.p(6))
        ttk.Combobox(network,textvariable=dhcp,values=dhcp_names,state="readonly").grid(row=0,column=1,sticky="ew")
        for i,(label,key) in enumerate((("IP 地址","ipaddr"),("子网掩码","mask"),("默认网关","gateway"),("DNS","dns")),1):
            field(network,i,label,key)
        ttk.Label(network,text="0.0.0.0 表示读取所选网卡的地址。\n续租作用于当前选中的有线网卡。",foreground=MUTED).grid(row=5,column=0,columnspan=2,sticky="w",pady=self.p(16))
        modes=["标准 802.1X","锐捷组播","CERNET 兼容"]
        mode=tk.StringVar(win,modes[int(draft["multicast_address"].get())%3])
        ttk.Label(auth,text="认证方式").grid(row=0,column=0,sticky="w",pady=self.p(6))
        ttk.Combobox(auth,textvariable=mode,values=modes,state="readonly").grid(row=0,column=1,sticky="ew")
        for i,(label,key) in enumerate((("客户端版本","version"),("响应超时（秒）","timeout"),("心跳间隔（秒）","interval"),("重连等待（秒）","wait"),("连续失败上限","fail_number")),1):
            field(auth,i,label,key)
        ttk.Label(auth,text="本机原版客户端").grid(row=6,column=0,columnspan=2,sticky="w",pady=(self.p(8),self.p(4)))
        path_row=ttk.Frame(auth)
        path_row.grid(row=7,column=0,columnspan=2,sticky="ew")
        ttk.Entry(path_row,textvariable=draft["client_exe"]).pack(side="left",fill="x",expand=True)
        def browse():
            path=filedialog.askopenfilename(parent=win,title="选择本机 8021x.exe",filetypes=[("锐捷客户端","8021x.exe")])
            if path: draft["client_exe"].set(path)
        ttk.Button(path_row,text="浏览…",command=browse,width=7).pack(side="right",padx=(self.p(6),0))
        selected=self._selected_interface()
        details=[("网卡",selected.description if selected else "尚未选择"),("连接",selected.status if selected else "—"),
                 ("MAC",selected.mac if selected else "—"),("IPv4",selected.ipv4 if selected else "—"),("GUID",selected.guid if selected else "—")]
        for i,(label,value) in enumerate(details):
            ttk.Label(device,text=label).grid(row=i,column=0,sticky="nw",padx=(0,self.p(14)),pady=self.p(9))
            ttk.Label(device,text=value,wraplength=self.p(310)).grid(row=i,column=1,sticky="w")
        ttk.Label(device,text="Wi-Fi、VPN 和虚拟网卡可同时保留。",foreground=MUTED).grid(row=5,column=0,columnspan=2,sticky="w",pady=self.p(16))
        actions=ttk.Frame(win,padding=(self.p(14),0,self.p(14),self.p(14)))
        actions.pack(fill="x")
        def settings_config():
            data={k:v.get() for k,v in draft.items()}
            data.update(dhcp_mode=dhcp_names.index(dhcp.get()),multicast_address=modes.index(mode.get()),
                remember_password=self.remember.get(),auto_connect=self.auto_connect.get())
            return MentohustConfig.from_dict(data)
        self._settings_config=settings_config
        def accept():
            if self._boot_busy:
                messagebox.showinfo("正在更新设置","后台设置完成后即可保存。",parent=win)
                return
            if self._boot["enabled"]:
                if settings_config().to_dict()==self._collect().to_dict():
                    win.destroy()
                else:
                    messagebox.showinfo("后台连接中","修改网络参数前，请关闭开机自动联网。",parent=win)
                return
            if self.client:
                messagebox.showinfo("连接进行中","请断开连接后修改网络设置。",parent=win)
                return
            try:
                data={k:v.get() for k,v in draft.items()}
                data.update(dhcp_mode=dhcp_names.index(dhcp.get()),multicast_address=modes.index(mode.get()),
                    remember_password=self.remember.get(),auto_connect=self.auto_connect.get())
                config=MentohustConfig.from_dict(data)
                for key in ("ipaddr","mask","gateway","dns"): ipaddress.IPv4Address(getattr(config,key))
                if not all(1<=getattr(config,k)<=300 for k in ("timeout","interval","wait")): raise ValueError("间隔应为 1–300 秒。")
                if not 1<=config.fail_number<=100: raise ValueError("失败上限应为 1–100 次。")
                if not re.fullmatch(r"\d{1,3}\.\d{1,3}",config.version) or any(int(x)>255 for x in config.version.split(".")): raise ValueError("版本格式如 5.00。")
                if not self.smoke_test: config.save_json(self.profile_path)
                self._apply(config)
                self._adapter_status()
                win.destroy()
            except (ValueError,OSError,RuntimeError) as exc:
                messagebox.showerror("设置未保存",str(exc),parent=win)
        ttk.Button(actions,text="确定",command=accept,width=9).pack(side="right",padx=(self.p(8),0))
        ttk.Button(actions,text="取消",command=win.destroy,width=9).pack(side="right")
        ttk.Button(actions,text="导出设置",command=self._export).pack(side="left")

    def _open_logs(self):
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.lift()
            return
        self.log_window=self._tool_window("连接日志 · 新锐捷",600,360)
        self.logs=scrolledtext.ScrolledText(self.log_window,font=("Microsoft YaHei UI",9),wrap="word",relief="sunken",bd=1)
        self.logs.pack(fill="both",expand=True,padx=self.p(12),pady=self.p(12))
        self.logs.insert("end","\n".join(self.log_buffer)+"\n")
        self.logs.configure(state="disabled")
        ttk.Button(self.log_window,text="关闭",command=self.log_window.destroy).pack(anchor="e",padx=self.p(12),pady=(0,self.p(12)))

    def _about(self):
        messagebox.showinfo("关于新锐捷",f"新锐捷 {APP_VERSION}\nWindows 有线校园网客户端\n\nEAP-MD5 认证 · 在线心跳 · 多网卡共存\n\n基于 MentoHUST Win Modern\n应用源码采用 GPL-3.0，依赖遵循各自许可。\n独立兼容客户端，与锐捷官方无隶属关系。",parent=self)

    def _selected_interface(self):
        guid=self.form["interface_id"].get().lower()
        return next((x for x in self.interfaces if guid in (x.guid.lower(),x.network_name.lower())),None) if guid else None

    def _refresh(self):
        if not self.smoke_test and not self._boot["ready"]: self._query_boot()
        self.refresh_button.configure(state="disabled")
        def worker():
            try: self.events.put(("interfaces",[x for x in list_interfaces() if x.is_wired]))
            except Exception: self.events.put(("refresh_error","网卡读取失败，请检查驱动或权限。"))
        threading.Thread(target=worker,daemon=True).start()

    def _select_adapter(self,event=None):
        index=self.adapter_combo.current()
        item=self.interfaces[index] if 0<=index<len(self.interfaces) else None
        self.form["interface_id"].set(item.guid if item else "")
        self.form["interface_description"].set(item.description if item else "")
        self._adapter_status()

    def _adapter_status(self):
        if self.client or self._boot["enabled"] or self._boot["legacy_enabled"]: return
        selected=self._selected_interface()
        self.status_dot.configure(fg="#98A6B2")
        self.status.set("等待连接")
        if selected: self.status_detail.set("网线已连接，可以登录" if selected.status=="Up" else "网线未连接，请插好校园网线")
        elif self.interfaces: self.status_detail.set("请选择校园网线连接的网卡")
        else: self.status_detail.set("未发现物理以太网卡，请检查网卡连接")

    def _apply(self,config):
        for k,v in config.to_dict().items():
            if k in self.form: self.form[k].set(str(v))
        self.remember.set(config.remember_password)
        self.auto_connect.set(config.auto_connect)
        self.username_entry.configure(values=[config.username] if config.username else [])
        selected=self._selected_interface()
        self.interface_var.set(adapter_caption(selected) if selected else "")

    def _collect(self):
        data={k:v.get() for k,v in self.form.items()}
        data.update(remember_password=self.remember.get(),auto_connect=self.auto_connect.get(),enable=True)
        return MentohustConfig.from_dict(data)

    def _save(self):
        if not self.smoke_test:
            try: self._collect().save_json(self.profile_path)
            except Exception: self._log("设置保存未完成。")

    def _export(self):
        path=filedialog.asksaveasfilename(parent=self,title="导出设置（账号和密码留空）",defaultextension=".json",filetypes=[("设置","*.json")])
        if path:
            try: self._collect().save_json(path,export=True)
            except Exception: messagebox.showerror("导出未完成","请检查保存位置的访问权限。",parent=self)

    def _query_boot(self):
        self._boot_query_serial += 1
        serial = self._boot_query_serial
        def worker():
            try: self.events.put(("boot_query",(serial,boot_status())))
            except Exception as exc: self.events.put(("boot_query_error",(serial,str(exc))))
        threading.Thread(target=worker,daemon=True).start()

    def _maybe_auto_connect(self):
        current=self._selected_interface()
        if (self._boot["ready"] and not self._boot["enabled"] and not self._boot["legacy_enabled"]
            and not self._boot_busy and self.auto_connect.get() and self.remember.get()
            and current and current.status=="Up" and not getattr(self,"_auto_attempted",False)):
            self._auto_attempted=True
            self.after(200,lambda: self._connect(automatic=True))

    def _update_boot_controls(self):
        if self.settings_window and self.settings_window.winfo_exists() and hasattr(self,"boot_check"):
            if not self._boot_busy: self.boot_var.set(self._boot["enabled"])
            self.boot_info.set(self._boot_message)
            self.boot_check.configure(state="disabled" if self.smoke_test or self._boot_busy or not self._boot["ready"] else "normal")
        self.connect_button.configure(state="disabled" if self._boot_busy else "normal")

    def _toggle_boot(self):
        if self.smoke_test or self._boot_busy or not self._boot["ready"]: return
        enabled=self.boot_var.get()
        config=self._collect()
        if enabled:
            try:
                if hasattr(self,"_settings_config"):
                    config=self._settings_config()
                errors=validation_errors(config)
                if errors: raise ValueError(errors[0])
                config.save_json(self.profile_path)
                self._apply(config)
            except Exception as exc:
                self.boot_var.set(self._boot["enabled"])
                self.boot_info.set(str(exc))
                return
        self._boot_query_serial += 1
        self._boot_busy=True
        self._auto_attempted=True
        self._boot_message="正在验证并启用后台连接…" if enabled else "正在关闭后台连接…"
        self._update_boot_controls()
        current_client=self.client
        def worker():
            try:
                if current_client: current_client.stop()
                result=set_boot_enabled(enabled,config)
                self.events.put(("boot",result))
            except Exception as exc:
                try: self.events.put(("boot",boot_status()))
                except Exception: pass
                self.events.put(("boot_error",str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def _primary_action(self):
        if self.smoke_test: return
        if self._boot["enabled"] or self._boot["legacy_enabled"]:
            self._open_settings()
            return
        self._disconnect() if self.client else self._connect()

    def _set_busy(self,busy):
        self.connect_button.configure(text="取消连接" if busy else "连接",state="normal",bg=BLUE if busy else GREEN)
        self.adapter_combo.configure(state="disabled" if busy else "readonly")
        self.username_entry.configure(state="disabled" if busy else "normal")
        self.password_entry.configure(state="disabled" if busy else "normal")

    def _connect(self, *, automatic=False):
        if self.exiting or self.client is not None or self._boot_busy: return
        if not self._boot["ready"] or self._boot["enabled"] or self._boot["legacy_enabled"]:
            self._boot_message = "请在网络设置中管理后台连接。"
            return
        self._hide_after_connect = automatic
        try:
            config=self._collect()
            errors=validation_errors(config)
            if errors:
                self._hide_after_connect = False
                self.status.set("请检查连接设置")
                self.status_detail.set(errors[0])
                self.status_dot.configure(fg="#D79B3D")
                return
            config.save_json(self.profile_path)
            self.client=MentohustClient(config,log_callback=lambda text:self.events.put(("log",text)),
                state_callback=lambda text:self.events.put(("state",text)))
            self._set_busy(True)
            self.online_since=None
            self.client.start()
        except Exception:
            self._hide_after_connect = False
            self._log("连接未启动，请检查设置。")
            self.status.set("连接未启动")

    def _disconnect(self):
        self._hide_after_connect = False
        if self.client:
            self.status.set("正在断开…")
            self.connect_button.configure(state="disabled")
            self.client.stop(wait=False)

    def _log(self,message):
        for secret in (self.form["password"].get(),self.form["username"].get()):
            if secret: message=message.replace(secret,"[已隐藏]")
        self.logger.info(message)
        line=time.strftime("%H:%M:%S  ")+message
        self.log_buffer.append(line)
        if self.logs is not None and self.logs.winfo_exists():
            self.logs.configure(state="normal")
            self.logs.insert("end",line+"\n")
            if int(self.logs.index("end-1c").split(".")[0])>600: self.logs.delete("1.0","2.0")
            self.logs.see("end")
            self.logs.configure(state="disabled")
        if message.startswith("[错误]"): self.status_detail.set(message[4:])

    def _render_state(self,state):
        self.status.set(state)
        if state in ("认证成功","在线保持","等待心跳"):
            if self.online_since is None: self.online_since=time.monotonic()
            self.status_dot.configure(fg=GREEN)
            self.connect_button.configure(text="断开连接")
        elif state == "更新 IP":
            self.status_dot.configure(fg=BLUE)
            self.status_detail.set("正在更新所选网卡的 IP 地址")
        elif state in ("已停止","错误"):
            self._hide_after_connect = False
            self.online_since=None
            self.status_dot.configure(fg="#D77755" if state=="错误" else "#98A6B2")
            if state=="已停止": self.status_detail.set("连接已结束")
        else:
            self.online_since=None
            self.status_dot.configure(fg=BLUE)
            self.status_detail.set("正在与校园网认证服务器通信")
        if self.tray: self.tray.update_title(f"新锐捷 · {state}")

    def _drain(self):
        while not self.events.empty():
            kind,payload=self.events.get_nowait()
            if kind in ("boot_query", "boot_query_error"):
                serial,payload=payload
                if serial != self._boot_query_serial or self._boot_busy:
                    continue
                kind="boot" if kind=="boot_query" else "boot_error"
            if kind=="interfaces":
                self.interfaces=payload
                self.refresh_button.configure(state="normal")
                self.adapter_combo["values"]=[adapter_caption(x) for x in payload]
                if self.settings_adapter is not None and self.settings_adapter.winfo_exists():
                    self.settings_adapter["values"]=[adapter_caption(x) for x in payload]
                current=self._selected_interface()
                self.interface_var.set(adapter_caption(current) if current else "")
                if not current and not self.client: self.form["interface_id"].set("")
                self._adapter_status()
                self._log(f"发现 {len(payload)} 个物理以太网接口。")
                self._maybe_auto_connect()
            elif kind=="boot":
                self._boot.update(payload,ready=True)
                if payload["enabled"] or payload["legacy_enabled"]: self._auto_attempted=True
                self._boot_busy=False
                self._boot_message=("已开启：后台持续联网。" if payload["enabled"] else
                    "检测到旧版后台连接，开启此项可迁移。" if payload["legacy_enabled"] else "已关闭开机自动联网。")
                self._update_boot_controls()
                if not payload["enabled"] and not payload["legacy_enabled"] and not self.client:
                    self._set_busy(False)
                    self._adapter_status()
                self._maybe_auto_connect()
            elif kind=="boot_error":
                self._boot_busy=False
                self._boot_message=payload
                self._update_boot_controls()
            elif kind=="refresh_error":
                self.refresh_button.configure(state="normal")
                self._log(payload)
                self.status_detail.set(payload)
            elif kind=="log": self._log(payload)
            elif kind=="state": self._render_state(payload)
            elif kind=="show":
                self._show_window()
            elif kind=="exit": self._close()
        if self._boot["enabled"] and time.monotonic() >= self._next_boot_status:
            self._next_boot_status=time.monotonic()+2
            background=read_status()
            fresh=background.get("fresh",False)
            self.status.set("后台 · "+background.get("state","等待连接") if fresh else "后台等待恢复")
            self.status_detail.set("开机自动联网已开启")
            self.status_dot.configure(fg=GREEN if fresh and background.get("authenticated") else BLUE)
            self.connect_button.configure(text="后台设置",state="normal")
            self.online_since=None
        elif self._boot["legacy_enabled"]:
            self.status.set("旧版后台连接")
            self.status_detail.set("可在网络设置中迁移")
            self.connect_button.configure(text="后台设置",state="normal")
        if self.online_since is not None:
            seconds=int(time.monotonic()-self.online_since)
            self.status_detail.set(f"已连接  {seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}")
        if self.client and self.client.thread and not self.client.thread.is_alive():
            self.client=None
            self._hide_after_connect=False
            self.online_since=None
            self._set_busy(False)
        # These states follow DHCP completion. Authentication success alone is
        # too early; the selected adapter may still be obtaining its address.
        if self._hide_after_connect and self.status.get() in ("在线保持", "等待心跳"):
            if self.tray and self.tray.available:
                self._hide_to_tray()
        if self.exiting and self.client is None:
            if self.tray: self.tray.stop()
            if self.session_end: self.session_end.stop()
            self.destroy()
            return
        self.after(100,self._drain)

    def _hide_to_tray(self, event=None):
        if self.tray and self.tray.available:
            self._save()
            self._hide_after_connect = False
            self.withdraw()
        elif self.smoke_test:
            self.destroy()
        else:
            self._log("托盘图标暂不可用，窗口保持打开；校园网连接继续运行。")
        return "break"

    def _show_window(self):
        self._hide_after_connect = False
        self.deiconify()
        self.lift()

    def _close(self):
        """Explicit tray Exit: stop authentication before removing the icon."""
        if self._boot_busy:
            messagebox.showinfo("正在更新设置","后台设置完成后即可退出。",parent=self)
            return
        if not self.exiting:
            self._save()
            self.exiting=True
            self._disconnect()

