# -*- coding: utf-8 -*-
"""pywebview 界面层。

用系统浏览器内核渲染界面（Win10/11 走 Edge WebView2，Win7 降级到 IE11），
Python 只负责数据。相比 Tkinter 的 Treeview，DOM 渲染几千条也不会卡。

线程约定（很重要）：
  · JS 调 Python（pywebview.api.*）—— 运行在 webview 主线程，可以直接操作窗口
  · Python 想推给前端 —— 不直接 evaluate_js，而是把事件放进队列，
    由前端每 400ms 调一次 api.poll() 取走
  这样避免了跨线程调 evaluate_js 在部分平台上抽风的问题。
"""

import os
import re
import sys
import threading

from . import config
from .controller import Controller

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "web")


def index_url(base=""):
    """主页面地址。

    优先走本地 HTTP 服务（base 形如 http://127.0.0.1:port）。
    页面和接口同源，前端直接用相对路径 /api/xxx 就能调到后端，
    不需要任何 JS 注入机制。
    HTTP 起不来时才退回 file://（那时要靠 js_api，可能不可用）。
    """
    if base:
        return base + "/index.html"
    path = os.path.join(WEB_DIR, "index.html")
    return "file:///" + path.replace(os.sep, "/").lstrip("/")


def _js_json(value):
    """把 Python 数据转成能安全塞进 JS 字面量的 JSON。

    pywebview 的 evaluate_js 直接传字符串时，中文和引号容易出问题，
    这里统一做一次 JSON 编码 + 转义。
    """
    import json
    text = json.dumps(value, ensure_ascii=False)
    return text.replace("\\", "\\\\").replace("'", "\\'")


