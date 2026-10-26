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
        self.px = bytearray(self.w * self.h * 4)  # RGBA，逐行存放

    def fill(self, color):
        """整张填成不透明纯色。"""
        self.px = bytearray(bytes(_rgb(color) + (255,)) * (self.w * self.h))
        return self

    def paint(self, sdf, color, opacity=1.0, bbox=None):
        """bbox=(x0, y0, x1, y1)：只在这个范围里算，小形状不用扫整张图。"""
        r, g, b = _rgb(color)
        w, px = self.w, self.px
        x0, y0, x1, y1 = (0, 0, w, self.h) if bbox is None else (
            max(0, int(bbox[0]) - 1), max(0, int(bbox[1]) - 1), min(w, int(bbox[2]) + 2), min(self.h, int(bbox[3]) + 2))
        for y in range(y0, y1):
            cy = y + 0.5
            for x in range(x0, x1):
                a = 0.5 - sdf(x + 0.5, cy)
                if a <= 0:
                    continue
                a = (1.0 if a > 1 else a) * opacity
                i = (y * w + x) * 4
                da = px[i + 3] / 255
                k = da * (1 - a)
                oa = a + k
                px[i] = int((r * a + px[i] * k) / oa + 0.5)
                px[i + 1] = int((g * a + px[i + 1] * k) / oa + 0.5)
                px[i + 2] = int((b * a + px[i + 2] * k) / oa + 0.5)
                px[i + 3] = int(oa * 255 + 0.5)
        return self

    def png(self):
        n = self.w * 4
        raw = b"".join(b"\x00" + bytes(self.px[y * n:(y + 1) * n]) for y in range(self.h))

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))

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


def box(w, h, r, fill, border=None, bw=1.0, bg=None):
    """圆角矩形。先画一个刚好容下四个圆角的小图，再把中间那一行 / 一列拉长，大图也很快。

    bg：控件底下的颜色。给了就把圆角外面填上这个颜色，图片完全不透明——
    Windows 上 Tk 画半透明图片要先从屏幕读回背景再混合，很慢；不透明图片直接拷贝。
    """
    W, H = max(1, int(round(w))), max(1, int(round(h)))
    band = int(math.ceil(r + bw)) + 1
    sw, sh = min(W, 2 * band + 1), min(H, 2 * band + 1)
    small = Raster(sw, sh)
    if bg:
        small.fill(bg)
    if border:
        small.paint(rrect(0, 0, sw, sh, r), border)
        small.paint(rrect(bw, bw, sw - bw, sh - bw, max(r - bw, 0)), fill)
    else:
        small.paint(rrect(0, 0, sw, sh, r), fill)
    if (sw, sh) == (W, H):
        return small
    n, mx, my = sw * 4, sw // 2, sh // 2
    rows = []
    for y in range(sh):
        row = bytes(small.px[y * n:(y + 1) * n])
        rows.append(row[:mx * 4] + row[mx * 4:(mx + 1) * 4] * (W - sw + 1) + row[(mx + 1) * 4:])
    img = Raster(W, H)
    img.px = bytearray(b"".join(rows[:my] + [rows[my]] * (H - sh + 1) + rows[my + 1:]))
    return img


def banner_bg(w, h, span, r, c0, c1, page_bg, decos):
    """启动页横幅背景：横向渐变 + 半透明白色装饰圆 + 左侧圆角，整张不透明。

    span：渐变从 c0 走到 c1 的宽度，再往右保持 c1（窗口多宽都能用同一张图）。
    decos：[(圆心 x, 圆心 y, 半径, 不透明度)]。
    """
    w, h = int(w), int(h)
    a0, a1 = _rgb(c0), _rgb(c1)
    base = bytearray()
    for x in range(w):
        t = min(x / span, 1.0)
        base += bytes((int(a0[0] + (a1[0] - a0[0]) * t), int(a0[1] + (a1[1] - a0[1]) * t),
                       int(a0[2] + (a1[2] - a0[2]) * t), 255))
    rows = [bytearray(base) for _ in range(h)]

    def blend(y, x, color, a):
        row, i = rows[y], x * 4
        for k in range(3):
            row[i + k] = int(row[i + k] * (1 - a) + color[k] * a + 0.5)

    for cx, cy, rad, op in decos:
        for y in range(max(0, int(cy - rad - 1)), min(h, int(cy + rad + 2))):
            dy = y + 0.5 - cy
            half = math.sqrt(max(rad * rad - dy * dy, 0.0))
            for x in range(max(0, int(cx - half - 2)), min(w, int(cx + half + 3))):
                a = 0.5 - (math.hypot(x + 0.5 - cx, dy) - rad)
                if a > 0:
                    blend(y, x, (255, 255, 255), min(a, 1.0) * op)
    bgc = _rgb(page_bg)
    for y in range(int(r) + 1):
        for x in range(int(r) + 1):
            a = 0.5 - (r - math.hypot(x + 0.5 - r, y + 0.5 - r))
            if a > 0:
                blend(y, x, bgc, min(a, 1.0))
                blend(h - 1 - y, x, bgc, min(a, 1.0))
    img = Raster(w, h)
    img.px = bytearray(b"".join(rows))
    return img


