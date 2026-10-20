# -*- coding: utf-8 -*-
"""系统托盘常驻。

通知器的主入口就是托盘：程序启动后缩到托盘默默轮询，有新文章才出声。
托盘图标由 pystray 提供（Windows 上内部就是 Shell_NotifyIcon），
未读数量用 Pillow 画成角标叠在图标上。

pystray 缺失时整个模块降级为空实现，程序仍能当普通窗口用。
"""

import os
import threading
import time

from . import config

try:
    import pystray
    HAS_PYSTRAY = True
except Exception:  # pragma: no cover
    pystray = None
    HAS_PYSTRAY = False

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFont = None
    HAS_PIL = False


# 托盘图标：优先用专门的 tray.png（Goose 图标缩到 64px），
# 其次才是 app.png / rss.png。
BASE_ICON = os.path.join(config.icons_dir(), "tray.png")
FALLBACK_ICON = os.path.join(config.icons_dir(), "app.png")
SECOND_ICON = os.path.join(config.icons_dir(), "rss.png")


def _drawn_image():
    """用 Pillow 画一个 RSS 图案的兜底图标。

    图标文件缺失时不能就此放弃托盘——那样用户连"程序还在跑"都看不到了。
    画一个橙色圆角底 + 白色 RSS 波纹，够辨识就行。
    """
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # 橙色圆角方块（RSS 的标准配色）
    draw.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=12,
                           fill=(232, 106, 33, 255))
    # 白色 RSS 波纹：一个点 + 两段弧
    draw.ellipse([(12, 44), (24, 56)], fill=(255, 255, 255, 255))
    for inset, width in ((14, 5), (28, 5)):
        box = [(12, 12 + inset), (52, 52 + inset)]
        try:
            draw.arc(box, start=200, end=290, fill=(255, 255, 255, 255),
                     width=width)
        except TypeError:
            # 老 Pillow 的 arc 没有 width 参数，退化成细线
            draw.arc(box, start=200, end=290, fill=(255, 255, 255, 255))
    return image


def base_image():
    """托盘底图（Windows 托盘实际显示 16/32px，源图取 64px 就够）。

    只要 Pillow 可用就一定返回一张图：优先用图标文件，
    文件缺失就用上面那个画出来的。返回 None 会让托盘被判定为
    "不支持"，等于因为一个文件没找到就放弃整个托盘。
    """
    if not HAS_PIL:
        return None
    for path in (BASE_ICON, FALLBACK_ICON, SECOND_ICON):
        try:
            if os.path.isfile(path):
                image = Image.open(path).convert("RGBA")
                return image.resize((64, 64), Image.LANCZOS)
        except Exception:
            continue
    try:
        return _drawn_image()
    except Exception:
        # 连画都失败（Pillow 太老不支持 rounded_rectangle 等），
        # 退回纯色底，总之不能返回 None
        try:
            return Image.new("RGBA", (64, 64), (232, 106, 33, 255))
        except Exception:
            return None


def badge_image(count, base=None):
    """在图标右下角叠一个角标（未读数）。count <= 0 时返回原图。"""
    if not HAS_PIL:
        return base
    image = (base or base_image())
    if image is None:
        return None
    if not count or count <= 0:
        return image.copy()
    try:
        canvas = image.copy()
        draw = ImageDraw.Draw(canvas)
        size = canvas.size[0]
        radius = max(10, size // 3)
        # 右下角的红色圆
        box = (size - radius - 2, size - radius - 2, size - 2, size - 2)
        draw.ellipse(box, fill=(214, 45, 45, 255))
        text = str(count) if count < 100 else "99+"
        font = None
        for candidate in ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(candidate, max(8, int(radius * 0.95)))
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()
        # 文字居中（Pillow 版本差异：textbbox 不一定有）
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
        except Exception:
            width, height = draw.textsize(text, font=font)
        x = box[0] + (radius - width) / 2 - (bbox[0] if 'bbox' in dir() else 0)
        y = box[1] + (radius - height) / 2 - (bbox[1] if 'bbox' in dir() else 0)
        draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)
        return canvas
    except Exception:
        return image.copy()


