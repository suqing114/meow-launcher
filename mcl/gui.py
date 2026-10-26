"""tkinter 图形界面。"""
import json
import locale
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
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from . import __version__, auth, game, java, mods, net

IS_WINDOWS = sys.platform.startswith("win")
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
FAMILY = "Microsoft YaHei UI" if IS_WINDOWS else "TkDefaultFont"

BG, CARD, ACCENT, ACCENT_DARK = "#f3f5fa", "#ffffff", "#5b6ee1", "#4757c9"
FG, SUB, SIDEBAR, SIDEBAR_HI = "#1f2333", "#6b7280", "#23263a", "#34385a"

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
ACCOUNTS_PATH = os.path.join(APP_DIR, "accounts.json")

SOURCES = [("BMCLAPI 镜像（国内推荐）", "bmclapi"), ("官方源", "official")]
LOADERS = [("任意", ""), ("Fabric", "fabric"), ("Forge", "forge"), ("NeoForge", "neoforge"), ("Quilt", "quilt")]
INSTALL_LOADERS = [("原版（不装加载器）", ""), ("Fabric", "fabric"), ("Forge", "forge"), ("NeoForge", "neoforge")]
SORTS = [("相关度", "relevance"), ("下载量", "downloads"), ("关注数", "follows"),
         ("最新发布", "newest"), ("最近更新", "updated")]
TYPE_NAMES = {"release": "正式版", "snapshot": "快照", "old_beta": "远古 Beta", "old_alpha": "远古 Alpha"}


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


def scrolled_tree(parent, columns, headings, widths, height=12):
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=height, selectmode="browse")
    for c, h, w in zip(columns, headings, widths):
        tree.heading(c, text=h)
        tree.column(c, width=w, stretch=(w >= 200), anchor="w")
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    return frame, tree


# ====================================================================== App

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"喵喵启动器 MeowLauncher {__version__}")
        self.geometry("1100x700")
        self.minsize(900, 580)
        self.configure(bg=BG)

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
        self._setup_style()

        sidebar = tk.Frame(self, bg=SIDEBAR, width=190)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="=^･ω･^=", bg=SIDEBAR, fg="#ffd479",
                 font=(FAMILY, 18, "bold")).pack(pady=(22, 0))
        tk.Label(sidebar, text="喵喵启动器", bg=SIDEBAR, fg="white",
                 font=(FAMILY, 14, "bold")).pack(pady=(2, 22))

        content = ttk.Frame(self)
        content.pack(side="left", fill="both", expand=True)
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)

        self.pages, self.nav = {}, {}
        for key, text, cls in [("launch", "启动游戏", LaunchPage), ("download", "下载游戏", DownloadPage),
                               ("mods", "下载 Mod", ModPage), ("manage", "Mod 管理", ModManagePage),
                               ("settings", "设置", SettingsPage)]:
            page = cls(content, self)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = page
            btn = tk.Label(sidebar, text="    " + text, anchor="w", bg=SIDEBAR, fg="#d6d9ea",
                           font=(FAMILY, 11), padx=10, pady=10, cursor="hand2")
            btn.pack(fill="x")
            btn.bind("<Button-1>", lambda e, k=key: self.show(k))
            self.nav[key] = btn
        tk.Label(sidebar, text=f"v{__version__}", bg=SIDEBAR, fg="#6c7093",
                 font=(FAMILY, 9)).pack(side="bottom", pady=10)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show("launch")
        self.scan_java()

    def _setup_style(self):
        base = tkfont.nametofont("TkDefaultFont")
        base.configure(family=FAMILY, size=10)
        for name in ("TkTextFont", "TkHeadingFont", "TkMenuFont"):
            tkfont.nametofont(name).configure(family=FAMILY, size=10)
        line = base.metrics("linespace")
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, font=(FAMILY, 10))
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("TLabel", background=BG)
        s.configure("Card.TLabel", background=CARD)
        s.configure("H1.TLabel", font=(FAMILY, 17, "bold"))
        s.configure("Sub.TLabel", foreground=SUB)
        s.configure("CardSub.TLabel", foreground=SUB, background=CARD)
        s.configure("TCheckbutton", background=BG)
        s.configure("Card.TCheckbutton", background=CARD)
        s.configure("TButton", padding=(10, 4))
        s.configure("Accent.TButton", background=ACCENT, foreground="white",
                    font=(FAMILY, 13, "bold"), padding=(26, 14), borderwidth=0)
        s.map("Accent.TButton", background=[("disabled", "#aab2e6"), ("active", ACCENT_DARK)],
              foreground=[("disabled", "#eef0fb")])
        s.configure("Treeview", rowheight=int(line * 1.7), background=CARD, fieldbackground=CARD)
        s.configure("Treeview.Heading", font=(FAMILY, 10, "bold"))
        s.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
        s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#e3e6f2", borderwidth=0)

    def show(self, key):
        self.pages[key].tkraise()
        for k, btn in self.nav.items():
            btn.configure(bg=SIDEBAR_HI if k == key else SIDEBAR, fg="white" if k == key else "#d6d9ea")
        self.pages[key].on_show()

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
        super().__init__(master, padding=(22, 18))
        self.app = app
        self.build()

    def build(self):
        pass

    def on_show(self):
        pass

    def refresh_versions(self, select=None):
        pass

    def header(self, title, sub=""):
        ttk.Label(self, text=title, style="H1.TLabel").pack(anchor="w")
        ttk.Label(self, text=sub, style="Sub.TLabel").pack(anchor="w", pady=(2, 12))


