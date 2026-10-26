"""tkinter 图形界面。"""
import json
import locale
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, auth, game, java, mods, net, theme
from .theme import (ACCENT, ACCENT2, BG, CARD, DANGER, FAMILY, FG, LOG_BG, LOG_FG, MONO, MUTED,
                    SIDEBAR, SIDEBAR_ACTIVE, SIDEBAR_HOVER, SIDEBAR_TEXT, SUB, SUCCESS)

IS_WINDOWS = sys.platform.startswith("win")
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
ACCOUNTS_PATH = os.path.join(APP_DIR, "accounts.json")
REPO_URL = "https://github.com/suqing114/meow-launcher"

DEFAULTS = {
    "game_dir": os.path.join(APP_DIR, ".minecraft"),
    "username": "Player",
    "memory": 4096,
    "source": "bmclapi",
    "threads": 32,
    "java": "",
    "width": 854,
    "height": 480,
    "jvm_args": "",
    "last_version": "",
    "account": "",
    "ms_client_id": auth.DEFAULT_CLIENT_ID,
}

SOURCES = [("BMCLAPI 镜像（国内推荐）", "bmclapi"), ("官方源", "official")]
LOADERS = [("任意", ""), ("Fabric", "fabric"), ("Forge", "forge"), ("NeoForge", "neoforge"), ("Quilt", "quilt")]
INSTALL_LOADERS = [("原版（不装加载器）", ""), ("Fabric", "fabric"), ("Forge", "forge"), ("NeoForge", "neoforge")]
SORTS = [("相关度", "relevance"), ("下载量", "downloads"), ("关注数", "follows"),
         ("最新发布", "newest"), ("最近更新", "updated")]
TYPE_NAMES = {"release": "正式版", "snapshot": "快照", "old_beta": "远古 Beta", "old_alpha": "远古 Alpha"}
LOADER_NAMES = {"fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge", "quilt": "Quilt"}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def fmt_num(n):
    if n >= 1e8:
        return f"{n / 1e8:.1f} 亿"
    if n >= 1e4:
        return f"{n / 1e4:.1f} 万"
    return str(n)


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    if IS_WINDOWS:
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def decode_line(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(locale.getpreferredencoding(False), "replace")


class Log4jXml:
    """把游戏 log4j XMLLayout 的输出还原成普通的一行行日志。"""
    _EVENT = re.compile(r'<log4j:Event logger="[^"]*" timestamp="(\d+)" level="(\w+)" thread="([^"]*)"')
    _TAGS = ("Message", "Throwable")

    def __init__(self):
        self.head = None
        self.tag = None
        self.buf = []

    def feed(self, line):
        """返回要显示的文本，或 None（还没拼完一条）。"""
        s = line.strip()
        if self.tag:
            return self._collect(line)
        m = self._EVENT.match(s)
        if m:
            ts = time.strftime("%H:%M:%S", time.localtime(int(m.group(1)) / 1000))
            self.head = f"[{ts}] [{m.group(3)}/{m.group(2)}]"
            return None
        if self.head is not None:
            for tag in self._TAGS:
                if s.startswith(f"<log4j:{tag}>"):
                    self.tag, self.buf = tag, []
                    return self._collect(s[len(tag) + 8:])
            if s.startswith("</log4j:Event>"):
                self.head = None
            return None
        return line

    def _collect(self, text):
        end = f"</log4j:{self.tag}>"
        done = text.rstrip().endswith(end)
        if done:
            text = text.rstrip()[:-len(end)]
        self.buf.append(text)
        if not done:
            return None
        msg = "\n".join(self.buf).replace("<![CDATA[", "").replace("]]>", "")
        out = f"{self.head} {msg}" if self.tag == "Message" else msg
        self.tag = None
        return out


# ---------------------------------------------------------------- 列表

def make_tree(parent, columns, headings, widths, height=8, selectmode="browse"):
    """白底列表 + 细滚动条，带斑马纹和悬停高亮（填充后调用 restripe）。"""
    frame = ttk.Frame(parent, style="Surface.TFrame")
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=height, selectmode=selectmode)
    for c, h, w in zip(columns, headings, widths):
        tree.heading(c, text=h, anchor="w")
        tree.column(c, width=w, minwidth=40, stretch=(w >= 200), anchor="w")
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    tree.tag_configure("odd", background="#fafbfe")
    tree.tag_configure("hover", background=theme.HOVER)
    tree.tag_configure("accent", foreground=ACCENT)
    tree.tag_configure("muted", foreground=MUTED)
    hovered = [None]

    def set_hover(iid):
        old = hovered[0]
        if old == iid:
            return
        if old and tree.exists(old):
            tree.item(old, tags=[t for t in tree.item(old, "tags") if t != "hover"])
        if iid:
            tree.item(iid, tags=list(tree.item(iid, "tags")) + ["hover"])
        hovered[0] = iid
    tree.bind("<Motion>", lambda e: set_hover(tree.identify_row(e.y) or None), add="+")
    tree.bind("<Leave>", lambda e: set_hover(None), add="+")
    return frame, tree


def restripe(tree):
    for i, iid in enumerate(tree.get_children()):
        tags = [t for t in tree.item(iid, "tags") if t not in ("odd", "hover")]
        tree.item(iid, tags=tags + (["odd"] if i % 2 else []))


# ---------------------------------------------------------------- 侧边栏

class NavItem(tk.Frame):
    def __init__(self, master, T, icon, text, command):
        super().__init__(master, bg=SIDEBAR, cursor="hand2")
        self.active = False
        self.bar = tk.Frame(self, bg=SIDEBAR, width=T.px(3))
        self.bar.pack(side="left", fill="y")
        self.ic = tk.Label(self, text=T.icon(icon), font=T.icon_font(12), bg=SIDEBAR, fg=SIDEBAR_TEXT, width=2)
        self.ic.pack(side="left", padx=(T.px(12), T.px(4)), pady=T.px(10))
        self.lb = tk.Label(self, text=text, font=(FAMILY, 10), bg=SIDEBAR, fg=SIDEBAR_TEXT)
        self.lb.pack(side="left")
        for w in (self, self.bar, self.ic, self.lb):
            w.bind("<Enter>", lambda e: self._paint(True))
            w.bind("<Leave>", lambda e: self.after(10, self._check_leave))
            w.bind("<Button-1>", lambda e: command())

    def _check_leave(self):
        under = self.winfo_containing(*self.winfo_pointerxy())
        self._paint(under in (self, self.bar, self.ic, self.lb))

    def set_active(self, on):
        self.active = on
        self._paint(False)

    def _paint(self, hover):
        bg = SIDEBAR_ACTIVE if self.active else (SIDEBAR_HOVER if hover else SIDEBAR)
        fg = "#ffffff" if self.active else ("#e1e4f2" if hover else SIDEBAR_TEXT)
        for w in (self, self.ic, self.lb):
            w.configure(bg=bg)
        self.lb.configure(fg=fg)
        self.ic.configure(fg="#9fb0ff" if self.active else fg)
        self.bar.configure(bg=ACCENT if self.active else bg)


