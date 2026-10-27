"""下载 Java：用 Mojang 官方启动器同款的 Java 运行时。

每个游戏版本的 json 里都写了需要哪个运行时（javaVersion.component），例如
1.16.5 -> jre-legacy (Java 8)，1.20.5 -> java-runtime-delta (Java 21)。
下载后解开就能用，装在 <游戏目录>/runtime/<component>/ 下。
"""
import hashlib
import json
import lzma
import os
import stat

from . import java, net
from .game import ARCH, OS_NAME

ALL_URL = ("https://piston-meta.mojang.com/v1/products/java-runtime/"
           "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json")
# BMCLAPI 会把请求转到各地节点，部分节点证书有问题；Java 有几百个小文件，每个都先失败再回落太慢，直接走官方源

COMPONENT_FOR_MAJOR = {8: "jre-legacy", 16: "java-runtime-alpha", 17: "java-runtime-gamma",
                       21: "java-runtime-delta", 25: "java-runtime-epsilon"}
SUITS = {8: "1.16.5 及更早", 16: "1.17 – 1.17.1", 17: "1.18 – 1.20.4", 21: "1.20.5 – 1.21.11", 25: "26.1 及之后"}
_SKIP = {"java-runtime-beta", "minecraft-java-exe"}  # beta 和 gamma 是同一个 Java 17


def platform_key():
    if OS_NAME == "windows":
        return {"x86_64": "windows-x64", "arm64": "windows-arm64"}.get(ARCH, "windows-x86")
    if OS_NAME == "osx":
        return "mac-os-arm64" if ARCH == "arm64" else "mac-os"
    return "linux" if ARCH == "x86_64" else "linux-i386"


def runtime_root(game_root):
    return os.path.join(game_root, "runtime")


def java_exe(game_root, component):
    exe = "java.exe" if OS_NAME == "windows" else "java"
    return os.path.join(runtime_root(game_root), component, "bin", exe)


def _marker(game_root, component):
    return os.path.join(runtime_root(game_root), component, ".meow-runtime.json")


def installed(game_root):
    """已经完整装好的运行时：{component: 版本名}"""
    out = {}
    root = runtime_root(game_root)
    if not os.path.isdir(root):
        return out
    for comp in os.listdir(root):
        try:
            with open(_marker(game_root, comp), encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        if os.path.isfile(java_exe(game_root, comp)):
            out[comp] = info.get("version", "")
    return out


def list_runtimes():
    """[{component, version, major, suits, manifest}]，按 Java 版本排序。"""
    data = net.fetch_json(ALL_URL, mirror=False).get(platform_key(), {})
    out = []
    for comp, entries in data.items():
        if not entries or comp in _SKIP or "snapshot" in comp:
            continue
        name = entries[0]["version"]["name"]
        major = java.parse_major(name)
        out.append({"component": comp, "version": name, "major": major, "suits": SUITS.get(major, ""),
                    "manifest": entries[0]["manifest"]["url"]})
    return sorted(out, key=lambda r: r["major"])


def component_for(version_json, major):
    """游戏版本需要的运行时：优先用版本 json 里写的，没写就按 Java 主版本猜。"""
    comp = version_json.get("javaVersion", {}).get("component")
    return comp or COMPONENT_FOR_MAJOR.get(major, "jre-legacy")


def install(game_root, rt, progress=None):
    """下载并安装一个运行时，返回 java 可执行文件路径。rt 是 list_runtimes() 里的一项。"""
    comp = rt["component"]
    base = os.path.join(runtime_root(game_root), comp)
    if progress:
        progress(f"获取 Java {rt['major']} 文件列表", 0, 1)
    files = net.fetch_json(rt["manifest"], mirror=False)["files"]
    tasks, packed, executables, links = [], [], [], []
    for path, f in files.items():
        dest = os.path.join(base, *path.split("/"))
        if f["type"] == "directory":
            os.makedirs(dest, exist_ok=True)
        elif f["type"] == "file":
            raw, xz = f["downloads"]["raw"], f["downloads"].get("lzma")
            if f.get("executable"):
                executables.append(dest)
            if net.is_valid(dest, raw["sha1"], raw["size"]):
                continue
            if xz:  # 有 LZMA 压缩版就下压缩版，体积大约只有三分之一，下完再解压
                tasks.append(net.Task(xz["url"], dest + ".lzma", xz["sha1"], xz["size"], mirror=False))
                packed.append((dest, raw))
            else:
                tasks.append(net.Task(raw["url"], dest, raw["sha1"], raw["size"], mirror=False))
        elif f["type"] == "link":
            links.append((dest, f["target"]))
    net.download_all(tasks, progress, f"下载 Java {rt['major']}")
    for i, (dest, raw) in enumerate(packed):
        if progress:
            progress(f"解压 Java {rt['major']}", i + 1, len(packed))
        with lzma.open(dest + ".lzma") as src:
            data = src.read()
        if hashlib.sha1(data).hexdigest() != raw["sha1"]:
            raise net.DownloadError(f"{os.path.basename(dest)} 解压后校验失败")
        with open(dest, "wb") as out:
            out.write(data)
        os.remove(dest + ".lzma")
    if OS_NAME != "windows":
        for path in executables:
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        for dest, target in links:
            if not os.path.lexists(dest):
                os.symlink(target, dest)
    with open(_marker(game_root, comp), "w", encoding="utf-8") as f:
        json.dump({"component": comp, "version": rt["version"], "major": rt["major"]}, f)
    return java_exe(game_root, comp)


def needs_download(javas, required):
    """本机的 Java 能不能跑这个版本。

    更高版本的 Java 一般能跑要求较低的新版游戏，但 1.16.5 及更早（要求 Java 8）
    在新 Java 上经常起不来（尤其是 Forge），所以要求 8 时必须真有 Java 8。
    """
    majors = {j.major for j in javas}
    if required <= 8:
        return 8 not in majors
    return not any(m >= required for m in majors)
