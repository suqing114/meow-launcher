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
           project_type="mod", source="modrinth"):
    """返回 {'hits': [...], 'total_hits': n, 'offset': n}；project_type: mod / shader / resourcepack

    source='curseforge' 时走 CurseForge，结果会转换成和 Modrinth 一样的结构。
    """
    if source == "curseforge":
        return cf_search(query, game_version, loader, sort, offset, limit, project_type)
    facets = [[f"project_type:{project_type}"]]
    if game_version:
        facets.append([f"versions:{game_version}"])
    if loader:
        facets.append([f"categories:{loader}"])
    return _get("/search", query=query or None, facets=json.dumps(facets),
                index=sort, offset=offset, limit=limit)


def project(pid):
    if pid.startswith("cf:"):
        return cf_project(pid)
    return _get(f"/project/{pid}")


def project_versions(pid, game_version=None, loader=None):
    if pid.startswith("cf:"):
        return cf_files(pid, game_version, loader)
    return _get(f"/project/{pid}/version",
                game_versions=json.dumps([game_version]) if game_version else None,
                loaders=json.dumps([loader]) if loader else None)


def get_version(version_id):
    if version_id.startswith("cf:"):
        return cf_file(version_id)
    return _get(f"/version/{version_id}")


def primary_file(version):
    files = version.get("files") or []
    if not files:
        raise ValueError(f"{version.get('name')} 没有可下载的文件")
    return next((f for f in files if f.get("primary")), files[0])


def downloadable(version):
    """CurseForge 上有些作者不允许第三方启动器下载，这时没有下载地址。"""
    return bool(primary_file(version).get("url"))


# ---------------------------------------------------------------- CurseForge
# 官方 API 必须带 API Key（https://console.curseforge.com/ 免费申请）。
# 数据会被转换成和 Modrinth 一样的结构，项目 ID 写成 cf:<modId>，版本 ID 写成 cf:<modId>:<fileId>。

# 内置的 Key 还没通过 CurseForge 审核、没有搜索权限，先在界面上隐藏 CurseForge 来源；
# 审核通过后改成 True 就会出现在「下载资源 → 来源」里
CF_ENABLED = False

CF_API = "https://api.curseforge.com/v1"
CF_GAME = 432  # Minecraft
CF_CLASS = {"mod": 6, "resourcepack": 12, "shader": 6552}
CF_LOADER = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
CF_LOADER_NAMES = {1: "forge", 4: "fabric", 5: "quilt", 6: "neoforge"}
CF_SORT = {"relevance": 1, "downloads": 6, "follows": 12, "newest": 11, "updated": 3}
CF_RELEASE = {1: "release", 2: "beta", 3: "alpha"}
CF_DEPENDENCY = {3: "required", 2: "optional"}  # 1 内嵌库、4 工具、5 不兼容、6 包含，都不用装
try:
    # 内置的 API Key 放在 mcl/_private.py：已被 .gitignore 排除，不会推到公开仓库，打包时会一起带上
    from ._private import CURSEFORGE_API_KEY as cf_key
except ImportError:
    cf_key = ""


class CurseForgeKeyError(Exception):
    pass


def _cf(path, params=None, body=None):
    if not cf_key:
        raise CurseForgeKeyError("这个版本的喵喵启动器没有内置 CurseForge API Key，暂时只能用 Modrinth")
    import urllib.error
    url = CF_API + path + ("?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
                           if params else "")
    headers = {"x-api-key": cf_key, "Accept": "application/json"}
    try:
        if body is not None:
            return net.post_json(url, body, headers)
        return net.fetch_json(url, mirror=False, headers=headers)
    except (urllib.error.HTTPError, net.DownloadError) as e:
        if "403" in str(e) or "401" in str(e):
            if path == "/mods/search":  # 这个 Key 能用其他接口，但搜索要通过 CurseForge 审核才开放
                raise CurseForgeKeyError("内置的 CurseForge API Key 还没有搜索权限（需要向 CurseForge 申请审核），暂时只能用 Modrinth 搜索")
            raise CurseForgeKeyError("CurseForge 拒绝了内置的 API Key（可能已失效），暂时只能用 Modrinth")
        raise