# ====================================================================== App

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("喵喵启动器 MeowLauncher")
        self.geometry("1140x740")
        self.minsize(980, 640)
        self.configure(bg=BG)
        self.theme = T = theme.Theme(self)
        self.iconphoto(True, T.keep("icon64", theme.cat_logo(64)))

        self.cfg = load_config()
        net.set_source(self.cfg["source"])
        net.max_workers = int(self.cfg["threads"])
        self.gd = game.GameDir(self.cfg["game_dir"])
        self.manifest = None
        self.javas = []
        self.accounts = auth.Accounts(ACCOUNTS_PATH)
        if not self.accounts.items:  # 旧版配置里的离线用户名
            self.cfg["account"] = self.accounts.add(auth.offline_account(self.cfg.get("username") or "Player"))

        self._q = queue.Queue()
        self.after(40, self._poll)
        self._toast = None

        px = T.px
        side = tk.Frame(self, bg=SIDEBAR, width=px(212))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=SIDEBAR)
        brand.pack(fill="x", padx=px(20), pady=(px(24), px(28)))
        tk.Label(brand, image=T.keep("logo", theme.cat_logo(px(42))), bg=SIDEBAR).pack(side="left")
        names = tk.Frame(brand, bg=SIDEBAR)
        names.pack(side="left", padx=(px(10), 0))
        tk.Label(names, text="喵喵启动器", fg="#ffffff", bg=SIDEBAR, font=(FAMILY, 13, "bold")).pack(anchor="w")
        tk.Label(names, text="MeowLauncher", fg=SIDEBAR_TEXT, bg=SIDEBAR, font=(FAMILY, 9)).pack(anchor="w")

        self.content = ttk.Frame(self)
        self.content.pack(side="left", fill="both", expand=True)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        # 页面第一次打开时才创建，启动时只建首页
        self.pages, self.nav, self.page_classes = {}, {}, {}
        for key, text, icon, cls in [("launch", "启动游戏", "play", LaunchPage),
                                     ("download", "下载游戏", "download", DownloadPage),
                                     ("mods", "下载 Mod", "puzzle", ModPage),
                                     ("manage", "Mod 管理", "library", ModManagePage),
                                     ("settings", "设置", "settings", SettingsPage)]:
            self.page_classes[key] = cls
            item = NavItem(side, T, icon, text, lambda k=key: self.show(k))
            item.pack(fill="x", padx=px(12), pady=px(2))
            self.nav[key] = item

        foot = tk.Frame(side, bg=SIDEBAR)
        foot.pack(side="bottom", fill="x", padx=px(22), pady=px(18))
        gh = tk.Label(foot, text="GitHub ↗", fg=SIDEBAR_TEXT, bg=SIDEBAR, font=(FAMILY, 9), cursor="hand2")
        gh.pack(side="left")
        gh.bind("<Button-1>", lambda e: webbrowser.open(REPO_URL))
        gh.bind("<Enter>", lambda e: gh.configure(fg="#ffffff"))
        gh.bind("<Leave>", lambda e: gh.configure(fg=SIDEBAR_TEXT))
        tk.Label(foot, text=f"v{__version__}", fg="#5d6283", bg=SIDEBAR, font=(FAMILY, 9)).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show("launch")
        theme.style_titlebar(self)
        self.scan_java()

    def show(self, key):
        if key not in self.pages:
            self.pages[key] = self.page_classes[key](self.content, self)
        # 只摆放当前页；叠在下面的页面也会参与布局和绘制，切页会很卡
        for k, page in self.pages.items():
            if k == key:
                page.grid(row=0, column=0, sticky="nsew")
            else:
                page.grid_remove()
        for k, item in self.nav.items():
            item.set_active(k == key)
        self.pages[key].on_show()

    def toast(self, text, kind="success"):
        """页面底部的小提示，几秒后自动消失。"""
        T = self.theme
        if self._toast:
            self._toast.destroy()
        color, icon = {"success": (SUCCESS, "check"), "error": (DANGER, "error"),
                       "info": (ACCENT, "info")}[kind]
        f = tk.Frame(self.content, bg="#23263a")
        if T.icon_family:
            tk.Label(f, text=T.icon(icon), font=T.icon_font(12), fg=color, bg="#23263a").pack(
                side="left", padx=(T.px(16), 0), pady=T.px(11))
        tk.Label(f, text=text, fg="#ffffff", bg="#23263a", font=(FAMILY, 10)).pack(
            side="left", padx=T.px(12), pady=T.px(11))
        f.place(relx=0.5, rely=1.0, anchor="s", y=-T.px(26))
        f.lift()
        self._toast = f
        self.after(3200, lambda: f.winfo_exists() and f.destroy())

    # ---- 线程工具：后台线程只往队列里塞回调，由主线程执行
    def ui(self, fn, *args):
        self._q.put((fn, args))

    def _poll(self):
        try:
            while True:
                fn, args = self._q.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.after(40, self._poll)

    def bg(self, fn, ok=None, err=None):
        def worker():
            try:
                result = fn()
            except Exception as e:
                traceback.print_exc()
                self.ui(err or self.error, e)
            else:
                if ok:
                    self.ui(ok, result)
        threading.Thread(target=worker, daemon=True).start()

    def progress_cb(self, fn):
        """包装成线程安全、限流的进度回调 fn(text, done, total)。"""
        last = [0.0]

        def cb(text, done, total):
            now = time.time()
            if done >= total or now - last[0] > 0.08:
                last[0] = now
                self.ui(fn, text, done, total)
        return cb

    def error(self, e):
        messagebox.showerror("出错了喵", str(e), parent=self)

    def get_manifest(self, ok, err=None, force=False):
        if self.manifest and not force:
            ok(self.manifest)
            return

        def done(m):
            self.manifest = m
            ok(m)
        self.bg(game.fetch_manifest, done, err)

    def scan_java(self):
        def done(javas):
            self.javas = javas
            self.pages["launch"].set_javas(javas)
        self.bg(java.find_javas, done)

    def versions_changed(self, select=None):
        for p in self.pages.values():
            p.refresh_versions(select)

    def _on_close(self):
        try:
            self.pages["launch"].store()
            save_config(self.cfg)
        finally:
            self.destroy()


class Page(ttk.Frame):
    def __init__(self, master, app):
        self.T = app.theme
        self.px = app.theme.px
        super().__init__(master, padding=(self.px(28), self.px(22)))
        self.app = app
        self.build()

    def build(self):
        pass

    def on_show(self):
        pass

    def refresh_versions(self, select=None):
        pass

    def header(self, title, sub=""):
        h = ttk.Frame(self)
        h.pack(fill="x", pady=(0, self.px(16)))
        ttk.Label(h, text=title, style="H1.TLabel").pack(anchor="w")
        if sub:
            ttk.Label(h, text=sub, style="Sub.TLabel").pack(anchor="w", pady=(self.px(2), 0))
        return h

    def card(self, parent=None, pad=16):
        return ttk.Frame(parent or self, style="Card.TFrame", padding=self.px(pad))

    def card_title(self, parent, icon, text):
        row = ttk.Frame(parent, style="Surface.TFrame")
        if self.T.icon_family:
            ttk.Label(row, text=self.T.icon(icon), style="CardIcon.TLabel").pack(side="left", padx=(0, self.px(8)))
        ttk.Label(row, text=text, style="CardTitle.TLabel").pack(side="left")
        return row

    def row(self, parent, **pack):
        r = ttk.Frame(parent, style="Surface.TFrame")
        r.pack(fill="x", **pack)
        return r


# ====================================================================== 启动页

class Banner(tk.Canvas):
    """渐变横幅 + 大号启动按钮（画在 Canvas 上，圆角和半透明装饰都是生成的 PNG）。"""

    def __init__(self, master, T, command):
        self.T = T
        px = T.px
        self.h = px(170)
        super().__init__(master, height=self.h, bg=BG, highlightthickness=0, bd=0)
        self.command = command
        self.enabled = True
        self.hover = False
        self.title = "欢迎回来"
        self.sub = ""
        self.btn_text = "启动游戏"
        self.btn_sub = ""
        self.bw, self.bh = px(206), px(64)
        self.imgs = {k: T.keep(f"hero.{k}", theme.box(self.bw, self.bh, px(16), c))
                     for k, c in (("n", "#ffffff"), ("h", "#eef0ff"), ("d", "#dfe3fa"))}
        # 背景（渐变 + 装饰圆 + 左侧圆角）一次画成一张不透明大图，比每次重绘都叠几百个矩形和半透明图快得多
        r = px(16)
        wide = max(master.winfo_screenwidth(), px(1200))
        self.bg = T.keep("banner", theme.banner_bg(
            wide, self.h, px(900), r, ACCENT, ACCENT2, BG,
            [(px(690), px(10), px(130), 0.09), (px(435), self.h * 0.55 + px(75), px(75), 0.07)]))
        self.corners = []  # 右侧圆角跟着窗口宽度走，单独画
        for cx, cy, anchor in ((0, r, "ne"), (0, 0, "se")):
            img = theme.Raster(r, r).paint(lambda x, y, cx=cx, cy=cy: r - math.hypot(x - cx, y - cy), BG)
            self.corners.append((T.keep(f"corner.{anchor}", img), anchor))
        self._pending = None
        self.bind("<Configure>", lambda e: self.redraw())
        self.tag_bind("btn", "<Enter>", lambda e: self._set_hover(True))
        self.tag_bind("btn", "<Leave>", lambda e: self._set_hover(False))
        self.tag_bind("btn", "<Button-1>", lambda e: self.enabled and self.command())

    def _set_hover(self, on):
        self.hover = on
        self.configure(cursor="hand2" if on and self.enabled else "")
        self._draw_button()

    def set_info(self, title=None, sub=None, btn_sub=None):
        if title is not None:
            self.title = title
        if sub is not None:
            self.sub = sub
        if btn_sub is not None:
            self.btn_sub = btn_sub
        self.redraw()

    def set_enabled(self, on, text="启动游戏"):
        self.enabled, self.btn_text = on, text
        self._draw_button()

    def redraw(self):
        """合并同一轮里的多次重绘请求（启动时会连着 set_info 好几次）。"""
        if not self._pending:
            self._pending = self.after_idle(self._redraw)

    def _redraw(self):
        self._pending = None
        px = self.T.px
        self.delete("all")
        w, h = max(self.winfo_width(), 10), self.h
        self.create_image(0, 0, image=self.bg, anchor="nw")
        self.create_text(px(32), px(54), anchor="w", text=self.title, fill="#ffffff", font=(FAMILY, 18, "bold"))
        self.create_text(px(33), px(92), anchor="w", text=self.sub, fill="#e6e9ff", font=(FAMILY, 10))
        self.create_text(px(33), px(126), anchor="w", text="=^･ω･^=  准备好冒险了吗？", fill="#cfd5ff",
                         font=(FAMILY, 9))
        for img, anchor in self.corners:
            self.create_image(w, 0 if "n" in anchor else h, image=img, anchor=anchor)
        self._draw_button()

    def _draw_button(self):
        px = self.T.px
        self.delete("btn")
        w = max(self.winfo_width(), 10)
        x, y = w - px(36) - self.bw, (self.h - self.bh) / 2
        key = "d" if not self.enabled else ("h" if self.hover else "n")
        self.create_image(x, y, image=self.imgs[key], anchor="nw", tags="btn")
        color = ACCENT if self.enabled else "#8f99d6"
        cy = y + self.bh / 2
        if self.T.icon_family:
            self.create_text(x + px(30), cy, text=self.T.icon("play"), font=self.T.icon_font(16),
                             fill=color, tags="btn")
        self.create_text(x + px(54), cy - (px(9) if self.btn_sub else 0), anchor="w", text=self.btn_text,
                         fill=color, font=(FAMILY, 13, "bold"), tags="btn")
        if self.btn_sub:
            self.create_text(x + px(55), cy + px(12), anchor="w", text=self.btn_sub[:24], fill=SUB,
                             font=(FAMILY, 9), tags="btn")


