"""界面主题：配色、纯 Python 生成的抗锯齿圆角图片，以及 ttk 样式。

ttk 的 clam 主题只有直角和立体边框，这里用有向距离场（SDF）在内存里画出圆角按钮、
输入框、卡片等 PNG，再作为 ttk 的 image 元素使用，不依赖任何第三方库。
"""
import base64
import math
import struct
import sys
import zlib
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

IS_WINDOWS = sys.platform.startswith("win")
FAMILY = "Microsoft YaHei UI" if IS_WINDOWS else "TkDefaultFont"
MONO = "Consolas" if IS_WINDOWS else "TkFixedFont"

# ---------------------------------------------------------------- 配色
BG = "#f4f6fb"
CARD = "#ffffff"
LINE = "#e6e9f2"
FIELD_LINE = "#d5d9e6"
FIELD_HOVER = "#b4bbd1"
HOVER = "#f2f4fa"
PRESS = "#e8ebf4"
FG = "#1f2333"
SUB = "#6b7280"
MUTED = "#a0a6b6"
ACCENT = "#5b6ee1"
ACCENT_HOVER = "#4c5fd6"
ACCENT_PRESS = "#3f51c2"
ACCENT_SOFT = "#eaedfd"
ACCENT_DISABLED = "#bcc4f1"
ACCENT2 = "#9a6be0"  # 渐变的另一端
DANGER = "#e5484d"
SUCCESS = "#2fae76"
WARNING = "#e3a008"
SIDEBAR = "#1c1f31"
SIDEBAR_HOVER = "#262a40"
SIDEBAR_ACTIVE = "#2e3352"
SIDEBAR_TEXT = "#a9aec6"
LOG_BG = "#181b2b"
LOG_FG = "#cdd2e6"
LOG_HOVER = "#262a40"

AVATAR_COLORS = ["#5b6ee1", "#e0679a", "#2fae76", "#e39b2d", "#3aa0d8", "#9a6be0", "#d9534f", "#46b3a6"]

# Segoe Fluent Icons / Segoe MDL2 Assets 的字形
ICONS = {
    "play": "", "download": "", "puzzle": "", "library": "",
    "settings": "", "add": "", "delete": "", "folder": "",
    "refresh": "", "search": "", "globe": "", "person": "",
    "check": "", "info": "", "error": "", "link": "", "code": "",
}


# ---------------------------------------------------------------- 栅格化

def _rgb(c):
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


class Raster:
    """一张 RGBA 画布，用 SDF 函数上色，输出 PNG。"""

    def __init__(self, w, h):
        self.w, self.h = max(1, int(round(w))), max(1, int(round(h)))
        self.px = [0.0] * (self.w * self.h * 4)

    def paint(self, sdf, color, opacity=1.0):
        r, g, b = _rgb(color)
        w, px = self.w, self.px
        for y in range(self.h):
            cy = y + 0.5
            for x in range(w):
                a = 0.5 - sdf(x + 0.5, cy)
                if a <= 0:
                    continue
                a = (1.0 if a > 1 else a) * opacity
                i = (y * w + x) * 4
                da = px[i + 3]
                oa = a + da * (1 - a)
                k = da * (1 - a)
                px[i] = (r * a + px[i] * k) / oa
                px[i + 1] = (g * a + px[i + 1] * k) / oa
                px[i + 2] = (b * a + px[i + 2] * k) / oa
                px[i + 3] = oa
        return self

    def png(self):
        rows = bytearray()
        px = self.px
        for y in range(self.h):
            rows.append(0)
            base = y * self.w * 4
            for i in range(base, base + self.w * 4, 4):
                rows += bytes((int(px[i] + 0.5), int(px[i + 1] + 0.5), int(px[i + 2] + 0.5),
                               int(px[i + 3] * 255 + 0.5)))

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))

    def photo(self, master):
        return tk.PhotoImage(master=master, data=base64.b64encode(self.png()).decode("ascii"))


def rrect(x0, y0, x1, y1, r):
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw, hh = (x1 - x0) / 2 - r, (y1 - y0) / 2 - r

    def f(x, y):
        qx, qy = abs(x - cx) - hw, abs(y - cy) - hh
        return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
    return f


def circle(cx, cy, r):
    return lambda x, y: math.hypot(x - cx, y - cy) - r