def _cf_hit(m):
    loaders = sorted({CF_LOADER_NAMES[i["modLoader"]] for i in m.get("latestFilesIndexes", [])
                      if i.get("modLoader") in CF_LOADER_NAMES})
    return {
        "project_id": f"cf:{m['id']}", "slug": m.get("slug", ""), "title": m.get("name", ""),
        "description": m.get("summary", ""), "author": ", ".join(a["name"] for a in m.get("authors", [])[:2]),
        "downloads": int(m.get("downloadCount", 0)), "icon_url": (m.get("logo") or {}).get("thumbnailUrl"),
        "display_categories": [c["name"] for c in m.get("categories", [])][:3], "loaders": loaders,
        "url": (m.get("links") or {}).get("websiteUrl"), "source": "curseforge",
    }


def cf_search(query="", game_version=None, loader=None, sort="relevance", offset=0, limit=PAGE_SIZE,
              project_type="mod"):
    offset = min(offset, 10000 - limit)  # CurseForge 最多只能翻到第 10000 条
    data = _cf("/mods/search", {
        "gameId": CF_GAME, "classId": CF_CLASS[project_type], "searchFilter": query or None,
        "gameVersion": game_version, "modLoaderType": CF_LOADER.get(loader) if loader else None,
        "sortField": CF_SORT.get(sort, 2), "sortOrder": "desc", "index": offset, "pageSize": limit})
    total = min(data.get("pagination", {}).get("totalCount", 0), 10000)
    return {"hits": [_cf_hit(m) for m in data.get("data", [])], "total_hits": total, "offset": offset}


def cf_project(pid):
    m = _cf(f"/mods/{pid[3:]}")["data"]
    h = _cf_hit(m)
    return {"id": pid, "slug": h["slug"], "title": h["title"], "description": h["description"],
            "icon_url": h["icon_url"], "url": h["url"]}


def _cf_version(mod_id, f):
    gvs = [v for v in f.get("gameVersions", []) if re.fullmatch(r"\d+(\.\d+)+(-[\w.]+)?|\d\dw\d\d\w", v)]
    loaders = [v.lower() for v in f.get("gameVersions", []) if v.lower() in CF_LOADER]
    sha1 = next((h["value"] for h in f.get("hashes", []) if h.get("algo") == 1), None)
    return {
        "id": f"cf:{mod_id}:{f['id']}", "project_id": f"cf:{mod_id}", "name": f.get("displayName") or f["fileName"],
        "version_number": f["fileName"], "game_versions": gvs, "loaders": loaders,
        "version_type": CF_RELEASE.get(f.get("releaseType"), "release"), "date_published": f.get("fileDate", ""),
        "files": [{"url": f.get("downloadUrl"), "filename": f["fileName"], "primary": True,
                   "size": f.get("fileLength"), "hashes": {"sha1": sha1} if sha1 else {}}],
        "dependencies": [{"project_id": f"cf:{d['modId']}", "dependency_type": CF_DEPENDENCY[d["relationType"]]}
                         for d in f.get("dependencies", []) if d.get("relationType") in CF_DEPENDENCY],
    }


def cf_files(pid, game_version=None, loader=None, max_files=1000):
    """一个项目的全部文件（新的在前）。一页最多 50 个：先取第一页拿到总数，其余页并发取。"""
    from concurrent.futures import ThreadPoolExecutor
    mod_id = pid[3:]
    params = {"gameVersion": game_version, "modLoaderType": CF_LOADER.get(loader) if loader else None,
              "pageSize": 50}

    def page(index):
        return _cf(f"/mods/{mod_id}/files", {**params, "index": index})
    first = page(0)
    files = list(first.get("data", []))
    total = min(first.get("pagination", {}).get("totalCount", 0), max_files)
    with ThreadPoolExecutor(max_workers=8) as ex:
        for data in ex.map(page, range(50, total, 50)):
            files += data.get("data", [])
    out = [_cf_version(mod_id, f) for f in files]
    return sorted(out, key=lambda v: v["date_published"], reverse=True)


def cf_file(vid):
    _, mod_id, file_id = vid.split(":")
    return _cf_version(mod_id, _cf(f"/mods/{mod_id}/files/{file_id}")["data"])


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
                elif not downloadable(dv):
                    entry["status"] = "manual"  # 作者不允许第三方下载，只能去网页下
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
        if not f.get("url"):
            raise ValueError(f"{v.get('name')} 的作者不允许第三方启动器下载，请到 CurseForge 网页上手动下载")
        log(f"下载 {f['filename']}")
        # CurseForge 的 CDN 要求下载时也带 API Key（官方公告：不带会返回 401）
        headers = {"x-api-key": cf_key} if cf_key and "forgecdn.net" in f["url"] else None
        new = net.download_file(f["url"], os.path.join(target_dir, f["filename"]),
                                f.get("hashes", {}).get("sha1"), f.get("size"), headers=headers)
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
