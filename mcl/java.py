"""查找本机已安装的 Java，并按游戏需求挑一个。"""
import glob
import os
import re
import shutil
import subprocess
import sys

IS_WINDOWS = sys.platform.startswith("win")
JAVA_EXE = "java.exe" if IS_WINDOWS else "java"


class JavaInfo:
    def __init__(self, path, version):
        self.path = path
        self.version = version
        self.major = parse_major(version)

    def __repr__(self):
        return f"Java {self.major} ({self.path})"


def parse_major(version):
    m = re.match(r"(\d+)(?:\.(\d+))?", version or "")
    if not m:
        return 0
    major = int(m.group(1))
    if major == 1 and m.group(2):  # 1.8.0_xxx -> 8
        major = int(m.group(2))
    return major


def _version_of(exe):
    home = os.path.dirname(os.path.dirname(exe))
    release = os.path.join(home, "release")
    if os.path.isfile(release):
        try:
            with open(release, encoding="utf-8", errors="replace") as f:
                m = re.search(r'^JAVA_VERSION="([^"]+)"', f.read(), re.M)
            if m:
                return m.group(1)
        except OSError:
            pass
    try:
        flags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
        out = subprocess.run([exe, "-version"], capture_output=True, timeout=10, creationflags=flags)
        text = (out.stderr + out.stdout).decode("utf-8", "replace")
        m = re.search(r'version "([^"]+)"', text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def info(exe):
    v = _version_of(exe)
    return JavaInfo(exe, v) if v else None


def _candidates(extra_roots=()):
    found = []
    for root in extra_roots:  # 喵喵启动器自己下载的 Java：<游戏目录>/runtime/<component>/bin/java
        found += glob.glob(os.path.join(root, "*", "bin", JAVA_EXE))
    home = os.environ.get("JAVA_HOME")
    if home:
        found.append(os.path.join(home, "bin", JAVA_EXE))
    w = shutil.which("java")
    if w:
        found.append(w)
    if IS_WINDOWS:
        try:
            out = subprocess.run(["where", "java"], capture_output=True, timeout=5,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            found += [l.strip() for l in out.stdout.decode("mbcs", "replace").splitlines() if l.strip()]
        except Exception:
            pass
        roots = {os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")}
        vendors = ["Java", "Eclipse Adoptium", "Eclipse Foundation", "AdoptOpenJDK", "Zulu", "Microsoft",
                   "BellSoft", "Amazon Corretto", "Semeru", "OpenJDK", "Huawei", "Tencent", "Alibaba"]
        for root in filter(None, roots):
            for vendor in vendors:
                found += glob.glob(os.path.join(root, vendor, "*", "bin", JAVA_EXE))
        appdata = os.environ.get("APPDATA")
        if appdata:  # 官方启动器自带的 Java
            found += glob.glob(os.path.join(appdata, ".minecraft", "runtime", "*", "*", "*", "bin", JAVA_EXE))
    elif sys.platform == "darwin":
        found += glob.glob("/Library/Java/JavaVirtualMachines/*/Contents/Home/bin/java")
    else:
        found += glob.glob("/usr/lib/jvm/*/bin/java")
    return found


def find_javas(extra_roots=()):
    seen = set()
    result = []
    for exe in _candidates(extra_roots):
        if not os.path.isfile(exe):
            continue
        key = os.path.normcase(os.path.realpath(exe))
        if key in seen:
            continue
        seen.add(key)
        j = info(exe)
        if j and j.major:
            result.append(j)
    result.sort(key=lambda j: j.major)
    return result


def pick(javas, required):
    """优先完全匹配主版本，其次最小的更高版本，再不行就最高的那个。"""
    if not javas:
        return None
    exact = [j for j in javas if j.major == required]
    if exact:
        return exact[0]
    higher = [j for j in javas if j.major > required]
    if higher:
        return min(higher, key=lambda j: j.major)
    return max(javas, key=lambda j: j.major)
