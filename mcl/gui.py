"""tkinter 图形界面。"""
import json
import locale
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from . import __version__, auth, game, java, mods, net, runtime, theme
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
                                     ("mods", "下载资源", "puzzle", ModPage),
                                     ("manage", "资源管理", "library", ModManagePage),
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
        theme.style_titlebar(self)
        # 先让窗口和加载遮罩显示出来，再建首页
        self._boot = Overlay(self.content, T, "正在加载…", bg=BG)
        self.after(30, self._first_show)

    def _first_show(self):
        self.show("launch")
        self.scan_java()

    def show(self, key):
        if getattr(self, "_building", False):
            return
        if key not in self.pages:
            # 页面第一次打开要现建，先盖一层遮罩画出来，免得像是点了没反应
            ov = getattr(self, "_boot", None) or Overlay(self.content, self.theme, "加载中…", bg=BG)
            self._boot = None
            self._building = True
            try:
                ov.update()  # Windows 上新窗口要处理一轮消息才会画出来
                self.pages[key] = self.page_classes[key](self.content, self)
            finally:
                self._building = False
                ov.close()
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
        # 每轮最多处理 30ms，剩下的下一轮接着做，避免一次清空大量回调把界面卡住
        deadline = time.perf_counter() + 0.03
        try:
            while time.perf_counter() < deadline:
                fn, args = self._q.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.after(40 if self._q.empty() else 1, self._poll)

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

    def java_roots(self):
        """除了系统里装的，还要扫描启动器自己下载的 Java。"""
        return [runtime.runtime_root(self.gd.root)]

    def set_javas(self, javas):
        self.javas = javas
        if "launch" in self.pages:
            self.pages["launch"].set_javas(javas)

    def scan_java(self):
        roots = self.java_roots()
        self.bg(lambda: java.find_javas(roots), self.set_javas)

    def versions_changed(self, select=None):
        for p in self.pages.values():
            p.refresh_versions(select)

    def _on_close(self):
        try:
            if "launch" in self.pages:
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
        if on == self.hover:
            return
        self.hover = on
        self._update_button()

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
        self._update_button()

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
        """创建按钮的画布元素，只在整体重绘时调用。

        悬停 / 启用状态变化只用 _update_button 改属性，不能删了重建：
        删掉鼠标下的元素会触发 <Leave>，重建又触发 <Enter>，会无限循环把界面卡死。
        """
        px = self.T.px
        w = max(self.winfo_width(), 10)
        x, y = w - px(36) - self.bw, (self.h - self.bh) / 2
        cy = y + self.bh / 2
        self._btn_img = self.create_image(x, y, anchor="nw", tags="btn")
        self._btn_icon = self.create_text(x + px(30), cy, text=self.T.icon("play"), font=self.T.icon_font(16),
                                          tags="btn")
        self._btn_label = self.create_text(x + px(54), cy - (px(9) if self.btn_sub else 0), anchor="w",
                                           font=(FAMILY, 13, "bold"), tags="btn")
        self.create_text(x + px(55), cy + px(12), anchor="w", text=self.btn_sub[:24], fill=SUB,
                         font=(FAMILY, 9), tags="btn")
        self._update_button()

    def _update_button(self):
        if not getattr(self, "_btn_img", None):
            return
        key = "d" if not self.enabled else ("h" if self.hover else "n")
        color = ACCENT if self.enabled else "#8f99d6"
        self.itemconfigure(self._btn_img, image=self.imgs[key])
        self.itemconfigure(self._btn_icon, fill=color)
        self.itemconfigure(self._btn_label, text=self.btn_text, fill=color)
        self.configure(cursor="hand2" if self.hover and self.enabled else "")


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
        ttk.Button(r, text="管理", command=self._manage).pack(side="left")
        ttk.Button(r, text="文件夹", command=lambda: self._open("")).pack(side="left", padx=px(6))
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
        ttk.Button(r, text="下载 Java", command=lambda: JavaDownloadDialog(self.app)).pack(side="left")
        ttk.Button(r, text="手动选择…", command=self._browse_java).pack(side="left", padx=px(6))
        ttk.Button(r, text="↻", style="Ghost.TButton", width=2, command=self.app.scan_java).pack(side="left")

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
        self._log_buf = []
        self._log_lock = threading.Lock()
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
            info = self.app.gd.instance_info(vid)
            gv = info["game"]
            loader = " + ".join(f"{game.LOADER_NAMES[k]} {game.short_version(k, gv, v)}"
                                for k, v in info["loaders"].items()) or "原版"
            need = self.app.gd.required_java(vid)
            self.lbl_info.configure(text=f"{gv} · {loader}")
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

    def _manage(self):
        vid = self.var_ver.get()
        if vid:
            InstanceDialog(self.app, vid)

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
    LOG_MAX = 5000  # 文本框里最多保留的行数

    @staticmethod
    def _log_tag(line):
        return ("meow" if line.startswith("[喵喵启动器]") else
                "error" if "/ERROR]" in line or "/FATAL]" in line or "Exception" in line else
                "warn" if "/WARN]" in line else "")

    def log(self, *lines):
        """一次插入多行（只调用一次 insert / see，大量日志时也不卡）。"""
        args = []
        for line in lines:
            args += [line + "\n", self._log_tag(line)]
        self.txt.insert("end", *args)
        n = int(self.txt.index("end-1c").split(".")[0])
        if n > self.LOG_MAX:
            self.txt.delete("1.0", f"{n - self.LOG_MAX}.0")
        self.txt.see("end")

    def _queue_log(self, line):
        """后台线程调用：先攒进缓冲区，界面空下来时一批写进去。"""
        with self._log_lock:
            self._log_buf.append(line)
            first = len(self._log_buf) == 1
        if first:
            self.app.ui(self._flush_log)

    def _flush_log(self):
        with self._log_lock:
            buf, self._log_buf = self._log_buf, []
        # 一批太多时只显示最后 800 行：一次插入几千行也要卡半秒
        if len(buf) > 800:
            skipped = len(buf) - 800
            buf = [f"[喵喵启动器] （日志刷得太快，省略了 {skipped} 行）"] + buf[-800:]
        if buf:
            self.log(*buf)

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
                javas = java.find_javas(self.app.java_roots())  # 每次重新扫描，刚下载的 Java 也能用上
                self.app.ui(self.app.set_javas, javas)
                if runtime.needs_download(javas, required):
                    raise NeedJava(required, runtime.component_for(gd.resolve(vid), required))
                j = java.pick(javas, required)
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
            if isinstance(e, NeedJava):
                self.lbl_status.configure(text=f"缺少 Java {e.major}")
                self.log(f"[喵喵启动器] 这个版本需要 Java {e.major}，本机没有合适的")
                if messagebox.askyesno("缺少 Java", f"{vid} 需要 Java {e.major}，本机没有合适的。\n\n"
                                       "要自动下载并安装吗？（Mojang 官方提供，约 100–200 MB）", parent=self.app):
                    JavaDownloadDialog(self.app, auto=e.component, on_done=self.launch)
                return
            self.lbl_status.configure(text="启动失败")
            self.log(f"[喵喵启动器] 启动失败：{e}")
            self.app.error(e)

        self.app.bg(work, started, failed)

    def _read_output(self, proc):
        parser = Log4jXml()
        for raw in proc.stdout:
            text = parser.feed(decode_line(raw).rstrip("\r\n"))
            if text is not None:
                self._queue_log(text)
        code = proc.wait()
        self._queue_log(f"[喵喵启动器] 游戏已退出，退出码 {code}")
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


# ====================================================================== 通用部件

