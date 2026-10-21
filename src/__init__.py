# -*- coding: utf-8 -*-
"""GooseRSS —— 新文章通知器。

界面用 pywebview（系统浏览器内核渲染），不再依赖 Tkinter。

依赖：
    pywebview          界面窗口（Win10/11 用 Edge WebView2，Win7 降级到 IE11）
    feedparser         RSS / Atom / RDF / JSON Feed 解析
    requests           HTTP 请求
    pystray + Pillow   系统托盘与图标（可选）
    winotify           Win10/11 Toast 通知（可选，默认不用）

Python 3.6+ / Windows 7 及以上均可运行。
"""

__version__ = "1.0.0"
__all__ = ["config", "icons", "models", "fetcher", "opml", "htmlutil",
           "notifier", "tray", "controller", "webview_app"]