class LaunchPage(Page):
    def build(self):
        cfg = self.app.cfg
        px = self.px
        self.banner = Banner(self, self.T, self.launch)
        self.banner.pack(fill="x")

        cards = ttk.Frame(self)
        cards.pack(fill="x", pady=(px(16), 0))
        for i in range(3):
            cards.columnconfigure(i, weight=1, uniform="cards")

        # 账户
        c = self.card(cards)
        c.grid(row=0, column=0, sticky="nsew", padx=(0, px(8)))
        self.card_title(c, "person", "账户").pack(fill="x")
        r = self.row(c, pady=(px(12), px(10)))
        self.av = tk.Canvas(r, width=px(40), height=px(40), bg=CARD, highlightthickness=0, bd=0)
        self.av.pack(side="left")
        info = ttk.Frame(r, style="Surface.TFrame")
        info.pack(side="left", padx=(px(10), 0))
        self.lbl_acc_name = ttk.Label(info, text="", style="CardTitle.TLabel")
        self.lbl_acc_name.pack(anchor="w")
        self.lbl_acc_type = ttk.Label(info, text="", style="CardSub.TLabel")
        self.lbl_acc_type.pack(anchor="w")
        self.cb_acc = ttk.Combobox(c, state="readonly")
        self.cb_acc.pack(fill="x")
        self.cb_acc.bind("<<ComboboxSelected>>", lambda e: self._show_account())
        r = self.row(c, pady=(px(10), 0))
        ttk.Button(r, text="添加账户", command=lambda: AddAccountDialog(self.app)).pack(side="left")
        ttk.Button(r, text="删除", style="Danger.TButton", command=self._remove_account).pack(side="left", padx=px(6))
        self._acc_ids = []

        # 游戏版本
        c = self.card(cards)
        c.grid(row=0, column=1, sticky="nsew", padx=px(8))
        self.card_title(c, "library", "游戏版本").pack(fill="x")
        self.var_ver = tk.StringVar()
        self.cb_ver = ttk.Combobox(c, textvariable=self.var_ver, state="readonly")
        self.cb_ver.pack(fill="x", pady=(px(12), px(6)))
        self.cb_ver.bind("<<ComboboxSelected>>", lambda e: self._update_info())
        self.lbl_info = ttk.Label(c, text="", style="CardSub.TLabel")
        self.lbl_info.pack(anchor="w", fill="x", pady=(0, px(8)))
        r = self.row(c)
        ttk.Button(r, text="版本文件夹", command=lambda: self._open("")).pack(side="left")
        ttk.Button(r, text="mods", command=lambda: self._open("mods")).pack(side="left", padx=px(6))
        ttk.Button(r, text="删除", style="Danger.TButton", command=self._delete).pack(side="left")

        # 运行设置
        c = self.card(cards)
        c.grid(row=0, column=2, sticky="nsew", padx=(px(8), 0))
        self.card_title(c, "settings", "运行设置").pack(fill="x")
        g = ttk.Frame(c, style="Surface.TFrame")
        g.pack(fill="x", pady=(px(12), px(10)))
        g.columnconfigure(1, weight=1)
        ttk.Label(g, text="内存 MB", style="CardSub.TLabel").grid(row=0, column=0, sticky="w", padx=(0, px(10)))
        self.var_mem = tk.IntVar(value=cfg["memory"])
        ttk.Spinbox(g, from_=512, to=65536, increment=512, textvariable=self.var_mem,
                    width=8).grid(row=0, column=1, sticky="w")
        ttk.Label(g, text="Java", style="CardSub.TLabel").grid(row=1, column=0, sticky="w", pady=(px(8), 0))
        self.var_java = tk.StringVar()
        self.cb_java = ttk.Combobox(g, textvariable=self.var_java, state="readonly", width=10)
        self.cb_java.grid(row=1, column=1, sticky="we", pady=(px(8), 0))
        r = self.row(c)
        ttk.Button(r, text="重新扫描", command=self.app.scan_java).pack(side="left")
        ttk.Button(r, text="手动选择…", command=self._browse_java).pack(side="left", padx=px(6))

        # 日志
        lc = ttk.Frame(self, style="LogCard.TFrame", padding=(px(16), px(12)))
        lc.pack(fill="both", expand=True, pady=(px(16), 0))
        head = ttk.Frame(lc, style="Log.TFrame")
        head.pack(fill="x", pady=(0, px(6)))
        if self.T.icon_family:
            ttk.Label(head, text=self.T.icon("code"), style="LogIcon.TLabel").pack(side="left", padx=(0, px(8)))
        ttk.Label(head, text="游戏日志", style="Log.TLabel").pack(side="left")
        self.lbl_status = ttk.Label(head, text="", style="LogSub.TLabel")
        self.lbl_status.pack(side="left", padx=px(12))
        ttk.Button(head, text="清空", style="LogGhost.TButton",
                   command=lambda: self.txt.delete("1.0", "end")).pack(side="right")
        body = ttk.Frame(lc, style="Log.TFrame")
        body.pack(fill="both", expand=True)
        self.txt = tk.Text(body, bg=LOG_BG, fg=LOG_FG, insertbackground=LOG_FG, selectbackground="#3a4170",
                           font=(MONO, 9), relief="flat", bd=0, highlightthickness=0, wrap="none", height=6)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.txt.yview, style="Log.Vertical.TScrollbar")
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.txt.tag_configure("warn", foreground="#f2c94c")
        self.txt.tag_configure("error", foreground="#ff8080")
        self.txt.tag_configure("meow", foreground="#a5b4ff")
        self.log("[喵喵启动器] 准备就绪，点击右上角的「启动游戏」开始吧～")

        self.refresh_versions(cfg.get("last_version"))
        self.refresh_accounts(cfg.get("account"))

    # ---- 账户
    def refresh_accounts(self, select=None):
        items = self.app.accounts.items
        self._acc_ids = [a["id"] for a in items]
        self.cb_acc["values"] = [auth.describe(a) for a in items]
        cur = select or self._selected_account_id()
        if cur in self._acc_ids:
            self.cb_acc.current(self._acc_ids.index(cur))
        elif items:
            self.cb_acc.current(0)
        else:
            self.cb_acc.set("还没有账户")
        self._show_account()

    def _selected_account_id(self):
        i = self.cb_acc.current()
        return self._acc_ids[i] if 0 <= i < len(self._acc_ids) else None

    def _show_account(self):
        acc = self.app.accounts.get(self._selected_account_id())
        self.av.delete("all")
        if not acc:
            self.lbl_acc_name.configure(text="未登录")
            self.lbl_acc_type.configure(text="点「添加账户」")
            self.banner.set_info(title="欢迎来到喵喵启动器")
            return
        n = self.px(40)
        self.av.create_image(n / 2, n / 2, image=self.T.avatar(acc["name"], 40))
        self.av.create_text(n / 2, n / 2, text=acc["name"][:1].upper(), fill="#ffffff", font=(FAMILY, 13, "bold"))
        kind = auth.TYPE_NAMES.get(acc["type"], acc["type"])
        if acc["type"] == "authlib":
            kind += f" · {acc.get('server_name')}"
        self.lbl_acc_name.configure(text=acc["name"])
        self.lbl_acc_type.configure(text=kind)
        self.banner.set_info(title=f"欢迎回来，{acc['name']}")

    def _remove_account(self):
        aid = self._selected_account_id()
        acc = aid and self.app.accounts.get(aid)
        if acc and messagebox.askyesno("删除账户", f"确定删除账户 {auth.describe(acc)} 吗？"):
            self.app.accounts.remove(aid)
            self.refresh_accounts()

    # ---- 版本
    def refresh_versions(self, select=None):
        vs = self.app.gd.list_versions()
        self.cb_ver["values"] = vs
        cur = select or self.var_ver.get()
        self.var_ver.set(cur if cur in vs else (vs[0] if vs else ""))
        self._update_info()

    def _update_info(self):
        vid = self.var_ver.get()
        if not vid:
            self.lbl_info.configure(text="还没有安装任何版本")
            self.banner.set_info(sub="还没有安装游戏，先去「下载游戏」装一个吧", btn_sub="")
            return
        try:
            gv = self.app.gd.game_version_of(vid)
            loader = LOADER_NAMES.get(self.app.gd.loader_of(vid), "原版")
            need = self.app.gd.required_java(vid)
            self.lbl_info.configure(text=f"{gv} · {loader} · 需要 Java {need}")
            self.banner.set_info(sub=f"当前版本  {vid}    ·    {loader}    ·    Java {need}", btn_sub=vid)
        except Exception as e:
            self.lbl_info.configure(text=f"版本文件有问题：{e}")
            self.banner.set_info(sub="版本文件有问题", btn_sub=vid)

    # ---- Java
    def set_javas(self, javas):
        want = self.app.cfg.get("java", "")
        known = [j.path for j in javas]
        self._java_paths = [""] + known + ([want] if want and want not in known else [])

        def label(j):
            folder = os.path.basename(os.path.dirname(os.path.dirname(j.path)))
            return f"Java {j.major} · {folder}"
        self.cb_java["values"] = ["自动选择"] + [label(j) for j in javas] + (
            [f"自定义 · {want}"] if want and want not in known else [])
        self.cb_java.current(self._java_paths.index(want) if want in self._java_paths else 0)

    def _selected_java(self):
        i = self.cb_java.current()
        paths = getattr(self, "_java_paths", [""])
        return paths[i] if 0 <= i < len(paths) else ""

    def _browse_java(self):
        p = filedialog.askopenfilename(title="选择 java 可执行文件",
                                       filetypes=[("Java", "java.exe javaw.exe java")] if IS_WINDOWS else [])
        if p:
            self.app.cfg["java"] = os.path.normpath(p)
            self.set_javas(self.app.javas)

    def store(self):
        cfg = self.app.cfg
        cfg["account"] = self._selected_account_id() or ""
        try:
            cfg["memory"] = int(self.var_mem.get())
        except (tk.TclError, ValueError):
            pass
        cfg["java"] = self._selected_java()
        cfg["last_version"] = self.var_ver.get()

    def _open(self, sub):
        vid = self.var_ver.get()
        if vid:
            open_folder(os.path.join(self.app.gd.version_dir(vid), sub))

    def _delete(self):
        vid = self.var_ver.get()
        if vid and messagebox.askyesno("删除版本", f"确定要删除 {vid} 吗？\n存档、mods 等该版本目录下的文件都会被删除！"):
            self.app.gd.delete_version(vid)
            self.app.versions_changed()
            self.app.toast(f"已删除 {vid}")

    # ---- 日志
    def log(self, line):
        tag = ("meow" if line.startswith("[喵喵启动器]") else
               "error" if "/ERROR]" in line or "/FATAL]" in line or "Exception" in line else
               "warn" if "/WARN]" in line else ())
        self.txt.insert("end", line + "\n", tag)
        if int(self.txt.index("end-1c").split(".")[0]) > 6000:
            self.txt.delete("1.0", "1000.0")
        self.txt.see("end")

    def _progress(self, text, done, total):
        self.lbl_status.configure(text=f"{text} {done}/{total}" if total > 1 else text)

    def launch(self):
        vid = self.var_ver.get()
        if not vid:
            self.app.toast("请先在「下载游戏」里安装一个版本喵", "info")
            return
        acc = self.app.accounts.get(self._selected_account_id())
        if not acc:
            self.app.toast("请先添加一个账户喵", "info")
            return
        self.store()
        save_config(self.app.cfg)
        cfg = self.app.cfg
        java_sel = cfg["java"]
        progress = self.app.progress_cb(self._progress)
        self.banner.set_enabled(False, "启动中…")
        self.log(f"[喵喵启动器] 准备启动 {vid} ……")

        def work():
            gd = self.app.gd
            progress("检查账户", 1, 1)
            login = auth.prepare(acc, gd.root, cfg["ms_client_id"])
            self.app.accounts.save()  # 令牌可能刚刷新过
            gd.complete(vid, progress)
            required = gd.required_java(vid)
            if java_sel:
                j = java.info(java_sel)
                if not j:
                    raise RuntimeError(f"无法识别选择的 Java：{java_sel}")
            else:
                j = java.pick(self.app.javas or java.find_javas(), required)
                if not j:
                    raise RuntimeError(f"没有找到 Java！这个版本需要 Java {required}，请先安装。")
            progress("正在启动", 1, 1)
            cmd, cwd = gd.build_launch_command(vid, j.path, login, cfg["memory"],
                                               cfg["width"], cfg["height"], cfg["jvm_args"])
            flags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
            proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, creationflags=flags)
            return proc, j, required

        def started(res):
            proc, j, required = res
            self.log(f"[喵喵启动器] 使用 {j}")
            if j.major < required:
                self.log(f"[喵喵启动器] 警告：该版本需要 Java {required}，当前 Java {j.major} 可能无法启动")
            self.lbl_status.configure(text=f"游戏运行中 · PID {proc.pid}")
            self.app.toast("游戏启动啦，祝玩得开心喵～")
            self.after(3000, lambda: self.banner.set_enabled(True))
            threading.Thread(target=self._read_output, args=(proc,), daemon=True).start()

        def failed(e):
            self.banner.set_enabled(True)
            self.lbl_status.configure(text="启动失败")
            self.log(f"[喵喵启动器] 启动失败：{e}")
            self.app.error(e)

        self.app.bg(work, started, failed)

    def _read_output(self, proc):
        parser = Log4jXml()
        for raw in proc.stdout:
            text = parser.feed(decode_line(raw).rstrip("\r\n"))
            if text is not None:
                self.app.ui(self.log, text)
        code = proc.wait()
        self.app.ui(self.log, f"[喵喵启动器] 游戏已退出，退出码 {code}")
        self.app.ui(self.lbl_status.configure, {"text": f"游戏已退出（{code}）"})