class Overlay(tk.Frame):
    """加载遮罩：盖在某个区域上，中间一个转圈和一行字。"""

    def __init__(self, target, T, text="加载中…", bg=CARD, inset=0):
        super().__init__(target, bg=bg)
        self.T = T
        n = T.px(34)
        self.n = n
        self.cv = tk.Canvas(self, width=n, height=n, bg=bg, highlightthickness=0, bd=0)
        self.cv.place(relx=0.5, rely=0.42, anchor="center")
        self.lbl = tk.Label(self, text=text, bg=bg, fg=SUB, font=(FAMILY, 10))
        self.lbl.place(relx=0.5, rely=0.42, anchor="n", y=n // 2 + T.px(10))
        i = T.px(inset)
        self.place(x=i, y=i, relwidth=1, relheight=1, width=-2 * i, height=-2 * i)
        self.lift()
        self.angle = 90
        self._job = None
        self._spin()

    def _spin(self):
        w = self.T.px(3.5)
        n = self.n
        self.cv.delete("all")
        self.cv.create_oval(w, w, n - w, n - w, outline=theme.LINE, width=w)
        self.cv.create_arc(w, w, n - w, n - w, start=self.angle, extent=110, style="arc", outline=ACCENT, width=w)
        self.angle = (self.angle - 14) % 360
        self._job = self.after(33, self._spin)

    def show_message(self, text):
        """停止转圈，只显示一行提示（加载失败、没有结果时用）。"""
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        self.cv.place_forget()
        self.lbl.configure(text=text)
        self.lbl.place(relx=0.5, rely=0.42, anchor="center", y=0)

    def close(self):
        if self._job:
            self.after_cancel(self._job)
        self.destroy()


def overlay_once(holder, attr, target, T, text, **kw):
    """在 target 上换一个新遮罩，旧的先关掉；返回新遮罩。"""
    old = getattr(holder, attr, None)
    if old is not None and old.winfo_exists():
        old.close()
    ov = Overlay(target, T, text, **kw)
    setattr(holder, attr, ov)
    return ov


def close_overlay(holder, attr):
    ov = getattr(holder, attr, None)
    if ov is not None and ov.winfo_exists():
        ov.close()
    setattr(holder, attr, None)


class Segmented(ttk.Frame):
    """一排互斥的按钮（类似选项卡）。"""

    def __init__(self, master, items, command, style_bg="Surface.TFrame"):
        super().__init__(master, style=style_bg)
        self.command = command
        self.buttons = {}
        for key, text in items:
            b = ttk.Button(self, text=text, command=lambda k=key: self.select(k, True))
            b.pack(side="left", padx=(0, 4))
            self.buttons[key] = b
        self.current = items[0][0]
        self.select(self.current)

    def select(self, key, notify=False):
        self.current = key
        for k, b in self.buttons.items():
            b.configure(style="Accent.TButton" if k == key else "TButton")
        if notify:
            self.command(key)


def center_on(win, parent):
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_height()) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")


# ====================================================================== 加载器选择

KIND_ORDER = ["forge", "neoforge", "fabric", "quilt", "optifine"]


def default_instance_name(gv, sel):
    if not sel:
        return gv or ""
    parts = [gv]
    for k in KIND_ORDER:
        if k in sel:
            spec = sel[k]
            v = spec["version"] if isinstance(spec, dict) else spec
            parts.append(f"{game.LOADER_NAMES[k]}-{game.short_version(k, gv, v)}")
    return "-".join(parts)


class LoaderPicker(ttk.Frame):
    """HMCL 式的加载器多选：每个加载器一行，冲突的会变灰并说明原因。"""

    def __init__(self, master, app, on_change=None):
        super().__init__(master, style="Surface.TFrame")
        self.app = app
        self.T = app.theme
        self.on_change = on_change
        self.gv = None
        self.sel = {}
        self.of_forge = ""
        self.rows = {}
        px = self.T.px
        self.columnconfigure(1, weight=1)
        for i, kind in enumerate(KIND_ORDER):
            name = ttk.Label(self, text=game.LOADER_NAMES[kind], style="Card.TLabel", width=9)
            status = ttk.Label(self, text="", style="CardSub.TLabel")
            btn = ttk.Button(self, text="选择", command=lambda k=kind: self.pick(k))
            bx = ttk.Button(self, text="✕", style="Ghost.TButton", width=2, command=lambda k=kind: self.remove(k))
            name.grid(row=i, column=0, sticky="w", pady=px(3))
            status.grid(row=i, column=1, sticky="w", padx=(px(6), px(6)))
            btn.grid(row=i, column=2, sticky="e")
            bx.grid(row=i, column=3, sticky="e", padx=(px(4), 0))
            self.rows[kind] = (name, status, btn, bx)
        self.warn = ttk.Label(self, text="", style="CardDanger.TLabel", wraplength=px(280), justify="left")
        self.warn.grid(row=len(KIND_ORDER), column=0, columnspan=4, sticky="w", pady=(px(4), 0))
        self.bind("<Configure>", lambda e: self.warn.configure(wraplength=max(e.width - 4, 100)))
        self.refresh()

    def set_game(self, gv, sel=None):
        self.gv = gv
        self.sel = dict(sel or {})
        self.of_forge = ""
        self.refresh()

    def selection(self):
        return dict(self.sel)

    def remove(self, kind):
        self.sel.pop(kind, None)
        self.refresh()
        if self.on_change:
            self.on_change()

    def pick(self, kind):
        VersionPickerDialog(self.app, kind, self.gv, self._picked)

    def _picked(self, kind, spec, info=None):
        self.sel[kind] = spec
        if kind == "optifine":  # BMCLAPI 会标出这个 OptiFine 是配哪个 Forge 测试的
            self.of_forge = ((info or {}).get("forge") or "").replace("Forge", "").strip()
        self.refresh()
        if self.on_change:
            self.on_change()

    def refresh(self):
        for kind, (name, status, btn, bx) in self.rows.items():
            blockers = [game.LOADER_NAMES[o] for o in self.sel if o in game.CONFLICTS[kind]]
            if kind in self.sel:
                spec = self.sel[kind]
                v = spec["version"] if isinstance(spec, dict) else spec
                text = game.short_version(kind, self.gv or "", v) + ("（本机文件）" if isinstance(spec, dict) else "")
                name.configure(style="Card.TLabel")
                status.configure(text=text, style="CardAccent.TLabel")
                btn.configure(text="更换")
                btn.state(["!disabled"])
                bx.grid()
            elif blockers:
                name.configure(style="CardMuted.TLabel")
                status.configure(text=f"与 {blockers[0]} 冲突", style="CardSub.TLabel")
                btn.configure(text="选择")
                btn.state(["disabled"])
                bx.grid_remove()
            else:
                name.configure(style="Card.TLabel" if self.gv else "CardMuted.TLabel")
                status.configure(text="不安装" if self.gv else "先选择游戏版本", style="CardSub.TLabel")
                btn.configure(text="选择")
                btn.state(["!disabled"] if self.gv else ["disabled"])
                bx.grid_remove()
        # OptiFine 和新版 Forge 经常不兼容，BMCLAPI 标了它是配哪个 Forge 测试的
        want, warn = self.of_forge, ""
        if "optifine" in self.sel and "forge" in self.sel and want and want != "N/A":
            fs = self.sel["forge"]
            have = game.short_version("forge", self.gv or "", fs["version"] if isinstance(fs, dict) else fs)
            if have != want:
                warn = f"⚠ 这个 OptiFine 是配 Forge {want} 测试的，搭配 Forge {have} 可能会崩溃"
        self.warn.configure(text=warn)
        self.warn.grid() if warn else self.warn.grid_remove()


class VersionPickerDialog(tk.Toplevel):
    """选择某个加载器的版本，或者用本机文件。"""

    def __init__(self, app, kind, gv, on_pick):
        super().__init__(app)
        self.app, self.kind, self.gv, self.on_pick = app, kind, gv, on_pick
        self.T = app.theme
        px = self.T.px
        name = game.LOADER_NAMES[kind]
        self.title(f"选择 {name} 版本")
        self.configure(bg=BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._closed = False
        self._info = {}
        wrap = ttk.Frame(self, padding=(px(20), px(16)))
        wrap.pack(fill="both", expand=True)
        ttk.Label(wrap, text=name, style="H1.TLabel").pack(anchor="w")
        ttk.Label(wrap, text=f"Minecraft {gv}", style="Sub.TLabel").pack(anchor="w", pady=(0, px(12)))
        bottom = ttk.Frame(wrap)
        bottom.pack(side="bottom", fill="x", pady=(px(14), 0))
        ttk.Button(bottom, text="从本机文件…", style="Bg.TButton", command=self._local).pack(side="left")
        ttk.Button(bottom, text="取消", style="Bg.TButton", command=self._close).pack(side="right")
        self.btn_ok = ttk.Button(bottom, text="确定", style="BgAccent.TButton", command=self._ok)
        self.btn_ok.pack(side="right", padx=px(8))
        self.btn_ok.state(["disabled"])
        box = ttk.Frame(wrap, style="Card.TFrame", padding=px(6))
        box.pack(fill="both", expand=True)
        frame, self.tree = make_tree(box, ("ver", "tag"), ("版本", "说明"), (220, 170), height=12)
        frame.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self._ok())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.btn_ok.state(["!disabled"]))
        self.geometry(f"{px(480)}x{px(540)}")
        center_on(self, app)
        theme.style_titlebar(self)
        self.grab_set()
        self.ov = Overlay(box, self.T, f"正在获取 {name} 版本…", inset=6)
        app.bg(lambda: game.loader_versions(kind, gv), self._fill, self._fail)

    def _fill(self, lst):
        if self._closed:
            return
        self.ov.close()
        self._info = {x["version"]: x for x in lst}
        word = {"forge": "推荐", "optifine": "正式版"}.get(self.kind, "稳定版")
        for x in lst:
            tag = word if x["stable"] else ("预览版" if self.kind == "optifine" else "测试版")
            if self.kind == "optifine" and x.get("forge"):
                tag += f" · {x['forge']}"
            self.tree.insert("", "end", iid=x["version"], values=(game.short_version(self.kind, self.gv, x["version"]), tag),
                             tags=("accent",) if x["stable"] else ())
        restripe(self.tree)
        if lst:
            first = next((x["version"] for x in lst if x["stable"]), lst[0]["version"])
            self.tree.selection_set(first)
            self.tree.see(first)
        else:
            self.ov = Overlay(self.tree.master.master, self.T, "", inset=6)
            self.ov.show_message(f"Minecraft {self.gv} 没有可用的 {game.LOADER_NAMES[self.kind]}")

    def _fail(self, e):
        if not self._closed:
            self.ov.show_message(f"获取失败：{e}")

    def _ok(self):
        sel = self.tree.selection()
        if sel:
            self.on_pick(self.kind, sel[0], self._info.get(sel[0]))
            self._close()

    def _local(self):
        ft = [("版本配置", "*.json")] if self.kind in ("fabric", "quilt") else [("Jar 文件", "*.jar")]
        path = filedialog.askopenfilename(parent=self, title=f"选择本机的 {game.LOADER_NAMES[self.kind]} 文件",
                                          filetypes=ft + [("所有文件", "*.*")])
        if not path:
            return
        try:
            kind, gv, ver = game.inspect_local_file(path)
        except Exception as e:
            messagebox.showerror("无法使用这个文件", str(e), parent=self)
            return
        if kind != self.kind:
            messagebox.showerror("文件不对", f"这是 {game.LOADER_NAMES[kind]} 的文件，不是 {game.LOADER_NAMES[self.kind]}",
                                 parent=self)
            return
        if gv and gv != self.gv:
            messagebox.showerror("版本不匹配", f"这个文件是给 Minecraft {gv} 用的，当前是 {self.gv}", parent=self)
            return
        self.on_pick(self.kind, {"version": ver, "file": os.path.normpath(path)})
        self._close()

    def _close(self):
        self._closed = True
        self.grab_release()
        self.destroy()


