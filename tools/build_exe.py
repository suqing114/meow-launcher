"""打包成单文件 exe：先 pip install pyinstaller，再运行 python tools/build_exe.py，结果在 dist/ 里。

- 图标用 theme.cat_logo 现画，直接写成多尺寸的 .ico（ICO 里可以内嵌 PNG）
- 不打包 mcl/_private.py（内置 Key 之类的私密配置）：exe 公开发布，很容易被解包；
  需要时加上 --with-private
"""
import os
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mcl import __version__, theme  # noqa: E402

BUILD = os.path.join(ROOT, "build")


def make_ico(path, sizes=(16, 24, 32, 48, 64, 128, 256)):
    pngs = [theme.cat_logo(n).png() for n in sizes]
    offset = 6 + 16 * len(sizes)
    entries = b""
    for n, data in zip(sizes, pngs):
        # 宽、高（256 记作 0）、调色板数、保留、色彩平面、位深、数据长度、数据偏移
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(sizes)) + entries + b"".join(pngs))


def main():
    os.makedirs(BUILD, exist_ok=True)
    ico = os.path.join(BUILD, "icon.ico")
    make_ico(ico)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
           "--name", "MeowLauncher", "--icon", ico,
           "--distpath", os.path.join(ROOT, "dist"), "--workpath", os.path.join(BUILD, "work"),
           "--specpath", BUILD, os.path.join(ROOT, "main.py")]
    if "--with-private" not in sys.argv:
        cmd[3:3] = ["--exclude-module", "mcl._private"]
    print(f"喵喵启动器 v{__version__}：", " ".join(cmd[3:]))
    subprocess.run(cmd, check=True, cwd=ROOT)
    exe = os.path.join(ROOT, "dist", "MeowLauncher.exe")
    print(f"完成：{exe}（{os.path.getsize(exe) / 1e6:.1f} MB）")


if __name__ == "__main__":
    main()