# ====================================================================== 添加账户

class AddAccountDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.T = app.theme
        px = self.T.px
        self.title("添加账户")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app)
        self._cancel = False
        self._device = None
        self._ygg = None
        self.protocol("WM_DELETE_WINDOW", self._close)

        wrap = ttk.Frame(self, padding=(px(20), px(16)))
        wrap.pack(fill="both", expand=True)
        ttk.Label(wrap, text="添加账户", style="H1.TLabel").pack(anchor="w")
        ttk.Label(wrap, text="选择登录方式", style="Sub.TLabel").pack(anchor="w", pady=(0, px(12)))
        nb = ttk.Notebook(wrap)
        nb.pack(fill="both", expand=True)
        nb.add(self._offline_tab(nb), text="离线")
        nb.add(self._ms_tab(nb), text="微软正版")
        nb.add(self._ygg_tab(nb), text="外置登录")

        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_width()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        theme.style_titlebar(self)
        self.grab_set()

    def _tab(self, nb):
        f = ttk.Frame(nb, style="Surface.TFrame", padding=self.T.px(20))
        f.columnconfigure(1, weight=1)
        return f

    def _done(self, acc):
        aid = self.app.accounts.add(acc)
        self.app.pages["launch"].refresh_accounts(aid)
        self.app.toast(f"已添加账户 {acc['name']}")
        if self.winfo_exists():
            self._close()

    def _close(self):
        self._cancel = True
        self.grab_release()
        self.destroy()

    # ---- 离线
    def _offline_tab(self, nb):
        px = self.T.px
        f = self._tab(nb)
        ttk.Label(f, text="用户名", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, px(12)))
        var = tk.StringVar()
        e = ttk.Entry(f, textvariable=var, width=30)
        e.grid(row=0, column=1, sticky="we")
        ttk.Label(f, text="3–16位字母、数字或下划线。离线账户只能进单人或关闭了正版验证的服务器。",
                  style="CardSub.TLabel", wraplength=px(400)).grid(row=1, column=0, columnspan=2, sticky="w",
                                                                    pady=(px(10), px(16)))

        def add():
            name = var.get().strip()
            if not re.fullmatch(r"\w{3,16}", name, re.ASCII):
                if not name or not messagebox.askyesno("用户名", "用户名不太规范，可能进不了部分服务器，仍然使用吗？",
                                                       parent=self):
                    return
            self._done(auth.offline_account(name))
        e.bind("<Return>", lambda ev: add())
        ttk.Button(f, text="添加", style="Accent.TButton", command=add).grid(row=2, column=1, sticky="e")
        return f

    # ---- 微软
    def _ms_tab(self, nb):
        px = self.T.px
        f = self._tab(nb)
        has_id = bool(self.app.cfg.get("ms_client_id"))
        ttk.Label(f, text="使用设备码登录：点「开始登录」后，在浏览器里输入下面显示的代码并登录微软账号。"
                  + ("" if has_id else "\n\n⚠ 还没有填写 Client ID，请先到「设置」页填写。"),
                  style="Card.TLabel", wraplength=px(420), justify="left").grid(row=0, column=0, columnspan=2,
                                                                               sticky="w")
        self.lbl_code = tk.Label(f, text="· · · ·", bg=CARD, fg=ACCENT, font=(MONO, 22, "bold"))
        self.lbl_code.grid(row=1, column=0, columnspan=2, pady=px(16))
        self.btn_ms = ttk.Button(f, text="开始登录", style="Accent.TButton", command=self._ms_start)
        self.btn_ms.grid(row=2, column=0, sticky="w")
        self.btn_open = ttk.Button(f, text="复制代码并打开网页", command=self._ms_open, state="disabled")
        self.btn_open.grid(row=2, column=1, sticky="e")
        self.lbl_ms = ttk.Label(f, text="", style="CardSub.TLabel", wraplength=px(420), justify="left")
        self.lbl_ms.grid(row=3, column=0, columnspan=2, sticky="w", pady=(px(12), 0))
        return f

    def _ms_start(self):
        cid = self.app.cfg.get("ms_client_id", "")
        self.btn_ms.state(["disabled"])
        self.lbl_ms.configure(text="正在申请登录代码……")

        def got_code(device):
            if self._cancel:
                return
            self._device = device
            self.lbl_code.configure(text=device["user_code"])
            self.btn_open.state(["!disabled"])
            self.lbl_ms.configure(text=f"请打开 {device['verification_uri']} 输入上面的代码，完成后这里会自动继续……")
            self._ms_open()
            self.app.bg(lambda: auth.ms_finish(cid, device, lambda: self._cancel), logged_in, failed)

        def logged_in(acc):
            if not self._cancel:
                self._done(acc)

        def failed(e):
            if not self._cancel:
                self.btn_ms.state(["!disabled"])
                self.lbl_code.configure(text="· · · ·")
                self.lbl_ms.configure(text=f"登录失败：{e}")
        self.app.bg(lambda: auth.ms_start(cid), got_code, failed)

    def _ms_open(self):
        if self._device:
            self.clipboard_clear()
            self.clipboard_append(self._device["user_code"])
            webbrowser.open(self._device["verification_uri"])

    # ---- 外置
    def _ygg_tab(self, nb):
        px = self.T.px
        f = self._tab(nb)
        self.var_srv = tk.StringVar(value="littleskin.cn")
        self.var_login = tk.StringVar()
        self.var_pw = tk.StringVar()
        for r, (text, var, show) in enumerate((("服务器地址", self.var_srv, ""), ("邮箱 / 用户名", self.var_login, ""),
                                               ("密码", self.var_pw, "•"))):
            ttk.Label(f, text=text, style="Card.TLabel").grid(row=r, column=0, sticky="w", pady=px(4),
                                                              padx=(0, px(12)))
            ttk.Entry(f, textvariable=var, width=34, show=show).grid(row=r, column=1, sticky="we", pady=px(4))
        ttk.Label(f, text="支持任何兼容 authlib-injector 的皮肤站，例如 LittleSkin。密码只用来登录，不会被保存。",
                  style="CardSub.TLabel", wraplength=px(420)).grid(row=3, column=0, columnspan=2, sticky="w",
                                                                    pady=px(8))
        self.cb_prof = ttk.Combobox(f, state="readonly")
        self.btn_ygg = ttk.Button(f, text="登录", style="Accent.TButton", command=self._ygg_login)
        self.btn_ygg.grid(row=5, column=1, sticky="e", pady=(px(6), 0))
        self.lbl_ygg = ttk.Label(f, text="", style="CardSub.TLabel", wraplength=px(420))
        self.lbl_ygg.grid(row=6, column=0, columnspan=2, sticky="w", pady=(px(8), 0))
        return f

    def _ygg_login(self):
        if self._ygg:  # 第二步：选了角色
            acc, profiles = self._ygg
            prof = profiles[self.cb_prof.current()]
            self.btn_ygg.state(["disabled"])
            self.app.bg(lambda: auth.yggdrasil_select(acc, prof) or acc, self._done, self._ygg_failed)
            return
        srv, login, pw = self.var_srv.get(), self.var_login.get().strip(), self.var_pw.get()
        if not (srv.strip() and login and pw):
            self.lbl_ygg.configure(text="请把服务器、账号和密码都填上")
            return
        self.btn_ygg.state(["disabled"])
        self.lbl_ygg.configure(text="登录中……")

        def ok(res):
            acc, profiles = res
            self.var_pw.set("")
            if acc["name"]:
                self._done(acc)
                return
            if len(profiles) == 1:
                self.app.bg(lambda: auth.yggdrasil_select(acc, profiles[0]) or acc, self._done, self._ygg_failed)
                return
            self._ygg = (acc, profiles)
            self.cb_prof.configure(values=[p["name"] for p in profiles])
            self.cb_prof.current(0)
            self.cb_prof.grid(row=4, column=1, sticky="we", pady=self.T.px(4))
            ttk.Label(self.cb_prof.master, text="选择角色", style="Card.TLabel").grid(row=4, column=0, sticky="w")
            self.btn_ygg.configure(text="使用这个角色")
            self.btn_ygg.state(["!disabled"])
            self.lbl_ygg.configure(text="这个账号有多个角色，请选一个")
        self.app.bg(lambda: auth.yggdrasil_login(srv, login, pw), ok, self._ygg_failed)

    def _ygg_failed(self, e):
        self.btn_ygg.state(["!disabled"])
        self.lbl_ygg.configure(text=f"失败：{e}")


