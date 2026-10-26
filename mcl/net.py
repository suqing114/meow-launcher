"""网络与下载：镜像切换、带重试和 sha1 校验的下载、多线程批量下载。"""
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import __version__

USER_AGENT = f"MeowLauncher/{__version__} (Python Minecraft launcher)"
TIMEOUT = 30
max_workers = 32

BMCLAPI = "https://bmclapi2.bangbang93.com"
# 官方地址前缀 -> BMCLAPI 镜像前缀
_MIRROR_RULES = [
    ("https://piston-meta.mojang.com", BMCLAPI),
    ("https://launchermeta.mojang.com", BMCLAPI),
    ("https://piston-data.mojang.com", BMCLAPI),
    ("https://launcher.mojang.com", BMCLAPI),
    ("https://resources.download.minecraft.net", BMCLAPI + "/assets"),
    ("https://libraries.minecraft.net", BMCLAPI + "/maven"),
    ("https://meta.fabricmc.net", BMCLAPI + "/fabric-meta"),
    ("https://maven.fabricmc.net", BMCLAPI + "/maven"),
    ("https://maven.minecraftforge.net", BMCLAPI + "/maven"),
    ("https://files.minecraftforge.net/maven", BMCLAPI + "/maven"),
    ("https://maven.neoforged.net/releases", BMCLAPI + "/maven"),
    ("https://authlib-injector.yushi.moe", BMCLAPI + "/mirrors/authlib-injector"),
]

_source = "bmclapi"


def set_source(name):
    """name: 'bmclapi' 或 'official'"""
    global _source
    _source = name


class DownloadError(Exception):
    pass


class Task:
    __slots__ = ("url", "path", "sha1", "size")

    def __init__(self, url, path, sha1=None, size=None):
        self.url, self.path, self.sha1, self.size = url, path, sha1, size


def candidate_urls(url):
    """按当前下载源给出要尝试的地址列表，镜像失败时回落到官方。"""
    urls = []
    if _source == "bmclapi":
        for prefix, repl in _MIRROR_RULES:
            if url.startswith(prefix):
                urls.append(repl + url[len(prefix):])
                break
    urls.append(url)
    return urls


def _open(url, data=None, headers=None):
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def fetch_bytes(url, retries=2):
    last = None
    for u in candidate_urls(url):
        for i in range(retries):
            try:
                with _open(u) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (400, 403, 404, 410):
                    break
            except Exception as e:
                last = e
            time.sleep(0.3 * (i + 1))
    raise DownloadError(f"无法获取 {url}：{last}")


def fetch_json(url):
    return json.loads(fetch_bytes(url).decode("utf-8"))


def post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")
    with _open(url, data=body, headers={"Content-Type": "application/json"}) as r:
        return json.loads(r.read().decode("utf-8"))


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_valid(path, sha1=None, size=None):
    """已有文件是否可以直接用。给了 size 只比大小（快），否则比 sha1。"""
    if not os.path.isfile(path):
        return False
    if size is not None:
        return os.path.getsize(path) == size
    if sha1:
        return sha1_file(path).lower() == sha1.lower()
    return True


def download_file(url, path, sha1=None, size=None, retries=3):
    """下载单个文件，返回 True 表示真的下载了，False 表示本地已有。"""
    if is_valid(path, sha1, size):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    last = None
    for u in candidate_urls(url):
        for i in range(retries):
            try:
                with _open(u) as r:
                    data = r.read()
                if sha1 and hashlib.sha1(data).hexdigest().lower() != sha1.lower():
                    raise DownloadError("sha1 校验失败")
                tmp = f"{path}.{threading.get_ident()}.part"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
                return True
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (403, 404, 410):
                    break
            except Exception as e:
                last = e
            time.sleep(0.3 * (i + 1))
    raise DownloadError(f"{os.path.basename(path)} 下载失败：{last}")


def download_all(tasks, progress=None, label="下载文件"):
    """多线程下载一批 Task。progress(label, done, total)。"""
    unique = {}
    for t in tasks:
        unique.setdefault(os.path.normcase(t.path), t)
    tasks = list(unique.values())
    total = len(tasks)
    if progress:
        progress(label, 0, total)
    if not total:
        return
    done = 0
    errors = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(download_file, t.url, t.path, t.sha1, t.size): t for t in tasks}
        for fut in as_completed(futures):
            done += 1
            try:
                fut.result()
            except Exception as e:
                errors.append(str(e))
            if progress:
                progress(label, done, total)
    if errors:
        more = f"\n……等共 {len(errors)} 个" if len(errors) > 5 else ""
        raise DownloadError(f"{len(errors)} 个文件下载失败：\n" + "\n".join(errors[:5]) + more)