class TrayIcon(object):
    """托盘图标 + 右键菜单。

    菜单项：打开主窗口 / 立即检查新文章 / 暂停提醒 / 退出
    左键双击 = 打开主窗口。
    """

    def __init__(self, app, settings=None):
        self.app = app
        self.settings = settings or config.Settings()
        self.icon = None
        self._base = base_image()
        self._thread = None
        self._running = False
        self._ready = None

    # ------------------------------------------------------------------
    @property
    def supported(self):
        return HAS_PYSTRAY and self._base is not None

    def start(self):
        """启动托盘。返回是否成功。不支持时返回 False，不抛异常。"""
        if not self.supported or self._running:
            self._explain()
            return False
        try:
            menu = pystray.Menu(
                pystray.MenuItem("打开主窗口", self._open, default=True),
                pystray.MenuItem("立即检查新文章", self._refresh),
                pystray.MenuItem(self._mute_label, self._toggle_mute),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self._quit),
            )
            self.icon = pystray.Icon(
                config.APP_ID, self._base, config.APP_NAME, menu=menu)
            self._running = True

            self._log_backend()

            # 起线程跑 icon.run()，不传 setup、不轮询 visible。
            #
            # Windows 上 pystray 允许从任何线程调用 run()，官方也只要求
            # macOS 必须在主线程。这里越简单越好：
            #   · 不用 run_detached()（它是给别的框架驱动主循环用的）
            #   · 不传自定义 setup（默认那个会自动置 visible=True）
            #   · 不轮询 visible 判定成败（等待期间出问题反而误报）
            # 参考实现就是这一行裸的 icon.run()。
            self._thread = threading.Thread(
                target=self.icon.run, daemon=True)
            self._thread.start()

            # 只做一次轻量确认：线程若立刻死了说明内部抛了异常
            time.sleep(0.3)
            if self._thread is not None and not self._thread.is_alive():
                print("托盘：消息循环线程已退出，图标不会显示")
                self._running = False
                return False
            return True
        except Exception as exc:
            self._running = False
            print("托盘启动失败：%s: %s" % (type(exc).__name__, exc))
            self._explain()
            return False

    def _log_backend(self):
        """打印托盘后端信息，方便定位"图标为什么没出来"。"""
        try:
            ver = getattr(pystray, "__version__", "未知")
        except Exception:
            ver = "未知"
        print("托盘：pystray %s，后端 %s"
              % (ver, type(self.icon).__module__.split(".")[-1]))
        try:
            print("托盘：菜单支持 %s，通知支持 %s"
                  % (pystray.Icon.HAS_MENU, pystray.Icon.HAS_NOTIFICATION))
        except Exception:
            pass

    def _run_loop(self):
        """备用入口：带异常捕获地跑 icon.run()。

        主路径直接用 threading.Thread(target=self.icon.run)，
        和参考实现一致；这个方法只在需要捕获日志时用。
        """
        try:
            self.icon.run()
        except Exception as exc:
            print("托盘事件循环退出：%s" % exc)

    def reason(self):
        """托盘为什么不可以用（一句话，给界面显示）。"""
        if not HAS_PYSTRAY:
            return "未安装 pystray"
        if not HAS_PIL:
            return "未安装 Pillow"
        if self._base is None:
            return "没有可用图标文件"
        return "启动失败"

    def _explain(self):
        """说清楚托盘为什么没出来。

        之前失败时只是静默 return False，用户只看到"托盘没出来"，
        不知道到底是缺库、缺图标还是运行报错。
        """
        if not HAS_PYSTRAY:
            print("托盘不可用：未安装 pystray。安装：pip install pystray")
            return
        if not HAS_PIL:
            print("托盘不可用：未安装 Pillow（画托盘图标用）。"
                  "安装：pip install Pillow")
            return
        if self._base is None:
            print("托盘不可用：没有可用的图标文件（%s）" % config.icons_dir())
            return
        if self._running:
            return
        print("托盘不可用：pystray 已装但启动失败，详见上方报错。")
        import platform
        if platform.system() == "Windows":
            print("      Windows 7 上图标默认会被收进通知区域的溢出区"
                  "（小三角里），可在任务栏属性里设为始终显示。")

    def stop(self):
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
        self._running = False

    # ------------------------------------------------------------------
    def _mute_label(self, item=None):
        """暂停/恢复提醒的菜单文字。

        这个回调会被 pystray 的 update_menu() 调用，而 update_menu()
        又发生在 _mark_ready() 里：一旦它抛异常，后面的 _mainloop()
        就永远执行不到，托盘线程直接死掉、图标完全不显示。
        所以这里必须兜住所有异常，保证永远返回一个字符串。
        """
        try:
            muted = not self.settings.get("notify_enabled", True)
            return "恢复提醒" if muted else "暂停提醒"
        except Exception:
            return "暂停提醒"

    def _toggle_mute(self, icon=None, item=None):
        self.settings.set("notify_enabled",
                          not self.settings.get("notify_enabled", True))
        self.settings.save()
        if self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:
                pass
        try:
            self.app.status_message(
                "已%s提醒" % ("恢复" if self.settings.get("notify_enabled")
                              else "暂停"))
        except Exception:
            pass

    # 托盘回调跑在 pystray 自己的线程里，不能在这里直接操作界面。
    # 统一转成"请求"，由主界面（Tk 的 after 或 webview 的 poll）取走执行。
    def _request(self, action):
        app = self.app
        for name in ("request_action", "emit"):
            handler = getattr(app, name, None)
            if callable(handler):
                try:
                    if name == "emit":
                        handler("tray_action", action=action)
                    else:
                        handler(action)
                    return
                except Exception:
                    pass
        # 兼容旧的 Tk 主程序
        try:
            root = getattr(app, "root", None)
            if root is not None:
                root.after(0, lambda: self._tk_fallback(action))
        except Exception:
            pass

    def _tk_fallback(self, action):
        app = self.app
        try:
            if action == "show":
                app.show_window()
            elif action == "refresh":
                app.refresh_all()
            elif action == "quit":
                app.quit_app()
        except Exception:
            pass

    def _open(self, icon=None, item=None):
        self._request("show")

    def _refresh(self, icon=None, item=None):
        self._request("refresh")

    def _quit(self, icon=None, item=None):
        self._request("quit")

    # ------------------------------------------------------------------
    def update_badge(self, count):
        """更新角标。托盘更新要放到后台线程，避免卡住界面。"""
        if not self._running or self.icon is None:
            return
        if not self.settings.get("notify_badge", True):
            count = 0
        try:
            image = badge_image(count, self._base)
            if image is None:
                return
            self.icon.icon = image
            unread = count
            self.icon.title = (config.APP_NAME if not unread
                               else "%s（%d 篇新文章）" % (config.APP_NAME, unread))
        except Exception:
            pass

    def _refresh_badge(self):
        try:
            count = self.app.store.unnotified_count()
        except Exception:
            count = 0
        self.update_badge(count)