# ====================================================================== 下载游戏

class DownloadPage(Page):
    def build(self):
        px = self.px
        self.header("下载游戏", "安装任意版本的 Minecraft，可以顺手装上 Fabric / Forge / NeoForge")
        self.selected = None
        self._loader_list = []
        self._token = 0

        bar = self.card(pad=12)
        bar.pack(fill="x", pady=(0, px(14)))
        r = self.row(bar)
        if self.T.icon_family:
            ttk.Label(r, text=self.T.icon("search"), style="CardIcon.TLabel").pack(side="left", padx=(px(4), px(8)))
        self.var_filter = tk.StringVar()
        self.var_filter.trace_add("write", lambda *a: self.fill())
        ttk.Entry(r, textvariable=self.var_filter, width=24).pack(side="left")
        self.var_types = {}
        for t, default in (("release", True), ("snapshot", False), ("old_beta", False)):
            var = tk.BooleanVar(value=default)
            self.var_types[t] = var
            ttk.Checkbutton(r, text={"old_beta": "远古版"}.get(t, TYPE_NAMES[t]), variable=var,
                            style="Card.TCheckbutton", command=self.fill).pack(side="left", padx=(px(18), 0))
        ttk.Button(r, text="刷新列表", command=lambda: self.load(force=True)).pack(side="right")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        left = self.card(body, pad=6)
        left.pack(side="left", fill="both", expand=True)
        frame, self.tree = make_tree(left, ("id", "type", "time", "state"), ("版本", "类型", "发布时间", "状态"),
                                     (240, 110, 140, 100))
        frame.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        panel = self.card(body, pad=20)
        panel.configure(width=px(310))
        panel.pack(side="right", fill="y", padx=(px(14), 0))
        panel.pack_propagate(False)
        ttk.Label(panel, text="已选择", style="CardSub.TLabel").pack(anchor="w")
        self.lbl_sel = ttk.Label(panel, text="还没选版本", style="CardBig.TLabel")
        self.lbl_sel.pack(anchor="w")
        self.lbl_sel_sub = ttk.Label(panel, text="在左边列表里点一个版本", style="CardSub.TLabel")
        self.lbl_sel_sub.pack(anchor="w", pady=(0, px(14)))
        ttk.Separator(panel).pack(fill="x", pady=(0, px(14)))
        ttk.Label(panel, text="Mod 加载器", style="Card.TLabel").pack(anchor="w")
        self.cb_loader = ttk.Combobox(panel, state="readonly", values=[n for n, _ in INSTALL_LOADERS])
        self.cb_loader.current(0)
        self.cb_loader.pack(fill="x", pady=(px(4), px(12)))
        self.cb_loader.bind("<<ComboboxSelected>>", lambda e: self._load_loader())
        self.lbl_lv = ttk.Label(panel, text="加载器版本", style="Card.TLabel")
        self.lbl_lv.pack(anchor="w")
        self.cb_lv = ttk.Combobox(panel, state="disabled")
        self.cb_lv.pack(fill="x", pady=(px(4), px(20)))
        self.btn_install = ttk.Button(panel, text="安装", style="Big.Accent.TButton", command=self.install)
        self.btn_install.pack(fill="x")
        self.bar = ttk.Progressbar(panel, mode="determinate")
        self.bar.pack(fill="x", pady=(px(18), px(6)))
        self.lbl_status = ttk.Label(panel, text="", style="CardSub.TLabel", wraplength=px(230), justify="left")
        self.lbl_status.pack(anchor="w", fill="x")
        self.lbl_status.bind("<Configure>", lambda e: self.lbl_status.configure(wraplength=max(e.width, 100)))

    def on_show(self):
        if not self.app.manifest:
            self.load()

    def load(self, force=False):
        self.lbl_status.configure(text="正在获取版本列表……")

        def ok(m):
            latest = m.get("latest", {})
            self.lbl_status.configure(text=f"最新正式版 {latest.get('release')}，最新快照 {latest.get('snapshot')}")
            self.fill()

        def err(e):
            self.lbl_status.configure(text=f"获取版本列表失败：{e}")
        self.app.get_manifest(ok, err, force)

    def fill(self):
        m = self.app.manifest
        if not m:
            return
        kw = self.var_filter.get().strip().lower()
        show_old = self.var_types["old_beta"].get()
        self.tree.delete(*self.tree.get_children())
        installed = set(self.app.gd.list_versions())
        for v in m["versions"]:
            t = v["type"]
            if t.startswith("old_"):
                if not show_old:
                    continue
            elif not self.var_types.get(t, tk.BooleanVar(value=False)).get():
                continue
            if kw and kw not in v["id"].lower():
                continue
            done = v["id"] in installed
            self.tree.insert("", "end", iid=v["id"], tags=("accent",) if done else (),
                             values=(v["id"], TYPE_NAMES.get(t, t), v["releaseTime"][:10], "✔ 已安装" if done else ""))
        restripe(self.tree)

    def refresh_versions(self, select=None):
        self.fill()

    def _on_select(self, _):
        sel = self.tree.selection()
        if not sel:
            return
        self.selected = sel[0]
        v = next((x for x in self.app.manifest["versions"] if x["id"] == self.selected), {})
        self.lbl_sel.configure(text=self.selected)
        self.lbl_sel_sub.configure(text=f"{TYPE_NAMES.get(v.get('type'), '')} · 发布于 {v.get('releaseTime', '')[:10]}")
        self._load_loader()

    def _load_loader(self):
        name, kind = INSTALL_LOADERS[self.cb_loader.current()]
        self._token += 1
        token = self._token
        self._loader_list = []
        self.lbl_lv.configure(text=f"{name} 版本" if kind else "加载器版本")
        if not kind or not self.selected:
            self.cb_lv.configure(state="disabled")
            self.cb_lv.set("")
            return
        gv = self.selected
        self.cb_lv.configure(state="disabled")
        self.cb_lv.set("加载中……")
        stable_word = "推荐" if kind == "forge" else "稳定"

        def ok(lst):
            if token != self._token:
                return
            self._loader_list = lst
            if not lst:
                self.cb_lv.set(f"{gv} 没有可用的 {name}")
                return
            self.cb_lv.configure(state="readonly",
                                 values=[f"{x['version']}{f'（{stable_word}）' if x['stable'] else ''}" for x in lst])
            self.cb_lv.current(next((i for i, x in enumerate(lst) if x["stable"]), 0))

        def err(e):
            if token == self._token:
                self.cb_lv.set("获取失败")
                self.lbl_status.configure(text=f"获取 {name} 版本失败：{e}")
        self.app.bg(lambda: game.loader_versions(kind, gv), ok, err)

    def _progress(self, text, done, total):
        self.bar.configure(maximum=max(total, 1), value=done)
        self.lbl_status.configure(text=f"{text}：{done}/{total}" if total > 1 else f"{text}……")

    def install(self):
        if not self.selected:
            self.app.toast("先在左边选一个版本喵", "info")
            return
        gv = self.selected
        gd, manifest = self.app.gd, self.app.manifest
        progress = self.app.progress_cb(self._progress)
        name, kind = INSTALL_LOADERS[self.cb_loader.current()]
        if kind:
            i = self.cb_lv.current()
            if i < 0 or not self._loader_list:
                self.app.toast(f"请先选择 {name} 版本", "info")
                return
            lv = self._loader_list[i]["version"]
            if kind == "fabric":
                work = lambda: gd.install_fabric(gv, lv, progress, manifest)
            else:
                work = lambda: gd.install_forge(kind, gv, lv, progress, manifest)
        else:
            work = lambda: gd.install_vanilla(gv, progress, manifest)
        self.btn_install.state(["disabled"])
        self.btn_install.configure(text="安装中…")

        def ok(vid):
            self.btn_install.state(["!disabled"])
            self.btn_install.configure(text="安装")
            self.lbl_status.configure(text=f"安装完成：{vid}")
            self.app.versions_changed(select=vid)
            self.app.toast(f"{vid} 安装好啦，去「启动游戏」开玩吧！")

        def err(e):
            self.btn_install.state(["!disabled"])
            self.btn_install.configure(text="安装")
            self.lbl_status.configure(text=f"安装失败：{e}")
            self.app.error(e)
        self.app.bg(work, ok, err)