class InstanceDialog(tk.Toplevel):
    """管理已安装的版本：更换、卸载、加装加载器（可用本机文件）。"""

    def __init__(self, app, vid):
        super().__init__(app)
        self.app, self.vid = app, vid
        self.T = app.theme
        px = self.T.px
        gd = app.gd
        self.info = gd.instance_info(vid)
        self.gv = self.info["game"]
        self.title("管理版本")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._close)
        wrap = ttk.Frame(self, padding=(px(22), px(18)))
        wrap.pack(fill="both", expand=True)
        ttk.Label(wrap, text=vid, style="H1.TLabel").pack(anchor="w")
        ttk.Label(wrap, text=f"Minecraft {self.gv}", style="Sub.TLabel").pack(anchor="w", pady=(0, px(12)))

        card = ttk.Frame(wrap, style="Card.TFrame", padding=px(18))
        card.pack(fill="x")
        ttk.Label(card, text="模组加载器", style="CardTitle.TLabel").pack(anchor="w", pady=(0, px(8)))
        self.picker = LoaderPicker(card, app, self._changed)
        self.picker.pack(fill="x")
        self.picker.set_game(self.gv, self.info["loaders"])
        self.var_name = tk.StringVar(value=vid)
        if vid == self.gv:
            r = ttk.Frame(card, style="Surface.TFrame")
            r.pack(fill="x", pady=(px(12), 0))
            ttk.Label(r, text="新版本名称", style="Card.TLabel").pack(side="left", padx=(0, px(10)))
            ttk.Entry(r, textvariable=self.var_name).pack(side="left", fill="x", expand=True)
            note = "这是原版版本，其他版本都依赖它；加装加载器会另存为一个新版本。"
        else:
            note = "更换或卸载加载器不会删除存档和配置；mods 里的 Mod 可能要换成对应加载器的版本。"
        ttk.Label(card, text=note, style="CardSub.TLabel", wraplength=px(430), justify="left").pack(
            anchor="w", pady=(px(12), 0))
        self.bar = ttk.Progressbar(card, mode="determinate")
        self.lbl = ttk.Label(card, text="", style="CardSub.TLabel", wraplength=px(430), justify="left")

        bottom = ttk.Frame(wrap)
        bottom.pack(fill="x", pady=(px(16), 0))
        ttk.Button(bottom, text="取消", style="Bg.TButton", command=self._close).pack(side="right")
        self.btn = ttk.Button(bottom, text="应用更改", style="BgAccent.TButton", command=self._apply)
        self.btn.pack(side="right", padx=px(8))
        center_on(self, app)
        theme.style_titlebar(self)
        self.grab_set()

    def _changed(self):
        if self.vid == self.gv:
            self.var_name.set(default_instance_name(self.gv, self.picker.selection()))

    def _progress(self, text, done, total):
        self.bar.configure(maximum=max(total, 1), value=done)
        self.lbl.configure(text=f"{text}：{done}/{total}" if total > 1 else f"{text}……")

    def _apply(self):
        px = self.T.px
        sel = self.picker.selection()
        if sel == self.info["loaders"]:
            self._close()
            return
        if self.vid == self.gv:
            name, replace = self.var_name.get().strip(), False
        else:
            name, replace = self.vid, True
        self.btn.state(["disabled"])
        self.bar.pack(fill="x", pady=(px(12), px(4)))
        self.lbl.pack(anchor="w")
        progress = self.app.progress_cb(self._progress)
        gd, gv = self.app.gd, self.gv

        def ok(vid):
            self.app.versions_changed(select=vid)
            what = "已卸载所有加载器" if not sel else "加载器已更新"
            self.app.toast(f"{vid}：{what}")
            self._close()

        def err(e):
            self.btn.state(["!disabled"])
            self.lbl.configure(text=f"失败：{e}")
            self.app.error(e)
        self.app.bg(lambda: gd.install_instance(name, gv, sel, progress, self.app.manifest, replace), ok, err)

    def _close(self):
        self.grab_release()
        self.destroy()


# ====================================================================== 下载 Java

class NeedJava(Exception):
    """启动时发现本机没有合适的 Java。"""

    def __init__(self, major, component):
        super().__init__(f"需要 Java {major}")
        self.major, self.component = major, component


class JavaDownloadDialog(tk.Toplevel):
    """下载 Mojang 官方的 Java 运行时，装到游戏目录的 runtime 文件夹，启动时自动选用。

    auto：打开后直接下载这个运行时（启动时缺 Java 用）；on_done：装好后调用（比如接着启动游戏）。
    """

    def __init__(self, app, auto=None, on_done=None):
        super().__init__(app)
        self.app, self.auto, self.on_done = app, auto, on_done
        self.T = app.theme
        px = self.T.px
        self.rts = {}
        self._busy = False
        self._closed = False
        self.title("下载 Java")
        self.configure(bg=BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._close)
        wrap = ttk.Frame(self, padding=(px(20), px(16)))
        wrap.pack(fill="both", expand=True)
        ttk.Label(wrap, text="下载 Java", style="H1.TLabel").pack(anchor="w")
        ttk.Label(wrap, text="Mojang 官方提供的 Java，下载后自动安装到游戏目录的 runtime 文件夹，启动时自动选用",
                  style="Sub.TLabel", wraplength=px(500), justify="left").pack(anchor="w", pady=(0, px(12)))

        bottom = ttk.Frame(wrap)
        bottom.pack(side="bottom", fill="x", pady=(px(12), 0))
        ttk.Button(bottom, text="关闭", style="Bg.TButton", command=self._close).pack(side="right")
        self.btn = ttk.Button(bottom, text="下载并安装", style="BgAccent.TButton", command=self.install)
        self.btn.pack(side="right", padx=px(8))
        self.btn.state(["disabled"])
        self.lbl = ttk.Label(bottom, text="", style="Sub.TLabel")
        self.lbl.pack(side="left")
        self.bar = ttk.Progressbar(wrap, mode="determinate")

        box = ttk.Frame(wrap, style="Card.TFrame", padding=px(6))
        box.pack(fill="both", expand=True)
        frame, self.tree = make_tree(box, ("ver", "suits", "state"), ("Java 版本", "适用的游戏版本", "状态"),
                                     (190, 160, 90), height=6)
        frame.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.install())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.btn.state(["disabled" if self._busy else "!disabled"]))
        self.geometry(f"{px(540)}x{px(440)}")
        center_on(self, app)
        theme.style_titlebar(self)
        self.grab_set()
        self.ov = Overlay(box, self.T, "正在获取 Java 列表…", inset=6)
        app.bg(runtime.list_runtimes, self._fill, lambda e: self._closed or self.ov.show_message(f"获取失败：{e}"))

    def _fill(self, rts):
        if self._closed:
            return
        self.ov.close()
        have = runtime.installed(self.app.gd.root)
        for rt in rts:
            self.rts[rt["component"]] = rt
            self.tree.insert("", "end", iid=rt["component"],
                             values=(f"Java {rt['major']}（{rt['version']}）", rt["suits"],
                                     "✔ 已安装" if rt["component"] in have else ""),
                             tags=("accent",) if rt["component"] in have else ())
        restripe(self.tree)
        if self.auto in self.rts:
            self.tree.selection_set(self.auto)
            self.install()
        elif rts:
            first = next((r["component"] for r in rts if r["component"] not in have), rts[0]["component"])
            self.tree.selection_set(first)

    def _progress(self, text, done, total):
        if self._closed:
            return
        self.bar.configure(maximum=max(total, 1), value=done)
        self.lbl.configure(text=f"{text}：{done}/{total}" if total > 1 else f"{text}……")

    def install(self):
        sel = self.tree.selection()
        if not sel or self._busy:
            return
        rt = self.rts[sel[0]]
        self._busy = True
        self.btn.state(["disabled"])
        self.bar.pack(side="bottom", fill="x", pady=(self.T.px(12), 0))
        progress = self.app.progress_cb(self._progress)
        root = self.app.gd.root

        def ok(path):
            self._busy = False
            self.app.scan_java()
            self.app.toast(f"Java {rt['major']} 安装好啦")
            if self._closed:
                return
            self.tree.set(rt["component"], "state", "✔ 已安装")
            self.tree.item(rt["component"], tags=("accent",))
            self.lbl.configure(text=f"已安装：{path}")
            self.btn.state(["!disabled"])
            if self.on_done:
                self._close()
                self.on_done()

        def err(e):
            self._busy = False
            if not self._closed:
                self.btn.state(["!disabled"])
                self.lbl.configure(text="下载失败")
            self.app.error(e)
        self.app.bg(lambda: runtime.install(root, rt, progress), ok, err)

    def _close(self):
        if self._busy and not messagebox.askyesno("正在下载", "Java 还在下载，关掉窗口会在后台继续下载完。\n确定关闭吗？",
                                                  parent=self):
            return
        self._closed = True
        self.grab_release()
        self.destroy()


