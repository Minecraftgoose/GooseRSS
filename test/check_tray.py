# -*- coding: utf-8 -*-
"""托盘诊断：一步步查清托盘为什么没出来。

用法（Windows 命令行）：
    python test/check_tray.py

依次检查：依赖 → 图标文件 → 能否创建 Icon → 能否真正显示，
最后给出结论和建议。请把整段输出贴给开发者。
"""

import os
import platform
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

LINE = "=" * 62


def head(text):
    print("\n" + LINE)
    print(text)
    print(LINE)


def main():
    print(LINE)
    print("GooseRSS —— 托盘诊断")
    print(LINE)
    print("系统：%s %s" % (platform.system(), platform.release()))
    print("Python：%s（%s 位）"
          % (sys.version.split()[0],
             "64" if sys.maxsize > 2 ** 32 else "32"))

    # ---------- 1. 依赖 ----------
    head("1. 依赖检查")
    has_pystray = False
    has_pil = False
    try:
        import pystray
        has_pystray = True
        ver = getattr(pystray, "__version__", "未知")
        print("pystray：已安装，版本 %s" % ver)
        try:
            from pystray import Icon as _I
            print("  后端：%s" % type(_I).__module__.split(".")[-1])
            print("  菜单支持：%s，通知支持：%s"
                  % (_I.HAS_MENU, _I.HAS_NOTIFICATION))
        except Exception as exc:
            print("  读取后端信息失败：%s" % exc)
    except Exception as exc:
        print("pystray：未安装或导入失败 —— %s" % exc)
    try:
        from PIL import Image
        has_pil = True
        print("Pillow：已安装")
    except Exception as exc:
        print("Pillow：未安装或导入失败 —— %s" % exc)

    if not has_pystray or not has_pil:
        print("\n>>> 结论：缺少必需依赖，托盘必然不会出来。")
        print(">>> 修复：pip install pystray Pillow")
        return 1

    # ---------- 1.5 Windows 7 / 老旧环境专项 ----------
    head("1.5 老旧环境专项检查")
    py38 = sys.version_info[:2] <= (3, 8)
    if py38:
        print("Python <= 3.8：注意 Pillow 11.0 起已放弃 3.8，"
              "最高只能用 10.4")
        try:
            import PIL
            print("  当前 Pillow：%s" % getattr(PIL, "__version__", "未知"))
            major = int(str(getattr(PIL, "__version__", "0")).split(".")[0])
            if major >= 11:
                print("  >>> 版本过高，可能装不上或运行异常，"
                      "建议 pip install 'Pillow<11'")
        except Exception:
            pass
    else:
        print("Python 版本较新，无 3.8 相关限制")

    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full")
            release, _ = winreg.QueryValueEx(key, "Release")
            print(".NET Framework 4.x Release = %s" % release)
            if release < 461808:
                print("  >>> 低于 4.7.2（461808）。pywebview 在 Win7 上"
                      "需要 .NET 4.7.2，请安装")
        except Exception as exc:
            print(".NET 版本读取失败：%s" % exc)
        print()
        print("若上面都正常但任务栏仍看不到图标，清一次通知区缓存：")
        print("  1) 任务管理器结束 explorer.exe")
        print("  2) 删除注册表 TrayNotify 下的 IconStreams / "
              "PastIconsStream")
        print("     路径：HKEY_CURRENT_USER\\Software\\Classes\\"
              "Local Settings\\Software\\Microsoft\\Windows\\"
              "CurrentVersion\\TrayNotify")
        print("  3) 重新运行 explorer.exe")

    # ---------- 2. 图标文件 ----------
    head("2. 图标文件")
    from src import config
    icons_dir = config.icons_dir()
    print("图标目录：%s" % icons_dir)
    found = None
    for name in ("rss.png", "app.png"):
        path = os.path.join(icons_dir, name)
        if os.path.isfile(path):
            print("  可用：%s（%d 字节）" % (name, os.path.getsize(path)))
            if found is None:
                found = path
        else:
            print("  缺失：%s" % name)
    if found is None:
        print("\n>>> 结论：图标文件缺失，托盘不会显示。")
        print(">>> 修复：确认 assets/icons 或 web/icons 目录完整。")
        return 1

    # ---------- 3. 能否创建 Icon ----------
    head("3. 创建托盘对象")
    icon = None
    try:
        image = Image.open(found).convert("RGBA").resize((64, 64))
        print("底图：%s，尺寸 %s" % (found, image.size))
        icon = pystray.Icon("GooseRSSTrayTest", image, "托盘诊断")
        print("pystray.Icon 创建成功")
        print("后端：%s" % type(icon).__name__)
    except Exception as exc:
        print("创建失败：%s: %s" % (type(exc).__name__, exc))
        print("\n>>> 结论：pystray 无法创建托盘对象。")
        return 1

    # ---------- 4. 能否真正显示 ----------
    head("4. 启动并显示（等待 5 秒）")
    ready = []

    def setup(ic):
        ic.visible = True
        ready.append(True)

    thread = None
    try:
        import threading
        thread = threading.Thread(target=lambda: icon.run(setup), daemon=True)
        thread.start()
    except Exception as exc:
        print("启动失败：%s: %s" % (type(exc).__name__, exc))
        print("\n>>> 结论：托盘消息循环起不来。")
        return 1

    deadline = time.time() + 5.0
    while time.time() < deadline and not icon.visible:
        time.sleep(0.2)

    visible = bool(getattr(icon, "visible", False))
    print("icon.visible = %s" % visible)
    print("setup 回调执行 = %s" % (bool(ready)))
    print("线程存活 = %s" % (thread.is_alive() if thread else "无"))
    if not visible and thread is not None and not thread.is_alive():
        print()
        print(">>> 注意：托盘线程已经退出，说明 icon.run() 内部抛了异常")
        print(">>>      或消息循环立即结束。这是图标不出现的直接原因。")

    # ---------- 结论 ----------
    head("结论")
    if visible:
        print("托盘已成功显示。")
        print()
        print("如果你在任务栏右下角仍然看不到它，那是 Windows 把它")
        print("收进了通知区域的「溢出区」——这是 Windows 7 及以后的默认行为：")
        print("  1. 点击任务栏右侧的小三角（显示隐藏的图标）")
        print("  2. 找到本程序图标，把它拖到通知区域里")
        print("  3. 或：任务栏右键 → 属性 → 通知区域 → 自定义")
        print("     → 把本程序设为「显示图标和通知」")
        print()
        print("程序本身没问题，是系统把它折叠了。")
    else:
        print("托盘未能显示。可能原因：")
        print("  · pystray 版本与系统不兼容（Windows 7 建议 pystray>=0.19）")
        print("  · 通知区域被组策略禁用（企业环境常见）")
        print("  · Explorer 通知区缓存损坏：")
        print("    可尝试结束 explorer.exe 后重新运行它")
        print()
        print("请把本脚本的完整输出发给开发者。")

    try:
        icon.stop()
    except Exception:
        pass
    return 0 if visible else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("\n诊断脚本自身出错：%s: %s" % (type(exc).__name__, exc))
        sys.exit(2)
