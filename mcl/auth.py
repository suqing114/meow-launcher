"""账户：离线、微软正版（设备码登录）、外置登录（authlib-injector / Yggdrasil）。"""
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import uuid as uuidlib

from . import net
from .game import offline_uuid

MS_DEVICE = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
MS_TOKEN = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
MS_SCOPE = "XboxLive.signin offline_access"
XBL_AUTH = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE = "https://api.minecraftservices.com/minecraft/profile"
AUTHLIB_LATEST = "https://authlib-injector.yushi.moe/artifact/latest.json"

# 喵喵启动器自己的 Azure 应用（Client ID 是公开信息，可以随程序一起分发）
DEFAULT_CLIENT_ID = "535f8dc7-96f8-4c5b-814c-2a52bb913669"

# 申请 / 注册地址（设置页里也有按钮）
AZURE_REGISTER_URL = "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"
MOJANG_REVIEW_URL = "https://aka.ms/mce-reviewappid"

XERR = {
    2148916233: "这个微软账号还没有 Xbox 档案，请先在 xbox.com 登录一次创建档案",
    2148916235: "你所在的国家/地区无法使用 Xbox Live",
    2148916236: "该账号需要完成成人验证（韩国）",
    2148916237: "该账号需要完成成人验证（韩国）",
    2148916238: "未成年账号需要由家长加入 Microsoft 家庭组后才能登录",
}

TYPE_NAMES = {"offline": "离线", "microsoft": "微软正版", "authlib": "外置"}


class AuthError(Exception):
    pass


def _request(url, data=None, form=None, token=None):
    """返回 (状态码, json 字典, 响应头)；HTTP 错误不抛异常，方便读错误信息。"""
    import urllib.request  # 第一次联网时才导入，见 net.py
    headers ={"User-Agent": net.USER_AGENT, "Accept": "application/json"}
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=net.TIMEOUT) as r:
            status, raw, h = r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        status, raw, h = e.code, e.read(), e.headers
    except OSError as e:
        raise AuthError(f"网络错误：{e}")
    try:
        j = json.loads(raw) if raw.strip() else {}
    except ValueError:
        j = {"raw": raw.decode("utf-8", "replace")[:300]}
    return status, j, h


# ---------------------------------------------------------------- 离线

def offline_account(name):
    return {"type": "offline", "name": name, "uuid": offline_uuid(name)}


# ---------------------------------------------------------------- 微软

def ms_start(client_id):
    """第一步：申请设备码。返回里有 user_code / verification_uri。"""
    if not client_id:
        raise AuthError("还没有填写微软登录的 Client ID，请到「设置」里填写")
    st, j, _ = _request(MS_DEVICE, form={"client_id": client_id, "scope": MS_SCOPE})
    if st != 200:
        raise AuthError(f"申请登录代码失败：{j.get('error_description') or j}")
    return j


def ms_finish(client_id, device, cancelled=lambda: False):
    """第二步：等用户在浏览器里完成授权，然后一路换到 Minecraft 令牌。"""
    interval = device.get("interval", 5)
    deadline = time.time() + device.get("expires_in", 900)
    while time.time() < deadline:
        wake = time.time() + interval
        while time.time() < wake:
            if cancelled():
                raise AuthError("已取消登录")
            time.sleep(0.2)
        st, j, _ = _request(MS_TOKEN, form={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id, "device_code": device["device_code"]})
        if st == 200:
            return _ms_to_minecraft(j)
        err = j.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        if err == "authorization_declined":
            raise AuthError("你在网页上拒绝了授权")
        raise AuthError(f"微软登录失败：{j.get('error_description') or err or st}")
    raise AuthError("登录代码已过期，请重新开始")


def _ms_to_minecraft(ms):
    st, j, _ = _request(XBL_AUTH, data={
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com",
                       "RpsTicket": "d=" + ms["access_token"]},
        "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"})
    if st != 200:
        raise AuthError(f"Xbox Live 认证失败（{st}）")
    xbl, uhs = j["Token"], j["DisplayClaims"]["xui"][0]["uhs"]

    st, j, _ = _request(XSTS_AUTH, data={
        "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl]},
        "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"})
    if st != 200:
        raise AuthError(XERR.get(j.get("XErr"), f"XSTS 认证失败（{st}）：{j}"))
    xsts = j["Token"]

    st, j, _ = _request(MC_LOGIN, data={"identityToken": f"XBL3.0 x={uhs};{xsts}"})
    if st == 403:
        raise AuthError("Minecraft 拒绝了这个 Client ID：应用还没有通过 Mojang 审核。\n"
                        f"申请地址：{MOJANG_REVIEW_URL}")
    if st != 200:
        raise AuthError(f"登录 Minecraft 失败（{st}）：{j}")
    mc_token, expires = j["access_token"], j.get("expires_in", 86400)

    st, p, _ = _request(MC_PROFILE, token=mc_token)
    if st == 404:
        raise AuthError("这个微软账号没有购买 Minecraft Java 版（或还没设置游戏名）")
    if st != 200:
        raise AuthError(f"获取正版档案失败（{st}）")
    return {"type": "microsoft", "name": p["name"], "uuid": p["id"],
            "access_token": mc_token, "expires_at": time.time() + expires,
            "refresh_token": ms.get("refresh_token")}


def ms_refresh(acc, client_id):
    if not acc.get("refresh_token"):
        raise AuthError("微软登录已失效，请删除该账户后重新登录")
    st, j, _ = _request(MS_TOKEN, form={"grant_type": "refresh_token", "client_id": client_id,
                                        "refresh_token": acc["refresh_token"], "scope": MS_SCOPE})
    if st != 200:
        raise AuthError("微软登录已失效，请删除该账户后重新登录")
    acc.update(_ms_to_minecraft(j))