# ====================================================================== 下载游戏

class DownloadPage(Page):
    def build(self):
        px = self.px
        self.header("下载游戏", "安装任意版本的 Minecraft，可以同时装上多个兼容的模组加载器")
        self.selected = None
        self._auto_name = ""

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
        panel = self.card(body, pad=18)
        panel.configure(width=px(360))
        panel.pack(side="right", fill="y", padx=(px(14), 0))
        panel.pack_propagate(False)
        self.left = self.card(body, pad=6)
        self.left.pack(side="left", fill="both", expand=True)
        frame, self.tree = make_tree(self.left, ("id", "type", "time", "state"), ("版本", "类型", "发布时间", "状态"),
                                     (200, 90, 120, 110))
        frame.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        ttk.Label(panel, text="已选择", style="CardSub.TLabel").pack(anchor="w")
        self.lbl_sel = ttk.Label(panel, text="还没选版本", style="CardBig.TLabel")
        self.lbl_sel.pack(anchor="w")
        self.lbl_sel_sub = ttk.Label(panel, text="在左边列表里点一个版本", style="CardSub.TLabel")
        self.lbl_sel_sub.pack(anchor="w", pady=(0, px(10)))
        ttk.Separator(panel).pack(fill="x", pady=(0, px(10)))
        ttk.Label(panel, text="模组加载器（可多选）", style="Card.TLabel").pack(anchor="w", pady=(0, px(4)))
        self.picker = LoaderPicker(panel, self.app, self._loaders_changed)
        self.picker.pack(fill="x")
        ttk.Label(panel, text="版本名称", style="Card.TLabel").pack(anchor="w", pady=(px(12), px(4)))
        self.var_name = tk.StringVar()
        ttk.Entry(panel, textvariable=self.var_name).pack(fill="x")
        self.btn_install = ttk.Button(panel, text="安装", style="Big.Accent.TButton", command=self.install)
        self.btn_install.pack(fill="x", pady=(px(14), 0))
        self.bar = ttk.Progressbar(panel, mode="determinate")
        self.bar.pack(fill="x", pady=(px(12), px(6)))
        self.lbl_status = ttk.Label(panel, text="", style="CardSub.TLabel", wraplength=px(230), justify="left")
        self.lbl_status.pack(anchor="w", fill="x")
        self.lbl_status.bind("<Configure>", lambda e: self.lbl_status.configure(wraplength=max(e.width, 100)))

    def on_show(self):
        if not self.app.manifest:
            self.load()

    def load(self, force=False):
        self.lbl_status.configure(text="正在获取版本列表……")
        overlay_once(self, "_ov", self.left, self.T, "正在获取版本列表…", inset=6)

        def ok(m):
            close_overlay(self, "_ov")
            latest = m.get("latest", {})
            self.lbl_status.configure(text=f"最新正式版 {latest.get('release')}，最新快照 {latest.get('snapshot')}")
            self.fill()

        def err(e):
            self._ov.show_message(f"获取版本列表失败：{e}")
            self.lbl_status.configure(text="获取版本列表失败，点「刷新列表」重试")
        self.app.get_manifest(ok, err, force)

    def fill(self):
        m = self.app.manifest
        if not m:
            return
        kw = self.var_filter.get().strip().lower()
        show_old = self.var_types["old_beta"].get()
        self.tree.delete(*self.tree.get_children())
        installed = {self.app.gd.instance_info(v)["game"] for v in self.app.gd.list_versions()}
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
        self.picker.set_game(self.selected)
        self._loaders_changed()

    def _loaders_changed(self):
        # 用户没手动改过名字，就跟着选择自动更新
        name = default_instance_name(self.selected, self.picker.selection())
        if self.var_name.get() in ("", self._auto_name):
            self.var_name.set(name)
        self._auto_name = name

    def _progress(self, text, done, total):
        self.bar.configure(maximum=max(total, 1), value=done)
        self.lbl_status.configure(text=f"{text}：{done}/{total}" if total > 1 else f"{text}……")

    def install(self):
        if not self.selected:
            self.app.toast("先在左边选一个版本喵", "info")
            return
        gv, sel, name = self.selected, self.picker.selection(), self.var_name.get().strip()
        gd, manifest = self.app.gd, self.app.manifest
        progress = self.app.progress_cb(self._progress)
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
        self.app.bg(lambda: gd.install_instance(name, gv, sel, progress, manifest, replace=(name == gv)), ok, err)


# ====================================================================== 资源下载

RES_TYPES = [("mod", "Mod"), ("shader", "光影"), ("resourcepack", "资源包")]
RES_NAMES = dict(RES_TYPES)
LOADER_TAGS = {"fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge", "quilt": "Quilt", "liteloader": "LiteLoader",
               "iris": "Iris", "optifine": "OptiFine", "canvas": "Canvas", "vanilla": "原版光影", "minecraft": ""}
CATEGORY_NAMES = {
    "optimization": "优化", "utility": "实用", "library": "支持库", "decoration": "装饰", "adventure": "冒险",
    "magic": "魔法", "technology": "科技", "storage": "存储", "worldgen": "世界生成", "food": "食物",
    "equipment": "装备", "mobs": "生物", "transportation": "交通", "management": "管理", "economy": "经济",
    "social": "社交", "game-mechanics": "游戏机制", "cursed": "奇特", "minigame": "小游戏",
    "realistic": "写实", "semi-realistic": "半写实", "cartoon": "卡通", "fantasy": "奇幻", "vanilla-like": "原版风",
    "atmosphere": "氛围", "bloom": "泛光", "shadows": "阴影", "reflections": "反射", "pbr": "PBR",
    "simplistic": "简约", "themed": "主题", "tweaks": "微调", "combat": "战斗", "audio": "音效", "fonts": "字体",
    "gui": "界面", "models": "模型", "core-shaders": "核心着色器", "locale": "语言", "modded": "Mod 适配",
    "potato": "低配", "low": "低配", "medium": "中配", "high": "高配", "screenshot": "截图",
}
VERSION_TYPES = {"release": ("Release", "#2fae76"), "beta": ("Beta", "#e39b2d"), "alpha": ("Alpha", "#e5484d")}


def tag_list(item):
    """搜索结果 / 项目信息 -> 显示用的标签：分类在前，加载器在后。"""
    cats = item.get("display_categories") or item.get("categories") or []
    loaders = list(item.get("loaders") or [])
    for c in cats:
        if c in LOADER_TAGS and c not in loaders:
            loaders.append(c)
    names = [CATEGORY_NAMES.get(c, c) for c in cats if c not in LOADER_TAGS][:3]
    return names + [LOADER_TAGS[c] for c in loaders if LOADER_TAGS.get(c)]


def bind_wheel(widget, canvas):
    """鼠标在 widget 上时，滚轮滚动 canvas。"""
    def wheel(e):
        canvas.yview_scroll(int(-e.delta / 120), "units")
    widget.bind("<Enter>", lambda e: widget.bind_all("<MouseWheel>", wheel), add="+")
    widget.bind("<Leave>", lambda e: widget.unbind_all("<MouseWheel>"), add="+")


