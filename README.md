# 喵喵启动器 MeowLauncher =^･ω･^=

一个类似 HMCL 的 Minecraft 启动器，纯 Python 编写，**不需要安装任何第三方库**（只用标准库 + tkinter）。

## 运行

需要 Python 3.9+ 和 Java（游戏需要的版本：1.20.5+ 要 Java 21，1.17–1.20.4 要 Java 17，更老的要 Java 8）。

```bash
python main.py
```

Windows 下也可以直接双击 `启动.bat`。

## 功能

| 页面 | 功能 |
| --- | --- |
| 启动游戏 | 多账户（离线 / 微软正版 / 外置登录）、选择版本、内存、自动/手动选择 Java、实时游戏日志 |
| 下载游戏 | 列出全部版本（正式版 / 快照 / 远古版，可搜索），一键安装原版、**Fabric**、**Forge**、**NeoForge** |
| 下载 Mod | 通过 Modrinth 搜索 Mod，按游戏版本 / 加载器 / 排序筛选，选择任意版本下载，**自动下载必需前置** |
| Mod 管理 | 查看某个版本的 Mod，启用 / 禁用 / 删除 |
| 设置 | 游戏目录、下载源（BMCLAPI 镜像 / 官方）、下载线程数、窗口大小、额外 JVM 参数 |

- 下载源默认使用 BMCLAPI 镜像，镜像失败会自动回落到官方源；所有文件做 sha1 校验，多线程下载。
- 每个版本的游戏目录是 `.minecraft/versions/<版本名>`（版本隔离），存档、Mod、配置互不干扰。
- 启动前会自动补全缺失的游戏文件。
- Forge / NeoForge 使用官方安装器：解出安装器里的文件、下载依赖、运行安装处理器（需要本机有 Java）。
  1.12.2 及更早的 Forge 也支持安装，但运行需要 **Java 8**（LaunchWrapper 在新 Java 上跑不起来）。
- 游戏日志会从 log4j 的 XML 格式还原成普通文本显示。

## 账户

- **离线**：随便起个名字。
- **外置登录**：支持 LittleSkin 等兼容 authlib-injector 的皮肤站，填服务器地址和账号密码即可（密码不保存）。启动时会自动下载 authlib-injector。
- **微软正版**：启动器已内置 Client ID，玩家直接点「添加账户 → 微软正版」按提示登录即可。

> 给想换成自己 Client ID 的开发者（在「设置」页填写即可覆盖内置的）：
>
>  1. 到 [Azure 应用注册](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade) 新建应用，账户类型选「仅个人 Microsoft 帐户」，重定向 URI 不用填；
>  2. 在应用的「身份验证」里把「允许公共客户端流」设为「是」；
>  3. 复制「应用程序(客户端) ID」，填到启动器「设置」页；
>  4. 到 [Mojang 审核申请](https://aka.ms/mce-reviewappid) 提交这个 ID，审核通过前 Minecraft 会返回 403。

账户保存在 `accounts.json`，里面有登录令牌，不要分享给别人。把启动器打包发给朋友时，
不要带上 `accounts.json`、`config.json` 和 `.minecraft/`（`.gitignore` 已经排除了它们）。

## 目录结构

```
main.py          入口
mcl/net.py       下载、镜像、重试、校验
mcl/game.py      版本清单、安装原版/Fabric、库与资源文件、拼启动参数
mcl/java.py      扫描本机 Java 并按需求选择
mcl/auth.py      账户：离线、微软设备码登录、authlib-injector 外置登录
mcl/mods.py      Modrinth 搜索、版本、前置依赖解析与下载
mcl/gui.py       tkinter 界面
```

## 暂不支持

- CurseForge：API 需要申请 key
- 自动下载 Java