def polyline(points, width):
    """一条有宽度、两端圆头的折线。"""
    segs = list(zip(points, points[1:]))
    half = width / 2

    def f(x, y):
        best = 1e9
        for (ax, ay), (bx, by) in segs:
            ex, ey = bx - ax, by - ay
            t = max(0.0, min(1.0, ((x - ax) * ex + (y - ay) * ey) / (ex * ex + ey * ey)))
            best = min(best, math.hypot(x - ax - ex * t, y - ay - ey * t))
        return best - half
    return f


def polygon(pts):
    def f(x, y):
        d = (x - pts[0][0]) ** 2 + (y - pts[0][1]) ** 2
        s = 1.0
        j = len(pts) - 1
        for i in range(len(pts)):
            ex, ey = pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]
            wx, wy = x - pts[i][0], y - pts[i][1]
            t = max(0.0, min(1.0, (wx * ex + wy * ey) / (ex * ex + ey * ey)))
            bx, by = wx - ex * t, wy - ey * t
            d = min(d, bx * bx + by * by)
            c1, c2, c3 = y >= pts[i][1], y < pts[j][1], ex * wy > ey * wx
            if (c1 and c2 and c3) or (not c1 and not c2 and not c3):
                s = -s
            j = i
        return s * math.sqrt(d)
    return f


def union(*fs):
    return lambda x, y: min(f(x, y) for f in fs)


def box(w, h, r, fill, border=None, bw=1.0):
    img = Raster(w, h)
    if border:
        img.paint(rrect(0, 0, img.w, img.h, r), border)
        img.paint(rrect(bw, bw, img.w - bw, img.h - bw, max(r - bw, 0)), fill)
    else:
        img.paint(rrect(0, 0, img.w, img.h, r), fill)
    return img


def cat_logo(n, color=ACCENT):
    """一只猫猫头。"""
    img = Raster(n, n)
    ear = 0.30 * n
    head = union(circle(n * 0.5, n * 0.58, n * 0.36),
                 polygon([(n * 0.16, n * 0.42), (n * 0.20, n * 0.08), (n * 0.16 + ear, n * 0.30)]),
                 polygon([(n * 0.84, n * 0.42), (n * 0.84 - ear, n * 0.30), (n * 0.80, n * 0.08)]))
    img.paint(head, color)
    img.paint(polygon([(n * 0.23, n * 0.30), (n * 0.245, n * 0.16), (n * 0.36, n * 0.26)]), "#ffb3cb")
    img.paint(polygon([(n * 0.77, n * 0.30), (n * 0.64, n * 0.26), (n * 0.755, n * 0.16)]), "#ffb3cb")
    for ex in (0.37, 0.63):
        img.paint(circle(n * ex, n * 0.56, n * 0.075), "#ffffff")
        img.paint(circle(n * ex + n * 0.012, n * 0.57, n * 0.04), "#1c1f31")
    img.paint(polygon([(n * 0.46, n * 0.67), (n * 0.54, n * 0.67), (n * 0.5, n * 0.72)]), "#ff8fb1")
    img.paint(polyline([(n * 0.42, n * 0.75), (n * 0.46, n * 0.79), (n * 0.5, n * 0.75),
                        (n * 0.54, n * 0.79), (n * 0.58, n * 0.75)], max(1.2, n * 0.025)), "#ffffff")
    return img


# ---------------------------------------------------------------- 主题

def icon_font_family(root):
    fams = set(tkfont.families(root))
    for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if f in fams:
            return f
    return None


def style_titlebar(win, caption=SIDEBAR, text="#ffffff"):
    """Windows 11：把系统标题栏染成和侧边栏一样的颜色。"""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = int(win.wm_frame(), 16)

        def colorref(c):
            r, g, b = _rgb(c)
            return ctypes.c_int(r | (g << 8) | (b << 16))
        for attr, c in ((35, caption), (36, text)):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(colorref(c)), 4)
    except Exception:
        pass