class IconCache:
    """Modrinth 图标：后台下载，PNG / GIF 直接用（Tk 不认识 WebP，那些就显示首字母）。"""

    def __init__(self, app, size):
        self.app, self.size = app, app.theme.px(size)
        self.cache, self.waiting = {}, {}

    def get(self, url, callback):
        if not url:
            return None
        if url in self.cache:
            return self.cache[url]
        if url in self.waiting:
            self.waiting[url].append(callback)
        else:
            self.waiting[url] = [callback]
            self.app.bg(lambda: net.fetch_bytes(url, retries=1, mirror=False),
                        lambda data: self._loaded(url, data), lambda e: self._loaded(url, b""))
        return None

    def _loaded(self, url, data):
        img = None
        if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:6] in (b"GIF87a", b"GIF89a"):
            try:
                import base64
                img = tk.PhotoImage(master=self.app, data=base64.b64encode(data).decode("ascii"))
                factor = -(-max(img.width(), img.height()) // self.size)
                if factor > 1:
                    img = img.subsample(factor)
            except tk.TclError:
                img = None
        self.cache[url] = img
        for cb in self.waiting.pop(url, []):
            try:
                cb(img)
            except tk.TclError:
                pass  # 列表已经换页 / 窗口关了


def draw_chips(cv, x, y, names, tags, font, color=ACCENT, fill=None, px=lambda v: v):
    """在 Canvas 上画一排标签小块，返回右边界 x。"""
    fill = fill or theme.ACCENT_SOFT
    for name in names:
        t = cv.create_text(x + px(6), y, text=name, font=font, fill=color, anchor="w", tags=tags)
        x0, y0, x1, y1 = cv.bbox(t)
        r = cv.create_rectangle(x0 - px(5), y0, x1 + px(5), y1, fill=fill, outline="", tags=tags)
        cv.tag_lower(r, t)
        x = x1 + px(10)
    return x


def fit_text(text, font, width):
    """按像素宽度截断文字，超出加省略号。"""
    text = " ".join((text or "").split())
    if font.measure(text) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + "…") <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


class ResultList(ttk.Frame):
    """第一级：搜索结果，一行一个项目（图标、名称、标签、简介），整张画在一个 Canvas 上，换页不卡。"""

    ROW = 70

    def __init__(self, master, page, on_open):
        super().__init__(master, style="Surface.TFrame")
        self.page, self.T, self.on_open = page, page.T, on_open
        self.cv = tk.Canvas(self, bg=CARD, highlightthickness=0, bd=0, yscrollincrement=page.px(24))
        sb = ttk.Scrollbar(self, orient="vertical", command=self.cv.yview)
        self.cv.configure(yscrollcommand=sb.set)
        self.cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        bind_wheel(self.cv, self.cv)
        self.f_title = tkfont.Font(family=FAMILY, size=11, weight="bold")
        self.f_small = tkfont.Font(family=FAMILY, size=9)
        self.icons = IconCache(page.app, 40)
        self.hits = []
        self._width = 0  # 注意不能叫 _w：那是 tkinter 存控件路径名的属性
        self.cv.bind("<Configure>", lambda e: e.width != self._width and self.redraw())

    def set_hits(self, hits):
        self.hits = hits
        self.cv.yview_moveto(0)
        self.redraw()

    def redraw(self):
        px, cv = self.page.px, self.cv
        cv.delete("all")
        w = self._width = max(cv.winfo_width(), 100)
        rh = px(self.ROW)
        for i, h in enumerate(self.hits):
            y, tag = i * rh, f"r{i}"
            cv.create_rectangle(0, y, w, y + rh, fill=CARD, outline="", tags=(tag, f"bg{i}"))
            if i:
                cv.create_line(px(14), y, w - px(14), y, fill=theme.LINE)
            self._draw_icon(i, h, px(16), y + rh / 2)
            x = px(16) + px(40) + px(14)
            title = cv.create_text(x, y + px(22), text=h["title"], font=self.f_title, fill=FG, anchor="w", tags=tag)
            draw_chips(cv, cv.bbox(title)[2] + px(4), y + px(22), tag_list(h), tag, self.f_small, px=px)
            dl = cv.create_text(w - px(18), y + px(22), text=f"↓ {fmt_num(h.get('downloads', 0))}", anchor="e",
                                font=self.f_small, fill=MUTED, tags=tag)
            desc = fit_text(h.get("description", ""), self.f_small, w - x - px(30))
            cv.create_text(x, y + px(48), text=desc, font=self.f_small, fill=SUB, anchor="w", tags=tag)
            cv.tag_bind(tag, "<Enter>", lambda e, i=i: self._hover(i, True))
            cv.tag_bind(tag, "<Leave>", lambda e, i=i: self._hover(i, False))
            cv.tag_bind(tag, "<Button-1>", lambda e, h=h: self.on_open(h))
        cv.configure(scrollregion=(0, 0, w, max(len(self.hits) * rh, 1)))

    def _draw_icon(self, i, h, x, cy):
        px, cv = self.page.px, self.cv
        n = px(40)
        img = self.icons.get(h.get("icon_url"), lambda im, i=i, h=h: self._icon_ready(i, h, im))
        if img:
            cv.create_image(x + n / 2, cy, image=img, tags=(f"r{i}", f"ic{i}"))
        else:
            cv.create_image(x + n / 2, cy, image=self.T.avatar(h["title"], 40), tags=(f"r{i}", f"ic{i}"))
            cv.create_text(x + n / 2, cy, text=h["title"][:1].upper(), fill="#ffffff",
                           font=(FAMILY, 13, "bold"), tags=(f"r{i}", f"ic{i}"))

    def _icon_ready(self, i, h, img):
        if img and i < len(self.hits) and self.hits[i] is h:
            self.cv.delete(f"ic{i}")
            px = self.page.px
            self.cv.create_image(px(16) + px(20), i * px(self.ROW) + px(self.ROW) / 2, image=img, tags=(f"r{i}", f"ic{i}"))

    def _hover(self, i, on):
        # 只改颜色，不删除重建（删掉鼠标下的元素会触发 Leave / Enter，导致无限循环）
        self.cv.itemconfigure(f"bg{i}", fill=theme.HOVER if on else CARD)
        self.cv.configure(cursor="hand2" if on else "")


class ScrollFrame(ttk.Frame):
    """可以滚动的竖向容器：往 .inner 里放东西。"""

    def __init__(self, master, page):
        super().__init__(master, style="Surface.TFrame")
        self.cv = tk.Canvas(self, bg=CARD, highlightthickness=0, bd=0, yscrollincrement=page.px(24))
        sb = ttk.Scrollbar(self, orient="vertical", command=self.cv.yview)
        self.cv.configure(yscrollcommand=sb.set)
        self.cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.inner = tk.Frame(self.cv, bg=CARD)
        win = self.cv.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfigure(win, width=e.width))
        bind_wheel(self, self.cv)

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.cv.yview_moveto(0)


def version_row(parent, T, v, on_click):
    """一行版本：类型徽标、名称、加载器、日期；整行可点。"""
    px = T.px
    row = tk.Frame(parent, bg=CARD, cursor="hand2")
    typ, color = VERSION_TYPES.get(v.get("version_type"), ("Release", SUCCESS))
    badge = tk.Label(row, text=typ[0], bg=color, fg="#ffffff", font=(FAMILY, 9, "bold"), width=2)
    badge.pack(side="left", padx=(px(16), px(12)), pady=px(10))
    mid = tk.Frame(row, bg=CARD)
    mid.pack(side="left", fill="x", expand=True)
    tk.Label(mid, text=v.get("name") or v.get("version_number"), bg=CARD, fg=FG, font=(FAMILY, 10),
             anchor="w").pack(anchor="w")
    loaders = " · ".join(LOADER_TAGS.get(l, l) for l in v.get("loaders", []) if LOADER_TAGS.get(l, l))
    tk.Label(mid, text=f"{typ}  {loaders}", bg=CARD, fg=SUB, font=(FAMILY, 9), anchor="w").pack(anchor="w")
    tk.Label(row, text=v.get("date_published", "")[:10], bg=CARD, fg=MUTED, font=(FAMILY, 9)).pack(
        side="right", padx=px(16))
    widgets = [row, mid] + list(mid.winfo_children()) + [w for w in row.winfo_children() if w is not badge]

    def paint(on):
        for w in widgets:
            w.configure(bg=theme.HOVER if on else CARD)

    def leave(e):
        under = row.winfo_containing(*row.winfo_pointerxy())
        paint(under in widgets or under is badge)
    for w in widgets + [badge]:
        w.bind("<Enter>", lambda e: paint(True))
        w.bind("<Leave>", leave)
        w.bind("<Button-1>", lambda e: on_click(v))
    return row


