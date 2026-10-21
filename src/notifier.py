# -*- coding: utf-8 -*-
"""新文章通知引擎。

Windows 上「全版本通用」的通知接口其实有两套，能力不一样：

    接口                              Win7   Win8   Win10/11
    Shell_NotifyIcon + NIF_INFO 气球   ✅     ✅     ✅
    Toast 通知                         ❌     ✅     ✅

默认顺序是 **气球优先**，这不是随意排的：

  · 气球走 Shell_NotifyIcon，Win7 起全版本通用，且由托盘图标直接发出，
    不需要任何额外注册 —— 实测最稳。
  · Toast（winotify）看起来更现代，但它要求应用已注册 AUMID；
    Python 脚本通常没有，此时 `toast.show()` **不抛异常也不弹通知**，
    调用方无从判断失败，于是永远不会降级到气球 —— 用户看到的就是
    "通知完全没反应"。

所以把 Toast 降级为可选（设置项 `notify_prefer_toast`，默认关），
想用的自己打开，出问题也知道去哪儿找。

所有后端都包在 try/except 里：任何一个失败都自动往下一层降，不会因为通知
功能把主程序拖垮。
"""

import os
import sys
import time
import threading

from . import config

IS_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------------------
# 后端能力探测
# ---------------------------------------------------------------------------

def windows_version():
    """返回 (major, minor, build)；非 Windows 返回 None。"""
    if not IS_WINDOWS:
        return None
    try:
        return tuple(config.windows_version() or ())
    except Exception:
        return None


def is_win10_or_later():
    version = windows_version()
    return bool(version) and version[0] >= 10


def is_win7():
    version = windows_version()
    return bool(version) and version[:2] == (6, 1)


def _has_winotify():
    if not IS_WINDOWS or not is_win10_or_later():
        return False
    try:
        import winotify  # noqa: F401
        return True
    except Exception:
        return False


def available_backends(prefer_toast=False):
    """返回当前环境可用的通知后端，按优先级排序。

    默认气球优先（Win7 起全版本通用、实测最稳）。
    prefer_toast=True 时才把 Toast 排到前面 —— 但注意 Toast 失败是静默的，
    排在前面就意味着它可能"吞掉"后续所有降级路径。
    """
    backends = []
    if IS_WINDOWS:
        if prefer_toast and _has_winotify():
            backends.append("toast")
        backends.append("balloon")
        # Toast 可用但没排前面时，仍作为气球之后的第二选择
        if not prefer_toast and _has_winotify():
            backends.append("toast")
    backends.append("bubble")
    return backends


def describe_backends():
    """给设置界面 / 自检脚本用的中文说明。"""
    names = {
        "toast": "系统 Toast",
        "balloon": "托盘气球",
        "bubble": "页面气泡",
    }
    return [(key, names[key]) for key in available_backends()]


# ---------------------------------------------------------------------------
# 提示音
# ---------------------------------------------------------------------------

def play_sound():
    """播放一段简短提示音。失败就静默（通知器不该因为没声卡而崩）。"""
    if not IS_WINDOWS:
        return False
    try:
        import winsound
        # 用系统默认提示音，不依赖任何音频文件
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
        return True
    except Exception:
        try:
            import winsound
            winsound.Beep(880, 120)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# 免打扰时段
# ---------------------------------------------------------------------------

def _parse_hour(value):
    try:
        hour = int(str(value).split(":")[0])
        minute = int(str(value).split(":")[1]) if ":" in str(value) else 0
        return hour * 60 + minute
    except Exception:
        return None


def in_quiet_hours(rule, now=None):
    """判断当前是否处于免打扰时段。rule 形如 "23:00-07:00"。"""
    if not rule or "-" not in rule:
        return False
    start_raw, end_raw = rule.split("-", 1)
    start = _parse_hour(start_raw.strip())
    end = _parse_hour(end_raw.strip())
    if start is None or end is None:
        return False
    now = now or time.localtime()
    current = now.tm_hour * 60 + now.tm_min
    if start <= end:
        return start <= current < end
    # 跨零点，如 23:00-07:00
    return current >= start or current < end


# ---------------------------------------------------------------------------
# 通知引擎
# ---------------------------------------------------------------------------