# ====================================================================== 下载 Mod

class ModPage(Page):
    def build(self):
        px = self.px
        self.header("下载 Mod", "数据来自 Modrinth，用英文名搜索效果最好（如 sodium、jei），会自动下载前置")
        self.page = 0
        self.total = 0
        self.hits = {}
        self.versions = {}
        self._search_token = 0
        self._ver_token = 0

        top = self.card(pad=14)
        top.pack(fill="x", pady=(0, px(14)))
        r = self.row(top)
        if self.T.icon_family:
            ttk.Label(r, text=self.T.icon("search"), style="CardIcon.TLabel").pack(side="left", padx=(px(4), px(8)))
        self.var_q = tk.StringVar()
        e = ttk.Entry(r, textvariable=self.var_q)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.search(0))
        ttk.Button(r, text="搜索", style="Accent.TButton", command=lambda: self.search(0)).pack(
            side="left", padx=(px(10), 0))
        r = self.row(top, pady=(px(12), 0))

        def field(text, widget):
            ttk.Label(r, text=text, style="CardSub.TLabel").pack(side="left", padx=(0, px(6)))
            widget.pack(side="left", padx=(0, px(18)))
            return widget
        self.var_inst = tk.StringVar()
        self.cb_inst = field("安装到", ttk.Combobox(r, textvariable=self.var_inst, state="readonly", width=28))
        self.cb_inst.bind("<<ComboboxSelected>>", lambda ev: self._sync_filters())
        self.var_gv = tk.StringVar()
        field("游戏版本", ttk.Combobox(r, textvariable=self.var_gv, width=9))
        self.cb_loader = field("加载器", ttk.Combobox(r, state="readonly", width=9, values=[n for n, _ in LOADERS]))
        self.cb_loader.current(0)
        self.cb_sort = field("排序", ttk.Combobox(r, state="readonly", width=8, values=[n for n, _ in SORTS]))
        self.cb_sort.current(0)
        self.cb_sort.bind("<<ComboboxSelected>>", lambda ev: self.search(0))

        # 底栏先固定在底部，避免窗口矮的时候被列表挤出去
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x", pady=(px(12), 0))
        self.btn_prev = ttk.Button(bottom, text="‹ 上一页", style="Bg.TButton", command=lambda: self.search(self.page - 1))
        self.btn_prev.pack(side="left")
        self.lbl_page = ttk.Label(bottom, text="", style="Sub.TLabel")
        self.lbl_page.pack(side="left", padx=px(12))
        self.btn_next = ttk.Button(bottom, text="下一页 ›", style="Bg.TButton", command=lambda: self.search(self.page + 1))
        self.btn_next.pack(side="left")
        self.lbl_status = ttk.Label(bottom, text="", style="Sub.TLabel")
        self.lbl_status.pack(side="right")

        pane = ttk.Panedwindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = self.card(pane, pad=6)
        frame, self.tree = make_tree(left, ("title", "author", "dl", "desc"),
                                     ("名称", "作者", "下载量", "简介"), (190, 100, 80, 300))
        frame.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_pick)
        pane.add(left, weight=3)

        right = self.card(pane, pad=16)
        pane.add(right, weight=2)
        self.lbl_title = ttk.Label(right, text="选择一个 Mod", style="CardBig.TLabel")
        self.lbl_title.pack(anchor="w")
        self.lbl_meta = ttk.Label(right, text="在左边列表里点一个 Mod 查看可用版本", style="CardSub.TLabel")
        self.lbl_meta.pack(anchor="w")
        self.lbl_desc = ttk.Label(right, text="", style="Card.TLabel", justify="left")
        self.lbl_desc.pack(anchor="w", fill="x", pady=(px(8), px(10)))
        right.bind("<Configure>", lambda ev: self.lbl_desc.configure(wraplength=max(ev.width - px(36), 100)))
        r = ttk.Frame(right, style="Surface.TFrame")
        r.pack(side="bottom", fill="x", pady=(px(12), 0))
        vframe, self.vtree = make_tree(right, ("name", "gv", "loader", "date"),
                                       ("版本", "游戏版本", "加载器", "日期"), (200, 110, 90, 90), height=6)
        vframe.pack(fill="both", expand=True)
        self.vtree.bind("<Double-1>", lambda e: self.download())
        self.var_deps = tk.BooleanVar(value=True)
        ttk.Checkbutton(r, text="同时下载必需前置", variable=self.var_deps, style="Card.TCheckbutton").pack(side="left")
        self.btn_dl = ttk.Button(r, text="下载所选版本", style="Accent.TButton", command=self.download)
        self.btn_dl.pack(side="right")
        ttk.Button(r, text="网页", style="Ghost.TButton", command=self._open_web).pack(side="right", padx=px(4))

        self.refresh_versions()
        self._searched = False

    def on_show(self):
        if not self._searched:
            self._searched = True
            self.search(0)

    def refresh_versions(self, select=None):
        vs = self.app.gd.list_versions()
        self.cb_inst["values"] = vs
        cur = select or self.var_inst.get()
        new = cur if cur in vs else (vs[0] if vs else "")
        changed = new != self.var_inst.get()
        self.var_inst.set(new)
        if changed or select:
            self._sync_filters()

    def _sync_filters(self):
        vid = self.var_inst.get()
        if not vid:
            return
        gd = self.app.gd
        self.var_gv.set(gd.game_version_of(vid))
        loader = gd.loader_of(vid)
        codes = [c for _, c in LOADERS]
        self.cb_loader.current(codes.index(loader) if loader in codes else 0)
        if not loader:
            self.lbl_status.configure(text=f"提示：{vid} 是原版，没装加载器的话 Mod 不会生效哦")

    def _filters(self):
        return self.var_gv.get().strip() or None, LOADERS[self.cb_loader.current()][1] or None

    def search(self, page):
        if page < 0:
            return
        gv, loader = self._filters()
        q = self.var_q.get().strip()
        sort = SORTS[self.cb_sort.current()][1]
        self._search_token += 1
        token = self._search_token
        self.lbl_status.configure(text="搜索中……")

        def ok(res):
            if token != self._search_token:
                return
            self.page = page
            self.total = res.get("total_hits", 0)
            self.hits = {h["project_id"]: h for h in res["hits"]}
            self.tree.delete(*self.tree.get_children())
            for h in res["hits"]:
                self.tree.insert("", "end", iid=h["project_id"], values=(
                    h["title"], h.get("author", ""), fmt_num(h.get("downloads", 0)), h.get("description", "")))
            restripe(self.tree)
            pages = max(1, -(-self.total // mods.PAGE_SIZE))
            self.lbl_page.configure(text=f"第 {page + 1} / {pages} 页 · 共 {self.total} 个")
            self.btn_prev.state(["!disabled"] if page > 0 else ["disabled"])
            self.btn_next.state(["!disabled"] if page + 1 < pages else ["disabled"])
            self.lbl_status.configure(text="" if self.total else "什么都没搜到喵，换个关键词或放宽筛选试试")

        def err(e):
            if token == self._search_token:
                self.lbl_status.configure(text=f"搜索失败：{e}")
        self.app.bg(lambda: mods.search(q, gv, loader, sort, page * mods.PAGE_SIZE), ok, err)

    def _on_pick(self, _):
        sel = self.tree.selection()
        if not sel:
            return
        pid = sel[0]
        h = self.hits.get(pid, {})
        self.lbl_title.configure(text=h.get("title", ""))
        self.lbl_meta.configure(text=f"by {h.get('author')}  ·  {fmt_num(h.get('downloads', 0))} 次下载")
        self.lbl_desc.configure(text=h.get("description", ""))
        gv, loader = self._filters()
        self._ver_token += 1
        token = self._ver_token
        self.vtree.delete(*self.vtree.get_children())
        self.lbl_status.configure(text="获取版本列表……")

        def ok(vs):
            if token != self._ver_token:
                return
            self.versions = {v["id"]: v for v in vs}
            for v in vs:
                gvs = v.get("game_versions", [])
                gv_text = gvs[-1] + (f" 等 {len(gvs)} 个" if len(gvs) > 1 else "") if gvs else ""
                self.vtree.insert("", "end", iid=v["id"], values=(
                    v.get("name") or v.get("version_number"), gv_text,
                    ", ".join(v.get("loaders", [])), v.get("date_published", "")[:10]))
            restripe(self.vtree)
            if vs:
                self.vtree.selection_set(vs[0]["id"])
                self.lbl_status.configure(text=f"找到 {len(vs)} 个版本，双击即可安装")
            else:
                self.lbl_status.configure(text=f"没有适配 {gv or '任意版本'} / {loader or '任意加载器'} 的版本")

        def err(e):
            if token == self._ver_token:
                self.lbl_status.configure(text=f"获取版本失败：{e}")
        self.app.bg(lambda: mods.project_versions(pid, gv, loader), ok, err)

    def _open_web(self):
        sel = self.tree.selection()
        if sel:
            h = self.hits.get(sel[0], {})
            webbrowser.open(f"https://modrinth.com/mod/{h.get('slug') or sel[0]}")

    def download(self):
        inst = self.var_inst.get()
        if not inst:
            self.app.toast("还没有安装游戏版本，先去「下载游戏」装一个喵", "info")
            return
        sel = self.vtree.selection()
        if not sel:
            self.app.toast("请先在右侧选择一个 Mod 版本", "info")
            return
        v = self.versions[sel[0]]
        gv, loader = self._filters()
        mods_dir = self.app.gd.mods_dir(inst)
        deps = self.var_deps.get()
        log = lambda s: self.app.ui(self.lbl_status.configure, {"text": s})
        self.btn_dl.state(["disabled"])

        def ok(result):
            self.btn_dl.state(["!disabled"])
            new = sum(1 for _, n in result if n)
            self.lbl_status.configure(text="、".join(n for n, _ in result))
            self.app.toast(f"已安装到 {inst}：新下载 {new} 个，共 {len(result)} 个文件")

        def err(e):
            self.btn_dl.state(["!disabled"])
            self.lbl_status.configure(text=f"下载失败：{e}")
            self.app.error(e)
        self.app.bg(lambda: mods.install(v, mods_dir, gv, loader, deps, log), ok, err)


# ====================================================================== Mod 管理

class ModManagePage(Page):
    def build(self):
        px = self.px
        self.header("Mod 管理", "查看、启用 / 禁用、删除某个版本里已安装的 Mod")
        bar = self.card(pad=12)
        bar.pack(fill="x", pady=(0, px(14)))
        r = self.row(bar)
        ttk.Label(r, text="版本", style="CardSub.TLabel").pack(side="left", padx=(px(4), px(8)))
        self.var_inst = tk.StringVar()
        self.cb = ttk.Combobox(r, textvariable=self.var_inst, state="readonly", width=34)
        self.cb.pack(side="left")
        self.cb.bind("<<ComboboxSelected>>", lambda e: self.fill())
        ttk.Button(r, text="打开 mods 文件夹", command=self.open).pack(side="right")
        ttk.Button(r, text="删除", style="Danger.TButton", command=self.delete).pack(side="right", padx=px(6))
        ttk.Button(r, text="启用 / 禁用", command=self.toggle).pack(side="right")
        ttk.Button(r, text="刷新", command=self.fill).pack(side="right", padx=px(6))

        box = self.card(pad=6)
        box.pack(fill="both", expand=True)
        frame, self.tree = make_tree(box, ("name", "state", "size"), ("文件名", "状态", "大小"),
                                     (480, 100, 100), height=10, selectmode="extended")
        frame.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.toggle())
        self.lbl = ttk.Label(self, text="", style="Sub.TLabel")
        self.lbl.pack(anchor="w", pady=(px(10), 0))
        self.refresh_versions()

    def on_show(self):
        self.fill()

    def refresh_versions(self, select=None):
        vs = self.app.gd.list_versions()
        self.cb["values"] = vs
        cur = select or self.var_inst.get()
        self.var_inst.set(cur if cur in vs else (vs[0] if vs else ""))
        self.fill()

    def _dir(self):
        vid = self.var_inst.get()
        return self.app.gd.mods_dir(vid) if vid else None

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        d = self._dir()
        if not d or not os.path.isdir(d):
            self.lbl.configure(text="这个版本还没有 Mod，去「下载 Mod」挑几个吧")
            return
        files = sorted(f for f in os.listdir(d) if f.endswith((".jar", ".jar.disabled")))
        for f in files:
            on = f.endswith(".jar")
            self.tree.insert("", "end", iid=f, tags=() if on else ("muted",), values=(
                f.removesuffix(".disabled"), "● 已启用" if on else "○ 已禁用",
                fmt_size(os.path.getsize(os.path.join(d, f)))))
        restripe(self.tree)
        enabled = sum(f.endswith(".jar") for f in files)
        self.lbl.configure(text=f"共 {len(files)} 个 Mod，已启用 {enabled} 个 · 双击切换启用状态，可按住 Ctrl 多选")

    def toggle(self):
        d = self._dir()
        for f in self.tree.selection():
            src = os.path.join(d, f)
            dst = src.removesuffix(".disabled") if f.endswith(".disabled") else src + ".disabled"
            os.replace(src, dst)
        self.fill()

    def delete(self):
        sel = self.tree.selection()
        if sel and messagebox.askyesno("删除 Mod", f"确定删除选中的 {len(sel)} 个 Mod 吗？"):
            for f in sel:
                os.remove(os.path.join(self._dir(), f))
            self.fill()
            self.app.toast(f"已删除 {len(sel)} 个 Mod")

    def open(self):
        d = self._dir()
        if d:
            open_folder(d)


# ====================================================================== 设置

class SettingsPage(Page):
    def build(self):
        cfg = self.app.cfg
        px = self.px
        self.header("设置", "修改后记得点「保存设置」")

        def grid_card(parent, icon, title):
            c = self.card(parent, pad=18)
            self.card_title(c, icon, title).pack(fill="x", pady=(0, px(12)))
            g = ttk.Frame(c, style="Surface.TFrame")
            g.pack(fill="x")
            g.columnconfigure(1, weight=1)
            return c, g

        def label(g, r, t):
            ttk.Label(g, text=t, style="Card.TLabel").grid(row=r, column=0, sticky="w", pady=px(5), padx=(0, px(16)))

        c, g = grid_card(self, "folder", "游戏")
        c.pack(fill="x")
        label(g, 0, "游戏目录")
        row = ttk.Frame(g, style="Surface.TFrame")
        row.grid(row=0, column=1, sticky="we")
        self.var_dir = tk.StringVar(value=cfg["game_dir"])
        ttk.Entry(row, textvariable=self.var_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览…", command=self._browse).pack(side="left", padx=px(6))
        ttk.Button(row, text="打开", command=lambda: open_folder(self.var_dir.get())).pack(side="left")
        label(g, 1, "游戏窗口大小")
        row = ttk.Frame(g, style="Surface.TFrame")
        row.grid(row=1, column=1, sticky="w")
        self.var_w = tk.IntVar(value=cfg["width"])
        self.var_h = tk.IntVar(value=cfg["height"])
        ttk.Spinbox(row, from_=320, to=7680, textvariable=self.var_w, width=7).pack(side="left")
        ttk.Label(row, text="  ×  ", style="Card.TLabel").pack(side="left")
        ttk.Spinbox(row, from_=240, to=4320, textvariable=self.var_h, width=7).pack(side="left")
        label(g, 2, "额外 JVM 参数")
        self.var_jvm = tk.StringVar(value=cfg["jvm_args"])
        ttk.Entry(g, textvariable=self.var_jvm).grid(row=2, column=1, sticky="we")

        two = ttk.Frame(self)
        two.pack(fill="x", pady=(px(14), 0))
        two.columnconfigure(0, weight=2, uniform="s")
        two.columnconfigure(1, weight=3, uniform="s")
        c, g = grid_card(two, "download", "下载")
        c.grid(row=0, column=0, sticky="nsew", padx=(0, px(7)))
        label(g, 0, "下载源")
        self.cb_src = ttk.Combobox(g, state="readonly", values=[n for n, _ in SOURCES])
        codes = [c for _, c in SOURCES]
        self.cb_src.current(codes.index(cfg["source"]) if cfg["source"] in codes else 0)
        self.cb_src.grid(row=0, column=1, sticky="we")
        label(g, 1, "下载线程数")
        self.var_threads = tk.IntVar(value=cfg["threads"])
        ttk.Spinbox(g, from_=1, to=128, textvariable=self.var_threads, width=7).grid(row=1, column=1, sticky="w")

        c, g = grid_card(two, "person", "微软登录")
        c.grid(row=0, column=1, sticky="nsew", padx=(px(7), 0))
        label(g, 0, "Client ID")
        self.var_cid = tk.StringVar(value=cfg["ms_client_id"])
        ttk.Entry(g, textvariable=self.var_cid).grid(row=0, column=1, sticky="we")
        row = ttk.Frame(g, style="Surface.TFrame")
        row.grid(row=1, column=1, sticky="w", pady=(px(6), 0))
        ttk.Button(row, text="注册 Azure 应用", style="Ghost.TButton",
                   command=lambda: webbrowser.open(auth.AZURE_REGISTER_URL)).pack(side="left")
        ttk.Button(row, text="申请 Mojang 审核", style="Ghost.TButton",
                   command=lambda: webbrowser.open(auth.MOJANG_REVIEW_URL)).pack(side="left")
        ttk.Label(g, text="已内置喵喵启动器的 Client ID，一般不用改。", style="CardSub.TLabel").grid(
            row=2, column=1, sticky="w", pady=(px(4), 0))

        foot = ttk.Frame(self)
        foot.pack(fill="x", pady=(px(18), 0))
        ttk.Button(foot, text="保存设置", style="Big.BgAccent.TButton", command=self.save).pack(side="left")
        ttk.Label(foot, text="账户保存在 accounts.json（含登录令牌，不要发给别人）\n"
                             "每个版本的存档 / Mod / 配置都单独放在 versions/<版本名> 下（版本隔离）",
                  style="Sub.TLabel", justify="left").pack(side="left", padx=px(18))

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.var_dir.get())
        if d:
            self.var_dir.set(os.path.normpath(d))

    def save(self):
        cfg = self.app.cfg
        try:
            cfg["threads"] = max(1, int(self.var_threads.get()))
            cfg["width"] = int(self.var_w.get())
            cfg["height"] = int(self.var_h.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("设置", "请输入有效的数字")
            return
        cfg["game_dir"] = self.var_dir.get().strip() or DEFAULTS["game_dir"]
        cfg["source"] = SOURCES[self.cb_src.current()][1]
        cfg["jvm_args"] = self.var_jvm.get().strip()
        cfg["ms_client_id"] = self.var_cid.get().strip()
        net.set_source(cfg["source"])
        net.max_workers = cfg["threads"]
        self.app.gd = game.GameDir(cfg["game_dir"])
        self.app.pages["launch"].store()
        save_config(cfg)
        self.app.versions_changed()
        self.app.toast("设置已保存喵～")


def main():
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()