class Api(object):
    """暴露给前端的后端接口。

    现在主通道是本地 HTTP 服务（见 server.py），这个类同时保留，
    作为 js_api 注入成功时的备用通道，以及轮询兜底通道的方法表。

    属性一律用下划线开头：pywebview 扫描 api 实例时会遍历公开属性，
    之前把窗口对象挂在上面，它会顺着
    controller.window.native.AccessibilityObject.Bounds.Empty...
    一路递归到爆栈。
    """

    def __init__(self, controller, window=None):
        # 必须用下划线开头：pywebview 扫描 api 实例时会跳过私有属性。
        # 如果这里叫 controller / window，它会顺着
        # controller.window.native.AccessibilityObject.Bounds.Empty...
        # 一路递归下去直到爆栈（Win7 上尤其明显）。
        self._controller = controller
        self._window = window

    # ---------------- 数据 ----------------
    def bootstrap(self):
        return self._controller.bootstrap()

    def backend_info(self):
        return self._controller.backend_info()

    def get_feeds(self):
        return self._controller.get_feeds()

    def stats(self):
        return self._controller.stats()

    def get_articles(self, view="all", feed_id=None, group=None,
                     query="", offset=0, limit=60):
        return self._controller.get_articles(
            view, feed_id, group, query, offset, limit)

    def get_article(self, article_id):
        return self._controller.get_article(article_id)

    def reader_prev_next(self, article_id, delta=1):
        return self._controller.reader_prev_next(article_id, delta)

    # ---------------- 订阅管理 ----------------
    def probe_feeds(self, text):
        return self._controller.probe_feeds(text)

    def add_feeds(self, urls=None, group=""):
        return self._controller.add_feeds(urls or [], group=group or "")

    def update_feed(self, feed_id, fields=None):
        return self._controller.update_feed(feed_id, fields or {})

    def remove_feed(self, feed_id):
        return self._controller.remove_feed(feed_id)

    def add_group(self, name):
        return self._controller.add_group(name)

    def rename_group(self, old, new):
        return self._controller.rename_group(old, new)

    def remove_group(self, name, delete_feeds=False):
        return self._controller.remove_group(name, delete_feeds=delete_feeds)

    # ---------------- 文章操作 ----------------
    def mark_read(self, article_id, read=True):
        return self._controller.mark_read(article_id, bool(read))

    def toggle_star(self, article_id):
        return self._controller.toggle_star(article_id)

    def mark_all_read(self, view="all", feed_id=None, group=None):
        return self._controller.mark_all_read(view, feed_id, group)

    def clear_articles(self, keep_starred=True):
        return self._controller.clear_articles(keep_starred=bool(keep_starred))

    # ---------------- 刷新与通知 ----------------
    def refresh(self, feed_ids=None):
        return self._controller.refresh(feed_ids)

    def cancel_refresh(self):
        return self._controller.cancel_refresh()

    def send_notifications(self, limit=None):
        return self._controller.send_notifications(limit)

    def test_notification(self):
        return self._controller.test_notification()

    # ---------------- OPML ----------------
    def opml_text(self):
        return self._controller.opml_text()

    def export_opml(self, path=None):
        if not path and self._window is not None:
            try:
                chosen = self._window.create_file_dialog(
                    self._window.SAVE_DIALOG,
                    directory="", save_filename="subscriptions.opml")
                if not chosen:
                    return {"ok": False, "cancelled": True}
                path = chosen[0] if isinstance(chosen, (list, tuple)) else chosen
            except Exception:
                return {"ok": False, "error": "无法打开文件对话框"}
        return self._controller.export_opml(path)

    def import_opml(self, path=None, group=""):
        if not path and self._window is not None:
            try:
                chosen = self._window.create_file_dialog(
                    self._window.OPEN_DIALOG, directory="",
                    allow_multiple=False)
                if not chosen:
                    return {"ok": False, "cancelled": True}
                path = chosen[0] if isinstance(chosen, (list, tuple)) else chosen
            except Exception:
                return {"ok": False, "error": "无法打开文件对话框"}
        return self._controller.import_opml(path, group=group or "")

    # ---------------- 其它 ----------------
    def open_url(self, url):
        return self._controller.open_url(url)

    def copy_link(self, text):
        """复制链接到剪贴板（不再复用 open_url('') 这种模糊语义）。"""
        ok = self._controller.copy_link(text)
        if ok:
            self._controller.emit("copied", text=text or "")
        return ok

    def open_reader(self, article_id):
        """打开阅读页。

        已经改成 SPA：不再另开窗口，直接把文章数据交给前端，
        由前端在主页面里切到阅读视图。开文章是瞬时的，也不堆窗口。
        """
        article = self._controller.get_article(article_id)
        if not article:
            return {"ok": False, "error": "文章不存在"}
        return {"ok": True, "article": article}

    def save_settings(self, data=None):
        return self._controller.save_settings(data or {})

    def show_window(self):
        if self._window is not None:
            try:
                self._window.show()
                return True
            except Exception:
                pass
        return False

    def hide_window(self):
        if self._window is not None:
            try:
                self._window.hide()
                return True
            except Exception:
                pass
        return False

    def quit_app(self):
        try:
            self._controller.flush_save()
        except Exception:
            pass
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass


def create_api(controller, window=None):
    return Api(controller, window)


def _gui_name():
    """当前实际使用的渲染内核名。"""
    try:
        import webview
        name = getattr(webview, "gui", None)
        if name:
            return str(name)
    except Exception:
        pass
    return "unknown"


