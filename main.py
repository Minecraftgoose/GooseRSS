# -*- coding: utf-8 -*-
"""GooseRSS 启动脚本。

界面用 pywebview（系统浏览器内核渲染），不再用 Tkinter——
后者在几千条文章时重建列表会明显卡顿。

用法：
    python main.py                 # 正常启动
    python main.py --data-dir DIR  # 指定数据目录（便于多份订阅分开存放）
    python main.py --hidden        # 只驻留托盘，不显示窗口

依赖安装：
    pip install -r requirements.txt

注意：src/ 是一个 Python 包，必须按包的方式导入（from src.app import ...）。
如果把 src/ 直接塞进 sys.path 再写 `from app import ...`，包内的
`from . import config` 会因为失去包上下文而报
"ImportError: attempted relative import with no known parent package"。
"""

import os
import sys

# 项目根目录（main.py 所在目录）放进 sys.path，保证能按包名导入 src
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _check_dependencies():
    """核心依赖缺了就没法跑；可选依赖缺了只是少个功能。"""
    missing = []
    for module, package in (("feedparser", "feedparser"),
                            ("requests", "requests"),
                            ("webview", "pywebview")):
        try:
            __import__(module)
        except Exception:
            missing.append(package)
    if missing:
        print("缺少依赖：%s" % "、".join(missing))
        print("请先执行：pip install -r requirements.txt")
        return False
    return True


def _check_webview_version():
    """pywebview 3.5 起才真正兼容 pystray（修好了内部图标处理）。

    版本过低时托盘会被 pywebview 的窗口初始化干扰，
    这里提前提醒，省得用户以为是程序坏了。
    """
    try:
        import webview
    except Exception:
        return
    raw = str(getattr(webview, "__version__", "") or "")
    try:
        parts = [int(x) for x in raw.split(".")[:2] if x.isdigit()]
    except Exception:
        return
    if len(parts) >= 2 and tuple(parts[:2]) < (3, 5):
        print("提示：pywebview %s 过低（需 >=3.5），托盘可能不稳定。"
              % raw)
        print("      升级：pip install -U pywebview")


def _check_tray_deps():
    """托盘是通知器的主入口，缺了它关掉窗口就再也唤不回来。

    这里只在 Windows 上做一次温和提醒，不阻断启动——
    没有托盘时程序仍能当普通窗口用，只是不能缩到后台。
    """
    import platform
    if platform.system() != "Windows":
        return
    missing = []
    try:
        import pystray  # noqa: F401
    except Exception:
        missing.append("pystray")
    try:
        import PIL  # noqa: F401
    except Exception:
        missing.append("Pillow")
    if not missing:
        return
    print("提示：托盘依赖缺失（%s），托盘不会出现。" % "、".join(missing))
    print("      安装：pip install pystray Pillow")
    print("      诊断：python test/check_tray.py")


def _check_pythonnet():
    """Windows 上 pywebview 靠 pythonnet 起 WinForms。

    缺了它窗口建不起来或 JS 注入失败，报错信息很难懂，
    这里提前给一句人话提示。
    """
    import platform
    if not platform.system() == "Windows":
        return
    try:
        import clr  # noqa: F401
        return
    except Exception:
        pass
    print("提示：未检测到 pythonnet（pywebview 在 Windows 上的必需依赖）。")
    print("      pip install pythonnet")


def _quiet_pywebview_noise():
    """过滤 pywebview 在老系统上反复刷的 DPI 警告。

    GetDpiForWindow 是 Windows 10 1607 才引入的 API，Win7 和老版 Win10
    上不存在，pywebview 每次窗口操作都会警告一次，很吵但无害。
    只过滤这一条，其它警告照常显示。
    """
    import logging

    class _SkipDpi(object):
        def filter(self, record):
            try:
                return "DPI" not in record.getMessage()
            except Exception:
                return True

    logging.getLogger("pywebview").addFilter(_SkipDpi())


def _warn_optional():
    tips = []
    try:
        import webview
        import platform
        if sys.platform.startswith("win"):
            try:
                gui = webview.gui if hasattr(webview, "gui") else ""
            except Exception:
                gui = ""
            print("渲染内核：%s" % (gui or "自动选择"))
    except Exception:
        pass
    try:
        __import__("pystray")
    except Exception:
        tips.append("pystray（系统托盘；不装则只能靠窗口，关窗即退出）")
    try:
        __import__("PIL")
    except Exception:
        tips.append("Pillow（托盘图标与角标；不装则无托盘图标）")
    if tips:
        print("可选依赖未安装（功能会降级，不影响启动）：")
        for item in tips:
            print("  · %s" % item)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    data_dir = None
    hidden = False
    debug = False
    gui = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--data-dir" and index + 1 < len(argv):
            data_dir = argv[index + 1]
            index += 2
            continue
        if arg == "--hidden":
            hidden = True
        elif arg == "--debug":
            debug = True
        elif arg == "--gui" and index + 1 < len(argv):
            gui = argv[index + 1]
            index += 2
            continue
        index += 1

    if not _check_dependencies():
        return 1

    _quiet_pywebview_noise()
    _check_pythonnet()
    _check_webview_version()
    _check_tray_deps()

    from src import config
    if config.is_windows_7():
        print("检测到 Windows 7：将使用 IE11 内核渲染界面，"
              "系统缩放可能不跟随（可在系统显示设置里调整）。")
    if data_dir:
        config.set_data_dir(data_dir)

    from src.controller import Controller
    from src.webview_app import run

    _warn_optional()

    controller = Controller()

    # 设置里要求"启动即隐藏"，或命令行显式指定
    if not hidden and controller.settings.get("start_hidden", False):
        hidden = True

    # 托盘已经起来了但窗口要隐藏，说明用户想要后台常驻
    if hidden and controller.tray is None:
        print("提示：未启用托盘，隐藏启动将无法再唤出窗口，改为正常显示。")
        hidden = False

    print("%s 启动中…（数据目录：%s）" % (config.APP_NAME, config.data_dir()))
    run(controller, hidden=hidden, debug=debug, gui=gui)
    return 0


if __name__ == "__main__":
    sys.exit(main())