class ProjectView(ttk.Frame):
    """第二级：项目详情。推荐版本 + 按 Minecraft 版本分组、可展开的版本列表。"""

    def __init__(self, master, page):
        super().__init__(master)
        self.page, self.T = page, page.T
        px = self.T.px
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, px(12)))
        ttk.Button(bar, text="‹ 返回", style="Bg.TButton", command=page.back).pack(side="left")
        self.lbl_path = ttk.Label(bar, text="", style="Sub.TLabel")
        self.lbl_path.pack(side="left", padx=px(12))

        head = page.card(self, pad=16)
        head.pack(fill="x", pady=(0, px(14)))
        self.icon = tk.Canvas(head, width=px(56), height=px(56), bg=CARD, highlightthickness=0, bd=0)
        self.icon.pack(side="left", padx=(0, px(14)))
        info = ttk.Frame(head, style="Surface.TFrame")
        info.pack(side="left", fill="x", expand=True)
        self.lbl_title = ttk.Label(info, text="", style="CardBig.TLabel")
        self.lbl_title.pack(anchor="w")
        self.lbl_tags = ttk.Label(info, text="", style="CardAccent.TLabel")
        self.lbl_tags.pack(anchor="w")
        self.lbl_desc = ttk.Label(info, text="", style="CardSub.TLabel", justify="left")
        self.lbl_desc.pack(anchor="w", fill="x", pady=(px(4), 0))
        info.bind("<Configure>", lambda e: self.lbl_desc.configure(wraplength=max(e.width - px(10), 100)))
        ttk.Button(head, text="Modrinth ↗", style="Ghost.TButton", command=self._web).pack(side="right", anchor="n")

        self.body = page.card(self, pad=4)
        self.body.pack(fill="both", expand=True)
        self.sf = ScrollFrame(self.body, page)
        self.sf.pack(fill="both", expand=True)
        self.hit = None
        self._token = 0
        self._ov = None

    def _web(self):
        if self.hit:
            webbrowser.open(f"https://modrinth.com/{self.page.kind}/{self.hit.get('slug') or self.hit['project_id']}")

    def load(self, hit):
        self.hit = hit
        page = self.page
        self.lbl_path.configure(text=f"下载资源 › {RES_NAMES[page.kind]} › {hit['title']}")
        self.lbl_title.configure(text=hit["title"])
        self.lbl_tags.configure(text="   ".join(tag_list(hit)))
        self.lbl_desc.configure(text=f"by {hit.get('author', '')}  ·  {fmt_num(hit.get('downloads', 0))} 次下载\n"
                                     f"{hit.get('description', '')}")
        self._set_icon(page.results.icons.get(hit.get("icon_url"), self._set_icon))
        self.sf.clear()
        self._token += 1
        token = self._token
        overlay_once(self, "_ov", self.body, self.T, "正在获取版本列表…", inset=6)

        def ok(vs):
            if token == self._token:
                close_overlay(self, "_ov")
                self._fill(vs)

        def err(e):
            if token == self._token:
                self._ov.show_message(f"获取版本失败：{e}")
        pid = hit["project_id"]
        self.page.app.bg(lambda: mods.project_versions(pid), ok, err)

    def _set_icon(self, img):
        px = self.T.px
        self.icon.delete("all")
        n = px(56)
        if img:
            self.icon.create_image(n / 2, n / 2, image=img)
        elif self.hit:
            self.icon.create_image(n / 2, n / 2, image=self.T.avatar(self.hit["title"], 56))
            self.icon.create_text(n / 2, n / 2, text=self.hit["title"][:1].upper(), fill="#ffffff",
                                  font=(FAMILY, 18, "bold"))

    def _fill(self, vs):
        px, T, page = self.T.px, self.T, self.page
        inner = self.sf.inner
        gv, loader = page._filters()
        open_v = lambda v: page.open_version(v, self.hit)

        def section(text):
            tk.Label(inner, text=text, bg=CARD, fg=SUB, font=(FAMILY, 9), anchor="w").pack(
                fill="x", padx=px(16), pady=(px(12), px(4)))

        rec = next((v for v in vs if (not gv or gv in v.get("game_versions", []))
                    and (not loader or loader in v.get("loaders", []))), None)
        section(f"推荐版本 · Minecraft {gv}" + (f" · {LOADER_TAGS.get(loader, loader)}" if loader else "")
                if gv else "最新版本")
        if rec or (not gv and vs):
            version_row(inner, T, rec or vs[0], open_v).pack(fill="x")
        else:
            tk.Label(inner, text=f"没有适配 Minecraft {gv} 的版本，可以在下面按游戏版本找", bg=CARD, fg=MUTED,
                     font=(FAMILY, 10), anchor="w").pack(fill="x", padx=px(16), pady=px(8))

        groups = {}
        for v in vs:
            for g in v.get("game_versions", []):
                groups.setdefault(g, []).append(v)
        manifest_order = {v["id"]: i for i, v in enumerate((self.page.app.manifest or {}).get("versions", []))}

        def order(g):  # 有版本清单就按清单顺序，否则正式版按版本号从新到旧，快照放最后
            if g in manifest_order:
                return (2, -manifest_order[g], [])
            return (1 if re.fullmatch(r"\d+(\.\d+)+", g) else 0, 0, game._version_key(g))
        section(f"全部版本 · 共 {len(vs)} 个")
        for g in sorted(groups, key=order, reverse=True):
            self._group(inner, g, groups[g], open_v, expanded=(g == gv))

    def _group(self, inner, g, versions, open_v, expanded=False):
        px = self.T.px
        tk.Frame(inner, bg=theme.LINE, height=1).pack(fill="x", padx=px(12))
        head = tk.Frame(inner, bg=CARD, cursor="hand2")
        head.pack(fill="x")
        title = tk.Label(head, text=f"Minecraft {g}", bg=CARD, fg=FG, font=(FAMILY, 10), anchor="w")
        title.pack(side="left", padx=px(16), pady=px(9))
        arrow = tk.Label(head, text="▾", bg=CARD, fg=SUB, font=(FAMILY, 10))
        arrow.pack(side="right", padx=px(16))
        tk.Label(head, text=f"{len(versions)} 个版本", bg=CARD, fg=MUTED, font=(FAMILY, 9)).pack(side="right")
        box = tk.Frame(inner, bg=CARD)
        state = {"open": False, "built": False}

        def toggle(e=None):
            state["open"] = not state["open"]
            if state["open"]:
                if not state["built"]:  # 展开时才创建，几百个版本也不卡
                    for v in versions:
                        version_row(box, self.T, v, open_v).pack(fill="x")
                    state["built"] = True
                box.pack(fill="x", after=head)
            else:
                box.pack_forget()
            arrow.configure(text="▴" if state["open"] else "▾")
        for w in (head, title, arrow, *head.winfo_children()):
            w.bind("<Button-1>", toggle)
        if expanded:
            toggle()