# ---------------------------------------------------------------- 外置登录

def yggdrasil_resolve(address):
    """把用户输入（如 littleskin.cn）解析成 API 根地址，并取回元数据。"""
    url = address.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    st, j, h = _request(url)
    loc = h.get("X-Authlib-Injector-API-Location") if h else None
    if loc:  # API 地址指示（ALI）
        url = urllib.parse.urljoin(url, loc)
        st, j, h = _request(url)
    if st != 200 or not ("meta" in j or "skinDomains" in j):
        raise AuthError("这不是有效的外置登录服务器地址")
    if not url.endswith("/"):
        url += "/"
    return url, j


def yggdrasil_login(address, username, password):
    """返回 (账户字典, 可用角色列表)。账户里还没有选定角色时 name 为空。"""
    root, meta = yggdrasil_resolve(address)
    client_token = uuidlib.uuid4().hex
    st, j, _ = _request(root + "authserver/authenticate", data={
        "agent": {"name": "Minecraft", "version": 1}, "username": username,
        "password": password, "clientToken": client_token, "requestUser": False})
    if st != 200:
        raise AuthError(j.get("errorMessage") or f"登录失败（{st}）")
    acc = {"type": "authlib", "server": root,
           "server_name": meta.get("meta", {}).get("serverName") or root,
           "client_token": j.get("clientToken", client_token),
           "access_token": j["accessToken"], "login": username, "meta": meta,
           "name": "", "uuid": ""}
    profiles = j.get("availableProfiles") or []
    if not profiles:
        raise AuthError("这个账号下还没有角色，请先到皮肤站网页上创建一个")
    sel = j.get("selectedProfile")
    if sel:
        acc["name"], acc["uuid"] = sel["name"], sel["id"]
    return acc, profiles


def yggdrasil_select(acc, profile):
    """多角色账号：用 refresh 接口绑定选中的角色。"""
    st, j, _ = _request(acc["server"] + "authserver/refresh", data={
        "accessToken": acc["access_token"], "clientToken": acc["client_token"],
        "selectedProfile": {"id": profile["id"], "name": profile["name"]}})
    if st != 200:
        raise AuthError(j.get("errorMessage") or f"选择角色失败（{st}）")
    sp = j.get("selectedProfile") or profile
    acc.update(access_token=j["accessToken"], name=sp["name"], uuid=sp["id"])


def yggdrasil_ensure(acc):
    st, _, _ = _request(acc["server"] + "authserver/validate", data={
        "accessToken": acc["access_token"], "clientToken": acc["client_token"]})
    if st == 204:
        return
    st, j, _ = _request(acc["server"] + "authserver/refresh", data={
        "accessToken": acc["access_token"], "clientToken": acc["client_token"]})
    if st != 200:
        raise AuthError("外置登录已失效，请删除该账户后重新添加")
    acc["access_token"] = j["accessToken"]


def authlib_injector_jar(game_root):
    path = os.path.join(game_root, "authlib-injector.jar")
    if not os.path.isfile(path):
        info = net.fetch_json(AUTHLIB_LATEST)
        net.download_file(info["download_url"], path)
    return path


# ---------------------------------------------------------------- 启动前准备

def prepare(acc, game_root, client_id=""):
    """检查 / 刷新令牌，返回启动需要的信息：name, uuid, token, user_type, jvm。"""
    t = acc["type"]
    if t == "offline":
        return {"name": acc["name"], "uuid": acc["uuid"], "token": "0", "user_type": "legacy", "jvm": []}
    if t == "microsoft":
        if time.time() > acc.get("expires_at", 0) - 300:
            ms_refresh(acc, client_id)
        return {"name": acc["name"], "uuid": acc["uuid"], "token": acc["access_token"],
                "user_type": "msa", "jvm": []}
    if t == "authlib":
        yggdrasil_ensure(acc)
        jar = authlib_injector_jar(game_root)
        prefetched = base64.b64encode(json.dumps(acc["meta"]).encode("utf-8")).decode("ascii")
        return {"name": acc["name"], "uuid": acc["uuid"], "token": acc["access_token"],
                "user_type": "mojang",
                "jvm": [f"-javaagent:{jar}={acc['server']}",
                        f"-Dauthlibinjector.yggdrasil.prefetched={prefetched}"]}
    raise AuthError(f"未知账户类型：{t}")


# ---------------------------------------------------------------- 账户存储

def account_id(acc):
    return f"{acc['type']}:{acc['uuid']}:{acc.get('server', '')}"


def describe(acc):
    kind = TYPE_NAMES.get(acc["type"], acc["type"])
    if acc["type"] == "authlib":
        kind += f" · {acc.get('server_name')}"
    return f"{acc['name']}（{kind}）"


class Accounts:
    """accounts.json：账户列表（里面有登录令牌，别发给别人）。"""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            with open(path, encoding="utf-8") as f:
                self.items = json.load(f)
        except (OSError, ValueError):
            self.items = []

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.items, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    def add(self, acc):
        acc["id"] = account_id(acc)
        self.items = [a for a in self.items if a.get("id") != acc["id"]] + [acc]
        self.save()
        return acc["id"]

    def remove(self, aid):
        self.items = [a for a in self.items if a.get("id") != aid]
        self.save()

    def get(self, aid):
        return next((a for a in self.items if a.get("id") == aid), None)
