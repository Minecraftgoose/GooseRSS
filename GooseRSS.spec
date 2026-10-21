# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

# 界面资源目录：web/ 是 pywebview 加载的前端（index.html / app.css / app.js /
# icons/ / vendor/），assets/ 是窗口与托盘图标，licenses/ 是第三方许可证。
# 这三个目录必须整目录随包，缺任何一个都会白屏或图标全丢。
# 注意：web/ 目录定位在 src/server.py 的 _find_web_dir() 里做了 _MEIPASS 适配，
# 否则打包后按 __file__ 往上两级会指到 _MEIPASS 内部而找不到界面。
runtime_assets = [
    (str(dir_path), dir_path.name)
    for dir_path in [Path("web"), Path("assets"), Path("licenses")]
    if dir_path.is_dir()
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=runtime_assets,
    # pywebview 按平台动态选择后端（WinForms / EdgeChromium / MSHTML），
    # pystray 按 OS 动态选择后端（_win32），PyInstaller 静态分析发现不了，
    # 缺了会导致"界面出来了、后端没开"或托盘不显示。
    # feedparser 子模块与 PIL 的 ImageDraw（画托盘未读角标）同理。
    hiddenimports=[
        "webview",
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "webview.platforms.mshtml",
        "pystray",
        "pystray._win32",
        "feedparser",
        "feedparser.api",
        "feedparser.datetimes",
        "feedparser.encodings",
        "feedparser.sanitizer",
        "feedparser.html",
        "PIL.ImageDraw",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# UPX 排除项：这些模块带 TLS/ASLR/自校验特性，压缩后在 Windows 7 上可能加载失败或触发杀软误报
# UPX 未安装时 upx=True 会被 PyInstaller 静默跳过，不会导致构建失败
upx_exclude = [
    "python38.dll",       # 解释器主 DLL
    "python3.dll",
    "_ssl.pyd",           # OpenSSL，压缩后握手偶发异常
    "_hashlib.pyd",
    "pythoncom38.dll",    # pywin32 COM，含 TLS，压缩后易崩
    "pywintypes38.dll",   # pywin32 基础类型
    "Python.Runtime.dll", # pythonnet，pywebview 的 WinForms 后端依赖
    "clr.pyd",
    "vcruntime140.dll",
    "msvcp140.dll",
]

# 文件夹（onedir）模式：exe 只携带 pyz + 脚本，依赖与数据由 COLLECT 收集到同名目录下
# 产物固定为 dist/GooseRSS/（内含 exe 与 _internal/），供 NSIS 整体打包
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GooseRSS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=upx_exclude,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    icon=['assets/app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=upx_exclude,
    name='GooseRSS',
)
