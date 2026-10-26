"""Mod 搜索与下载（Modrinth API v2）。"""
import json
import os
import urllib.parse

from . import net

API = "https://api.modrinth.com/v2"
PAGE_SIZE = 20


def _get(path, **params):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return net.fetch_json(f"{API}{path}" + (f"?{q}" if q else ""))


# 资源类型 -> 实例目录下的文件夹
FOLDERS = {"mod": "mods", "shader": "shaderpacks", "resourcepack": "resourcepacks"}


def search(query="", game_version=None, loader=None, sort="relevance", offset=0, limit=PAGE_SIZE,
           project_type="mod"):
    """返回 {'hits': [...], 'total_hits': n, 'offset': n}；project_type: mod / shader / resourcepack"""
    facets = [[f"project_type:{project_type}"]]
    if game_version:
        facets.append([f"versions:{game_version}"])
    if loader:
        facets.append([f"categories:{loader}"])
    return _get("/search", query=query or None, facets=json.dumps(facets),
                index=sort, offset=offset, limit=limit)


def project(pid):
    return _get(f"/project/{pid}")


def project_versions(pid, game_version=None, loader=None):
    return _get(f"/project/{pid}/version",
                game_versions=json.dumps([game_version]) if game_version else None,
                loaders=json.dumps([loader]) if loader else None)


def get_version(version_id):
    return _get(f"/version/{version_id}")


def primary_file(version):
    files = version.get("files") or []
    if not files:
        raise ValueError(f"{version.get('name')} 没有可下载的文件")
    return next((f for f in files if f.get("primary")), files[0])


def installed_project_ids(mods_dir):
    """用 sha1 反查 mods 目录里已有文件属于哪些 Modrinth 项目。"""
    if not os.path.isdir(mods_dir):
        return set()
    hashes = [net.sha1_file(os.path.join(mods_dir, f))
              for f in os.listdir(mods_dir) if f.endswith((".jar", ".jar.disabled"))]
    if not hashes:
        return set()
    try:
        found = net.post_json(f"{API}/version_files", {"hashes": hashes, "algorithm": "sha1"})
    except Exception:
        return set()
    return {v["project_id"] for v in found.values()}


def install(version, mods_dir, game_version=None, loader=None, with_deps=True, log=None):
    """下载一个版本到 mods_dir（光影、资源包也用它，传对应文件夹并关掉前置即可）。

    可选递归下载必需前置。返回 [(文件名, 是否新下载)]。
    """
    log = log or (lambda s: None)
    seen = {version["project_id"]}
    if with_deps:
        log("检查已安装的 Mod……")
        seen |= installed_project_ids(mods_dir)
    queue = [version]
    result = []
    while queue:
        v = queue.pop(0)
        f = primary_file(v)
        log(f"下载 {f['filename']}")
        new = net.download_file(f["url"], os.path.join(mods_dir, f["filename"]),
                                f.get("hashes", {}).get("sha1"), f.get("size"))
        result.append((f["filename"], new))
        if not with_deps:
            continue
        for d in v.get("dependencies") or []:
            if d.get("dependency_type") != "required":
                continue
            pid, vid = d.get("project_id"), d.get("version_id")
            if pid and pid in seen:
                continue
            if vid:
                dv = get_version(vid)
            elif pid:
                candidates = project_versions(pid, game_version, loader)
                if not candidates:
                    log(f"找不到前置 {pid} 适配 {game_version}/{loader} 的版本，已跳过")
                    continue
                dv = candidates[0]
            else:
                continue
            if dv["project_id"] in seen:
                continue
            seen.add(dv["project_id"])
            queue.append(dv)
    return result
