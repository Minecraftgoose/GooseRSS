# -*- coding: utf-8 -*-
"""环境自检脚本。

用法：
    python test/check_env.py

会依次检查：Python 版本、依赖库、图标资源、主题可用性、标题栏增强能力，
并给出修复建议。Windows 7 上也能直接运行。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

OK = "  [OK]   "
WARN = "  [WARN] "
FAIL = "  [FAIL] "


def line(status, message, hint=""):
    print(status + message + (("\n          -> " + hint) if hint else ""))
    return status == OK


def main():
    print("=" * 68)
    print("GooseRSS 环境自检")
    print("=" * 68)

    results = []

    # 1. Python 版本
    print("\n[1] 运行环境")
    print("     Python %s @ %s" % (sys.version.split()[0], sys.platform))
    results.append(line(OK if sys.version_info >= (3, 6) else FAIL,
                        "Python 版本 >= 3.6",
                        "Windows 7 建议使用 Python 3.8.10" if
                        sys.version_info < (3, 6) else ""))
    try:
        import tkinter
        results.append(line(OK, "tkinter 可用（Tk %s）" % tkinter.TkVersion))
    except Exception as exc:
        results.append(line(FAIL, "tkinter 不可用：%s" % exc,
                            "Linux: apt install python3-tk；"
                            "Windows: 重装 Python 时勾选 tcl/tk"))

    # 2. 依赖
    print("\n[2] 依赖库")
    packages = [
        ("pywebview", "界面主题（浅色 arc / 深色 equilux）"),
        ("feedparser", "RSS / Atom 解析"),
        ("requests", "网络请求"),
        ("PIL", "Pillow，图标缩放与托盘角标"),
        ("pystray", "系统托盘常驻（Windows 内部即 Shell_NotifyIcon）"),
        ("winotify", "Win10/11 原生 Toast 通知（可选，缺失时降级）"),
        ("pywinstyles", "Windows 标题栏配色（PyPI 包名 pywinstyles，"
                        "不是 GitHub 仓库名 py-window-styles）"),
    ]
    for module, desc in packages:
        optional = module in ("winotify", "pywinstyles")
        try:
            __import__(module)
            results.append(line(OK, "%-14s %s" % (module, desc)))
        except Exception:
            hint = "pip install %s" % module
            if optional:
                results.append(line(WARN,
                                    "%-14s 未安装（可选）%s" % (module, desc),
                                    "会自动降级，程序仍可运行；装了体验更好："
                                    + hint))
            elif module == "pystray":
                results.append(line(WARN, "%-14s 未安装（%s）" % (module, desc),
                                    "没有托盘，程序只能当普通窗口用；" + hint))
            else:
                results.append(line(FAIL, "%-14s 未安装（%s）" % (module, desc),
                                    hint))

    # 3. 图标资源
    print("\n[3] 微软 3D Emoji 图标")
    from src import config
    icons_dir = config.icons_dir()
    if os.path.isdir(icons_dir):
        files = [f for f in os.listdir(icons_dir) if f.endswith(".png")]
        results.append(line(OK if len(files) >= 20 else WARN,
                            "图标目录：%s（%d 个）" % (icons_dir, len(files)),
                            "" if files else "图标缺失时界面会回退到 Unicode 符号"))
        need = ["app", "add", "refresh", "delete", "search", "star", "moon",
                "sun", "settings", "folder", "globe", "check", "import",
                "export", "read"]
        missing = [n for n in need if not os.path.isfile(
            os.path.join(icons_dir, n + ".png"))]
        results.append(line(OK if not missing else WARN,
                            "关键图标齐全" if not missing else
                            "缺少图标：" + "、".join(missing)))
    else:
        results.append(line(FAIL, "图标目录不存在：" + icons_dir))

    # 4. 主题
    print("\n[4] 主题")
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        try:
            from pywebview import ThemedStyle
            themes = sorted(ThemedStyle(root).get_themes())
        except Exception:
            from tkinter import ttk
            themes = sorted(ttk.Style(root).theme_names())
        has_light = "arc" in themes
        has_dark = "equilux" in themes
        results.append(line(OK, "可用主题 %d 个" % len(themes)))
        results.append(line(OK if has_light else WARN, "浅色主题 arc",
                            "" if has_light else "可在设置里改用 clam"))
        results.append(line(OK if has_dark else WARN, "深色主题 equilux",
                            "" if has_dark else "可在设置里改用 clam/black"))
        root.destroy()
    except Exception as exc:
        results.append(line(FAIL, "无法创建窗口：%s" % exc))

    # 5. 通知能力
    print("\n[5] 新文章通知能力")
    try:
        from src import notifier as notifier_module
        backends = notifier_module.describe_backends()
        for key, name in backends:
            print("     %-9s %s" % (key, name))
        if any(key == "toast" for key, _ in backends):
            print(OK + "Win10/11 原生 Toast 可用（通知可点击、进操作中心）")
        elif notifier_module.is_win7():
            print(OK + "Windows 7：使用 Shell_NotifyIcon 托盘气球（Win7 唯一可用的系统通知）")
        else:
            print(WARN + "没有系统通知后端，将使用自绘气泡兜底")
    except Exception as exc:
        results.append(line(FAIL, "通知模块加载失败：%s" % exc))

    # 6. Windows 标题栏
    print("\n[6] 窗口标题栏增强（仅 Windows）")
    version = config.windows_version()
    if version is None:
        print(WARN + "非 Windows 平台，py-window-styles 不会生效（属正常现象）")
    else:
        name = {(6, 1): "Windows 7", (6, 2): "Windows 8",
                (6, 3): "Windows 8.1", (10, 0): "Windows 10/11"}.get(
            (version[0], version[1]), "Windows %s.%s" % (version[0],
                                                         version[1]))
        print("     检测到：%s（build %s）" % (name, version[2]))
        if config.is_windows_7():
            print(WARN + "Windows 7 不支持深色标题栏，将保持系统原生 Aero 风格")
        else:
            try:
                import pywinstyles
                print(OK + "py-window-styles 可用，标题栏会跟随深浅色"
                            + ("（Win11 可启用 Mica）" if config.is_windows_11()
                               else ""))
            except Exception:
                print(WARN + "py-window-styles 未安装，标题栏保持系统默认")

    # 7. 数据目录
    print("\n[7] 数据目录")
    data = config.data_dir()
    results.append(line(OK if os.path.isdir(data) else WARN,
                        "数据目录：%s" % data))

    print("\n" + "=" * 68)
    failed = results.count(False)
    if failed:
        print("自检完成：有 %d 项需要处理" % failed)
    else:
        print("自检完成：环境正常，可以运行 python main.py")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