# ====================================================================== 启动页

class LaunchPage(Page):
    def build(self):
        cfg = self.app.cfg
        self.header("启动游戏", "选择一个已安装的版本，填个离线用户名就能开玩喵～")

        card = ttk.Frame(self, style="Card.TFrame", padding=16)
        card.pack(fill="x")
        card.columnconfigure(1, weight=1)

        def label(r, t):
            ttk.Label(card, text=t, style="Card.TLabel").grid(row=r, column=0, sticky="w", pady=5, padx=(0, 12))

        label(0, "账户")
        row = ttk.Frame(card, style="Card.TFrame")
        row.grid(row=0, column=1, sticky="w")
        self.cb_acc = ttk.Combobox(row, state="readonly", width=42)
        self.cb_acc.pack(side="left")
        ttk.Button(row, text="添加账户…", command=lambda: AddAccountDialog(self.app)).pack(side="left", padx=6)
        ttk.Button(row, text="删除", command=self._remove_account).pack(side="left")
        self._acc_ids = []

        label(1, "游戏版本")
        row = ttk.Frame(card, style="Card.TFrame")
        row.grid(row=1, column=1, sticky="w")
        self.var_ver = tk.StringVar()
        self.cb_ver = ttk.Combobox(row, textvariable=self.var_ver, state="readonly", width=42)
        self.cb_ver.pack(side="left")
        self.cb_ver.bind("<<ComboboxSelected>>", lambda e: self._update_info())
        ttk.Button(row, text="刷新", command=self.refresh_versions).pack(side="left", padx=6)
        self.lbl_info = ttk.Label(row, text="", style="CardSub.TLabel")
        self.lbl_info.pack(side="left", padx=6)

        label(2, "最大内存（MB）")
        self.var_mem = tk.IntVar(value=cfg["memory"])
        ttk.Spinbox(card, from_=512, to=65536, increment=512, textvariable=self.var_mem,
                    width=10).grid(row=2, column=1, sticky="w")

        label(3, "Java")
        row = ttk.Frame(card, style="Card.TFrame")
        row.grid(row=3, column=1, sticky="we")
        self.var_java = tk.StringVar()
        self.cb_java = ttk.Combobox(row, textvariable=self.var_java, state="readonly", width=62)
        self.cb_java.pack(side="left")
        ttk.Button(row, text="重新扫描", command=self.app.scan_java).pack(side="left", padx=6)
        ttk.Button(row, text="手动选择…", command=self._browse_java).pack(side="left")

        self.btn_launch = ttk.Button(card, text="▶  启动游戏", style="Accent.TButton", command=self.launch)
        self.btn_launch.grid(row=0, column=2, rowspan=4, sticky="nse", padx=(16, 0))

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(12, 6))
        ttk.Button(bar, text="打开版本文件夹", command=lambda: self._open("")).pack(side="left")
        ttk.Button(bar, text="打开 mods 文件夹", command=lambda: self._open("mods")).pack(side="left", padx=6)
        ttk.Button(bar, text="删除该版本", command=self._delete).pack(side="left")
        ttk.Button(bar, text="清空日志", command=lambda: self.txt.delete("1.0", "end")).pack(side="right")
        self.lbl_status = ttk.Label(bar, text="", style="Sub.TLabel")
        self.lbl_status.pack(side="left", padx=12)

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        self.txt = tk.Text(frame, bg="#1d2030", fg="#d7dbef", insertbackground="white",
                           font=("Consolas", 9), relief="flat", wrap="none", padx=8, pady=6)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.refresh_versions(cfg.get("last_version"))
        self.refresh_accounts(cfg.get("account"))

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
            self.cb_acc.set("还没有账户，点「添加账户」")

    def _selected_account_id(self):
        i = self.cb_acc.current()
        return self._acc_ids[i] if 0 <= i < len(self._acc_ids) else None

    def _remove_account(self):
        aid = self._selected_account_id()
        acc = aid and self.app.accounts.get(aid)
        if acc and messagebox.askyesno("删除账户", f"确定删除账户 {auth.describe(acc)} 吗？"):
            self.app.accounts.remove(aid)
            self.refresh_accounts()

    def refresh_versions(self, select=None):
        vs = self.app.gd.list_versions()
        self.cb_ver["values"] = vs
        cur = select or self.var_ver.get()
        self.var_ver.set(cur if cur in vs else (vs[0] if vs else ""))
        self._update_info()

    def _update_info(self):
        vid = self.var_ver.get()
        if not vid:
            self.lbl_info.configure(text="还没有安装任何版本，去「下载游戏」装一个吧")
            return
        try:
            gv = self.app.gd.game_version_of(vid)
            loader = self.app.gd.loader_of(vid) or "原版"
            need = self.app.gd.required_java(vid)
            self.lbl_info.configure(text=f"{gv} · {loader} · 需要 Java {need}")
        except Exception as e:
            self.lbl_info.configure(text=f"版本文件有问题：{e}")

    def set_javas(self, javas):
        want = self.app.cfg.get("java", "")
        self._java_paths = [""] + [j.path for j in javas]
        if want and want not in self._java_paths:
            self._java_paths.append(want)
        self.cb_java["values"] = ["自动选择（按版本需求）"] + [
            f"Java {j.major}  ({j.version})  {j.path}" for j in javas] + (
            [want] if want and want not in [j.path for j in javas] else [])
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

    def log(self, line):
        self.txt.insert("end", line + "\n")
        if int(self.txt.index("end-1c").split(".")[0]) > 6000:
            self.txt.delete("1.0", "1000.0")
        self.txt.see("end")

    def _progress(self, text, done, total):
        self.lbl_status.configure(text=f"{text} {done}/{total}" if total > 1 else text)

    def launch(self):
        vid = self.var_ver.get()
        if not vid:
            messagebox.showinfo("提示", "请先在「下载游戏」里安装一个版本喵")
            return
        acc = self.app.accounts.get(self._selected_account_id())
        if not acc:
            messagebox.showinfo("提示", "请先添加一个账户喵")
            return
        self.store()
        save_config(self.app.cfg)
        cfg = self.app.cfg
        java_sel = cfg["java"]
        progress = self.app.progress_cb(self._progress)
        self.btn_launch.state(["disabled"])
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
            self.lbl_status.configure(text=f"游戏运行中（PID {proc.pid}）")
            self.after(3000, lambda: self.btn_launch.state(["!disabled"]))
            threading.Thread(target=self._read_output, args=(proc,), daemon=True).start()

        def failed(e):
            self.btn_launch.state(["!disabled"])
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
        self.title("添加账户")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()
        self._cancel = False
        self._device = None
        self._ygg = None
        self.protocol("WM_DELETE_WINDOW", self._close)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=14, pady=14)
        nb.add(self._offline_tab(nb), text="  离线  ")
        nb.add(self._ms_tab(nb), text="  微软正版  ")
        nb.add(self._ygg_tab(nb), text="  外置登录  ")

    def _tab(self, nb):
        f = ttk.Frame(nb, style="Card.TFrame", padding=18)
        f.columnconfigure(1, weight=1)
        return f

    def _done(self, acc):
        aid = self.app.accounts.add(acc)
        self.app.pages["launch"].refresh_accounts(aid)
        self._close()

    def _close(self):
        self._cancel = True
        self.grab_release()
        self.destroy()

    # ---- 离线
    def _offline_tab(self, nb):
        f = self._tab(nb)
        ttk.Label(f, text="用户名", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        var = tk.StringVar()
        e = ttk.Entry(f, textvariable=var, width=30)
        e.grid(row=0, column=1, sticky="we")
        ttk.Label(f, text="3–16 位字母、数字或下划线。离线账户只能进单人或关闭了正版验证的服务器。",
                  style="CardSub.TLabel", wraplength=380).grid(row=1, column=0, columnspan=2, sticky="w", pady=8)

        def add():
            name = var.get().strip()
            if not re.fullmatch(r"\w{3,16}", name, re.ASCII):
                if not name or not messagebox.askyesno("用户名", "用户名不太规范，可能进不了部分服务器，仍然使用吗？",
                                                       parent=self):
                    return
            self._done(auth.offline_account(name))
        ttk.Button(f, text="添加", command=add).grid(row=2, column=1, sticky="e")
        return f

    # ---- 微软
    def _ms_tab(self, nb):
        f = self._tab(nb)
        has_id = bool(self.app.cfg.get("ms_client_id"))
        ttk.Label(f, text="使用设备码登录：点「开始登录」后，在浏览器里输入下面显示的代码并登录微软账号。"
                  + ("" if has_id else "\n\n⚠ 还没有填写 Client ID，请先到「设置」页填写。"),
                  style="Card.TLabel", wraplength=400, justify="left").grid(row=0, column=0, columnspan=2, sticky="w")
        self.lbl_code = tk.Label(f, text="", bg=CARD, fg=ACCENT, font=("Consolas", 26, "bold"))
        self.lbl_code.grid(row=1, column=0, columnspan=2, pady=12)
        self.btn_ms = ttk.Button(f, text="开始登录", command=self._ms_start)
        self.btn_ms.grid(row=2, column=0, sticky="w")
        self.btn_open = ttk.Button(f, text="复制代码并打开网页", command=self._ms_open, state="disabled")
        self.btn_open.grid(row=2, column=1, sticky="e")
        self.lbl_ms = ttk.Label(f, text="", style="CardSub.TLabel", wraplength=400, justify="left")
        self.lbl_ms.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
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
                messagebox.showinfo("登录成功", f"欢迎，{acc['name']}！", parent=self)
                self._done(acc)

        def failed(e):
            if not self._cancel:
                self.btn_ms.state(["!disabled"])
                self.lbl_code.configure(text="")
                self.lbl_ms.configure(text=f"登录失败：{e}")
        self.app.bg(lambda: auth.ms_start(cid), got_code, failed)

    def _ms_open(self):
        if self._device:
            self.clipboard_clear()
            self.clipboard_append(self._device["user_code"])
            webbrowser.open(self._device["verification_uri"])

    # ---- 外置
    def _ygg_tab(self, nb):
        f = self._tab(nb)
        self.var_srv = tk.StringVar(value="littleskin.cn")
        self.var_login = tk.StringVar()
        self.var_pw = tk.StringVar()
        for r, (text, var, show) in enumerate((("服务器地址", self.var_srv, ""), ("邮箱 / 用户名", self.var_login, ""),
                                               ("密码", self.var_pw, "•"))):
            ttk.Label(f, text=text, style="Card.TLabel").grid(row=r, column=0, sticky="w", pady=4, padx=(0, 10))
            ttk.Entry(f, textvariable=var, width=34, show=show).grid(row=r, column=1, sticky="we")
        ttk.Label(f, text="支持任何兼容 authlib-injector 的皮肤站，例如 LittleSkin。密码只用来登录，不会被保存。",
                  style="CardSub.TLabel", wraplength=400).grid(row=3, column=0, columnspan=2, sticky="w", pady=6)
        self.cb_prof = ttk.Combobox(f, state="readonly")
        self.btn_ygg = ttk.Button(f, text="登录", command=self._ygg_login)
        self.btn_ygg.grid(row=5, column=1, sticky="e")
        self.lbl_ygg = ttk.Label(f, text="", style="CardSub.TLabel", wraplength=400)
        self.lbl_ygg.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
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
            self.cb_prof.grid(row=4, column=1, sticky="we", pady=4)
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
        self.header("下载游戏", "安装任意版本的 Minecraft，可以顺手装上 Fabric / Forge / NeoForge")
        self.selected = None
        self._loader_list = []
        self._token = 0

        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 8))
        self.var_types = {}
        for t, default in (("release", True), ("snapshot", False), ("old_beta", False)):
            var = tk.BooleanVar(value=default)
            self.var_types[t] = var
            ttk.Checkbutton(top, text={"old_beta": "远古版"}.get(t, TYPE_NAMES[t]), variable=var,
                            command=self.fill).pack(side="left", padx=(0, 10))
        ttk.Label(top, text="搜索：").pack(side="left", padx=(12, 0))
        self.var_filter = tk.StringVar()
        self.var_filter.trace_add("write", lambda *a: self.fill())
        ttk.Entry(top, textvariable=self.var_filter, width=20).pack(side="left")
        ttk.Button(top, text="刷新列表", command=lambda: self.load(force=True)).pack(side="right")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        frame, self.tree = scrolled_tree(body, ("id", "type", "time"), ("版本", "类型", "发布时间"),
                                         (240, 110, 160))
        frame.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        panel = ttk.Frame(body, style="Card.TFrame", padding=16, width=300)
        panel.pack(side="right", fill="y", padx=(12, 0))
        panel.pack_propagate(False)
        self.lbl_sel = ttk.Label(panel, text="← 先选一个版本", style="Card.TLabel", font=(FAMILY, 13, "bold"))
        self.lbl_sel.pack(anchor="w", pady=(0, 12))
        ttk.Label(panel, text="Mod 加载器", style="Card.TLabel").pack(anchor="w")
        self.cb_loader = ttk.Combobox(panel, state="readonly", values=[n for n, _ in INSTALL_LOADERS])
        self.cb_loader.current(0)
        self.cb_loader.pack(fill="x", pady=(2, 10))
        self.cb_loader.bind("<<ComboboxSelected>>", lambda e: self._load_loader())
        self.lbl_lv = ttk.Label(panel, text="加载器版本", style="Card.TLabel")
        self.lbl_lv.pack(anchor="w")
        self.cb_lv = ttk.Combobox(panel, state="disabled")
        self.cb_lv.pack(fill="x", pady=(2, 16))
        self.btn_install = ttk.Button(panel, text="安装", style="Accent.TButton", command=self.install)
        self.btn_install.pack(fill="x")
        self.bar = ttk.Progressbar(panel, mode="determinate")
        self.bar.pack(fill="x", pady=(16, 4))
        self.lbl_status = ttk.Label(panel, text="", style="CardSub.TLabel", wraplength=260, justify="left")
        self.lbl_status.pack(anchor="w")

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
            name = v["id"] + ("   ✔ 已安装" if v["id"] in installed else "")
            self.tree.insert("", "end", iid=v["id"],
                             values=(name, TYPE_NAMES.get(t, t), v["releaseTime"][:10]))

    def refresh_versions(self, select=None):
        self.fill()

    def _on_select(self, _):
        sel = self.tree.selection()
        if not sel:
            return
        self.selected = sel[0]
        self.lbl_sel.configure(text=self.selected)
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
            messagebox.showinfo("提示", "先在左边选一个版本喵")
            return
        gv = self.selected
        gd, manifest = self.app.gd, self.app.manifest
        progress = self.app.progress_cb(self._progress)
        name, kind = INSTALL_LOADERS[self.cb_loader.current()]
        if kind:
            i = self.cb_lv.current()
            if i < 0 or not self._loader_list:
                messagebox.showinfo("提示", f"请先选择 {name} 版本")
                return
            lv = self._loader_list[i]["version"]
            if kind == "fabric":
                work = lambda: gd.install_fabric(gv, lv, progress, manifest)
            else:
                work = lambda: gd.install_forge(kind, gv, lv, progress, manifest)
        else:
            work = lambda: gd.install_vanilla(gv, progress, manifest)
        self.btn_install.state(["disabled"])

        def ok(vid):
            self.btn_install.state(["!disabled"])
            self.lbl_status.configure(text=f"安装完成：{vid}")
            self.app.versions_changed(select=vid)
            messagebox.showinfo("安装完成", f"{vid} 安装好啦，去「启动游戏」开玩吧！")

        def err(e):
            self.btn_install.state(["!disabled"])
            self.lbl_status.configure(text=f"安装失败：{e}")
            self.app.error(e)
        self.app.bg(work, ok, err)