def cat_logo(n, color=ACCENT):
    """一只猫猫头。"""
    img = Raster(n, n)
    ear = 0.30 * n

    def poly(pts, col):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        img.paint(polygon(pts), col, bbox=(min(xs), min(ys), max(xs), max(ys)))

    def disc(cx, cy, r, col):
        img.paint(circle(cx, cy, r), col, bbox=(cx - r, cy - r, cx + r, cy + r))

    head = union(circle(n * 0.5, n * 0.58, n * 0.36),
                 polygon([(n * 0.16, n * 0.42), (n * 0.20, n * 0.08), (n * 0.16 + ear, n * 0.30)]),
                 polygon([(n * 0.84, n * 0.42), (n * 0.84 - ear, n * 0.30), (n * 0.80, n * 0.08)]))
    img.paint(head, color, bbox=(n * 0.13, n * 0.07, n * 0.87, n * 0.95))
    poly([(n * 0.23, n * 0.30), (n * 0.245, n * 0.16), (n * 0.36, n * 0.26)], "#ffb3cb")
    poly([(n * 0.77, n * 0.30), (n * 0.64, n * 0.26), (n * 0.755, n * 0.16)], "#ffb3cb")
    for ex in (0.37, 0.63):
        disc(n * ex, n * 0.56, n * 0.075, "#ffffff")
        disc(n * ex + n * 0.012, n * 0.57, n * 0.04, "#1c1f31")
    poly([(n * 0.46, n * 0.67), (n * 0.54, n * 0.67), (n * 0.5, n * 0.72)], "#ff8fb1")
    img.paint(polyline([(n * 0.42, n * 0.75), (n * 0.46, n * 0.79), (n * 0.5, n * 0.75),
                        (n * 0.54, n * 0.79), (n * 0.58, n * 0.75)], max(1.2, n * 0.025)), "#ffffff",
              bbox=(n * 0.39, n * 0.72, n * 0.61, n * 0.82))
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
        imgs = [self.keep(f"{name}.{k}", box(p(320), p(64), p(r), c, border and (b or border), p(1), bg=bg))
                for k, c, b in (("n", fill, None), ("h", hover, None), ("p", press, None),
                                ("d", disabled, LINE if border else None))]
        el = f"{name}.bg"
        # 图片故意画得比按钮大：ttk 拉伸时是平铺中间区域，图太小会贴上千次，非常卡。
        # width/height 是最小尺寸，不设的话按钮会被撑成图片那么大。
        # padding 默认等于 border，会把按钮撑高，显式设为 0，留白交给 Button.padding
        s.element_create(el, "image", imgs[0], ("disabled", imgs[3]), ("pressed", imgs[2]),
                         ("active", imgs[1]), border=p(r + 2), padding=0, sticky="nsew",
                         width=p(2 * r + 6), height=p(2 * r + 6))
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
            img = self.keep(name, box(p(960), p(640), p(12), fill, border, p(1), bg=bg))
            s.element_create(f"{name}.bg", "image", img, border=p(14), padding=0, sticky="nsew",
                             width=p(30), height=p(30))
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
                ("CardBig.TLabel", CARD, FG, (FAMILY, 13, "bold")),
                ("CardAccent.TLabel", CARD, ACCENT, (FAMILY, 10, "bold")),
                ("Log.TLabel", LOG_BG, LOG_FG, (FAMILY, 11, "bold")),
                ("LogSub.TLabel", LOG_BG, MUTED, (FAMILY, 9))):
            s.configure(name, background=bg, foreground=fg, font=font)
        if self.icon_family:
            s.configure("CardIcon.TLabel", background=CARD, foreground=ACCENT, font=(self.icon_family, 12))
            s.configure("LogIcon.TLabel", background=LOG_BG, foreground=LOG_FG, font=(self.icon_family, 12))

        # 按钮
        self._button(s, "TButton", CARD, HOVER, PRESS, "#f6f7fa", border=FIELD_LINE)
        self._button(s, "Bg.TButton", CARD, HOVER, PRESS, "#f6f7fa", border=FIELD_LINE, bg=BG)
        self._button(s, "Accent.TButton", ACCENT, ACCENT_HOVER, ACCENT_PRESS, ACCENT_DISABLED)
        self._button(s, "BgAccent.TButton", ACCENT, ACCENT_HOVER, ACCENT_PRESS, ACCENT_DISABLED, bg=BG)
        self._button(s, "Ghost.TButton", CARD, HOVER, PRESS, CARD)
        self._button(s, "LogGhost.TButton", LOG_BG, LOG_HOVER, "#30344e", LOG_BG, bg=LOG_BG)
        s.map("TButton", foreground=[("disabled", MUTED)])
        s.map("Bg.TButton", foreground=[("disabled", MUTED)])
        s.map("Ghost.TButton", foreground=[("disabled", MUTED)])
        for name in ("Accent.TButton", "BgAccent.TButton"):
            s.configure(name, foreground="#ffffff", font=(FAMILY, 10, "bold"))
            s.map(name, foreground=[("disabled", "#f2f4fe")])
            s.configure("Big." + name, font=(FAMILY, 13, "bold"), padding=(p(20), p(11)))
        s.configure("Danger.TButton", foreground=DANGER)
        s.configure("Ghost.TButton", foreground=ACCENT, padding=(p(10), p(5)))
        s.configure("LogGhost.TButton", foreground=LOG_FG, padding=(p(10), p(4)))

        # 输入框 / 下拉框 / 数字框共用的圆角边框
        fimgs = [self.keep(f"field.{k}", box(p(960), p(64), p(6), fill, line, p(w), bg=CARD))
                 for k, fill, line, w in (("n", CARD, FIELD_LINE, 1), ("h", CARD, FIELD_HOVER, 1),
                                          ("f", CARD, ACCENT, 1.6), ("d", "#f5f6f9", LINE, 1))]
        s.element_create("Field.bg", "image", fimgs[0], ("disabled", fimgs[3]), ("focus", fimgs[2]),
                         ("hover", fimgs[1]), border=p(8), padding=0, sticky="nsew",
                         width=p(20), height=p(20))
        # 下拉箭头画在输入框底色上（正常白色、禁用浅灰），保持不透明；
        # 数字框的上下箭头贴着边框，不透明会盖住边框线，而且只有几个，保留透明
        aw = p(26)
        chev = {st: Raster(aw, p(16)).fill(bgc).paint(
            polyline([(aw * 0.32, p(6)), (aw * 0.5, p(10)), (aw * 0.68, p(6))], p(1.6)), SUB)
            for st, bgc in (("n", CARD), ("d", "#f5f6f9"))}
        up = Raster(p(18), p(10)).paint(polyline([(p(5), p(7)), (p(9), p(3.5)), (p(13), p(7))], p(1.4)), SUB)
        dn = Raster(p(18), p(10)).paint(polyline([(p(5), p(3)), (p(9), p(6.5)), (p(13), p(3))], p(1.4)), SUB)
        s.element_create("Meow.downarrow", "image", self.keep("chev", chev["n"]),
                         ("disabled", self.keep("chev.d", chev["d"])), sticky="")
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
            img = Raster(b + gap, b).fill(CARD)  # 复选框都放在卡片上
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
        for prefix, trough, cols in (("", CARD, ("#cdd2df", "#a9b0c4")), ("Log.", LOG_BG, ("#3a3f5c", "#545a7c"))):
            thumbs = []
            for col in cols:
                pill = box(tw - p(4), p(800), p(3), col, bg=trough)
                img = Raster(tw, p(800)).fill(trough)  # 左右各留 2px 槽的颜色
                n, m = img.w * 4, pill.w * 4
                for y in range(img.h):
                    img.px[y * n + p(2) * 4:y * n + p(2) * 4 + m] = pill.px[y * m:(y + 1) * m]
                thumbs.append(self.keep(f"{prefix}thumb.{col}", img))
            el = f"Meow{prefix.rstrip('.')}.vthumb"
            s.element_create(el, "image", thumbs[0], ("pressed", thumbs[1]), ("active", thumbs[1]),
                             border=(0, p(6)), padding=0, sticky="nsew", width=tw, height=p(24))
            s.layout(f"{prefix}Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                (el, {"expand": "1", "sticky": "nsew"})]})])
            s.configure(f"{prefix}Vertical.TScrollbar", troughcolor=trough, background=trough, bordercolor=trough,
                        lightcolor=trough, darkcolor=trough, arrowsize=tw, gripcount=0)

        # 进度条：圆角细条
        ph = p(8)
        tr = self.keep("ptrough", box(p(800), ph, ph / 2, "#e6e9f3", bg=CARD))
        bar = self.keep("pbar", box(p(800), ph, ph / 2, ACCENT, bg="#e6e9f3"))
        s.element_create("Meow.ptrough", "image", tr, border=(p(5), 0), padding=0, sticky="ew",
                         width=p(24), height=ph)
        s.element_create("Meow.pbar", "image", bar, border=(p(5), 0), padding=0, sticky="nsew",
                         width=p(10), height=ph)
        s.layout("Horizontal.TProgressbar", [("Meow.ptrough", {"sticky": "nsew", "children": [
            ("Meow.pbar", {"side": "left", "sticky": "ns"})]})])
        s.configure("Horizontal.TProgressbar", background=CARD, thickness=ph)

        # 列表
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nsew"})])
        s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG, borderwidth=0,
                    rowheight=int(self.linespace * 2.1), font=(FAMILY, 10))
        s.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", FG)])
        s.configure("Treeview.Heading", background=CARD, foreground=SUB, font=(FAMILY, 9),
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