class Theme:
    def __init__(self, root):
        self.root = root
        self.s = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self.icon_family = icon_font_family(root)
        self.images = {}
        self._avatar_cache = {}
        self._fonts()
        self._style()

    def px(self, v):
        return int(round(v * self.s))

    def keep(self, key, raster):
        self.images[key] = raster.photo(self.root)
        return self.images[key]

    def icon(self, name):
        """图标字形；系统没有图标字体时返回空串。"""
        return ICONS.get(name, "") if self.icon_family else ""

    def icon_font(self, size=12):
        return (self.icon_family or FAMILY, size)

    def avatar(self, seed, size=44):
        color = AVATAR_COLORS[sum(map(ord, seed)) % len(AVATAR_COLORS)]
        key = (color, size)
        if key not in self._avatar_cache:
            n = self.px(size)
            self._avatar_cache[key] = Raster(n, n).paint(circle(n / 2, n / 2, n / 2), color).photo(self.root)
        return self._avatar_cache[key]

    def _fonts(self):
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
            tkfont.nametofont(name).configure(family=FAMILY, size=10)
        self.linespace = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        r = self.root
        r.option_add("*TCombobox*Listbox.font", (FAMILY, 10))
        r.option_add("*TCombobox*Listbox.background", CARD)
        r.option_add("*TCombobox*Listbox.foreground", FG)
        r.option_add("*TCombobox*Listbox.selectBackground", ACCENT_SOFT)
        r.option_add("*TCombobox*Listbox.selectForeground", FG)
        r.option_add("*TCombobox*Listbox.borderWidth", 0)
        r.option_add("*TCombobox*Listbox.relief", "flat")

    def _button(self, s, name, fill, hover, press, disabled, border=None, r=7, bg=CARD):
        p = self.px
        n = p(2 * r + 10)
        imgs = [self.keep(f"{name}.{k}", box(n, n, p(r), c, border and (b or border), p(1)))
                for k, c, b in (("n", fill, None), ("h", hover, None), ("p", press, None),
                                ("d", disabled, LINE if border else None))]
        el = f"{name}.bg"
        # padding 默认等于 border，会把按钮撑得很高，这里显式设为 0，留白交给 Button.padding
        s.element_create(el, "image", imgs[0], ("disabled", imgs[3]), ("pressed", imgs[2]),
                         ("active", imgs[1]), border=p(r + 2), padding=0, sticky="nsew")
        s.layout(name, [(el, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])
        # clam 默认给按钮 11 个字符的最小宽度，短按钮会被撑得很宽
        s.configure(name, background=bg, padding=(p(14), p(6)), anchor="center", relief="flat", width=0)

    def _style(self):
        p = self.px
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, font=(FAMILY, 10), bordercolor=LINE,
                    lightcolor=BG, darkcolor=BG, troughcolor=BG, focuscolor=ACCENT,
                    selectbackground=ACCENT, selectforeground="#ffffff", insertcolor=FG)

        # 卡片：圆角白底 + 浅色描边
        for name, fill, border, bg in (("Card", CARD, LINE, BG), ("LogCard", LOG_BG, None, BG)):
            n = p(40)
            img = self.keep(name, box(n, n, p(12), fill, border, p(1)))
            s.element_create(f"{name}.bg", "image", img, border=p(14), padding=0, sticky="nsew")
            s.layout(f"{name}.TFrame", [(f"{name}.bg", {"sticky": "nsew"})])
            s.configure(f"{name}.TFrame", background=bg)
        s.configure("Surface.TFrame", background=CARD)
        s.configure("Log.TFrame", background=LOG_BG)

        # 文字
        for name, bg, fg, font in (
                ("TLabel", BG, FG, (FAMILY, 10)),
                ("Card.TLabel", CARD, FG, (FAMILY, 10)),
                ("H1.TLabel", BG, FG, (FAMILY, 18, "bold")),
                ("Sub.TLabel", BG, SUB, (FAMILY, 10)),
                ("CardSub.TLabel", CARD, SUB, (FAMILY, 9)),
                ("CardTitle.TLabel", CARD, FG, (FAMILY, 11, "bold")),
                ("CardBig.TLabel", CARD, FG, (FAMILY, 15, "bold")),
                ("CardAccent.TLabel", CARD, ACCENT, (FAMILY, 10, "bold")),
                ("Log.TLabel", LOG_BG, LOG_FG, (FAMILY, 11, "bold")),
                ("LogSub.TLabel", LOG_BG, MUTED, (FAMILY, 9))):
            s.configure(name, background=bg, foreground=fg, font=font)
        if self.icon_family:
            s.configure("CardIcon.TLabel", background=CARD, foreground=ACCENT, font=(self.icon_family, 13))
            s.configure("LogIcon.TLabel", background=LOG_BG, foreground=LOG_FG, font=(self.icon_family, 12))

        # 按钮
        self._button(s, "TButton", CARD, HOVER, PRESS, "#f6f7fa", border=FIELD_LINE)
        self._button(s, "Bg.TButton", CARD, HOVER, PRESS, "#f6f7fa", border=FIELD_LINE, bg=BG)
        self._button(s, "Accent.TButton", ACCENT, ACCENT_HOVER, ACCENT_PRESS, ACCENT_DISABLED)
        self._button(s, "Ghost.TButton", CARD, HOVER, PRESS, CARD)
        self._button(s, "LogGhost.TButton", LOG_BG, LOG_HOVER, "#30344e", LOG_BG, bg=LOG_BG)
        s.map("TButton", foreground=[("disabled", MUTED)])
        s.map("Bg.TButton", foreground=[("disabled", MUTED)])
        s.map("Ghost.TButton", foreground=[("disabled", MUTED)])
        s.configure("Accent.TButton", foreground="#ffffff", font=(FAMILY, 10, "bold"))
        s.map("Accent.TButton", foreground=[("disabled", "#f2f4fe")])
        s.configure("Big.Accent.TButton", font=(FAMILY, 12, "bold"), padding=(p(20), p(11)))
        s.configure("Danger.TButton", foreground=DANGER)
        s.configure("Ghost.TButton", foreground=ACCENT, padding=(p(10), p(5)))
        s.configure("LogGhost.TButton", foreground=LOG_FG, padding=(p(10), p(4)))

        # 输入框 / 下拉框 / 数字框共用的圆角边框
        n = p(28)
        fimgs = [self.keep(f"field.{k}", box(n, n, p(6), fill, line, p(w)))
                 for k, fill, line, w in (("n", CARD, FIELD_LINE, 1), ("h", CARD, FIELD_HOVER, 1),
                                          ("f", CARD, ACCENT, 1.6), ("d", "#f5f6f9", LINE, 1))]
        s.element_create("Field.bg", "image", fimgs[0], ("disabled", fimgs[3]), ("focus", fimgs[2]),
                         ("hover", fimgs[1]), border=p(8), padding=0, sticky="nsew")
        aw = p(26)
        chev = Raster(aw, p(16)).paint(polyline([(aw * 0.32, p(6)), (aw * 0.5, p(10)), (aw * 0.68, p(6))],
                                                p(1.6)), SUB)
        up = Raster(p(18), p(10)).paint(polyline([(p(5), p(7)), (p(9), p(3.5)), (p(13), p(7))], p(1.4)), SUB)
        dn = Raster(p(18), p(10)).paint(polyline([(p(5), p(3)), (p(9), p(6.5)), (p(13), p(3))], p(1.4)), SUB)
        s.element_create("Meow.downarrow", "image", self.keep("chev", chev), sticky="")
        s.element_create("Spin.uparrow", "image", self.keep("up", up), sticky="")
        s.element_create("Spin.downarrow", "image", self.keep("dn", dn), sticky="")
        s.layout("TEntry", [("Field.bg", {"sticky": "nsew", "children": [
            ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
        s.layout("TCombobox", [("Field.bg", {"sticky": "nsew", "children": [
            ("Meow.downarrow", {"side": "right", "sticky": "ns"}),
            ("Combobox.padding", {"sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]})]})])
        s.layout("TSpinbox", [("Field.bg", {"sticky": "nsew", "children": [
            ("Spin.uparrow", {"side": "top", "sticky": "e"}),
            ("Spin.downarrow", {"side": "bottom", "sticky": "e"}),
            ("Spinbox.padding", {"sticky": "nsew", "children": [("Spinbox.textarea", {"sticky": "nsew"})]})]})])
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            s.configure(name, background=CARD, fieldbackground=CARD, foreground=FG, padding=(p(9), p(6)),
                        selectbackground=ACCENT_SOFT, selectforeground=FG)
            s.map(name, foreground=[("disabled", MUTED)])
        s.configure("TCombobox", padding=(p(9), p(6), p(2), p(6)))
        s.map("TCombobox", selectbackground=[("readonly", CARD)], selectforeground=[("readonly", FG)],
              fieldbackground=[("readonly", CARD)])
        s.configure("ComboboxPopdownFrame", relief="solid", borderwidth=1, bordercolor=FIELD_LINE)

        # 复选框
        b = p(18)
        gap = p(8)

        def check(fill, line, mark):
            img = Raster(b + gap, b)
            img.paint(rrect(0, 0, b, b, p(4.5)), line)
            img.paint(rrect(p(1.3), p(1.3), b - p(1.3), b - p(1.3), p(3.4)), fill)
            if mark:
                img.paint(polyline([(b * 0.27, b * 0.52), (b * 0.44, b * 0.68), (b * 0.74, b * 0.34)], p(2)), "#ffffff")
            return img
        on = self.keep("chk.on", check(ACCENT, ACCENT, True))
        off = self.keep("chk.off", check(CARD, FIELD_LINE, False))
        offh = self.keep("chk.offh", check(CARD, ACCENT, False))
        ond = self.keep("chk.ond", check(ACCENT_DISABLED, ACCENT_DISABLED, True))
        s.element_create("Meow.indicator", "image", off, ("disabled selected", ond), ("selected", on),
                         ("active", offh), sticky="")
        s.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
            ("Meow.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.label", {"side": "left", "sticky": "nsew"})]})])
        for name, bg in (("TCheckbutton", BG), ("Card.TCheckbutton", CARD)):
            s.configure(name, background=bg, foreground=FG, padding=(0, p(2)))
            s.map(name, background=[("active", bg)])

        # 细滚动条（无箭头）
        tw = p(10)
        for st, col in (("n", "#cdd2df"), ("h", "#a9b0c4")):
            self.keep(f"thumb.{st}", Raster(tw, p(30)).paint(rrect(p(2), 0, tw - p(2), p(30), p(3)), col))
        s.element_create("Meow.vthumb", "image", self.images["thumb.n"], ("pressed", self.images["thumb.h"]),
                         ("active", self.images["thumb.h"]), border=p(6), sticky="nsew")
        s.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Meow.vthumb", {"expand": "1", "sticky": "nsew"})]})])
        s.configure("Vertical.TScrollbar", troughcolor=CARD, background=CARD, bordercolor=CARD,
                    lightcolor=CARD, darkcolor=CARD, arrowsize=tw, gripcount=0)
        s.configure("Log.Vertical.TScrollbar", troughcolor=LOG_BG, background=LOG_BG, bordercolor=LOG_BG,
                    lightcolor=LOG_BG, darkcolor=LOG_BG)

        # 进度条：圆角细条
        ph = p(8)
        tr = self.keep("ptrough", box(p(24), ph, ph / 2, "#e6e9f3"))
        bar = self.keep("pbar", box(p(24), ph, ph / 2, ACCENT))
        s.element_create("Meow.ptrough", "image", tr, border=(p(5), 0), sticky="ew")
        s.element_create("Meow.pbar", "image", bar, border=(p(5), 0), sticky="nsew")
        s.layout("Horizontal.TProgressbar", [("Meow.ptrough", {"sticky": "nsew", "children": [
            ("Meow.pbar", {"side": "left", "sticky": "ns"})]})])
        s.configure("Horizontal.TProgressbar", background=CARD, thickness=ph)

        # 列表
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nsew"})])
        s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG, borderwidth=0,
                    rowheight=int(self.linespace * 2.1), font=(FAMILY, 10))
        s.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", FG)])
        s.configure("Treeview.Heading", background=CARD, foreground=SUB, font=(FAMILY, 9, "bold"),
                    relief="flat", borderwidth=0, bordercolor=CARD, lightcolor=CARD, darkcolor=CARD,
                    padding=(p(6), p(7)))
        s.map("Treeview.Heading", background=[("active", HOVER)], relief=[("pressed", "flat")])

        # 选项卡（添加账户对话框）
        s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, p(10)))
        s.configure("TNotebook.Tab", background=BG, foreground=SUB, padding=(p(18), p(8)),
                    font=(FAMILY, 10), borderwidth=0, bordercolor=BG, lightcolor=BG)
        s.map("TNotebook.Tab", background=[("selected", CARD), ("active", HOVER)],
              foreground=[("selected", ACCENT)], lightcolor=[("selected", CARD)],
              expand=[("selected", (0, 0, 0, 0))])

        s.configure("TPanedwindow", background=BG)
        s.configure("Sash", sashthickness=p(14), gripcount=0, background=BG, lightcolor=BG, bordercolor=BG)
        s.configure("TSeparator", background=LINE)
        self.style = s