class VersionDownloadDialog(tk.Toplevel):
    """第三级：下载某个版本。列出必需依赖（自动安装）和可选依赖（勾选安装）。"""

    def __init__(self, app, page, version, hit):
        super().__init__(app)
        self.app, self.page, self.v, self.hit = app, page, version, hit
        self.T = app.theme
        px = self.T.px
        self.kind = page.kind
        self.inst = page.var_inst.get()
        self.gv, self.loader = page._filters()
        self.required, self.optional, self.opt_vars = [], [], []
        self._closed = False
        self.title(f"下载 - {version.get('name')}")
        self.configure(bg=BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._close)
        wrap = ttk.Frame(self, padding=(px(22), px(18)))
        wrap.pack(fill="both", expand=True)
        ttk.Label(wrap, text=f"{RES_NAMES[self.kind]}下载", style="Sub.TLabel").pack(anchor="w")
        ttk.Label(wrap, text=version.get("name") or version.get("version_number"), style="H1.TLabel",
                  wraplength=px(520)).pack(anchor="w")
        typ = VERSION_TYPES.get(version.get("version_type"), ("Release",))[0]
        loaders = " · ".join(LOADER_TAGS.get(l, l) for l in version.get("loaders", []) if LOADER_TAGS.get(l, l))
        gvs = version.get("game_versions", [])
        ttk.Label(wrap, text=f"{hit['title']}  ·  {typ}  ·  {loaders}  ·  Minecraft {gvs[0] if len(gvs) == 1 else ', '.join(gvs[-3:])}"
                             f"  ·  {version.get('date_published', '')[:10]}",
                  style="Sub.TLabel", wraplength=px(520)).pack(anchor="w", pady=(px(2), px(12)))

        bottom = ttk.Frame(wrap)
        bottom.pack(side="bottom", fill="x", pady=(px(14), 0))
        ttk.Button(bottom, text="取消", style="Bg.TButton", command=self._close).pack(side="right")
        ttk.Button(bottom, text="下载到本机目录…", style="Bg.TButton", command=self._save_as).pack(side="right", padx=px(8))
        self.btn = ttk.Button(bottom, text=f"安装到 {self.inst}" if self.inst else "安装到当前版本",
                              style="BgAccent.TButton", command=self._install)
        self.btn.pack(side="right")
        self.lbl = ttk.Label(wrap, text="", style="Sub.TLabel", wraplength=px(520), justify="left")
        self.lbl.pack(side="bottom", anchor="w", pady=(px(8), 0))

        self.box = ttk.Frame(wrap, style="Card.TFrame", padding=px(4))
        self.box.pack(fill="both", expand=True)
        self.sf = ScrollFrame(self.box, page)
        self.sf.pack(fill="both", expand=True)
        self.geometry(f"{px(600)}x{px(500)}")
        center_on(self, app)
        theme.style_titlebar(self)
        self.grab_set()

        if not self.inst:
            self.btn.state(["disabled"])
            self.lbl.configure(text="还没有安装游戏版本，只能下载到本机目录")
        if self.kind != "mod" or not version.get("dependencies"):
            self._fill([], [])
            return
        self.btn.state(["disabled"])
        self.ov = Overlay(self.box, self.T, "正在分析依赖…", inset=6)
        mods_dir = self.app.gd.mods_dir(self.inst) if self.inst else None
        log = lambda s: self.app.ui(lambda: self._closed or self.ov.lbl.configure(text=s))
        gv, loader = self.gv, self.loader
        app.bg(lambda: mods.plan_dependencies(version, gv, loader, mods_dir, log),
               lambda r: self._closed or self._fill(*r),
               lambda e: self._closed or self.ov.show_message(f"分析依赖失败：{e}"))

    def _fill(self, required, optional):
        if hasattr(self, "ov"):
            self.ov.close()
        self.required, self.optional = required, optional
        px, inner = self.T.px, self.sf.inner
        if self.inst:
            self.btn.state(["!disabled"])

        def section(text, sub):
            tk.Label(inner, text=text, bg=CARD, fg=FG, font=(FAMILY, 10, "bold"), anchor="w").pack(
                fill="x", padx=px(16), pady=(px(14), 0))
            tk.Label(inner, text=sub, bg=CARD, fg=SUB, font=(FAMILY, 9), anchor="w").pack(fill="x", padx=px(16))

        if not required and not optional:
            tk.Label(inner, text="没有依赖，直接安装就好", bg=CARD, fg=SUB, font=(FAMILY, 10)).pack(pady=px(40))
            return
        if required:
            section("必需依赖", "会自动一起安装；缺少的话游戏可能无法启动")
            for e in required:
                self._dep_row(inner, e)
        if optional:
            section("可选依赖", "勾选的会一起安装（连同它们自己的必需依赖）")
            for e in optional:
                var = tk.BooleanVar(value=False)
                self.opt_vars.append((e, var))
                self._dep_row(inner, e, var)
        missing = [e["project"]["title"] for e in required if e["status"] == "missing"]
        if missing:
            self.lbl.configure(text=f"⚠ 找不到适配的版本：{'、'.join(missing)}，装了可能无法启动", style="Danger.TLabel")

    def _dep_row(self, inner, e, var=None):
        px = self.T.px
        tk.Frame(inner, bg=theme.LINE, height=1).pack(fill="x", padx=px(12), pady=(px(6), 0))
        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x", padx=px(16), pady=(px(6), 0))
        proj = e["project"]
        if var is not None:
            cb = ttk.Checkbutton(row, variable=var, style="Card.TCheckbutton")
            cb.pack(side="left")
            if e["status"] != "install":
                cb.state(["disabled"])
        if e["status"] == "installed":
            text, color = "✔ 已安装", SUCCESS
        elif e["status"] == "missing":
            text, color = f"找不到适配 {self.gv or ''} 的版本", DANGER
        else:
            ver = e["version"].get("version_number", "")
            text, color = f"将安装 {ver[:18] + '…' if len(ver) > 18 else ver}", ACCENT
        right = tk.Frame(row, bg=CARD)  # 先放右边的状态，左边的简介再长也挤不掉它
        right.pack(side="right", padx=(px(10), 0))
        mid = tk.Frame(row, bg=CARD)
        mid.pack(side="left", fill="x", expand=True)
        tk.Label(mid, text=proj["title"], bg=CARD, fg=FG, font=(FAMILY, 10, "bold"), anchor="w").pack(anchor="w")
        desc = proj.get("description", "")
        tk.Label(mid, text=desc[:48] + ("…" if len(desc) > 48 else ""), bg=CARD, fg=SUB, font=(FAMILY, 9),
                 anchor="w").pack(anchor="w")
        tk.Label(right, text=text, bg=CARD, fg=color, font=(FAMILY, 9)).pack(anchor="e")
        if e["via"]:
            tk.Label(right, text=f"由 {e['via']} 需要", bg=CARD, fg=MUTED, font=(FAMILY, 9)).pack(anchor="e")

    def _install(self):
        target = os.path.join(self.app.gd.version_dir(self.inst), mods.FOLDERS[self.kind])
        chosen = [e["version"] for e, var in self.opt_vars if var.get() and e["status"] == "install"]
        todo = [self.v] + [e["version"] for e in self.required if e["status"] == "install"]
        gv, loader, kind = self.gv, self.loader, self.kind
        log = lambda s: self.app.ui(lambda: self._closed or self.lbl.configure(text=s, style="Sub.TLabel"))
        self.btn.state(["disabled"])

        def work():
            seen = {v["project_id"] for v in todo}
            for ov in chosen:  # 勾选的可选依赖，连同它们自己的必需依赖
                if ov["project_id"] not in seen:
                    todo.append(ov)
                    seen.add(ov["project_id"])
                req, _ = mods.plan_dependencies(ov, gv, loader, target, log)
                for e in req:
                    if e["status"] == "install" and e["project"]["id"] not in seen:
                        todo.append(e["version"])
                        seen.add(e["project"]["id"])
            return mods.install_versions(todo, target, log)

        def ok(result):
            new = sum(1 for _, n in result if n)
            self.app.toast(f"已安装到 {self.inst}：新下载 {new} 个，共 {len(result)} 个文件")
            if kind == "shader":
                loaders = self.app.gd.instance_info(self.inst)["loaders"]
                if not ({"optifine", "fabric", "quilt", "neoforge"} & set(loaders)):
                    messagebox.showinfo("光影还用不了", f"{self.inst} 没有装 OptiFine 或 Iris 这类光影加载器，光影不会生效。\n"
                                        "可以在「启动游戏 → 管理」里加装 OptiFine，或者装 Fabric 后下载 Iris。", parent=self.app)
            if "manage" in self.app.pages:
                self.app.pages["manage"].fill()
            self._close()

        def err(e):
            if not self._closed:
                self.btn.state(["!disabled"])
                self.lbl.configure(text=f"下载失败：{e}", style="Sub.TLabel")
            self.app.error(e)
        self.app.bg(work, ok, err)

    def _save_as(self):
        d = filedialog.askdirectory(parent=self, title="下载到哪个文件夹")
        if not d:
            return
        v = self.v
        self.app.bg(lambda: mods.install_versions([v], d),
                    lambda r: self.app.toast(f"已下载到 {d}：{r[0][0]}"))
        self._close()

    def _close(self):
        self._closed = True
        self.grab_release()
        self.destroy()


