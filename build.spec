# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把整个程序打成单文件 exe。

打包命令（在项目根目录执行）：
    E:/ANACONDA/python.exe -m PyInstaller build.spec --noconfirm --clean

产物：dist/中东要闻推送.exe（单文件，双击即用，无需装 Python）。

要点：
· onefile + windowed：单文件、双击不弹黑窗。
· 把 app 包整个收进去（collect_submodules），避免漏掉动态导入的模块。
· 显式声明 ssl / _ssl / tkinter / queue 等，防止被裁掉。
· 排除 numpy/pandas/matplotlib 等用不到的大包，减小体积。
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.getcwd())
ICON = os.path.join(ROOT, "assets", "app.ico")

hidden = []
# 整个 app 包（core/sources/compose/push + runner/scheduler/gui）
hidden += collect_submodules("app")
# 可能被动态用到的标准库
hidden += [
    "tkinter", "tkinter.ttk", "tkinter.messagebox", "tkinter.font",
    "ssl", "_ssl", "_socket", "socket", "select", "struct", "base64",
    "queue", "json", "gzip", "zlib", "html", "hashlib", "hmac",
    "urllib.request", "urllib.parse", "urllib.error",
    "http.client", "http.cookiejar", "email", "email.parser",
    "encodings.idna", "encodings.utf_8", "encodings.gbk", "encodings.gb18030",
    "winreg", "ctypes", "ctypes.wintypes",
]

# 明确剔除：本程序完全用不到，去掉能显著瘦身
excludes = [
    "numpy", "pandas", "matplotlib", "scipy", "PIL", "PyQt5", "PySide2",
    "PySide6", "IPython", "jupyter", "notebook", "pytest", "setuptools",
    "pip", "wheel", "distutils", "test", "unittest", "sqlite3",
    "curses", "asyncio", "multiprocessing", "concurrent",
]

a = Analysis(
    ["main.py"],
    pathex=[ROOT],
    binaries=[],
    # 把图标也打进去，运行时用 resource_path('assets','app.ico') 取，
    # 用于给窗口标题栏设置图标。
    datas=[(ICON, "assets")] if os.path.exists(ICON) else [],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="中东要闻推送",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                     # 不用 UPX：杀软误报率高，且对 py 包收益有限
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                 # 双击不弹黑窗
    disable_windowed_traceback=True,   # 不用 PyInstaller 的裸 traceback 弹窗，
                                       # 改由 main.py 记日志 + 弹友好提示
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON if os.path.exists(ICON) else None,
)