def _start_fallback_pump(controller, window, api):
    """JS 桥不可用时的兜底通道。

    正常情况下前端通过 window.pywebview.api 调后端。但如果渲染内核
    没能注入 JS 桥（老 IE 内核、版本不匹配等），整个软件就只剩一张
    空壳页面。这里改用 Python 主动轮询：
      · 取出前端排队的命令 → 执行 → 把结果写回
      · 顺带把待发事件一起推过去
    开销是每 0.4 秒两次 evaluate_js，只在确认桥不可用时才启动。
    """
    import json
    import threading
    import time

    pending_poll = [0.0]

    def pump():
        while True:
            time.sleep(0.4)
            try:
                raw = window.evaluate_js(
                    "(function(){var c=window.__cmds||[];"
                    "window.__cmds=[];return JSON.stringify(c);})()")
                commands = json.loads(raw) if raw else []
            except Exception:
                commands = []

            results = []
            for command in commands:
                name = command.get("name") or ""
                try:
                    fn = getattr(api, name, None)
                    if fn is None or name.startswith("_"):
                        results.append({"id": command.get("id"),
                                        "error": "no method"})
                        continue
                    value = fn(*(command.get("args") or []))
                    results.append({"id": command.get("id"), "value": value})
                except Exception as exc:
                    results.append({"id": command.get("id"),
                                    "error": str(exc)})

            events = []
            try:
                events.extend(controller.poll_refresh())
                controller.auto_refresh_tick()
                events.extend(controller.drain())
            except Exception:
                pass

            try:
                window.evaluate_js(
                    "window.__pump(%s)"
                    % _js_json({"results": results, "events": events}))
            except Exception:
                pass

    threading.Thread(target=pump, daemon=True).start()


