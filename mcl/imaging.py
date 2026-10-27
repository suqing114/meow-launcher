"""把网上下载的图片（WebP / JPEG / PNG / GIF …）解码、缩放成 PNG，给 Tk 显示。

Tk 只认识 PNG / GIF，而 Modrinth 上很多图标是 WebP。这里不用第三方库：
Windows 上调用系统自带的 WIC（Windows Imaging Component，Win10 1809+ / Win11 自带 WebP 解码器）；
如果装了 Pillow 就优先用 Pillow。都不可用时返回 None，界面会显示首字母占位。
"""
import ctypes
import io
import os
import sys
import tempfile
import uuid

from . import theme

IS_WINDOWS = sys.platform.startswith("win")


def to_png(data, size):
    """图片字节 -> 不超过 size×size 的 PNG 字节；解不了返回 None。可以在后台线程调用。"""
    if not data:
        return None
    try:
        return _pillow(data, size)
    except ImportError:
        pass
    except Exception:
        return None
    if IS_WINDOWS:
        try:
            return _wic(data, size)
        except Exception:
            return None
    return None


def _fit(w, h, size):
    if w <= size and h <= size:
        return w, h
    k = size / max(w, h)
    return max(1, round(w * k)), max(1, round(h * k))


def _pillow(data, size):
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.seek(0)
    im = im.convert("RGBA")
    im = im.resize(_fit(im.width, im.height, size), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


# ---------------------------------------------------------------- Windows WIC（直接用 ctypes 调 COM 接口）

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(s):
    u = uuid.UUID(s)
    g = _GUID(u.time_low, u.time_mid, u.time_hi_version)
    g.Data4[:] = list(u.bytes[8:])
    return g


CLSID_WICImagingFactory = _guid("cacaf262-9370-4615-a13b-9f5539da4c0a")
IID_IWICImagingFactory = _guid("ec5ec8a9-c395-4314-9c77-54d7a935ff70")
GUID_WICPixelFormat32bppBGRA = _guid("6fddc324-4e03-4bfe-b185-3d77768dc90f")


def _method(obj, index, *argtypes):
    """取 COM 对象虚表里第 index 个方法（0-2 是 IUnknown 的 QueryInterface / AddRef / Release）。"""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    fn = proto(vtbl[index])
    return lambda *args: fn(obj, *args)


def _release(obj):
    if obj and obj.value:
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(obj)


def _wic(data, size):
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 0)  # 每个线程都要初始化 COM；已初始化过会返回 S_FALSE，无所谓
    P = ctypes.POINTER(ctypes.c_void_p)
    factory, decoder, frame, scaler, conv = (ctypes.c_void_p() for _ in range(5))
    fd, path = tempfile.mkstemp(suffix=".img")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        hr = ole32.CoCreateInstance(ctypes.byref(CLSID_WICImagingFactory), None, 1,
                                    ctypes.byref(IID_IWICImagingFactory), ctypes.byref(factory))
        if hr != 0:
            raise OSError(f"CoCreateInstance 失败：{hr:#x}")
        # IWICImagingFactory::CreateDecoderFromFilename(文件名, 厂商, GENERIC_READ, 按需读元数据, 解码器)
        _method(factory, 3, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, P)(
            path, None, 0x80000000, 0, ctypes.byref(decoder))
        _method(decoder, 13, ctypes.c_uint, P)(0, ctypes.byref(frame))  # IWICBitmapDecoder::GetFrame
        w, h = ctypes.c_uint(), ctypes.c_uint()
        _method(frame, 3, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint))(
            ctypes.byref(w), ctypes.byref(h))  # IWICBitmapSource::GetSize
        tw, th = _fit(w.value, h.value, size)
        _method(factory, 11, P)(ctypes.byref(scaler))  # CreateBitmapScaler
        _method(scaler, 8, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_int)(
            frame, tw, th, 3)  # IWICBitmapScaler::Initialize，3 = Fant 高质量缩小
        _method(factory, 10, P)(ctypes.byref(conv))  # CreateFormatConverter
        _method(conv, 8, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.c_int, ctypes.c_void_p,
                ctypes.c_double, ctypes.c_int)(scaler, ctypes.byref(GUID_WICPixelFormat32bppBGRA), 0, None, 0.0, 0)
        stride = tw * 4
        buf = (ctypes.c_ubyte * (stride * th))()
        _method(conv, 7, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_ubyte))(
            None, stride, len(buf), buf)  # IWICBitmapSource::CopyPixels
    finally:
        for obj in (conv, scaler, frame, decoder, factory):
            _release(obj)
        try:
            os.remove(path)
        except OSError:
            pass
    px = bytearray(buf)
    px[0::4], px[2::4] = px[2::4], px[0::4]  # BGRA -> RGBA
    img = theme.Raster(tw, th)
    img.px = px
    return img.png()
