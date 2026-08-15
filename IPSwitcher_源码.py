# -*- coding: utf-8 -*-
""" IP 快速切换工具 - 暗色半透明悬浮窗版
    无标题栏 / 默认置顶 / 靠边自动收成小图标 / 每行2个IP / 黑色半透明
"""
import os, sys, json, threading, subprocess, ctypes, math
import ctypes.wintypes as wt
import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageDraw
import pystray
import concurrent.futures
import re

# ---------------- 可调常量 ----------------
W = 640                 # 窗口固定宽度
CARD_H = 38             # 卡片高度
ROW_STEP = 44           # 每行卡片占用高度(含间距)
HEADER_H = 98           # 顶部固定区高度(拖动条+适配器行+标题行+底边距)
WINDOW_ALPHA = 0.90     # 窗口半透明度(0~1, 越小越透)
THR = 10                # 贴边判定阈值(px)
AWAY_MS = 1200          # 鼠标离开贴边窗口多久后收起
FONT_FAMILY = "Microsoft YaHei UI"
CREATE_NO_WINDOW = 0x08000000

# ---------------- 暗色玻璃配色 ----------------
WIN_BG     = "#14171c"   # 窗口半透明黑底
DRAG_GRIP  = "#4b525c"   # 拖动条抓手
TXT        = "#e8ebf0"   # 主文字
SUB        = "#9aa1ab"   # 次文字
BLUE       = "#3b82f6";  BLUE_H   = "#2f6fe0"
GREEN      = "#22c55e";  GREEN_H  = "#16a34a"
DBTN       = "#262b33";  DBTN_H   = "#323842"   # 深灰按钮
DBTN_DEL_H = "#3a2226"                          # 删除按钮hover
RED        = "#ff6b6b"
CARD       = "#20242b";  CARD_B     = "#333a44"
CARD_SEL   = "#262c36";  CARD_SEL_B = "#5b8def"
CARD_ACT   = "#1d2942";  CARD_ACT_B = "#3b82f6"
DOT        = "#5a626d"
TOAST_BG   = "#2563eb"

def F(size, weight="normal"):
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)

# ---------------- 系统工具 ----------------
def is_admin():
    try: return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception: return False

def run_as_admin():
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, subprocess.list2cmdline(sys.argv[1:])
    else:
        exe = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(exe): exe = sys.executable
        params = subprocess.list2cmdline(sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    sys.exit(0)

def cursor_pos():
    p = wt.POINT(); ctypes.windll.user32.GetCursorPos(ctypes.byref(p)); return p.x, p.y

def workarea():
    r = wt.RECT(); ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom

def clamp(v, lo, hi): return max(lo, min(hi, v))

# ---------------- 全局快捷键与任务栏隐藏 ----------------
class HotkeyManager:
    def __init__(self, callback):
        self.callback = callback
        self.thread = None
        self.thread_id = None
        self.running = False
        self.modifiers = 0
        self.vk = 0

    def start(self, modifiers, vk):
        self.stop()
        if vk == 0: return
        self.modifiers = modifiers
        self.vk = vk
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.thread_id = None

    def _run(self):
        user32 = ctypes.windll.user32
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        user32.UnregisterHotKey(None, 1)
        if not user32.RegisterHotKey(None, 1, self.modifiers, self.vk):
            return

        msg = wt.MSG()
        while self.running:
            bRet = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if bRet == 0 or bRet == -1:
                break
            if msg.message == 0x0312: # WM_HOTKEY
                self.callback()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.UnregisterHotKey(None, 1)

def hide_from_taskbar(window):
    try:
        GWL_EXSTYLE = -20
        WS_EX_APPWINDOW = 0x00040000
        WS_EX_TOOLWINDOW = 0x00000080
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass


# ---------------- 网络操作 ----------------
PS_SCRIPT = r"""
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
function P2M($p){
  if($p -le 0){return '0.0.0.0'}; if($p -ge 32){return '255.255.255.255'}
  $m=[uint32](((1 -shl 32) - 1) -shl (32-$p))
  return ("{0}.{1}.{2}.{3}" -f (($m -shr 24) -band 255),(($m -shr 16) -band 255),(($m -shr 8) -band 255),($m -band 255))
}
$out=@()
Get-NetAdapter | Where-Object {$_.HardwareInterface -eq $true -and $_.Status -ne 'Disabled'} | ForEach-Object {
  $n=$_.Name
  $ipObj=Get-NetIPAddress -InterfaceAlias $n -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1
  $gw=(Get-NetRoute -InterfaceAlias $n -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Select-Object -First 1).NextHop
  $dns=(Get-DnsClientServerAddress -InterfaceAlias $n -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses
  if($null -eq $dns){$dns=@()}else{$dns=@($dns)}
  $dhcp = if($ipObj){$ipObj.PrefixOrigin -eq 'Dhcp'}else{$false}
  $ip = if($ipObj){$ipObj.IPAddress}else{''}
  $mask = if($ipObj){P2M $ipObj.PrefixLength}else{''}
  $out += [ordered]@{name=$n; ip=$ip; mask=$mask; gateway=$gw; dns=$dns; dhcp=$dhcp}
}
[pscustomobject]@{adapters=$out} | ConvertTo-Json -Depth 5
"""

class NetManager:
    def _run(self, args):
        r = subprocess.run(args, creationflags=CREATE_NO_WINDOW,
                           capture_output=True, timeout=20)
        if r.returncode != 0:
            err = (r.stderr or b"").decode("gbk", "ignore").strip()
            raise RuntimeError(err or f"命令失败 code={r.returncode}")
        return r

    def get_all(self):
        r = self._run(["powershell", "-NoProfile", "-Command", PS_SCRIPT])
        data = json.loads(r.stdout.decode("utf-8", "ignore") or "{}")
        ad = data.get("adapters") or []
        if isinstance(ad, dict): ad = [ad]
        net = {}
        for a in ad:
            net[a.get("name")] = {
                "ip": a.get("ip") or "", "mask": a.get("mask") or "",
                "gateway": a.get("gateway") or "", "dns": a.get("dns") or [],
                "dhcp": bool(a.get("dhcp")),
            }
        return net

    def apply(self, adapter, p):
        n = f"name={adapter}"
        if p.get("dhcp"):
            self._run(["netsh", "interface", "ip", "set", "address", n, "dhcp"])
            self._run(["netsh", "interface", "ip", "set", "dns", n, "dhcp"])
            return
        ip = (p.get("ip") or "").strip()
        if not ip: raise RuntimeError("静态方案缺少 IP")
        mask = (p.get("mask") or "255.255.255.0").strip()
        gw = (p.get("gateway") or "").strip()
        cmd = ["netsh", "interface", "ip", "set", "address", n, "static", ip, mask]
        if gw: cmd += [gw, "1"]
        self._run(cmd)
        dns = [d.strip() for d in (p.get("dns") or []) if d.strip()]
        if dns:
            self._run(["netsh", "interface", "ip", "set", "dns", n, "static", dns[0]])
            for d in dns[1:]:
                self._run(["netsh", "interface", "ip", "add", "dns", n, f"addr={d}"])
        else:
            self._run(["netsh", "interface", "ip", "set", "dns", n, "dhcp"])

# ---------------- 配置存储 ----------------
def config_path():
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                           else os.path.abspath(__file__))
    p = os.path.join(base, "ip_switcher_config.json")
    try:
        with open(p, "a"): pass
        return p
    except Exception:
        d = os.path.join(os.environ.get("APPDATA", base), "IPSwitcher")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "ip_switcher_config.json")