def run(controller, hidden=False, debug=False, gui=None):
    """创建并运行主窗口（阻塞，直到窗口关闭）。

    gui 用于强制指定渲染内核（edgechromium / mshtml / cef / qt / gtk）。
    """
    import webview

    # 主通道：本地 HTTP 服务。它在任何内核上都可用，
    # 不依赖 js_api 注入（Win7 的 WebView2 109 上注入经常失败）。
    from .server import start_server
    server, base = start_server(controller)
    controller.api_server = server
    controller.http_base = base
    if base:
        print("后端服务已启动：%s" % base)
    else:
        print("警告：本地 HTTP 服务启动失败，退回 file:// + js_api"
              "（Windows 7 上可能无法连接后端）")

    # 托盘必须在建窗口之前启动。
    # pywebview 初始化窗口（尤其 WinForms + WebView2）的过程会干扰
    # 已存在的托盘图标句柄；先起托盘可以让它抢在窗口初始化之前完成
    # Shell_NotifyIcon 注册。参考实现就是先托盘、后窗口、最后 start()。
    try:
        controller.start_tray()
    except Exception as exc:
        print("托盘启动异常：%s" % exc)

    # 窗口尺寸从设置里恢复。以前硬编码 1080x700，用户调完下次启动
    # 又变回去 —— 设置项 window_geometry 一直存着却没人读。
    win_w, win_h = 1080, 700
    try:
        geo = str(controller.settings.get("window_geometry", "") or "")
        m = re.match(r"(\d+)\s*[xX\u00d7]\s*(\d+)", geo)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            if w >= 760 and h >= 480:
                win_w, win_h = w, h
    except Exception:
        pass

    controller._window = webview.create_window(
        "%s — 新文章提醒" % config.APP_NAME,
        url=index_url(base),
        width=win_w, height=win_h,
        min_size=(760, 480),
        hidden=bool(hidden),
        js_api=create_api(controller))

    def _save_geometry(width=None, height=None, x=None, y=None):
        """把当前窗口尺寸记进设置（下次启动恢复）。"""
        try:
            if width is None:
                try:
                    width = controller._window.width
                    height = controller._window.height
                    x = controller._window.x
                    y = controller._window.y
                except Exception:
                    return
            if not width or not height:
                return
            geo = "%dx%d" % (int(width), int(height))
            if x is not None and y is not None:
                geo += "+%d+%d" % (int(x), int(y))
            controller.settings.set("window_geometry", geo)
            controller.schedule_save()
        except Exception:
            pass

    controller._save_geometry = _save_geometry

    try:
        def on_resized(width, height):
            _save_geometry(width, height)

        controller._window.events.resized += on_resized
    except Exception:
        pass

    def on_closed():
        # 关闭主窗口只是藏起来，程序继续在托盘里跑。
        #
        # 关键：必须 return False。pywebview 的 closing 事件里，
        # 只有 handler 返回 False 才会取消关闭；返回 None 等于放行，
        # 窗口会被真的销毁 → webview.start() 返回 → main.py 走完
        # → Python 退出 → daemon 托盘线程被一起杀掉。
        # 表现就是"托盘图标不见了"——它出来过，然后随程序没了。
        try:
            _save_geometry()
        except Exception:
            pass
        try:
            controller.flush_save()
        except Exception:
            pass
        tray_ok = controller.tray is not None

        if not tray_ok:
            # 托盘没起来时，藏起来等于把程序变成"看不见也唤不回来"的
            # 幽灵进程：用户点✕没反应，任务栏里也没有它，只能去任务
            # 管理器杀。所以这种情况直接真正退出。
            print("托盘不可用，点关闭将直接退出程序。")
            try:
                controller.stop_all()
            except Exception:
                pass
            return True          # True = 放行关闭

        # 托盘可用：关窗口 = 缩到托盘，程序继续在后台盯着订阅源
        try:
            controller._window.hide()
        except Exception:
            pass
        return False             # False = 取消关闭

    controller._window.events.closing += on_closed

    def on_loaded():
        """页面加载完成后，主动把运行时信息推给前端。

        这一步同时是连通性自检：如果 evaluate_js 能成功而
        window.pywebview.api 却不存在，就能确定是 JS 桥没注入，
        而不是 Python 后端没起来。
        """
        try:
            info = {
                "gui": _gui_name(),
                "version": getattr(webview, "__version__", "") or "",
                "python": sys.version.split()[0],
                "http": bool(getattr(controller, "http_base", "")),
            }
            controller._window.evaluate_js(
                "window.__runtime = %s; window.__runtimeReady = true;"
                % _js_json(info))
        except Exception:
            pass

        # 给 JS 桥 2.5 秒的注入时间；之后仍不可用就启用兜底通道
        def check_bridge():
            import time
            time.sleep(2.5)
            try:
                ok = controller._window.evaluate_js(
                    "!!(window.pywebview && window.pywebview.api)")
            except Exception:
                ok = False
            if not ok:
                print("提示：JS 桥未注入，已启用轮询兜底通道（功能可用，"
                      "响应约有 0.4 秒延迟）")
                _start_fallback_pump(controller, controller._window,
                                     create_api(controller, controller._window))

        threading.Thread(target=check_bridge, daemon=True).start()

    try:
        controller._window.events.loaded += on_loaded
    except Exception:
        pass

    # 窗口图标（任务栏 / 标题栏）要传给 start()，不是 create_window()。
    # 之前写错位置，直接 TypeError: unexpected keyword argument 'icon'。
    #
    # 还要按平台挑格式：Windows 用 .ico（PNG 在任务栏可能不生效），
    # Linux/GTK 的 gdk-pixbuf 不认压缩过的 .ico，必须给 PNG，
    # 否则启动直接 GError: Compressed icons are not supported。
    if sys.platform.startswith("win"):
        icon_path = os.path.join(config.assets_dir(), "app.ico")
        if not os.path.isfile(icon_path):
            icon_path = os.path.join(config.icons_dir(), "app.png")
    else:
        icon_path = os.path.join(config.icons_dir(), "app.png")

    start_kwargs = {"debug": debug, "private_mode": False, "gui": gui}
    if os.path.isfile(icon_path):
        start_kwargs["icon"] = icon_path
    try:
        webview.start(**start_kwargs)
    except TypeError:
        # 老版本 pywebview 的 start() 不认 icon，那就不要图标
        start_kwargs.pop("icon", None)
        webview.start(**start_kwargs)
    except Exception:
        # 图标加载失败（格式不支持等）不该拖垮启动，去掉再试一次
        if "icon" in start_kwargs:
            start_kwargs.pop("icon", None)
            webview.start(**start_kwargs)
        else:
            raise
    try:
        controller.flush_save()
    except Exception:
        pass
    try:
        if getattr(controller, "api_server", None) is not None:
            controller.api_server.stop()
    except Exception:
        pass