class ModPage(Page):
    def build(self):
        px = self.px
        self.header("下载资源", "Mod、光影、资源包，数据来自 Modrinth；用英文名搜索效果最好（如 sodium、iris、faithful）")
        self.kind = "mod"
        self.page = 0
        self.total = 0
        self._search_token = 0

        # 第一级：搜索
        self.list_view = ttk.Frame(self)
        self.list_view.pack(fill="both", expand=True)
        top = self.card(self.list_view, pad=14)
        top.pack(fill="x", pady=(0, px(14)))
        r = self.row(top)
        self.seg = Segmented(r, RES_TYPES, self._set_kind)
        self.seg.pack(side="left", padx=(0, px(12)))
        if self.T.icon_family:
            ttk.Label(r, text=self.T.icon("search"), style="CardIcon.TLabel").pack(side="left", padx=(px(4), px(8)))
        self.var_q = tk.StringVar()
        e = ttk.Entry(r, textvariable=self.var_q)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.search(0))
        ttk.Button(r, text="搜索", style="Accent.TButton", command=lambda: self.search(0)).pack(
            side="left", padx=(px(10), 0))
        r = self.row(top, pady=(px(12), 0))

        def field(parent, text, widget):
            ttk.Label(parent, text=text, style="CardSub.TLabel").pack(side="left", padx=(0, px(6)))
            widget.pack(side="left", padx=(0, px(18)))
            return widget
        self.var_inst = tk.StringVar()
        self.cb_inst = field(r, "安装到", ttk.Combobox(r, textvariable=self.var_inst, state="readonly", width=28))
        self.cb_inst.bind("<<ComboboxSelected>>", lambda ev: (self._sync_filters(), self.search(0)))
        self.var_gv = tk.StringVar()
        field(r, "游戏版本", ttk.Combobox(r, textvariable=self.var_gv, width=9))
        self.loader_box = ttk.Frame(r, style="Surface.TFrame")
        self.loader_box.pack(side="left")
        self.cb_loader = field(self.loader_box, "加载器",
                               ttk.Combobox(self.loader_box, state="readonly", width=9, values=[n for n, _ in LOADERS]))
        self.cb_loader.current(0)
        self.lbl_sort = ttk.Label(r, text="排序", style="CardSub.TLabel")
        self.lbl_sort.pack(side="left", padx=(0, px(6)))
        self.cb_sort = ttk.Combobox(r, state="readonly", width=8, values=[n for n, _ in SORTS])
        self.cb_sort.pack(side="left")
        self.cb_sort.current(0)
        self.cb_sort.bind("<<ComboboxSelected>>", lambda ev: self.search(0))

        bottom = ttk.Frame(self.list_view)
        bottom.pack(side="bottom", fill="x", pady=(px(12), 0))
        self.btn_prev = ttk.Button(bottom, text="‹ 上一页", style="Bg.TButton", command=lambda: self.search(self.page - 1))
        self.btn_prev.pack(side="left")
        self.lbl_page = ttk.Label(bottom, text="", style="Sub.TLabel")
        self.lbl_page.pack(side="left", padx=px(12))
        self.btn_next = ttk.Button(bottom, text="下一页 ›", style="Bg.TButton", command=lambda: self.search(self.page + 1))
        self.btn_next.pack(side="left")
        self.lbl_status = ttk.Label(bottom, text="点一项查看详情和全部版本", style="Sub.TLabel")
        self.lbl_status.pack(side="right")

        self.left = self.card(self.list_view, pad=4)
        self.left.pack(fill="both", expand=True)
        self.results = ResultList(self.left, self, self.open_project)
        self.results.pack(fill="both", expand=True)

        # 第二级：项目详情（第三级是对话框）
        self.detail = ProjectView(self, self)

        self.refresh_versions()
        self._searched = False

    def on_show(self):
        if not self._searched:
            self._searched = True
            self.search(0)
            self.app.get_manifest(lambda m: None, lambda e: None)  # 详情页按游戏版本分组时用来排序

    def open_project(self, hit):
        self.list_view.pack_forget()
        self.detail.pack(fill="both", expand=True)
        self.detail.load(hit)

    def back(self):
        self.detail.pack_forget()
        self.list_view.pack(fill="both", expand=True)

    def open_version(self, version, hit):
        VersionDownloadDialog(self.app, self, version, hit)

    def _set_kind(self, kind):
        self.kind = kind
        if kind == "mod":
            self.loader_box.pack(side="left", before=self.lbl_sort)
        else:
            self.loader_box.pack_forget()
        self.back()
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
        self.var_gv.set(gd.instance_info(vid)["game"])
        loader = gd.loader_of(vid)
        codes = [c for _, c in LOADERS]
        self.cb_loader.current(codes.index(loader) if loader in codes else 0)
        self.lbl_status.configure(text=f"提示：{vid} 没装 Mod 加载器，Mod 不会生效哦" if not loader and self.kind == "mod"
                                  else "点一项查看详情和全部版本")

    def _filters(self):
        loader = (LOADERS[self.cb_loader.current()][1] or None) if self.kind == "mod" else None
        return self.var_gv.get().strip() or None, loader

    def search(self, page):
        if page < 0:
            return
        gv, loader = self._filters()
        q = self.var_q.get().strip()
        sort = SORTS[self.cb_sort.current()][1]
        kind = self.kind
        self._search_token += 1
        token = self._search_token
        overlay_once(self, "_ov", self.left, self.T, f"正在搜索{RES_NAMES[kind]}…", inset=6)

        def ok(res):
            if token != self._search_token:
                return
            close_overlay(self, "_ov")
            self.page = page
            self.total = res.get("total_hits", 0)
            self.results.set_hits(res["hits"])
            pages = max(1, -(-self.total // mods.PAGE_SIZE))
            self.lbl_page.configure(text=f"第 {page + 1} / {pages} 页 · 共 {self.total} 个")
            self.btn_prev.state(["!disabled"] if page > 0 else ["disabled"])
            self.btn_next.state(["!disabled"] if page + 1 < pages else ["disabled"])
            if not self.total:
                overlay_once(self, "_ov", self.left, self.T, "", inset=6).show_message(
                    "什么都没搜到喵，换个关键词或放宽筛选试试")

        def err(e):
            if token == self._search_token:
                self._ov.show_message(f"搜索失败：{e}")
        self.app.bg(lambda: mods.search(q, gv, loader, sort, page * mods.PAGE_SIZE, project_type=kind), ok, err)


# ====================================================================== 资源管理

class ModManagePage(Page):
    def build(self):
        px = self.px
        self.header("资源管理", "查看、启用 / 禁用、删除某个版本里的 Mod、光影和资源包，也可以从本机添加")
        self.kind = "mod"
        bar = self.card(pad=12)
        bar.pack(fill="x", pady=(0, px(14)))
        r = self.row(bar)
        self.seg = Segmented(r, RES_TYPES, self._set_kind)
        self.seg.pack(side="left", padx=(0, px(14)))
        self.var_inst = tk.StringVar()
        self.cb = ttk.Combobox(r, textvariable=self.var_inst, state="readonly", width=26)
        self.cb.pack(side="left")
        self.cb.bind("<<ComboboxSelected>>", lambda e: self.fill())
        ttk.Button(r, text="打开文件夹", command=self.open).pack(side="right")
        ttk.Button(r, text="从本机添加…", command=self.add_local).pack(side="right", padx=px(6))
        ttk.Button(r, text="删除", style="Danger.TButton", command=self.delete).pack(side="right")
        self.btn_toggle = ttk.Button(r, text="启用 / 禁用", command=self.toggle)
        self.btn_toggle.pack(side="right", padx=px(6))

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

    def _set_kind(self, kind):
        self.kind = kind
        self.btn_toggle.state(["!disabled"] if kind == "mod" else ["disabled"])
        self.fill()

    def refresh_versions(self, select=None):
        vs = self.app.gd.list_versions()
        self.cb["values"] = vs
        cur = select or self.var_inst.get()
        self.var_inst.set(cur if cur in vs else (vs[0] if vs else ""))
        self.fill()

    def _dir(self):
        vid = self.var_inst.get()
        return os.path.join(self.app.gd.version_dir(vid), mods.FOLDERS[self.kind]) if vid else None

    def _items(self, d):
        if self.kind == "mod":
            return sorted(f for f in os.listdir(d) if f.endswith((".jar", ".jar.disabled")))
        return sorted(f for f in os.listdir(d) if f.lower().endswith(".zip") or os.path.isdir(os.path.join(d, f)))

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        d = self._dir()
        what = RES_NAMES[self.kind]
        if not d or not os.path.isdir(d) or not self._items(d):
            self.lbl.configure(text=f"这个版本还没有{what}，可以去「下载资源」挑，或者点「从本机添加…」")
            return
        files = self._items(d)
        for f in files:
            path = os.path.join(d, f)
            if self.kind == "mod":
                on = f.endswith(".jar")
                state, tags = ("● 已启用", ()) if on else ("○ 已禁用", ("muted",))
            else:
                state, tags = ("文件夹" if os.path.isdir(path) else "压缩包"), ()
            size = "" if os.path.isdir(path) else fmt_size(os.path.getsize(path))
            self.tree.insert("", "end", iid=f, tags=tags, values=(f.removesuffix(".disabled"), state, size))
        restripe(self.tree)
        if self.kind == "mod":
            enabled = sum(f.endswith(".jar") for f in files)
            self.lbl.configure(text=f"共 {len(files)} 个 Mod，已启用 {enabled} 个 · 双击切换启用状态，可按住 Ctrl 多选")
        else:
            self.lbl.configure(text=f"共 {len(files)} 个{what} · 在游戏的「选项」里启用")

    def toggle(self):
        if self.kind != "mod":
            return
        d = self._dir()
        for f in self.tree.selection():
            src = os.path.join(d, f)
            dst = src.removesuffix(".disabled") if f.endswith(".disabled") else src + ".disabled"
            os.replace(src, dst)
        self.fill()

    def delete(self):
        sel = self.tree.selection()
        if sel and messagebox.askyesno("删除", f"确定删除选中的 {len(sel)} 个{RES_NAMES[self.kind]}吗？"):
            for f in sel:
                path = os.path.join(self._dir(), f)
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
            self.fill()
            self.app.toast(f"已删除 {len(sel)} 个{RES_NAMES[self.kind]}")

    def add_local(self):
        d = self._dir()
        if not d:
            self.app.toast("先安装一个游戏版本喵", "info")
            return
        ft = [("Mod", "*.jar")] if self.kind == "mod" else [("压缩包", "*.zip")]
        paths = filedialog.askopenfilenames(title=f"选择要添加的{RES_NAMES[self.kind]}",
                                            filetypes=ft + [("所有文件", "*.*")])
        if not paths:
            return
        os.makedirs(d, exist_ok=True)
        for p in paths:
            shutil.copy2(p, os.path.join(d, os.path.basename(p)))
        self.fill()
        self.app.toast(f"已添加 {len(paths)} 个{RES_NAMES[self.kind]}到 {self.var_inst.get()}")

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