# ====================================================================== 下载 Mod

class ModPage(Page):
    def build(self):
        self.header("下载 Mod", "数据来自 Modrinth，可按游戏版本、加载器筛选，自动下载前置")
        self.page = 0
        self.total = 0
        self.hits = {}
        self.versions = {}
        self._search_token = 0
        self._ver_token = 0

        row = ttk.Frame(self)
        row.pack(fill="x")
        self.var_q = tk.StringVar()
        e = ttk.Entry(row, textvariable=self.var_q, width=46)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: self.search(0))
        ttk.Button(row, text="搜索", command=lambda: self.search(0)).pack(side="left", padx=6)
        ttk.Label(row, text="（Modrinth 用英文名搜索效果最好，如 sodium、jei）", style="Sub.TLabel").pack(side="left")

        row = ttk.Frame(self)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="安装到").pack(side="left")
        self.var_inst = tk.StringVar()
        self.cb_inst = ttk.Combobox(row, textvariable=self.var_inst, state="readonly", width=34)
        self.cb_inst.pack(side="left", padx=(4, 14))
        self.cb_inst.bind("<<ComboboxSelected>>", lambda ev: self._sync_filters())
        ttk.Label(row, text="游戏版本").pack(side="left")
        self.var_gv = tk.StringVar()
        ttk.Combobox(row, textvariable=self.var_gv, width=12).pack(side="left", padx=(4, 14))
        ttk.Label(row, text="加载器").pack(side="left")
        self.cb_loader = ttk.Combobox(row, state="readonly", width=10, values=[n for n, _ in LOADERS])
        self.cb_loader.current(0)
        self.cb_loader.pack(side="left", padx=(4, 14))
        ttk.Label(row, text="排序").pack(side="left")
        self.cb_sort = ttk.Combobox(row, state="readonly", width=9, values=[n for n, _ in SORTS])
        self.cb_sort.current(0)
        self.cb_sort.pack(side="left", padx=4)
        self.cb_sort.bind("<<ComboboxSelected>>", lambda ev: self.search(0))

        pane = ttk.Panedwindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left, self.tree = scrolled_tree(pane, ("title", "author", "dl", "desc"),
                                        ("名称", "作者", "下载量", "简介"), (180, 100, 80, 320))
        self.tree.bind("<<TreeviewSelect>>", self._on_pick)
        pane.add(left, weight=3)

        right = ttk.Frame(pane, style="Card.TFrame", padding=10)
        pane.add(right, weight=2)
        self.lbl_detail = ttk.Label(right, text="选择左边的 Mod 查看可用版本", style="Card.TLabel",
                                    wraplength=380, justify="left")
        self.lbl_detail.pack(anchor="w", fill="x", pady=(0, 8))
        vframe, self.vtree = scrolled_tree(right, ("name", "gv", "loader", "date"),
                                           ("版本", "游戏版本", "加载器", "日期"), (200, 110, 90, 90))
        vframe.pack(fill="both", expand=True)
        self.vtree.bind("<Double-1>", lambda e: self.download())

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", pady=(8, 0))
        self.btn_prev = ttk.Button(bottom, text="上一页", command=lambda: self.search(self.page - 1))
        self.btn_prev.pack(side="left")
        self.lbl_page = ttk.Label(bottom, text="")
        self.lbl_page.pack(side="left", padx=8)
        self.btn_next = ttk.Button(bottom, text="下一页", command=lambda: self.search(self.page + 1))
        self.btn_next.pack(side="left")
        self.btn_dl = ttk.Button(bottom, text="下载所选版本", command=self.download)
        self.btn_dl.pack(side="right")
        self.var_deps = tk.BooleanVar(value=True)
        ttk.Checkbutton(bottom, text="同时下载必需前置", variable=self.var_deps).pack(side="right", padx=10)
        ttk.Button(bottom, text="在浏览器中查看", command=self._open_web).pack(side="right")
        self.lbl_status = ttk.Label(self, text="", style="Sub.TLabel")
        self.lbl_status.pack(anchor="w", pady=(6, 0))

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
            pages = max(1, -(-self.total // mods.PAGE_SIZE))
            self.lbl_page.configure(text=f"第 {page + 1} / {pages} 页（共 {self.total} 个）")
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
        self.lbl_detail.configure(text=f"{h.get('title')}  ·  {h.get('author')}\n{h.get('description', '')}")
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
            if vs:
                self.vtree.selection_set(vs[0]["id"])
                self.lbl_status.configure(text=f"找到 {len(vs)} 个版本，双击或点「下载所选版本」即可安装")
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
            messagebox.showinfo("提示", "还没有安装游戏版本，先去「下载游戏」装一个喵")
            return
        sel = self.vtree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在右侧选择一个 Mod 版本")
            return
        v = self.versions[sel[0]]
        gv, loader = self._filters()
        mods_dir = self.app.gd.mods_dir(inst)
        deps = self.var_deps.get()
        log = lambda s: self.app.ui(self.lbl_status.configure, {"text": s})
        self.btn_dl.state(["disabled"])

        def ok(result):
            self.btn_dl.state(["!disabled"])
            names = "\n".join(f"{'✔ 已下载' if new else '· 已存在'}  {n}" for n, new in result)
            self.lbl_status.configure(text=f"完成！共 {len(result)} 个文件 → {inst}")
            messagebox.showinfo("下载完成", f"已安装到 {inst}：\n\n{names}")

        def err(e):
            self.btn_dl.state(["!disabled"])
            self.lbl_status.configure(text=f"下载失败：{e}")
            self.app.error(e)
        self.app.bg(lambda: mods.install(v, mods_dir, gv, loader, deps, log), ok, err)


# ====================================================================== Mod 管理

class ModManagePage(Page):
    def build(self):
        self.header("Mod 管理", "查看、启用 / 禁用、删除某个版本里已安装的 Mod")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="版本").pack(side="left")
        self.var_inst = tk.StringVar()
        self.cb = ttk.Combobox(row, textvariable=self.var_inst, state="readonly", width=36)
        self.cb.pack(side="left", padx=6)
        self.cb.bind("<<ComboboxSelected>>", lambda e: self.fill())
        for text, cmd in (("刷新", self.fill), ("启用 / 禁用", self.toggle), ("删除", self.delete),
                          ("打开 mods 文件夹", self.open)):
            ttk.Button(row, text=text, command=cmd).pack(side="left", padx=3)
        frame, self.tree = scrolled_tree(self, ("name", "state", "size"), ("文件名", "状态", "大小"),
                                         (480, 90, 100), height=18)
        self.tree.configure(selectmode="extended")
        frame.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.toggle())
        self.lbl = ttk.Label(self, text="", style="Sub.TLabel")
        self.lbl.pack(anchor="w", pady=(6, 0))
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
            self.lbl.configure(text="这个版本还没有 Mod")
            return
        files = sorted(f for f in os.listdir(d) if f.endswith((".jar", ".jar.disabled")))
        for f in files:
            on = f.endswith(".jar")
            self.tree.insert("", "end", iid=f, values=(
                f.removesuffix(".disabled"), "启用" if on else "已禁用", fmt_size(os.path.getsize(os.path.join(d, f)))))
        enabled = sum(f.endswith(".jar") for f in files)
        self.lbl.configure(text=f"共 {len(files)} 个 Mod，已启用 {enabled} 个（双击切换启用状态）")

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

    def open(self):
        d = self._dir()
        if d:
            open_folder(d)


