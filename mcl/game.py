"""游戏核心：版本清单、安装原版 / Fabric、补全文件、拼启动命令。"""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
import zipfile

from . import __version__, java, net

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
FABRIC_META = "https://meta.fabricmc.net/v2"
ASSET_URL = "https://resources.download.minecraft.net"
FORGE_MAVEN = "https://maven.minecraftforge.net"
FORGE_PROMOTIONS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases"
QUILT_META = "https://meta.quiltmc.org/v3"
OPTIFINE_API = "https://bmclapi2.bangbang93.com/optifine"
LAUNCHER_NAME = "MeowLauncher"


def _os_name():
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "osx"
    return "linux"


OS_NAME = _os_name()
if OS_NAME == "windows":
    # 不用 platform.machine()/version()：Python 3.12 在 Windows 上会为此做一次 WMI 查询，导入就要 0.2 秒
    _MACHINE = (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64")).lower()
    _OS_VERSION = "%d.%d.%d" % tuple(sys.getwindowsversion()[:3])
else:
    _MACHINE = platform.machine().lower()
    _OS_VERSION = platform.release()
IS_64 = _MACHINE in ("amd64", "x86_64", "arm64", "aarch64")
ARCH = "arm64" if _MACHINE in ("arm64", "aarch64") else ("x86_64" if IS_64 else "x86")


# ---------------------------------------------------------------- 工具函数

def rule_allows(rules, features=None):
    """Mojang 的 rules：按顺序匹配，最后一条命中的规则说了算，默认不允许。"""
    if not rules:
        return True
    features = features or {}
    allowed = False
    for r in rules:
        ok = True
        o = r.get("os")
        if o:
            if "name" in o and o["name"] != OS_NAME:
                ok = False
            if "arch" in o and o["arch"] != ARCH:
                ok = False
            if "version" in o and not re.search(o["version"], _OS_VERSION):
                ok = False
        for k, v in (r.get("features") or {}).items():
            if bool(features.get(k)) != v:
                ok = False
        if ok:
            allowed = r.get("action") == "allow"
    return allowed


def maven_path(name):
    """group:artifact:version[:classifier][@ext] -> 相对路径"""
    ext = "jar"
    if "@" in name:
        name, ext = name.rsplit("@", 1)
    parts = name.split(":")
    group, artifact, version = parts[:3]
    classifier = parts[3] if len(parts) > 3 else None
    filename = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + f".{ext}"
    return "/".join(group.split(".") + [artifact, version, filename])


def offline_uuid(name):
    """和官方服务端一致的离线 UUID：md5("OfflinePlayer:" + name) 的 v3 UUID。"""
    h = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
    h[6] = (h[6] & 0x0F) | 0x30
    h[8] = (h[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(h)).hex


def fetch_manifest():
    return net.fetch_json(MANIFEST_URL)


def fabric_game_versions():
    return [v["version"] for v in net.fetch_json(f"{FABRIC_META}/versions/game")]


def fabric_loader_versions(game_version):
    """返回 [{'version': '0.16.5', 'stable': True}, ...]，不支持该版本时为空列表。"""
    data = net.fetch_json(f"{FABRIC_META}/versions/loader/{game_version}")
    return [{"version": d["loader"]["version"], "stable": d["loader"].get("stable", False)} for d in data]


def _maven_versions(metadata_url):
    xml = net.fetch_bytes(metadata_url).decode("utf-8")
    return re.findall(r"<version>([^<]+)</version>", xml)


def _version_key(v):
    return [int(x) for x in re.findall(r"\d+", v)]


def forge_versions(game_version):
    """返回该游戏版本可用的 Forge，新的在前；推荐版标记为 stable。"""
    vs = [v for v in _maven_versions(f"{FORGE_MAVEN}/net/minecraftforge/forge/maven-metadata.xml")
          if v.startswith(game_version + "-")]
    try:
        promos = net.fetch_json(FORGE_PROMOTIONS).get("promos", {})
    except Exception:
        promos = {}
    rec = promos.get(f"{game_version}-recommended")
    out = []
    for v in sorted(vs, key=_version_key, reverse=True):
        build = v[len(game_version) + 1:].split("-")[0]
        out.append({"version": v, "stable": build == rec})
    return out


def neoforge_versions(game_version):
    """NeoForge 版本号不带 MC 版本：1.21.1 -> 21.1.x，1.21 -> 21.0.x；1.20.1 用的是旧的 forge 构件。"""
    if game_version == "1.20.1":
        vs = [v for v in _maven_versions(f"{NEOFORGE_MAVEN}/net/neoforged/forge/maven-metadata.xml")
              if v.startswith("1.20.1-")]
    else:
        m = re.fullmatch(r"1\.(\d+)(?:\.(\d+))?", game_version)
        if m:
            prefix = f"{m.group(1)}.{m.group(2) or 0}."
        else:
            prefix = game_version + (".0." if game_version.count(".") == 1 else ".")
        vs = [v for v in _maven_versions(f"{NEOFORGE_MAVEN}/net/neoforged/neoforge/maven-metadata.xml")
              if v.startswith(prefix)]
    return [{"version": v, "stable": "beta" not in v and "alpha" not in v}
            for v in sorted(vs, key=_version_key, reverse=True)]


def quilt_loader_versions(game_version):
    data = net.fetch_json(f"{QUILT_META}/versions/loader/{game_version}")
    vs = sorted((d["loader"]["version"] for d in data), key=_version_key, reverse=True)
    return [{"version": v, "stable": "-" not in v} for v in vs]


def _optifine_key(version):
    """HD_U_I6 > HD_U_I6_pre6 > HD_U_I5 > HD_U_H9"""
    m = re.match(r"(?:HD_U|HD|L)_([A-Z])(\d+)(?:_pre(\d+))?", version)
    if not m:
        return ("", 0, 0, 0)
    return (m.group(1), int(m.group(2)), 0 if m.group(3) else 1, int(m.group(3) or 0))


def split_optifine(version):
    """HD_U_I6_pre6 -> ('HD_U', 'I6_pre6')，对应 BMCLAPI 下载地址里的 type 和 patch。"""
    m = re.match(r"(HD_U|HD|L)_(.+)$", version)
    if not m:
        raise ValueError(f"OptiFine 版本号格式不对：{version}")
    return m.group(1), m.group(2)


def optifine_versions(game_version):
    """OptiFine 没有官方 API，用 BMCLAPI 的列表。版本号形如 HD_U_I6。"""
    try:
        data = net.fetch_json(f"{OPTIFINE_API}/{game_version}")
    except net.DownloadError:
        return []
    out = [{"version": f"{d['type']}_{d['patch']}", "stable": "pre" not in d["patch"].lower(),
            "forge": d.get("forge") or ""} for d in data]
    return sorted(out, key=lambda x: _optifine_key(x["version"]), reverse=True)


def loader_versions(kind, game_version):
    return {"fabric": fabric_loader_versions, "quilt": quilt_loader_versions, "forge": forge_versions,
            "neoforge": neoforge_versions, "optifine": optifine_versions}[kind](game_version)


LOADER_NAMES = {"forge": "Forge", "neoforge": "NeoForge", "fabric": "Fabric", "quilt": "Quilt", "optifine": "OptiFine"}
# 互相冲突的加载器（和 HMCL 一致：OptiFine 只能和 Forge 一起用，其余两两互斥）
CONFLICTS = {
    "forge": {"neoforge", "fabric", "quilt"},
    "neoforge": {"forge", "fabric", "quilt", "optifine"},
    "fabric": {"forge", "neoforge", "quilt", "optifine"},
    "quilt": {"forge", "neoforge", "fabric", "optifine"},
    "optifine": {"neoforge", "fabric", "quilt"},
}


def check_conflicts(kinds):
    for a in kinds:
        for b in kinds:
            if b in CONFLICTS.get(a, ()):
                raise ValueError(f"{LOADER_NAMES[a]} 和 {LOADER_NAMES[b]} 不能同时安装")


def short_version(kind, game_version, version):
    """界面上显示用：1.20.1-47.4.28 -> 47.4.28"""
    if version.startswith(game_version + "-"):
        return version[len(game_version) + 1:]
    return version


def inspect_local_file(path):
    """识别本机的加载器文件，返回 (kind, 游戏版本, 加载器版本)。

    支持：Forge / NeoForge 安装器 jar、OptiFine jar、Fabric / Quilt 的版本 json。
    """
    name = os.path.basename(path)
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        for lib in j.get("libraries", []):
            parts = lib.get("name", "").split(":")
            if parts[:2] == ["net.fabricmc", "fabric-loader"]:
                return "fabric", j.get("inheritsFrom"), parts[2]
            if parts[:2] == ["org.quiltmc", "quilt-loader"]:
                return "quilt", j.get("inheritsFrom"), parts[2]
        raise ValueError(f"{name} 不是 Fabric / Quilt 的版本配置文件")
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError(f"{name} 不是有效的 jar 文件")
    with z:
        names = set(z.namelist())
        if "install_profile.json" in names:
            profile = json.loads(z.read("install_profile.json"))
            if "versionInfo" in profile:  # 1.12.2 及更早
                gv = profile["install"].get("minecraft")
                ver = profile["install"]["path"].split(":")[2]
                return "forge", gv, ver
            vj = json.loads(z.read(profile.get("json", "/version.json").lstrip("/")))
            gv = profile.get("minecraft") or vj.get("inheritsFrom")
            vid = profile.get("version") or vj.get("id", "")
            kind = "neoforge" if profile.get("profile", "").lower() == "neoforge" else "forge"
            # 版本 id 形如 1.20.1-forge-47.4.28 / neoforge-21.1.265，换成和在线列表一样的写法
            if vid.startswith("neoforge-"):
                return kind, gv, vid[len("neoforge-"):]
            if "-forge-" in vid:
                return kind, gv, vid.replace("-forge-", "-", 1)
            raise ValueError(f"认不出 {name} 是哪个加载器的安装器")
        if any(n.startswith("optifine/") for n in names) or "Config.class" in names:
            m = re.match(r"(?:preview_)?OptiFine_(.+?)_(HD_U_\w+?)(?:\.jar)?$", name, re.I)
            if not m:
                raise ValueError("认不出 OptiFine 的版本号，请保持官网下载时的文件名，例如 OptiFine_1.20.1_HD_U_I6.jar")
            return "optifine", m.group(1), m.group(2)
    raise ValueError(f"{name} 不是支持的加载器文件（Forge / NeoForge 安装器、OptiFine、Fabric / Quilt 版本 json）")


def installer_url(kind, game_version, version):
    if kind == "forge":
        return f"{FORGE_MAVEN}/net/minecraftforge/forge/{version}/forge-{version}-installer.jar"
    artifact = "forge" if game_version == "1.20.1" else "neoforge"
    return f"{NEOFORGE_MAVEN}/net/neoforged/{artifact}/{version}/{artifact}-{version}-installer.jar"


def _main_class(jar):
    with zipfile.ZipFile(jar) as z:
        text = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    text = text.replace("\r\n", "\n").replace("\n ", "")  # 合并续行
    m = re.search(r"^Main-Class:\s*(\S+)", text, re.M)
    if not m:
        raise RuntimeError(f"{os.path.basename(jar)} 没有 Main-Class")
    return m.group(1)


# ---------------------------------------------------------------- 游戏目录

class GameDir:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.versions = os.path.join(self.root, "versions")
        self.libraries = os.path.join(self.root, "libraries")
        self.assets = os.path.join(self.root, "assets")

    # ---- 版本信息
    def version_dir(self, vid):
        return os.path.join(self.versions, vid)

    def json_path(self, vid):
        return os.path.join(self.versions, vid, vid + ".json")

    def mods_dir(self, vid):
        # 版本隔离：每个版本的游戏目录就是 versions/<id>
        return os.path.join(self.version_dir(vid), "mods")

    def list_versions(self):
        if not os.path.isdir(self.versions):
            return []
        return sorted(v for v in os.listdir(self.versions) if os.path.isfile(self.json_path(v)))

    def read_json(self, vid):
        with open(self.json_path(vid), encoding="utf-8") as f:
            return json.load(f)

    def resolve(self, vid):
        """读取版本 json，并把 inheritsFrom 的父版本合并进来。"""
        j = self.read_json(vid)
        parent_id = j.get("inheritsFrom")
        if not parent_id:
            j["_jar"] = j.get("jar") or vid
            return j
        if not os.path.isfile(self.json_path(parent_id)):
            raise FileNotFoundError(f"版本 {vid} 依赖的原版 {parent_id} 不存在，请先安装它")
        parent = self.resolve(parent_id)
        merged = dict(parent)
        for k, v in j.items():
            if k == "libraries":
                merged["libraries"] = v + parent.get("libraries", [])
            elif k == "arguments":
                pa = parent.get("arguments", {})
                merged["arguments"] = {
                    "game": pa.get("game", []) + v.get("game", []),
                    "jvm": pa.get("jvm", []) + v.get("jvm", []),
                }
            elif k != "inheritsFrom":
                merged[k] = v
        merged["_jar"] = parent["_jar"]
        return merged

    def game_version_of(self, vid):
        j = self.read_json(vid)
        # HMCL 会把原版和加载器合并成一个 json，原版版本号记在 patches 里
        for patch in j.get("patches", []):
            if patch.get("id") == "game" and patch.get("version"):
                return patch["version"]
        while j.get("inheritsFrom"):
            parent = j["inheritsFrom"]
            if not os.path.isfile(self.json_path(parent)):
                return parent
            j = self.read_json(parent)
        return j.get("id", vid)

    def meta_path(self, vid):
        return os.path.join(self.versions, vid, "meow.json")

    def instance_info(self, vid):
        """{'game': 游戏版本, 'loaders': {kind: 版本}, 'optifine_file': OptiFine 放在 mods 里的文件名}

        喵喵启动器装的版本有 meow.json；别的启动器装的就从版本 json 的库里推断。
        """
        try:
            with open(self.meta_path(vid), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
        info = {"game": self.game_version_of(vid), "loaders": {}, "optifine_file": None}
        try:
            resolved = self.resolve(vid)
        except Exception:
            return info
        libs = resolved.get("libraries", [])
        # 新版 Forge / NeoForge 的版本号在启动参数里
        args = [a for a in resolved.get("arguments", {}).get("game", []) if isinstance(a, str)]
        for flag, kind in (("--fml.neoForgeVersion", "neoforge"), ("--fml.forgeVersion", "forge")):
            if flag in args[:-1]:
                ver = args[args.index(flag) + 1]
                info["loaders"][kind] = f"{info['game']}-{ver}" if kind == "forge" else ver
                break
        for lib in libs:
            parts = lib.get("name", "").split(":")
            if len(parts) < 3:
                continue
            ga = tuple(parts[:2])
            kind = {("net.fabricmc", "fabric-loader"): "fabric", ("org.quiltmc", "quilt-loader"): "quilt",
                    ("net.neoforged", "neoforge"): "neoforge", ("net.neoforged", "forge"): "neoforge",
                    ("net.minecraftforge", "forge"): "forge", ("optifine", "OptiFine"): "optifine"}.get(ga)
            if kind and kind not in info["loaders"]:
                ver = parts[2]
                if kind == "optifine":
                    ver = ver.split("_", 1)[1] if "_" in ver else ver
                info["loaders"][kind] = ver
        mods = self.mods_dir(vid)
        if "optifine" not in info["loaders"] and os.path.isdir(mods):
            for f in os.listdir(mods):
                m = re.match(r"(?:preview_)?OptiFine_.+?_(HD_U_\w+?)\.jar$", f, re.I)
                if m:
                    info["loaders"]["optifine"] = m.group(1)
                    info["optifine_file"] = f
        return info

    def loader_of(self, vid):
        """主要的 Mod 加载器（给 Modrinth 筛选用），OptiFine 不算。"""
        loaders = self.instance_info(vid)["loaders"]
        for kind in ("neoforge", "forge", "quilt", "fabric"):
            if kind in loaders:
                return kind
        return None

    def required_java(self, vid):
        return self.resolve(vid).get("javaVersion", {}).get("majorVersion", 8)

    def delete_version(self, vid):
        shutil.rmtree(self.version_dir(vid))

    # ---- 安装
    def _save_json(self, vid, data):
        os.makedirs(self.version_dir(vid), exist_ok=True)
        with open(self.json_path(vid), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def install_vanilla(self, vid, progress=None, manifest=None):
        self._fetch_vanilla_json(vid, progress, manifest)
        self.complete(vid, progress)
        return vid

    def _fetch_vanilla_json(self, vid, progress=None, manifest=None):
        if progress:
            progress("获取版本信息", 0, 1)
        manifest = manifest or fetch_manifest()
        entry = next((v for v in manifest["versions"] if v["id"] == vid), None)
        if not entry:
            raise ValueError(f"版本清单里找不到 {vid}")
        net.download_file(entry["url"], self.json_path(vid), entry.get("sha1"))

    def install_instance(self, name, game_version, loaders, progress=None, manifest=None, replace=False):
        """安装（或改装）一个实例：原版 + 任意组合的加载器。

        loaders：{kind: 版本号} 或 {kind: {"version": 版本号, "file": 本机文件路径}}，空字典就是原版。
        replace=True 时在已有实例上改装：换 / 卸载加载器，存档、配置、Mod 都保留。
        """
        name = name.strip()
        if not name or re.search(r'[\\/:*?"<>|]', name):
            raise ValueError("版本名不能为空，也不能包含 \\ / : * ? \" < > |")
        check_conflicts(loaders)
        exists = os.path.isfile(self.json_path(name))
        if exists and not replace:
            raise FileExistsError(f"已经有一个叫 {name} 的版本了，换个名字吧")
        if loaders and name == game_version:
            raise ValueError(f"装了加载器的版本不能叫 {game_version}（这是原版的名字），换个名字吧")
        old = self.instance_info(name) if exists else None

        if not os.path.isfile(self.json_path(game_version)):
            self._fetch_vanilla_json(game_version, progress, manifest)
        self.complete(game_version, progress)  # Forge 安装器和 OptiFine 都要用原版 jar
        if name == game_version:
            return name

        base = {"inheritsFrom": game_version, "type": "release"}
        for kind in ("forge", "neoforge"):
            if kind in loaders:
                base = self._forge_json(kind, game_version, loaders[kind], progress)
        for kind in ("fabric", "quilt"):
            if kind in loaders:
                base = self._loader_profile(kind, game_version, loaders[kind], progress)

        of_file = None
        if "optifine" in loaders:
            of_jar = self._optifine_jar(game_version, loaders["optifine"], progress)
            if "forge" in loaders:  # 和 Forge 一起用时，OptiFine 就是个普通 Mod
                os.makedirs(self.mods_dir(name), exist_ok=True)
                of_file = os.path.basename(of_jar)
                shutil.copyfile(of_jar, os.path.join(self.mods_dir(name), of_file))
            else:
                self._optifine_on_vanilla(base, game_version, of_jar, loaders["optifine"], progress)
        if old and old.get("optifine_file") and old["optifine_file"] != of_file:
            stale = os.path.join(self.mods_dir(name), old["optifine_file"])
            if os.path.isfile(stale):
                os.remove(stale)

        base["id"] = name
        self._save_json(name, base)
        meta = {"game": game_version, "optifine_file": of_file,
                "loaders": {k: (v["version"] if isinstance(v, dict) else v) for k, v in loaders.items()}}
        with open(self.meta_path(name), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        copied = os.path.join(self.version_dir(name), name + ".jar")  # 启动时会重新复制原版 jar
        if os.path.isfile(copied):
            os.remove(copied)
        self.complete(name, progress)
        return name

    def install_vanilla(self, vid, progress=None, manifest=None):
        return self.install_instance(vid, vid, {}, progress, manifest, replace=True)

    def install_fabric(self, game_version, loader_version, progress=None, manifest=None):
        return self.install_instance(f"{game_version}-Fabric-{loader_version}", game_version,
                                     {"fabric": loader_version}, progress, manifest, replace=True)

    def install_forge(self, kind, game_version, loader_version, progress=None, manifest=None):
        name = f"{game_version}-{LOADER_NAMES[kind]}-{short_version(kind, game_version, loader_version)}"
        return self.install_instance(name, game_version, {kind: loader_version}, progress, manifest, replace=True)

    def _loader_profile(self, kind, game_version, spec, progress):
        """Fabric / Quilt：官方 meta 直接给出完整的版本 json。"""
        if isinstance(spec, dict):
            with open(spec["file"], encoding="utf-8") as f:
                return json.load(f)
        if progress:
            progress(f"获取 {LOADER_NAMES[kind]} 配置", 0, 1)
        meta = FABRIC_META if kind == "fabric" else QUILT_META
        return net.fetch_json(f"{meta}/versions/loader/{game_version}/{spec}/profile/json")

    def _forge_json(self, kind, game_version, spec, progress):
        """运行 Forge / NeoForge 官方安装器（下载的或本机的），返回生成的版本 json。"""
        if isinstance(spec, dict):
            installer = spec["file"]
        else:
            if progress:
                progress("下载安装器", 0, 1)
            url = installer_url(kind, game_version, spec)
            installer = os.path.join(self.root, "cache", "installers", url.rsplit("/", 1)[1])
            net.download_file(url, installer)

        with zipfile.ZipFile(installer) as z:
            profile = json.loads(z.read("install_profile.json"))
            if "versionInfo" in profile:  # 1.12.2 及更早：直接带着 versionInfo 和 universal jar
                info, version_json = profile["install"], profile["versionInfo"]
                dest = self._lib(info["path"])
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(z.read(info["filePath"]))
                version_json.setdefault("inheritsFrom", game_version)
                version_json.setdefault("jar", game_version)
                return version_json
            version_json = json.loads(z.read(profile.get("json", "/version.json").lstrip("/")))
            # 安装器里自带的 maven 文件（例如 forge 本体）
            for item in z.infolist():
                if item.filename.startswith("maven/") and not item.is_dir():
                    dest = os.path.join(self.libraries, *item.filename[len("maven/"):].split("/"))
                    if not (os.path.isfile(dest) and os.path.getsize(dest) == item.file_size):
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with z.open(item) as src, open(dest, "wb") as dst:
                            shutil.copyfileobj(src, dst)

        _, _, tasks = self._libraries({"libraries": profile.get("libraries", [])}, dedupe=False)
        net.download_all(tasks, progress, "下载安装器依赖")
        self._run_processors(profile, installer, game_version, progress)
        return version_json

    def _optifine_jar(self, game_version, spec, progress):
        if isinstance(spec, dict):
            return spec["file"]
        typ, patch = split_optifine(spec)
        path = os.path.join(self.root, "cache", "optifine", f"OptiFine_{game_version}_{spec}.jar")
        if progress:
            progress("下载 OptiFine", 0, 1)
        net.download_file(f"{OPTIFINE_API}/{game_version}/{typ}/{patch}", path)
        return path

    def _optifine_on_vanilla(self, base, game_version, of_jar, spec, progress):
        """不带 Forge 时，像官方 OptiFine 安装器那样：用 Patcher 生成补丁后的库，用 launchwrapper 启动。"""
        version = spec["version"] if isinstance(spec, dict) else spec
        lib_name = f"optifine:OptiFine:{game_version}_{version}"
        out = self._lib(lib_name)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with zipfile.ZipFile(of_jar) as z:
            names = set(z.namelist())
            if "launchwrapper-of.txt" in names:
                lw_ver = z.read("launchwrapper-of.txt").decode().strip()
                lw_name = f"optifine:launchwrapper-of:{lw_ver}"
                lw_path = self._lib(lw_name)
                os.makedirs(os.path.dirname(lw_path), exist_ok=True)
                with open(lw_path, "wb") as f:
                    f.write(z.read(f"launchwrapper-of-{lw_ver}.jar"))
            else:
                lw_name = "net.minecraft:launchwrapper:1.12"
        if "optifine/Patcher.class" in names:
            if progress:
                progress("生成 OptiFine 补丁", 0, 1)
            j = java.pick(java.find_javas([os.path.join(self.root, "runtime")]), max(self.required_java(game_version), 8))
            if not j:
                raise RuntimeError("安装 OptiFine 需要 Java，但没有找到")
            client = os.path.join(self.versions, game_version, game_version + ".jar")
            flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
            r = subprocess.run([j.path, "-cp", of_jar, "optifine.Patcher", client, of_jar, out],
                               capture_output=True, creationflags=flags)
            if r.returncode != 0 or not os.path.isfile(out):
                tail = (r.stdout + r.stderr).decode("utf-8", "replace")[-1200:]
                raise RuntimeError(f"OptiFine 补丁生成失败：\n{tail}")
        else:  # 很老的 OptiFine 本身就是完整的库
            shutil.copyfile(of_jar, out)
        base["libraries"] = [{"name": lib_name}, {"name": lw_name}] + base.get("libraries", [])
        base["mainClass"] = "net.minecraft.launchwrapper.Launch"
        parent = self.read_json(game_version)
        if "minecraftArguments" in parent:
            base["minecraftArguments"] = parent["minecraftArguments"] + " --tweakClass optifine.OptiFineTweaker"
        else:
            base.setdefault("arguments", {}).setdefault("game", []).extend(["--tweakClass", "optifine.OptiFineTweaker"])

    def _lib(self, coord):
        return os.path.join(self.libraries, *maven_path(coord).split("/"))

    def _run_processors(self, profile, installer, game_version, progress):
        procs = [p for p in profile.get("processors", []) if "client" in p.get("sides", ["client"])]
        if not procs:
            return
        j = java.pick(java.find_javas([os.path.join(self.root, "runtime")]), max(self.required_java(game_version), 8))
        if not j:
            raise RuntimeError("运行安装器需要 Java，但没有找到")
        tmp = os.path.join(self.root, "cache", "installer_data", os.path.basename(installer))
        client_jar = os.path.join(self.versions, game_version, game_version + ".jar")
        data = {"SIDE": "client", "MINECRAFT_JAR": client_jar, "MINECRAFT_VERSION": game_version,
                "ROOT": self.root, "INSTALLER": installer, "LIBRARY_DIR": self.libraries}
        with zipfile.ZipFile(installer) as z:
            for key, value in profile.get("data", {}).items():
                val = value.get("client", "")
                if val.startswith("[") and val.endswith("]"):
                    data[key] = self._lib(val[1:-1])
                elif val.startswith("'") and val.endswith("'"):
                    data[key] = val[1:-1]
                elif val.startswith("/"):  # 安装器内的文件，例如 /data/client.lzma
                    out = os.path.join(tmp, *val.lstrip("/").split("/"))
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    with open(out, "wb") as f:
                        f.write(z.read(val.lstrip("/")))
                    data[key] = out
                else:
                    data[key] = val

        def sub(a):
            if a.startswith("[") and a.endswith("]"):
                return self._lib(a[1:-1])
            return re.sub(r"\{(\w+)\}", lambda m: data.get(m.group(1), m.group(0)), a)

        flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
        for i, p in enumerate(procs):
            if progress:
                progress("运行安装处理器", i, len(procs))
            outputs = {sub(k): sub(v).strip("'") for k, v in p.get("outputs", {}).items()}
            if outputs and all(os.path.isfile(k) and net.sha1_file(k) == v for k, v in outputs.items()):
                continue  # 之前已经生成过
            jar = self._lib(p["jar"])
            cp = [self._lib(c) for c in p.get("classpath", [])] + [jar]
            cmd = [j.path, "-cp", os.pathsep.join(cp), _main_class(jar)] + [sub(a) for a in p.get("args", [])]
            r = subprocess.run(cmd, cwd=self.root, capture_output=True, creationflags=flags)
            if r.returncode != 0:
                tail = (r.stdout + r.stderr).decode("utf-8", "replace")[-1500:]
                raise RuntimeError(f"安装处理器 {p['jar']} 运行失败（{r.returncode}）：\n{tail}")
            for path, sha in outputs.items():
                if os.path.isfile(path) and net.sha1_file(path) != sha:
                    raise RuntimeError(f"安装处理器输出校验失败：{os.path.basename(path)}")
        if progress:
            progress("运行安装处理器", len(procs), len(procs))
        shutil.rmtree(tmp, ignore_errors=True)

    def complete(self, vid, progress=None):
        """补全某个版本运行所需的全部文件（已存在的会跳过）。"""
        v = self.resolve(vid)
        tasks = []
        client = v.get("downloads", {}).get("client")
        if client:
            jar = v["_jar"]
            tasks.append(net.Task(client["url"], os.path.join(self.versions, jar, jar + ".jar"),
                                  client.get("sha1"), client.get("size")))
        _, _, lib_tasks = self._libraries(v)
        tasks += lib_tasks
        log = v.get("logging", {}).get("client")
        if log:
            f = log["file"]
            tasks.append(net.Task(f["url"], os.path.join(self.assets, "log_configs", f["id"]),
                                  f.get("sha1"), f.get("size")))
        if progress:
            progress("获取资源索引", 0, 1)
        tasks += self._asset_tasks(v)
        net.download_all(tasks, progress, "下载游戏文件")

    def _libraries(self, v, dedupe=True):
        """返回 (classpath 列表, [(natives 包路径, 排除列表)], 下载任务)。
        dedupe：同一 group:artifact 只保留第一个（子版本覆盖父版本）。"""
        seen = set()
        classpath, natives, tasks = [], [], []
        for lib in v.get("libraries", []):
            if not rule_allows(lib.get("rules")):
                continue
            name = lib.get("name", "")
            parts = name.split(":")
            has_native = "natives" in lib
            key = ":".join(parts[:2] + parts[3:])
            if dedupe and key in seen and not has_native:
                continue
            seen.add(key)
            downloads = lib.get("downloads", {})
            art = downloads.get("artifact")
            if art is not None:
                path = os.path.join(self.libraries, art.get("path") or maven_path(name))
                if art.get("url"):
                    tasks.append(net.Task(art["url"], path, art.get("sha1"), art.get("size")))
                classpath.append(path)
            elif "downloads" not in lib and not has_native:
                # Fabric 这类只给 maven 坐标和仓库地址的库
                rel = maven_path(name)
                base = lib.get("url") or "https://libraries.minecraft.net/"
                if not base.endswith("/"):
                    base += "/"
                base = base.replace("http://files.minecraftforge.net/maven/", FORGE_MAVEN + "/")
                path = os.path.join(self.libraries, rel)
                tasks.append(net.Task(base + rel, path, lib.get("sha1"), lib.get("size")))
                classpath.append(path)
            if has_native:
                cls = lib["natives"].get(OS_NAME)
                if cls:
                    cls = cls.replace("${arch}", "64" if IS_64 else "32")
                    c = downloads.get("classifiers", {}).get(cls)
                    if c:
                        path = os.path.join(self.libraries, c["path"])
                        tasks.append(net.Task(c["url"], path, c.get("sha1"), c.get("size")))
                        natives.append((path, lib.get("extract", {}).get("exclude", [])))
        return classpath, natives, tasks

    def _asset_index(self, v):
        ai = v.get("assetIndex")
        if not ai:
            return None, None
        path = os.path.join(self.assets, "indexes", ai["id"] + ".json")
        net.download_file(ai["url"], path, ai.get("sha1"))
        with open(path, encoding="utf-8") as f:
            return ai["id"], json.load(f)

    def _asset_tasks(self, v):
        _, index = self._asset_index(v)
        if not index:
            return []
        tasks, seen = [], set()
        for obj in index["objects"].values():
            h = obj["hash"]
            if h in seen:
                continue
            seen.add(h)
            tasks.append(net.Task(f"{ASSET_URL}/{h[:2]}/{h}", os.path.join(self.assets, "objects", h[:2], h),
                                  h, obj.get("size")))
        return tasks

    def _prepare_assets(self, v, game_dir):
        """老版本需要把资源按原名复制出来（virtual / map_to_resources）。"""
        index_id, index = self._asset_index(v)
        if not index:
            return self.assets
        if index.get("map_to_resources"):
            target = os.path.join(game_dir, "resources")
        elif index.get("virtual"):
            target = os.path.join(self.assets, "virtual", index_id)
        else:
            return self.assets
        for name, obj in index["objects"].items():
            dest = os.path.join(target, *name.split("/"))
            if os.path.isfile(dest):
                continue
            src = os.path.join(self.assets, "objects", obj["hash"][:2], obj["hash"])
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(src, dest)
        return target

    @staticmethod
    def _extract_natives(natives, target):
        os.makedirs(target, exist_ok=True)
        for path, exclude in natives:
            if not os.path.isfile(path):
                continue
            with zipfile.ZipFile(path) as z:
                for item in z.infolist():
                    n = item.filename
                    if item.is_dir() or n.startswith("META-INF") or any(n.startswith(e) for e in exclude):
                        continue
                    dest = os.path.join(target, *n.split("/"))
                    if os.path.isfile(dest) and os.path.getsize(dest) == item.file_size:
                        continue
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with z.open(item) as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)

    # ---- 启动
    def build_launch_command(self, vid, java_path, auth, memory_mb=4096,
                             width=854, height=480, extra_jvm=""):
        """auth: auth.prepare() 的返回值，或者直接传一个离线用户名字符串。"""
        if isinstance(auth, str):
            auth = {"name": auth, "uuid": offline_uuid(auth), "token": "0", "user_type": "legacy", "jvm": []}
        v = self.resolve(vid)
        game_dir = self.version_dir(vid)
        natives_dir = os.path.join(game_dir, "natives")
        classpath, natives, _ = self._libraries(v)
        jar = v["_jar"]
        client_jar = os.path.join(self.versions, jar, jar + ".jar")
        own = self.read_json(vid)
        if own.get("inheritsFrom") and not own.get("jar"):
            # 和官方启动器一样把原版 jar 复制成 <版本名>.jar，
            # Forge/NeoForge 的 -DignoreList 按这个文件名把它排除出模块路径
            copy = os.path.join(game_dir, vid + ".jar")
            if not net.is_valid(copy, size=os.path.getsize(client_jar)):
                shutil.copyfile(client_jar, copy)
            client_jar = copy
        classpath.append(client_jar)
        self._extract_natives(natives, natives_dir)
        game_assets = self._prepare_assets(v, game_dir)

        variables = {
            "auth_player_name": auth["name"],
            "version_name": vid,
            "game_directory": game_dir,
            "assets_root": self.assets,
            "game_assets": game_assets,
            "assets_index_name": v.get("assetIndex", {}).get("id", v.get("assets", "legacy")),
            "auth_uuid": auth["uuid"],
            "auth_access_token": auth["token"],
            "auth_session": auth["token"],
            "clientid": "",
            "auth_xuid": "",
            "user_type": auth["user_type"],
            "user_properties": "{}",
            "version_type": LAUNCHER_NAME,
            "natives_directory": natives_dir,
            "launcher_name": LAUNCHER_NAME,
            "launcher_version": __version__,
            "classpath": os.pathsep.join(classpath),
            "classpath_separator": os.pathsep,
            "library_directory": self.libraries,
            "resolution_width": str(width),
            "resolution_height": str(height),
        }

        def sub(s):
            return re.sub(r"\$\{(\w+)\}", lambda m: variables.get(m.group(1), m.group(0)), s)

        features = {"has_custom_resolution": True}

        def collect(items):
            out = []
            for a in items:
                if isinstance(a, str):
                    out.append(a)
                elif rule_allows(a.get("rules"), features):
                    val = a.get("value")
                    out += val if isinstance(val, list) else [val]
            return out

        args = v.get("arguments", {})
        jvm = collect(args.get("jvm", []))
        if not any("${classpath}" in a for a in jvm):  # 老版本 json 里没有 jvm 参数
            jvm = ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"] + jvm
        log = v.get("logging", {}).get("client")
        if log:
            log_path = os.path.join(self.assets, "log_configs", log["file"]["id"])
            jvm.append(log["argument"].replace("${path}", log_path))

        game = collect(args.get("game", []))
        if "minecraftArguments" in v:
            game = v["minecraftArguments"].split() + game
            game += ["--width", str(width), "--height", str(height)]

        cmd = [java_path,
               f"-Xmx{memory_mb}m",
               "-XX:+UseG1GC",
               "-XX:-OmitStackTraceInFastThrow",
               "-Dfile.encoding=UTF-8",
               "-Dstdout.encoding=UTF-8",
               "-Dstderr.encoding=UTF-8"]
        cmd += auth.get("jvm", [])
        cmd += extra_jvm.split()
        cmd += [sub(a) for a in jvm]
        cmd.append(v["mainClass"])
        cmd += [sub(a) for a in game]
        os.makedirs(game_dir, exist_ok=True)
        return cmd, game_dir