class Config:
    def __init__(self):
        self.path = config_path()
        self.data = {"adapter": "", "profiles": [], "hotkey": {"mod": 0, "vk": 0, "text": ""}}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data.update(json.load(f))
        except Exception: pass
        self.data.setdefault("profiles", [])

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception: pass

# ---------------- 提示框 ----------------
class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        self.id = None
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)

    def enter(self, event=None):
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        self.hidetip()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(400, self.showtip)

    def unschedule(self):
        id_ = self.id
        self.id = None
        if id_:
            self.widget.after_cancel(id_)

    def showtip(self, event=None):
        try:
            x = self.widget.winfo_rootx() + 10
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        except Exception:
            return
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        
        label = tk.Label(tw, text=self.text, bg=CARD, fg=TXT, font=("Microsoft YaHei UI", 9),
                         padx=4, pady=2, relief="solid", borderwidth=1, highlightbackground=CARD_B)
        label.pack()

    def hidetip(self):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()

# ---------------- 主界面 ----------------
class App:
    def __init__(self, root):
        self.root = root
        self.netmgr = NetManager()
        self.config = Config()
        self.net = {}
        self.selected = None
        self.card_widgets = []
        self.state = "shown"
        self.dock_enabled = False
        self._away = 0
        self._dock_side = None
        self._dock_geom = None
        self._drag = None

        l, t, r, b = workarea()
        self.root.title("IPSwitcher")
        self.root.overrideredirect(True)
        self.root.configure(fg_color=WIN_BG)
        self.root.geometry(f"{W}x140+{r - W - 60}+{t + 40}")
        self.root.attributes("-topmost", True)        # 默认置顶
        self.root.attributes("-alpha", WINDOW_ALPHA)  # 黑色半透明

        self._build_dragbar()
        self._build_body()
        self._build_dock_icon()
        self._create_tray_icon()                      # 创建托盘图标

        self.hotkey_mgr = HotkeyManager(self._toggle_visibility)
        hk = self.config.data.get("hotkey", {})
        if hk and hk.get("vk", 0) != 0:
            self.hotkey_mgr.start(hk.get("mod", 0), hk.get("vk", 0))

        # 隐藏任务栏图标
        self.root.after(10, lambda: hide_from_taskbar(self.root))

        self.root.after(150, self._tick)
        self.root.after(3000, self._enable_dock)
        self._run_async(self.netmgr.get_all, self._on_first_load)

    # ---- 异步执行 ----
    def _run_async(self, fn, on_done):
        def w():
            try: r, e = fn(), None
            except Exception as ex: r, e = None, ex
            self.root.after(0, lambda: on_done(r, e))
        threading.Thread(target=w, daemon=True).start()

    # ---- 带有最小化和关闭按钮的拖动条 ----
    def _build_dragbar(self):
        db = ctk.CTkFrame(self.root, height=24, corner_radius=0, fg_color="transparent")
        db.pack(fill="x"); db.pack_propagate(False)
        
        # 拖动条抓手部分 (居中)
        grip_frame = ctk.CTkFrame(db, fg_color="transparent")
        grip_frame.pack(side="left", expand=True, fill="both")
        grip = ctk.CTkFrame(grip_frame, width=34, height=4, corner_radius=2, fg_color=DRAG_GRIP)
        grip.place(relx=0.5, rely=0.5, anchor="center")
        
        for w in (db, grip_frame, grip):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<Enter>", lambda e: self.root.configure(cursor="fleur"))
            w.bind("<Leave>", lambda e: self.root.configure(cursor=""))

        # 最小化和关闭按钮
        btn_close = ctk.CTkButton(db, text="×", width=24, height=24, corner_radius=0,
            fg_color="transparent", hover_color=RED, text_color=TXT, font=F(14, "bold"),
            command=self.root.destroy)
        btn_close.pack(side="right")
        ToolTip(btn_close, "关闭")

        btn_min = ctk.CTkButton(db, text="一", width=24, height=24, corner_radius=0,
            fg_color="transparent", hover_color=DBTN_H, text_color=TXT, font=F(10, "bold"),
            command=self._minimize)
        btn_min.pack(side="right")
        ToolTip(btn_min, "最小化")
        
        btn_dock = ctk.CTkButton(db, text="◨", width=24, height=24, corner_radius=0,
            fg_color="transparent", hover_color=DBTN_H, text_color=TXT, font=F(14, "bold"),
            command=self._dock_nearest)
        btn_dock.pack(side="right")
        ToolTip(btn_dock, "自动贴边")
        
        btn_hotkey = ctk.CTkButton(db, text="⌨", width=24, height=24, corner_radius=0,
            fg_color="transparent", hover_color=DBTN_H, text_color=TXT, font=F(12, "bold"),
            command=self._hotkey_dialog)
        btn_hotkey.pack(side="right")
        ToolTip(btn_hotkey, "快捷键")

    def _drag_start(self, e):
        self._drag = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y())
    def _drag_move(self, e):
        if not self._drag: return
        x0, y0, wx, wy = self._drag
        self.root.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    def _minimize(self):
        self.root.withdraw()

    def _create_tray_icon(self):
        def create_image():
            # 画一个简单的蓝色方块作为托盘图标
            img = Image.new('RGB', (64, 64), color="#1d4ed8")
            d = ImageDraw.Draw(img)
            d.rectangle([16, 16, 48, 48], outline="white", width=4)
            return img

        def on_show(icon, item):
            self.root.after(0, self._restore_from_tray)
            
        def on_exit(icon, item):
            icon.stop()
            self.root.after(0, self.root.destroy)
            
        menu = pystray.Menu(
            pystray.MenuItem("显示主界面", on_show, default=True),
            pystray.MenuItem("退出", on_exit)
        )
        self.tray_icon = pystray.Icon("IPSwitcher", create_image(), "IPSwitcher", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _restore_from_tray(self):
        if self.state == "docked":
            self._undock()
        else:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self._fit()
            
    def _toggle_visibility(self):
        self.root.after(0, self._do_toggle_visibility)

    def _do_toggle_visibility(self):
        if self.state == "docked":
            self._undock()
        elif self.root.winfo_viewable():
            self._minimize()
        else:
            self._restore_from_tray()

    # ---- 主体 ----
    def _build_body(self):
        self.main_container = ctk.CTkFrame(self.root, fg_color="transparent", corner_radius=0)
        self.main_container.pack(fill="both", expand=True)

        # ====== 左侧：局域网扫描 ======
        self.left_panel = ctk.CTkFrame(self.main_container, fg_color="transparent", width=310)
        self.left_panel.pack(side="left", fill="both", expand=True, padx=(0, 5))
        self.left_panel.pack_propagate(False)
        self._build_lan_scanner(self.left_panel)

        # 分隔线
        sep = ctk.CTkFrame(self.main_container, width=1, fg_color=CARD_B)
        sep.pack(side="left", fill="y", pady=10)

        # ====== 右侧：IP管理 ======
        body = ctk.CTkFrame(self.main_container, fg_color="transparent", width=320)
        body.pack(side="right", fill="both", expand=True)
        body.pack_propagate(False)

        f1 = ctk.CTkFrame(body, fg_color="transparent"); f1.pack(fill="x", padx=10, pady=(8, 4))
        self.adapter_menu = ctk.CTkOptionMenu(f1, values=["(无适配器)"],
            command=self._on_adapter_change, width=120, height=30, font=F(12),
            dropdown_font=F(12))
        self.adapter_menu.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(f1, text="↻", width=34, height=30, fg_color=DBTN,
            hover_color=DBTN_H, text_color=TXT, font=F(15),
            command=self._on_refresh_click).pack(side="left", padx=(0, 6))
        ctk.CTkButton(f1, text="应用", width=56, height=30, fg_color=BLUE,
            hover_color=BLUE_H, font=F(12, "bold"),
            command=self._on_apply_selected).pack(side="left")

        f2 = ctk.CTkFrame(body, fg_color="transparent"); f2.pack(fill="x", padx=10, pady=(4, 4))
        ctk.CTkLabel(f2, text="IP 方案   双击切换", text_color=SUB,
            font=F(11)).pack(side="left")
        ctk.CTkButton(f2, text="删", width=30, height=24, fg_color=DBTN,
            hover_color=DBTN_DEL_H, text_color=RED, font=F(11),
            command=self._on_delete).pack(side="right", padx=(2, 0))
        ctk.CTkButton(f2, text="改", width=30, height=24, fg_color=DBTN,
            hover_color=DBTN_H, text_color=TXT, font=F(11),
            command=self._on_edit).pack(side="right", padx=(2, 0))
        ctk.CTkButton(f2, text="＋", width=30, height=24, fg_color=GREEN,
            hover_color=GREEN_H, text_color="white", font=F(13, "bold"),
            command=self._on_add).pack(side="right")

        self.card_container = ctk.CTkFrame(body, fg_color="transparent")
        self.card_container.pack(fill="x", padx=8, pady=(0, 8))
        self.card_container.columnconfigure(0, weight=1)   # 固定2列, 宽度自适应
        self.card_container.columnconfigure(1, weight=1)

        self.toast = ctk.CTkLabel(self.root, text="", fg_color=TOAST_BG,
            text_color="white", corner_radius=6, font=F(11))

    # ---- 局域网扫描 ----
    def _build_lan_scanner(self, parent):
        top_f = ctk.CTkFrame(parent, fg_color="transparent")
        top_f.pack(fill="x", padx=10, pady=(8, 4))
        
        ctk.CTkLabel(top_f, text="起始IP", text_color=TXT, font=F(12)).pack(side="left")
        self.scan_ip_base = ctk.CTkEntry(top_f, width=120, height=30, font=F(12), placeholder_text="192.168.0.")
        self.scan_ip_base.pack(side="left", padx=8)
        
        self.btn_scan = ctk.CTkButton(top_f, text="扫描", width=60, height=30, fg_color=BLUE, hover_color=BLUE_H, font=F(12, "bold"), command=self._start_scan)
        self.btn_scan.pack(side="right")
        
        # Grid area
        self.scan_scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent", border_width=1, border_color=CARD_B, corner_radius=6)
        self.scan_scroll.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        
        # Build 254 labels
        self.scan_labels = {}
        cols = 8
        for i in range(1, 255):
            r, c = divmod(i - 1, cols)
            lbl = ctk.CTkLabel(self.scan_scroll, text=str(i), width=30, height=24,
                               fg_color=DBTN, text_color=SUB, font=F(11), corner_radius=4)
            lbl.grid(row=r, column=c, padx=2, pady=2)
            self.scan_labels[i] = lbl
            
    def _start_scan(self):
        base_ip = self.scan_ip_base.get().strip()
        cur_ip = None
        cur = self._cur()
        if cur and cur.get("ip"):
            cur_ip = cur["ip"]

        if not base_ip:
            if cur_ip:
                parts = cur_ip.split(".")
                if len(parts) == 4:
                    base_ip = f"{parts[0]}.{parts[1]}.{parts[2]}."
                    self.scan_ip_base.delete(0, "end")
                    self.scan_ip_base.insert(0, base_ip)
            if not base_ip:
                self._toast("请输入起始IP, 如 192.168.0.")
                return
        if not base_ip.endswith("."):
            base_ip += "."
            
        self.btn_scan.configure(state="disabled", text="扫描中...")
        for i in range(1, 255):
            self.scan_labels[i].configure(fg_color=DBTN, text_color=SUB) # reset
            
        threading.Thread(target=self._run_scan_worker, args=(base_ip, cur_ip), daemon=True).start()
        
    def _run_scan_worker(self, base_ip, src_ip):
        import ctypes, socket, struct
        def ping_ip(i):
            ip = f"{base_ip}{i}"
            try:
                # 1. 先用 SendARP 检查是否有这个设备的 MAC（判断是否被占用）
                dest = struct.unpack('<I', socket.inet_aton(ip))[0]
                src = struct.unpack('<I', socket.inet_aton(src_ip))[0] if src_ip else 0
                mac = ctypes.create_string_buffer(6)
                mac_len = ctypes.c_ulong(6)
                res = ctypes.windll.iphlpapi.SendARP(dest, src, mac, ctypes.byref(mac_len))
                
                if res != 0:
                    # 返回非0说明 ARP 失败，局域网中无人使用此IP -> 灰色
                    return i, "gray"
                
                # 2. 如果 ARP 成功说明有人使用，再 Ping 一次区分是通(绿)还是禁Ping(红)
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                cmd = ["ping", "-n", "1", "-w", "300"]
                if src_ip:
                    cmd.extend(["-S", src_ip])
                cmd.append(ip)
                r = subprocess.run(cmd, capture_output=True, text=True, startupinfo=startupinfo)
                out = r.stdout.lower()
                
                if "ttl=" in out:
                    return i, "green"
                else:
                    return i, "red"
            except Exception:
                return i, "gray"
                
        with concurrent.futures.ThreadPoolExecutor(max_workers=60) as executor:
            futures = [executor.submit(ping_ip, i) for i in range(1, 255)]
            for future in concurrent.futures.as_completed(futures):
                i, state = future.result()
                self.root.after(0, self._update_scan_label, i, state)
                
        self.root.after(0, lambda: self.btn_scan.configure(state="normal", text="扫描"))
        
    def _update_scan_label(self, i, state):
        if state == "green":
            self.scan_labels[i].configure(fg_color=CARD_SEL, text_color=GREEN)
        elif state == "red":
            self.scan_labels[i].configure(fg_color=CARD_SEL, text_color=RED)
        else: # gray
            self.scan_labels[i].configure(fg_color=DBTN, text_color=SUB)

    # ---- 卡片(只显示IP, 宽度自适应) ----
    def _build_cards(self):
        for w in self.card_container.winfo_children(): w.destroy()
        self.card_widgets = []
        for i, p in enumerate(self.config.data["profiles"]):
            self.card_widgets.append(self._make_card(i, p))
        self._fit()
        self._update_highlight()

    def _make_card(self, i, p):
        card = ctk.CTkFrame(self.card_container, height=CARD_H,
            corner_radius=8, border_width=1, fg_color=CARD, border_color=CARD_B)
        card.pack_propagate(False)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(expand=True, fill="x", padx=8)
        dot = ctk.CTkLabel(inner, text="●", text_color=DOT, font=F(10), width=14)
        dot.pack(side="left")
        lab = ctk.CTkLabel(inner, text=p.get("label", ""), text_color=TXT,
            font=F(12, "bold"), anchor="w")
        lab.pack(side="left", padx=(2, 0))
        for w in (card, inner, dot, lab):
            w.bind("<Button-1>", lambda e, i=i: self._select(i))
            w.bind("<Double-Button-1>", lambda e, i=i: self._apply_index(i))
        card._index, card._dot, card._lab = i, dot, lab
        return card

    def _rearrange(self):
        for i, card in enumerate(self.card_widgets):     # 固定每行2个
            r, c = divmod(i, 2)
            card.grid(row=r, column=c, sticky="ew", padx=3, pady=3)
        self.card_container.update_idletasks()

    def _cur(self):
        return self.net.get(self._cur_adapter())

    def _cur_adapter(self):
        a = self.config.data.get("adapter")
        if a in self.net: return a
        return next(iter(self.net), "")

    def _match(self, p):
        cur = self._cur()
        if not cur: return False
        if p.get("dhcp"): return bool(cur.get("dhcp"))
        return (not cur.get("dhcp")) and cur.get("ip") == p.get("ip")

    def _update_highlight(self):
        for card in self.card_widgets:
            p = self.config.data["profiles"][card._index]
            act = self._match(p); sel = (card._index == self.selected)
            
            # 使用更明显的颜色对比和图标来区分状态
            if act:
                # 激活状态（当前生效的IP）：亮蓝色背景，白色文字，打钩图标
                card.configure(fg_color="#1d4ed8", border_color="#60a5fa", border_width=2)
                card._dot.configure(text_color="#ffffff", text="✔")
                card._lab.configure(text_color="#ffffff")
            elif sel:
                # 选中状态（单击准备修改/删除）：稍亮的灰底，蓝色边框
                card.configure(fg_color="#333a44", border_color="#3b82f6", border_width=2)
                card._dot.configure(text_color="#3b82f6", text="●")
                card._lab.configure(text_color="#ffffff")
            else:
                # 普通状态：暗色底，暗色边框，文字稍暗
                card.configure(fg_color="#20242b", border_color="#333a44", border_width=1)
                card._dot.configure(text_color="#5a626d", text="●")
                card._lab.configure(text_color="#9aa1ab")

    def _select(self, i):
        self.selected = i; self._update_highlight()

    def _apply_index(self, i):
        self.selected = i
        self._do_apply(self.config.data["profiles"][i])

    def _do_apply(self, p):
        adapter = self._cur_adapter()
        if not adapter:
            self._toast("没有可用适配器"); return
            
        # 乐观更新 (Optimistic UI Update)：立即假定切换成功并高亮，让用户觉得是实时的
        if adapter in self.net:
            self.net[adapter]["dhcp"] = p.get("dhcp", False)
            self.net[adapter]["ip"] = p.get("ip", "")
            self.net[adapter]["mask"] = p.get("mask", "")
            self.net[adapter]["gateway"] = p.get("gateway", "")
            self.net[adapter]["dns"] = p.get("dns", [])
        self._update_highlight()
        
        self._toast("应用中…")
        self._run_async(lambda: self.netmgr.apply(adapter, p),
            lambda r, e: (self._toast("已应用 ✔" if not e else "失败: " + str(e)),
                          self._run_async(self.netmgr.get_all, self._on_refresh)))

    # ---- 顶部按钮回调 ----
    def _on_adapter_change(self, name):
        if name in ("(无适配器)",): return
        self.config.data["adapter"] = name; self.config.save()
        self._update_highlight()
        
        # 自动更新局域网扫描的起始IP
        cur = self._cur()
        if cur and cur.get("ip"):
            parts = cur["ip"].split(".")
            if len(parts) == 4:
                base_ip = f"{parts[0]}.{parts[1]}.{parts[2]}."
                self.scan_ip_base.delete(0, "end")
                self.scan_ip_base.insert(0, base_ip)

    def _on_refresh_click(self):
        self._toast("刷新中…")
        self._run_async(self.netmgr.get_all, self._on_refresh)

    def _on_apply_selected(self):
        if self.selected is None:
            self._toast("请先单击/双击选中一个方案"); return
        self._do_apply(self.config.data["profiles"][self.selected])

    def _on_add(self): self._edit_dialog(None)
    def _on_edit(self):
        if self.selected is None: self._toast("先选中一个方案"); return
        self._edit_dialog(self.selected)
    def _on_delete(self):
        if self.selected is None: self._toast("先选中一个方案"); return
        self.config.data["profiles"].pop(self.selected)
        self.selected = None; self.config.save(); self._build_cards()

    # ---- 编辑弹窗 ----
    def _edit_dialog(self, index):
        editing = index is not None
        p = dict(self.config.data["profiles"][index]) if editing else \
            {"dhcp": False, "ip": "", "mask": "255.255.255.0", "gateway": "", "dns": []}
        win = ctk.CTkToplevel(self.root)
        win.title("编辑方案" if editing else "新增方案")
        win.attributes("-topmost", True); win.transient(self.root)
        win.geometry(f"320x280+{self.root.winfo_x()}+{self.root.winfo_y() + 30}")
        win.grab_set()

        var = tk.StringVar(value="on" if p.get("dhcp") else "off")
        rows = []
        def row(label, key, placeholder=""):
            f = ctk.CTkFrame(win, fg_color="transparent"); f.pack(fill="x", padx=14, pady=3)
            ctk.CTkLabel(f, text=label, width=46, anchor="w", font=F(12)).pack(side="left")
            e = ctk.CTkEntry(f, height=30, font=F(12), placeholder_text=placeholder)
            e.pack(side="left", fill="x", expand=True)
            e.insert(0, str(p.get(key, "") or ""))
            rows.append((key, e)); return e

        cf = ctk.CTkFrame(win, fg_color="transparent"); cf.pack(fill="x", padx=14, pady=(12, 6))
        cb = ctk.CTkCheckBox(cf, text="自动获取 IP (DHCP)", variable=var,
            onvalue="on", offvalue="off", font=F(12), command=lambda: toggle())
        cb.pack(side="left")

        e_ip = row("IP", "ip", "192.168.1.100")
        e_mask = row("掩码", "mask", "255.255.255.0")
        e_gw = row("网关", "gateway", "192.168.1.1")
        e_dns = row("DNS", "dns", "8.8.8.8, 114.114.114.114")

        def toggle():
            st = "disabled" if var.get() == "on" else "normal"
            for _, e in rows: e.configure(state=st)
        toggle()

        bf = ctk.CTkFrame(win, fg_color="transparent"); bf.pack(fill="x", padx=14, pady=12)
        def save():
            dhcp = var.get() == "on"
            ip = e_ip.get().strip(); mask = e_mask.get().strip() or "255.255.255.0"
            gw = e_gw.get().strip()
            dns = [d.strip() for d in e_dns.get().replace("；", ",").replace(";", ",").split(",") if d.strip()]
            if not dhcp and not ip:
                self._toast("静态方案必须填写 IP"); return
            label = "自动获取(DHCP)" if dhcp else (ip or "未设置IP")
            new = {"label": label, "dhcp": dhcp, "ip": ip, "mask": mask, "gateway": gw, "dns": dns}
            if editing: self.config.data["profiles"][index] = new; si = index
            else: self.config.data["profiles"].append(new); si = len(self.config.data["profiles"]) - 1
            self.config.save(); self.selected = si
            self._build_cards(); win.destroy()
        ctk.CTkButton(bf, text="取消", width=70, height=32, fg_color=DBTN,
            hover_color=DBTN_H, text_color=TXT, font=F(12),
            command=win.destroy).pack(side="right", padx=(6, 0))
        ctk.CTkButton(bf, text="保存", width=70, height=32, fg_color=BLUE,
            hover_color=BLUE_H, font=F(12, "bold"), command=save).pack(side="right")

    # ---- 快捷键设置弹窗 ----
    def _hotkey_dialog(self):
        win = ctk.CTkToplevel(self.root)
        win.title("快捷键设置")
        win.attributes("-topmost", True); win.transient(self.root)
        win.geometry(f"280x200+{self.root.winfo_x()}+{self.root.winfo_y() + 30}")
        win.grab_set()
        
        ctk.CTkLabel(win, text="请按下新的快捷键组合\n(例如 Ctrl+Alt+K)", font=F(12)).pack(pady=(15, 5))
        
        hk = self.config.data.get("hotkey", {})
        curr_text = hk.get("text", "")
        lbl_curr = ctk.CTkLabel(win, text=f"当前绑定: {curr_text}" if curr_text else "当前未绑定快捷键", font=F(12), text_color="#aaaaaa")
        lbl_curr.pack(pady=(0, 10))
        
        lbl_key = ctk.CTkLabel(win, text="[ 等待输入 ]", font=F(14, "bold"), text_color=BLUE)
        lbl_key.pack(pady=(0, 10))
        
        recorded = {"mod": 0, "vk": 0, "text": ""}
        
        def on_key(e):
            if e.keysym in ('Control_L', 'Control_R', 'Shift_L', 'Shift_R', 'Alt_L', 'Alt_R', 'Win_L', 'Win_R'):
                return
            vk = e.keycode
            mods = 0
            parts = []
            if e.state & 0x0004: 
                mods |= 0x0002; parts.append("Ctrl")
            if e.state & 0x0001: 
                mods |= 0x0004; parts.append("Shift")
            if e.state & 131072: 
                mods |= 0x0001; parts.append("Alt")
                
            parts.append(e.keysym.upper())
            recorded["mod"] = mods
            recorded["vk"] = vk
            recorded["text"] = "+".join(parts)
            lbl_key.configure(text=recorded["text"])

        win.bind("<Key>", on_key)
        
        bf = ctk.CTkFrame(win, fg_color="transparent"); bf.pack(fill="x", padx=14, pady=(10, 0))
        def save():
            if recorded["vk"] != 0:
                self.config.data["hotkey"] = {"mod": recorded["mod"], "vk": recorded["vk"], "text": recorded["text"]}
                self.config.save()
                self.hotkey_mgr.start(recorded["mod"], recorded["vk"])
            win.destroy()
        def clear():
            self.config.data["hotkey"] = {"mod": 0, "vk": 0, "text": ""}
            self.config.save()
            self.hotkey_mgr.stop()
            lbl_key.configure(text="[ 已清除 ]")
            recorded["vk"] = 0
            
        ctk.CTkButton(bf, text="清除", width=50, height=32, fg_color=RED,
            hover_color="#cc0000", font=F(12), command=clear).pack(side="left")
        ctk.CTkButton(bf, text="取消", width=60, height=32, fg_color=DBTN,
            hover_color=DBTN_H, font=F(12), command=win.destroy).pack(side="right")
        ctk.CTkButton(bf, text="保存", width=60, height=32, fg_color=BLUE,
            hover_color=BLUE_H, font=F(12, "bold"), command=save).pack(side="right", padx=(0, 6))

    # ---- 数据加载/刷新 ----
    def _on_first_load(self, net, e):
        if e:
            self._toast("读取网络失败: " + str(e)); return
        self.net = net
        self._sync_adapter_menu()
        if not self.config.data["profiles"]:
            cur = self._cur()
            if cur:
                if cur.get("dhcp"):
                    self.config.data["profiles"].append(
                        {"label": "自动获取(DHCP)", "dhcp": True, "ip": "", "mask": "", "gateway": "", "dns": []})
                else:
                    self.config.data["profiles"].append(
                        {"label": cur.get("ip") or "当前IP", "dhcp": False,
                         "ip": cur.get("ip", ""), "mask": cur.get("mask") or "255.255.255.0",
                         "gateway": cur.get("gateway", ""), "dns": cur.get("dns", [])})
                self.config.save()
        self._build_cards()
        self._toast("提示：拖到屏幕边缘可自动吸附隐藏")

    def _on_refresh(self, net, e):
        if e: self._toast("刷新失败"); return
        self.net = net; self._sync_adapter_menu(); self._update_highlight()

    def _sync_adapter_menu(self):
        names = list(self.net.keys()) or ["(无适配器)"]
        self.adapter_menu.configure(values=names)
        if self.config.data.get("adapter") not in self.net and self.net:
            self.config.data["adapter"] = next(iter(self.net))
        cur = self.config.data.get("adapter") or (next(iter(self.net), "") if self.net else "")
        try: self.adapter_menu.set(cur or names[0])
        except Exception: pass

    # ---- 尺寸自适应(每行2个, 高度精确计算, 不裁切) ----
    def _fit(self):
        self._rearrange()
        n = len(self.config.data["profiles"])
        rows = math.ceil(n / 2)            # 每行2个
        right_h = HEADER_H + rows * ROW_STEP + 15  # 增加一点底边距避免被裁切
        h = max(right_h, 380) # 保证左侧扫描面板有足够的高度
        x = self.root.winfo_x(); y = self.root.winfo_y()
        
        # 强制将窗口坐标限制在工作区内，确保不会被屏幕边缘裁切
        l, t, r, b = workarea()
        x = clamp(x, l, r - W)
        y = clamp(y, t, b - h)
        
        self.root.geometry(f"{W}x{h}+{int(x)}+{int(y)}")

    def _toast(self, msg, ms=2200):
        self.toast.configure(text=f"  {msg}  ")
        self.toast.place(relx=0.5, rely=1.0, anchor="s", y=-6)
        self.root.after(ms, lambda: self.toast.place_forget())

    # ---- 靠边吸附隐藏 ----
    def _enable_dock(self): self.dock_enabled = True

    def _build_dock_icon(self):
        di = ctk.CTkToplevel(self.root)
        di.overrideredirect(True); di.attributes("-topmost", True)
        di.attributes("-alpha", 0.92); di.withdraw()
        di.after(10, lambda: hide_from_taskbar(di))
        b = ctk.CTkButton(di, text="IP", fg_color=BLUE, hover_color=BLUE_H,
            corner_radius=6, text_color="white", font=F(11, "bold"), command=self._undock)
        b.pack(fill="both", expand=True, padx=2, pady=2)
        # 右键托盘图标也退出
        for w in (di, b): w.bind("<Button-3>", lambda e: self.root.destroy())
        self.dock_icon = di

    def _geom(self, win):
        return win.winfo_x(), win.winfo_y(), win.winfo_width(), win.winfo_height()

    def _pointer_in(self, win):
        if not win.winfo_viewable(): return False
        px, py = cursor_pos(); x, y, w, h = self._geom(win)
        return x <= px <= x + w and y <= py <= y + h

    def _edge_side(self):
        x, y, w, h = self._geom(self.root); l, t, r, b = workarea()
        if x <= l + THR: return "left"
        if x + w >= r - THR: return "right"
        if y <= t + THR: return "top"
        return None

    def _dock_nearest(self):
        x, y, w, h = self._geom(self.root); l, t, r, b = workarea()
        cx = x + w / 2; mid = (l + r) / 2
        self._dock("left" if cx < mid else "right")

    def _dock(self, side):
        self._dock_side = side; self._dock_geom = self._geom(self.root)
        self.state = "docked"; self._away = 0
        l, t, r, b = workarea(); x, y, w, h = self._dock_geom
        if side == "left":   iw, ih, ix, iy = 24, 64, l, clamp(y, t, b - 64)
        elif side == "right":iw, ih, ix, iy = 24, 64, r - 24, clamp(y, t, b - 64)
        else:                iw, ih, ix, iy = 64, 24, clamp(x, l, r - 64), t
        self.dock_icon.geometry(f"{iw}x{ih}+{ix}+{iy}")
        self.dock_icon.deiconify(); self.dock_icon.lift()
        self.root.withdraw()

    def _undock(self):
        if self.state != "docked" and self.root.winfo_viewable():
            self.root.lift(); return
        self.state = "shown"; self._away = 0
        self.dock_icon.withdraw()
        if self._dock_geom:
            x, y, _w, _h = self._dock_geom
            l, t, r, b = workarea()
            # 弹出时，确保窗口边缘与屏幕边缘对齐
            if self._dock_side == "right":
                x = r - W
            elif self._dock_side == "left":
                x = l
            elif self._dock_side == "top":
                y = t
            self.root.geometry(f"+{int(x)}+{int(y)}")
            self.root.update_idletasks()
        self.root.deiconify()
        self.root.attributes("-topmost", True); self.root.lift()
        self._fit()

    def _tick(self):
        try:
            if self.state == "shown" and self.dock_enabled:
                side = self._edge_side()
                if side and not self._pointer_in(self.root):
                    self._away += 150
                    if self._away >= AWAY_MS: self._dock(side)
                else:
                    self._away = 0
            elif self.state == "docked":
                if self._pointer_in(self.dock_icon) or self._pointer_in(self.root):
                    self._undock()
        except Exception: pass
        self.root.after(150, self._tick)

# ---------------- 入口 ----------------
if __name__ == "__main__":
    if not is_admin():
        run_as_admin()
    ctk.set_appearance_mode("dark")      # 暗色主题, 控件自动深色
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    App(root)
    root.mainloop()