# ====================================================================== 设置

class SettingsPage(Page):
    def build(self):
        cfg = self.app.cfg
        self.header("设置")
        card = ttk.Frame(self, style="Card.TFrame", padding=18)
        card.pack(fill="x")
        card.columnconfigure(1, weight=1)

        def label(r, t):
            ttk.Label(card, text=t, style="Card.TLabel").grid(row=r, column=0, sticky="w", pady=6, padx=(0, 14))

        label(0, "游戏目录")
        row = ttk.Frame(card, style="Card.TFrame")
        row.grid(row=0, column=1, sticky="we")
        self.var_dir = tk.StringVar(value=cfg["game_dir"])
        ttk.Entry(row, textvariable=self.var_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览…", command=self._browse).pack(side="left", padx=6)
        ttk.Button(row, text="打开", command=lambda: open_folder(self.var_dir.get())).pack(side="left")

        label(1, "下载源")
        self.cb_src = ttk.Combobox(card, state="readonly", width=28, values=[n for n, _ in SOURCES])
        codes = [c for _, c in SOURCES]
        self.cb_src.current(codes.index(cfg["source"]) if cfg["source"] in codes else 0)
        self.cb_src.grid(row=1, column=1, sticky="w")

        label(2, "下载线程数")
        self.var_threads = tk.IntVar(value=cfg["threads"])
        ttk.Spinbox(card, from_=1, to=128, textvariable=self.var_threads, width=8).grid(row=2, column=1, sticky="w")

        label(3, "游戏窗口大小")
        row = ttk.Frame(card, style="Card.TFrame")
        row.grid(row=3, column=1, sticky="w")
        self.var_w = tk.IntVar(value=cfg["width"])
        self.var_h = tk.IntVar(value=cfg["height"])
        ttk.Spinbox(row, from_=320, to=7680, textvariable=self.var_w, width=7).pack(side="left")
        ttk.Label(row, text=" × ", style="Card.TLabel").pack(side="left")
        ttk.Spinbox(row, from_=240, to=4320, textvariable=self.var_h, width=7).pack(side="left")

        label(4, "额外 JVM 参数")
        self.var_jvm = tk.StringVar(value=cfg["jvm_args"])
        ttk.Entry(card, textvariable=self.var_jvm).grid(row=4, column=1, sticky="we")

        label(5, "微软登录 Client ID")
        row = ttk.Frame(card, style="Card.TFrame")
        row.grid(row=5, column=1, sticky="we")
        self.var_cid = tk.StringVar(value=cfg["ms_client_id"])
        ttk.Entry(row, textvariable=self.var_cid, width=40).pack(side="left")
        ttk.Button(row, text="① 注册 Azure 应用",
                   command=lambda: webbrowser.open(auth.AZURE_REGISTER_URL)).pack(side="left", padx=6)
        ttk.Button(row, text="② 申请 Mojang 审核",
                   command=lambda: webbrowser.open(auth.MOJANG_REVIEW_URL)).pack(side="left")
        ttk.Label(card, text="注册时账户类型选「仅个人 Microsoft 帐户」，并在「身份验证」里打开「允许公共客户端流」。"
                  "审核通过之前微软登录会被 Minecraft 拒绝（403）。",
                  style="CardSub.TLabel", wraplength=640, justify="left").grid(row=6, column=1, sticky="w")

        ttk.Button(self, text="保存设置", command=self.save).pack(anchor="w", pady=14)
        ttk.Label(self, text="账户保存在启动器目录的 accounts.json（含登录令牌，不要发给别人）；每个版本的存档 / Mod / 配置都单独放在 versions/<版本名> 下（版本隔离）。",
                  style="Sub.TLabel").pack(anchor="w")

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
        messagebox.showinfo("设置", "已保存喵～")


def main():
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()