class Notifier(object):
    """发通知。

    用法：
        notifier = Notifier(settings, icon_path="...")
        notifier.notify("阮一峰的网络日志", "3 篇新文章", url="https://...")
    """

    def __init__(self, settings=None, icon_path="", tray_icon=None,
                 fallback_root=None, bubble_callback=None):
        self.settings = settings or config.Settings()
        self.icon_path = icon_path
        self.tray_icon = tray_icon      # pystray.Icon，用于 balloon 后端
        # Tk 自绘气泡已随 Tk 界面一起移除；现在气泡后端交给前端页面渲染，
        # 这里只需要一个回调把内容交出去
        self.fallback_root = fallback_root   # 兼容旧参数，已不再使用
        self.bubble_callback = bubble_callback
        self._app_id = config.APP_ID
        self._bubble = None
        self._last_error = ""
        self._count = 0

    # ------------------------------------------------------------------
    @property
    def last_error(self):
        return self._last_error

    @property
    def sent_count(self):
        return self._count

    def enabled(self):
        return bool(self.settings.get("notify_enabled", True))

    def quiet_now(self):
        return in_quiet_hours(self.settings.get("notify_silent_hours", ""))

    # ------------------------------------------------------------------
    def notify(self, title, message, url=""):
        """发一条通知。返回实际使用的后端名，全部失败返回 ""。

        会依次尝试：系统通知 → 托盘气球 → 自绘气泡。
        """
        self._last_error = ""
        if not self.enabled():
            return ""
        if self.quiet_now():
            self._last_error = "免打扰时段"
            return ""

        backends = []
        if self.settings.get("notify_system", True):
            prefer_toast = bool(self.settings.get("notify_prefer_toast", False))
            backends.extend(available_backends(prefer_toast=prefer_toast))
        elif "bubble" not in backends:
            backends.append("bubble")

        for name in backends:
            try:
                if name == "toast" and self._try_toast(title, message, url):
                    self._after_success()
                    return "toast"
                if name == "balloon" and self._try_balloon(title, message):
                    self._after_success()
                    return "balloon"
                if name == "bubble" and self._try_bubble(title, message):
                    self._after_success()
                    return "bubble"
            except Exception as exc:
                self._last_error = "%s 失败：%s" % (name, exc)
        return ""

    def _after_success(self):
        self._count += 1
        if self.settings.get("notify_sound", True):
            play_sound()

    # ------------------------------------------------------------------
    # 后端 1：Win10/11 Toast
    # ------------------------------------------------------------------
    def _try_toast(self, title, message, url=""):
        if not _has_winotify():
            return False
        from winotify import Notification, audio
        toast = Notification(app_id=self._app_id, title=title, msg=message,
                             icon=self.icon_path or "")
        try:
            if self.settings.get("notify_sound", True):
                toast.set_audio(audio.Default, loop=False)
            else:
                toast.set_audio(audio.Silent, loop=False)
        except Exception:
            pass
        # 点击通知直接打开原文（用户选定的行为）
        if url and self.settings.get("notify_open_on_click", True):
            try:
                toast.add_actions(label="打开原文", launch=url)
            except Exception:
                pass
        toast.show()
        return True

    # ------------------------------------------------------------------
    # 后端 2：Shell_NotifyIcon 气球（pystray 内部实现，Win7 起通用）
    # ------------------------------------------------------------------
    def _try_balloon(self, title, message):
        icon = self.tray_icon
        if icon is None:
            return False
        try:
            if not getattr(icon, "HAS_NOTIFICATION", False):
                return False
            # 一次只显示一条：先清掉上一条，否则会被排队、延迟很久才出现
            try:
                icon.remove_notification()
            except Exception:
                pass
            icon.notify(message, title=title)
            return True
        except Exception as exc:
            self._last_error = "balloon 失败：%s" % exc
            return False

    # ------------------------------------------------------------------
    # 后端 3：自绘 Tk 气泡（所有平台兜底）
    # ------------------------------------------------------------------
    def _try_bubble(self, title, message):
        """页面内气泡：把内容交给前端渲染（Win7 且无托盘时的兜底）。"""
        callback = self.bubble_callback
        if callback is None:
            return False
        try:
            callback({"title": title, "message": message, "kind": "bubble"})
            return True
        except Exception as exc:
            self._last_error = "bubble 失败：%s" % exc
            return False


# ---------------------------------------------------------------------------
# 通知内容组织
# ---------------------------------------------------------------------------


def build_notifications(articles, per_feed_limit=3, max_total=8):
    """把一批新文章组织成若干条通知，避免刷屏。

    规则：
    · 按订阅源分组，一个源一条通知
    · 单源文章数 <= per_feed_limit 时，标题里列出每篇标题
    · 超出则只显示 "N 篇新文章"，不逐条列
    · 总条数超过 max_total 时，剩下的源合并成一条汇总

    返回 [{"title", "message", "url", "articles"}...]
    """
    groups = {}
    order = []
    for art in articles:
        key = art.feed_id
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(art)

    notices = []
    for feed_id in order:
        items = groups[feed_id]
        feed_title = items[0].feed_title or "订阅源"
        # 最新的一篇作为点击目标
        items.sort(key=lambda a: (a.timestamp or a.added, a.added),
                   reverse=True)
        url = items[0].link or ""
        if len(items) == 1:
            notices.append({
                "title": feed_title,
                "message": items[0].title,
                "url": url,
                "articles": items,
            })
        elif len(items) <= per_feed_limit:
            message = "\n".join(a.title for a in items)
            notices.append({
                "title": "%s：%d 篇新文章" % (feed_title, len(items)),
                "message": message,
                "url": url,
                "articles": items,
            })
        else:
            head = "\n".join(a.title for a in items[:per_feed_limit])
            message = "%s\n…等共 %d 篇" % (head, len(items))
            notices.append({
                "title": "%s：%d 篇新文章" % (feed_title, len(items)),
                "message": message,
                "url": url,
                "articles": items,
            })

    if len(notices) > max_total:
        kept = notices[:max_total]
        rest = notices[max_total:]
        rest_articles = [a for n in rest for a in n["articles"]]
        rest_feeds = len(rest)
        kept.append({
            "title": "还有 %d 个订阅源有更新" % rest_feeds,
            "message": "共 %d 篇新文章，打开主窗口查看" % len(rest_articles),
            "url": "",
            "articles": rest_articles,
        })
        return kept
    return notices
