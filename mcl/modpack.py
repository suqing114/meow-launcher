"""Modrinth 整合包（.mrpack）安装。

.mrpack 是一个 zip：modrinth.index.json 列出需要的游戏版本、加载器和每个文件的下载地址 / 校验值；
overrides/ 和 client-overrides/ 里的东西原样复制进游戏目录（配置、资源包等）。
格式说明：https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack
"""
import json
import os
import re
import shutil
import urllib.parse
import zipfile

from . import game, mods, net

# 格式规范只允许从这几个域名下载，别的一律拒绝（防止整合包里藏奇怪的下载地址）
ALLOWED_HOSTS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com"}

# 清单里 dependencies 的键 -> 我们的加载器名
_LOADER_KEYS = {"fabric-loader": "fabric", "quilt-loader": "quilt", "forge": "forge", "neoforge": "neoforge"}


def read_index(path):
    with zipfile.ZipFile(path) as z:
        try:
            index = json.loads(z.read("modrinth.index.json"))
        except KeyError:
            raise ValueError(f"{os.path.basename(path)} 不是 Modrinth 整合包（里面没有 modrinth.index.json）")
    if index.get("game") != "minecraft":
        raise ValueError("这个整合包不是给 Minecraft 用的")
    return index


def requirements(index):
    """(游戏版本, {加载器: 版本})，加载器版本换成 game.install_instance 认识的写法。"""
    deps = index.get("dependencies", {})
    gv = deps.get("minecraft")
    if not gv:
        raise ValueError("整合包没有写需要哪个 Minecraft 版本")
    loaders = {}
    for key, ver in deps.items():
        kind = _LOADER_KEYS.get(key)
        if not kind:
            continue
        if kind == "forge" or (kind == "neoforge" and gv == "1.20.1"):
            ver = ver if ver.startswith(gv + "-") else f"{gv}-{ver}"  # Forge 的版本号带游戏版本前缀
        loaders[kind] = ver
    return gv, loaders


def summary(index):
    gv, loaders = requirements(index)
    files = [f for f in index.get("files", []) if f.get("env", {}).get("client") != "unsupported"]
    return {"name": index.get("name", ""), "version": index.get("versionId", ""), "summary": index.get("summary", ""),
            "game": gv, "loaders": loaders, "files": len(files)}


def _safe_dest(root, rel):
    """把整合包里的相对路径放到 root 下面；拒绝 ../ 之类跳出目录的路径（zip slip）。"""
    rel = rel.replace("\\", "/")
    if rel.startswith("/") or re.match(r"^[a-zA-Z]:", rel):
        raise ValueError(f"整合包里有不安全的路径：{rel}")
    dest = os.path.normpath(os.path.join(root, *rel.split("/")))
    if os.path.commonpath([os.path.normpath(root), dest]) != os.path.normpath(root):
        raise ValueError(f"整合包里有不安全的路径：{rel}")
    return dest


def install(gd, pack_path, name, progress=None, manifest=None):
    """安装一个 .mrpack 为新版本 name，返回版本名。"""
    index = read_index(pack_path)
    gv, loaders = requirements(index)
    # 先装游戏和加载器（名字重复会直接报错，不会覆盖已有版本）
    name = gd.install_instance(name, gv, loaders, progress, manifest)
    root = gd.version_dir(name)

    tasks = []
    for f in index.get("files", []):
        if f.get("env", {}).get("client") == "unsupported":
            continue  # 只给服务端用的文件
        urls = [u for u in f.get("downloads", []) if urllib.parse.urlparse(u).hostname in ALLOWED_HOSTS]
        if not urls:
            raise ValueError(f"{f['path']} 的下载地址不在允许的域名里，为了安全拒绝安装")
        tasks.append(net.Task(urls[0], _safe_dest(root, f["path"]), f.get("hashes", {}).get("sha1"),
                              f.get("fileSize"), mirror=False))
    net.download_all(tasks, progress, "下载整合包里的文件")

    if progress:
        progress("解压配置文件", 0, 1)
    with zipfile.ZipFile(pack_path) as z:
        for prefix in ("overrides/", "client-overrides/"):  # client-overrides 后解压，覆盖同名文件
            for item in z.infolist():
                if item.is_dir() or not item.filename.startswith(prefix):
                    continue
                dest = _safe_dest(root, item.filename[len(prefix):])
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with z.open(item) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)

    meta_path = gd.meta_path(name)  # 记一下来自哪个整合包
    meta = gd.instance_info(name)
    meta["modpack"] = {"name": index.get("name"), "version": index.get("versionId")}
    with open(meta_path, "w", encoding="utf-8") as fp:
        json.dump(meta, fp, ensure_ascii=False, indent=2)
    return name


def download(version, cache_dir, progress=None):
    """把 Modrinth 上某个整合包版本的 .mrpack 下载到缓存目录，返回路径。"""
    f = mods.primary_file(version)
    path = os.path.join(cache_dir, f["filename"])
    if progress:
        progress("下载整合包", 0, 1)
    net.download_file(f["url"], path, f.get("hashes", {}).get("sha1"), f.get("size"), mirror=False)
    return path


def default_name(pack_name, version=""):
    """整合包名 -> 合法的版本文件夹名。"""
    name = re.sub(r'[\\/:*?"<>|]', "", f"{pack_name} {version}".strip())
    return re.sub(r"\s+", " ", name)[:60] or "modpack"
