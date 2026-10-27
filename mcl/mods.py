"""Mod 搜索与下载（Modrinth API v2）。"""
import json
import os
import re
import urllib.parse
import zipfile

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


# ---------------------------------------------------------------- 已安装的 Mod

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _jar_mod_ids(path):
    """读 jar 里声明的 Mod ID：Fabric / Quilt / Forge / NeoForge 都支持。"""
    ids = set()
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "fabric.mod.json" in names:
                j = json.loads(z.read("fabric.mod.json").decode("utf-8", "replace"), strict=False)
                ids.add(j.get("id", ""))
                ids.update(j.get("provides", []))
            if "quilt.mod.json" in names:
                j = json.loads(z.read("quilt.mod.json").decode("utf-8", "replace"), strict=False)
                ids.add(j.get("quilt_loader", {}).get("id", ""))
            for toml in ("META-INF/mods.toml", "META-INF/neoforge.mods.toml"):
                if toml in names:
                    ids.update(re.findall(r'^\s*modId\s*=\s*"([^"]+)"', z.read(toml).decode("utf-8", "replace"), re.M))
    except (zipfile.BadZipFile, OSError, ValueError, KeyError):
        pass
    return {i for i in ids if i}


def scan_installed(mods_dir):
    """mods 目录里已经有哪些 Mod：{'projects': Modrinth 项目 ID, 'ids': 规整后的 Mod ID}。

    两路判断：sha1 反查 Modrinth（认得从 Modrinth 下的文件），读 jar 里的 Mod ID
    （从 CurseForge 等别处下的、版本不同的也认得）。
    """
    out = {"projects": set(), "ids": set()}
    if not os.path.isdir(mods_dir):
        return out
    jars = [os.path.join(mods_dir, f) for f in os.listdir(mods_dir) if f.endswith((".jar", ".jar.disabled"))]
    for p in jars:
        out["ids"] |= {_norm(i) for i in _jar_mod_ids(p)}
    if jars:
        try:
            found = net.post_json(f"{API}/version_files",
                                  {"hashes": [net.sha1_file(p) for p in jars], "algorithm": "sha1"})
            out["projects"] = {v["project_id"] for v in found.values()}
        except Exception:
            pass
    return out


def is_installed(proj, installed):
    if proj["id"] in installed["projects"]:
        return True
    ids = installed["ids"]
    for name in (proj.get("slug"), proj.get("title")):
        n = _norm(name)
        # architectury-api 的 Mod ID 是 architectury，xxx-lib 也常省掉 lib
        if n and (n in ids or any(n.endswith(suf) and n[:-len(suf)] in ids for suf in ("api", "lib", "mod"))):
            return True
    return False


# ---------------------------------------------------------------- 依赖

def plan_dependencies(version, game_version=None, loader=None, mods_dir=None, log=None):
    """分析一个版本的依赖，返回 (必需, 可选) 两个列表，每项：
    {'project': 项目信息, 'version': 要装的版本或 None, 'status': install/installed/missing, 'via': 谁需要它}

    必需依赖会递归展开（依赖的依赖），已经分析过的项目跳过，所以循环依赖也不会死循环；
    可选依赖只列出这个 Mod 自己的。
    """
    log = log or (lambda s: None)
    installed = scan_installed(mods_dir) if mods_dir else {"projects": set(), "ids": set()}
    required, optional = [], []
    seen = {version["project_id"]}
    queue = [(version, None)]
    while queue:
        v, via = queue.pop(0)
        for d in v.get("dependencies") or []:
            typ = d.get("dependency_type")
            if typ not in ("required", "optional") or (typ == "optional" and via is not None):
                continue
            pid, dvid = d.get("project_id"), d.get("version_id")
            dv = None
            if not pid and dvid:
                dv = get_version(dvid)
                pid = dv["project_id"]
            if not pid or pid in seen:
                continue
            seen.add(pid)
            proj = project(pid)
            log(f"分析依赖：{proj['title']}")
            entry = {"project": proj, "version": None, "status": "install", "via": via, "type": typ}
            if is_installed(proj, installed):
                entry["status"] = "installed"
            else:
                if dv is None:
                    if dvid:
                        dv = get_version(dvid)
                    else:
                        cands = project_versions(pid, game_version, loader)
                        dv = cands[0] if cands else None
                if dv is None:
                    entry["status"] = "missing"
                entry["version"] = dv
            (required if typ == "required" else optional).append(entry)
            if typ == "required" and entry["status"] == "install":
                queue.append((dv, proj["title"]))  # 依赖的依赖
    return required, optional


def install_versions(versions, target_dir, log=None):
    """下载一组版本的主文件到 target_dir。返回 [(文件名, 是否新下载)]。"""
    log = log or (lambda s: None)
    result = []
    for v in versions:
        f = primary_file(v)
        log(f"下载 {f['filename']}")
        new = net.download_file(f["url"], os.path.join(target_dir, f["filename"]),
                                f.get("hashes", {}).get("sha1"), f.get("size"))
        result.append((f["filename"], new))
    return result


def install(version, mods_dir, game_version=None, loader=None, with_deps=True, log=None, extra=()):
    """安装一个版本，连同（递归的）必需依赖；extra 是用户勾选的可选依赖版本，它们的必需依赖也一起装。"""
    todo, seen = [version], {version["project_id"]}
    if with_deps:
        for root in [version, *extra]:
            if root["project_id"] not in seen:
                todo.append(root)
                seen.add(root["project_id"])
            required, _ = plan_dependencies(root, game_version, loader, mods_dir, log)
            for e in required:
                if e["status"] == "install" and e["project"]["id"] not in seen:
                    todo.append(e["version"])
                    seen.add(e["project"]["id"])
    return install_versions(todo, mods_dir, log)
