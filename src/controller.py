# -*- coding: utf-8 -*-
"""业务逻辑中枢。

这一层不依赖任何 GUI 框架（没有 tkinter、没有 webview），
前端无论是 Tk、webview 还是命令行都能驱动它。

前端通过两类调用使用它：
  · 主动调用：add_feeds / get_articles / toggle_star ...
  · 被动取事件：poll() —— 刷新进度、新通知、气泡提示都从这里取走

之所以把所有"后端推给前端"的东西统一收进 poll()，
是因为 webview 里 Python 主动调 JS 的时机不好控制，
与其两边各维护一套推送通道，不如前端每 400ms 来取一次。
"""

import os
import threading
import time
import sys
import webbrowser

from . import config, opml
from .fetcher import Refresher, probe_url, fetch_feed
from .models import Store, Feed
from .notifier import Notifier, build_notifications


def _clean(text):
    return (text or "").strip()


def _version_newer(latest, current):
    """比较版本号：latest 比 current 新就返回 True。

    只比数字段（去掉 v 前缀和 -beta 之类的后缀），
    'v1.2.0' vs '1.1.0' → True；相同 → False。
    """
    def parts(v):
        nums = []
        for chunk in str(v or "").lstrip("vV").split("-")[0].split("."):
            digits = "".join(c for c in chunk if c.isdigit())
            nums.append(int(digits) if digits else 0)
        return nums

    a, b = parts(latest), parts(current)
    n = max(len(a), len(b))
    a += [0] * (n - len(a))
    b += [0] * (n - len(b))
    return a > b


class Controller(object):
    """订阅管理 + 刷新调度 + 通知，全部与界面无关。"""

    def __init__(self):
        self.settings = config.Settings()
        self._migrate_settings()
        self.store = Store(data_dir=config.data_dir(),
                           settings=self.settings)
        self.store.load()
        self.refresher = Refresher(self.store, self.settings)
        self.notifier = Notifier(
            self.settings, icon_path=os.path.join(config.icons_dir(), "app.ico"),
            bubble_callback=self._on_bubble)

        self.tray = None
        self._window = None      # 私有：别让它出现在可被扫描的属性里
        self.on_event = None          # 前端可设：用于即时推送

        self.events = []
        self.events_lock = threading.Lock()
        self.refreshing = False
        self.refresh_done = 0
        self.refresh_total = 0
        self.refresh_started = 0.0
        self._pending_new = []        # 本次刷新产出的新文章
        self._last_poll = 0.0
        self._auto_last = 0.0
        self._save_pending = False
        self._reader_view = "all"
        self._reader_feed = None
        self._reader_group = None
        self._reader_query = ""
        self._reader_view = 'all'
        self._reader_feed = None
        self._reader_group = None
        self._reader_query = ''

        self.store.prune()
        # 托盘不在这里启动。参考可用的实现，托盘线程必须在
        # webview.create_window() 之后再起，否则容易被窗口层的
        # 初始化干扰。由 webview_app.run() 显式调用 start_tray()。

    # ==================================================================
    # 事件队列（前端用 poll() 取走）
    # ==================================================================
    def emit(self, kind, **payload):
        payload["kind"] = kind
        with self.events_lock:
            self.events.append(payload)
        callback = self.on_event
        if callable(callback):
            try:
                callback(payload)
            except Exception:
                pass

    def drain(self):
        with self.events_lock:
            items = list(self.events)
            self.events = []
        return items

    def poll(self):
        """前端轮询接口：回报刷新进度 + 事件队列。

        前端一直调的是 call('poll')，但 HTTP 通道下查找的是 Controller
        上的方法，而这里原本只有 drain()，于是每次轮询都返回
        "没有这个方法：poll" —— 刷新进度条不动、通知不弹、侧栏不刷新。
        HTTP 通道下也没有别的地方驱动自动刷新，所以顺带在这里 tick。
        """
        try:
            self.auto_refresh_tick()
        except Exception:
            pass
        try:
            for item in (self.poll_refresh() or []):
                self.emit(item.get("kind", ""), **{
                    k: v for k, v in item.items() if k != "kind"})
        except Exception:
            pass
        return self.drain()

    def _on_bubble(self, data):
        """通知器在没有系统通知能力时的兜底：交给页面画气泡。"""
        self.emit("bubble", title=data.get("title", ""),
                  message=data.get("message", ""))

    # ==================================================================
    # 托盘
    # ==================================================================
    def _migrate_settings(self):
        """旧版本把主题存在 theme_mode 下，统一成 theme。"""
        try:
            if "theme_mode" in self.settings._data \
                    and "theme" not in self.settings._data:
                self.settings.set("theme", self.settings._data["theme_mode"])
        except Exception:
            pass

    def start_tray(self):
        """启动托盘。必须在 webview.create_window() 之后调用。"""
        self._load_tray()
        return self.tray is not None

    def _load_tray(self):
        if not self.settings.get("minimize_to_tray", True):
            self._tray_reason = "设置里关闭了缩到托盘"
            return
        try:
            from .tray import TrayIcon
            icon = TrayIcon(self, self.settings)
            self.tray = icon
            # supported 是 property，不是方法。
            # 写成 icon.supported() 会拿 bool 去调用 → TypeError，
            # 而下面的 except 会把它吞掉 —— 托盘从来没启动成功过，
            # 却一直看起来"没报错"。这就是托盘不出现的真正原因。
            if icon.supported:
                if not icon.start():
                    self._tray_reason = "启动失败"
                    self.tray = None
                else:
                    self._tray_reason = ""
            else:
                self._tray_reason = icon.reason()
                self.tray = None
        except Exception as exc:
            self._tray_reason = "异常：%s: %s" % (type(exc).__name__, exc)
            self.tray = None

    def update_badge(self):
        if self.tray is None:
            return
        try:
            self.tray.update_badge(self.store.unnotified_count())
        except Exception:
            pass

    # ==================================================================
    # 初始数据
    # ==================================================================
    def bootstrap(self):
        """前端启动时拉一次全量状态。"""
        return {
            "app": {"name": config.APP_NAME,
                    "version": config.VERSION,
                    "repo": config.REPO_URL,
                    "python": sys.version.split()[0]},
            "settings": self.settings.as_dict(),
            "groups": list(self.store.groups),
            "feeds": [self.feed_brief(f) for f in self.store.feeds],
            "counts": self.store.counts_by_feed(),
            "unread_total": self.store.unread_count(),
            "starred_total": self.store.starred_count(),
            "unnotified": self.store.unnotified_count(),
            "backends": self.backend_info(),
            "tray": self.tray_state(),
        }

    def tray_state(self):
        """托盘当前状态，让界面能如实告诉用户有没有托盘。"""
        if self.tray is None:
            reason = getattr(self, "_tray_reason", "") or "未启用"
            return {"ok": False, "reason": reason}
        visible = False
        try:
            visible = bool(getattr(self.tray.icon, "visible", False))
        except Exception:
            pass
        return {"ok": True, "visible": visible}

    # ---- 窗口控制 ----
    # 这几个原本只定义在 webview_app.Api 里，只在 js_api 通道下可用。
    # 现在主通道是本地 HTTP，查找的是 Controller 上的方法，
    # 于是托盘菜单的「打开主窗口」「退出」点了没反应。
    def show_window(self):
        window = self._window
        if window is None:
            return False
        try:
            window.show()
            window.restore()
            return True
        except Exception:
            try:
                window.show()
                return True
            except Exception:
                return False

    def hide_window(self):
        window = self._window
        if window is None:
            return False
        try:
            window.hide()
            return True
        except Exception:
            return False

    def quit_app(self):
        try:
            self.flush_save()
        except Exception:
            pass
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        window = self._window
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass
        return True

    def resize_window(self, width=0, height=0):
        """按设置里的数值调整主窗口大小。

        设置面板改了窗口尺寸后要立刻看到效果，不能等下次启动。
        失败不影响调用方（窗口可能还没建好）。
        """
        window = self._window
        if window is None:
            return False
        try:
            width = int(width)
            height = int(height)
        except Exception:
            return False
        if width < 760 or height < 480:
            return False
        try:
            window.resize(width, height)
            return True
        except Exception:
            return False

    def check_update(self, timeout=10):
        """查 GitHub 上有没有新版本。

        前端直接 fetch 会被 CORS 挡（页面是 127.0.0.1，不是 GitHub
        同源），而且 IE11 根本没有 fetch，所以放在后端做。

        返回 dict：
            ok        是否成功请求到
            error     失败原因（成功时为空）
            current   当前版本
            latest    最新版本号
            has       是否有更新
            name      发布标题
            body      更新说明（Markdown 原文，前端渲染）
            url       发布页地址
            published 发布时间
        """
        result = {
            "ok": False, "error": "", "current": config.VERSION,
            "latest": "", "has": False, "name": "", "body": "",
            "url": config.REPO_URL, "published": "",
        }
        try:
            from . import fetcher
        except Exception as exc:
            result["error"] = "内部错误：%s" % exc
            return result

        try:
            # _http_get 返回 (status, content, headers)，失败是抛异常
            status, body, _headers = fetcher._http_get(
                config.RELEASES_API, timeout=timeout)
        except Exception as exc:
            result["error"] = "请求失败：%s" % exc
            return result

        if status >= 400:
            if status == 404:
                result["error"] = "仓库还没有发布任何版本"
            elif status == 403:
                result["error"] = "GitHub API 限流，请稍后再试"
            else:
                result["error"] = "请求失败（HTTP %s）" % status
            return result

        try:
            import json as _json
            data = _json.loads(body.decode("utf-8", "replace"))
        except Exception as exc:
            result["error"] = "返回内容无法解析：%s" % exc
            return result

        latest = str(data.get("tag_name") or data.get("name") or "").strip()
        result["ok"] = True
        result["latest"] = latest
        result["name"] = data.get("name") or latest
        result["body"] = data.get("body") or ""
        result["url"] = data.get("html_url") or config.REPO_URL
        result["published"] = data.get("published_at") or ""
        result["has"] = _version_newer(latest, config.VERSION)
        return result

    def backend_info(self):
        from .notifier import describe_backends
        try:
            return describe_backends()
        except Exception:
            return []

    def feed_brief(self, feed):
        # counts_by_feed 返回 [总数, 未读数]
        counts = self.store.counts_by_feed().get(feed.id) or [0, 0]
        return {
            "id": feed.id, "title": feed.title, "url": feed.url,
            "group": feed.group, "site_url": feed.site_url,
            "enabled": feed.enabled, "tags": list(feed.tags or []),
            "note": feed.note or "",
            "refresh_minutes": feed.refresh_minutes,
            "last_status": feed.last_status or "",
            "last_updated": feed.last_updated or 0,
            "last_error": feed.last_error or "",
            "custom_title": bool(feed.custom_title),
            "unread": counts[1],
            "total": counts[0],
        }

    def get_feeds(self):
        return [self.feed_brief(f) for f in self.store.feeds]

    # ==================================================================
    # 文章列表（分页，前端滚动加载）
    # ==================================================================
    def get_articles(self, view="all", feed_id=None, group=None,
                     query="", offset=0, limit=60):
        """按当前筛选条件取一页文章。

        分页是前端流畅的关键：一次只给一页，
        前端滚动到底再要下一页，不会一次性插几千个节点。
        """
        view = view or "all"
        # 记住当前上下文，阅读窗口翻篇时才能跟着主窗口的筛选走
        self._reader_view = view
        self._reader_feed = feed_id
        self._reader_group = group
        self._reader_query = query or ""
        articles = self.query_articles(view, feed_id, group, query)
        total = len(articles)
        page = articles[offset:offset + limit]
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [self.article_brief(a) for a in page],
        }

    def query_articles(self, view, feed_id, group, query):
        keywords = [k for k in (query or "").split() if k]
        if view == "starred":
            items = [a for a in self.store.articles.values() if a.starred]
        elif view == "unread":
            items = self.store.articles_for(unread_only=True)
        else:
            items = self.store.articles_for()

        if feed_id:
            items = [a for a in items if a.feed_id == feed_id]
        elif group:
            feeds = {f.id for f in self.store.feeds if f.group == group}
            items = [a for a in items if a.feed_id in feeds]

        if keywords:
            items = [a for a in items if a.matches(keywords)]
        items.sort(key=lambda a: (a.timestamp or 0), reverse=True)
        return items

    def article_brief(self, art):
        return {
            "id": art.id, "title": art.title, "link": art.link,
            "feed_id": art.feed_id, "feed_title": art.feed_title,
            "author": art.author, "published": art.published,
            "timestamp": art.timestamp or 0,
            "read": bool(art.read), "starred": bool(art.starred),
            "summary": (art.summary or "")[:200],
            "group": self.feed_group(art.feed_id),
        }

    def feed_group(self, feed_id):
        feed = self.store.feed_by_id(feed_id)
        return feed.group if feed else ""

    def get_article(self, article_id):
        art = self.store.article(article_id)
        if art is None:
            return None
        feed = self.store.feed_by_id(art.feed_id)
        return {
            "id": art.id, "title": art.title, "link": art.link,
            "feed_title": art.feed_title, "author": art.author,
            "published": art.published, "timestamp": art.timestamp or 0,
            "read": bool(art.read), "starred": bool(art.starred),
            "images": list(art.images or []),
            "body": self.article_body(art),
            "feed_url": feed.url if feed else "",
            "group": self.feed_group(art.feed_id),
        }

    def article_body(self, art):
        """正文原样输出。

        以前这里调 strip_html_basic，那个函数把所有标签剥掉只留纯文本，
        RSS 里的图片、链接、加粗全没了。现在改成保留原始 HTML。

        注意不能是 staticmethod —— 要读设置里的 sanitize_body。
        """
        from . import htmlutil
        body = art.content or ""
        if not body.strip():
            body = art.summary or ""
        if not body.strip():
            return ""
        return htmlutil.rich_body(
            body, sanitize=self.settings.get("sanitize_body", False))

    # ==================================================================
    # 订阅源增删改
    # ==================================================================
    def probe_feeds(self, text):
        """探测一批地址，返回每个地址的结果（不入库）。"""
        urls = opml.parse_url_list(text or "")
        results = []
        for url in urls:
            existing = self.store.feed_by_url(url)
            if existing:
                results.append({"url": url, "ok": False, "status": "已订阅",
                                "title": "（已存在）", "count": 0,
                                "message": "这个地址已经在订阅列表里"})
                continue
            try:
                info = probe_url(url, int(self.settings.get(
                    "refresh_timeout", 15) or 15))
            except Exception as exc:
                info = {"ok": False, "message": str(exc)}
            results.append({
                "url": url,
                "ok": bool(info.get("ok")),
                "status": "可用" if info.get("ok") else "失败",
                "title": info.get("title") or "",
                "count": info.get("count", 0),
                "site_url": info.get("site_url", ""),
                "resolved_url": info.get("resolved_url", url),
                "message": info.get("message", ""),
                "entries": len(info.get("entries") or []),
            })
        return results

    def add_feeds(self, urls, group=""):
        """加入订阅。urls 可以是地址列表，也可以是一整段文本（自动解析）。

        网络请求在后台跑，这里先把订阅源建好并返回。
        """
        if isinstance(urls, str):
            urls = opml.parse_url_list(urls)
        group = _clean(group)
        added = []
        for url in urls or []:
            url = _clean(url)
            if not url or self.store.feed_by_url(url):
                continue
            feed = Feed(url=url, title=url, group=group)
            saved = self.store.add_feed(feed)
            if saved is not None:
                added.append(self.feed_brief(saved))
        if group and group not in self.store.groups:
            self.store.groups.append(group)
        if added:
            self.store.save_subscriptions()
            self.emit("feeds_changed")
            # 立刻抓一次，把标题和文章填上
            self.refresh([f["id"] for f in added])
        return added

    def update_feed(self, feed_id, fields):
        feed = self.store.feed_by_id(feed_id)
        if feed is None:
            return None
        if "title" in fields:
            feed.title = _clean(fields["title"]) or feed.title
        if "group" in fields:
            group = _clean(fields["group"])
            feed.group = group
            if group and group not in self.store.groups:
                self.store.groups.append(group)
        if "url" in fields:
            url = _clean(fields["url"])
            if url and url != feed.url:
                feed.url = url
                # 换了地址，条件请求头必须清掉，否则拿旧 ETag 去问新地址
                feed.etag = ""
                feed.last_modified = ""
                feed.last_status = ""
        if "site_url" in fields:
            feed.site_url = _clean(fields["site_url"])
        if "note" in fields:
            feed.note = _clean(fields["note"])
        if "tags" in fields:
            feed.tags = list(fields["tags"] or [])
        if "enabled" in fields:
            feed.enabled = bool(fields["enabled"])
        if "refresh_minutes" in fields:
            try:
                feed.refresh_minutes = max(0, int(fields["refresh_minutes"]))
            except (TypeError, ValueError):
                pass
        self.store.save_subscriptions()
        self.emit("feeds_changed")
        return self.feed_brief(feed)

    def remove_feed(self, feed_id):
        self.store.remove_feed(feed_id)
        self.store.save_subscriptions()
        self.store.save_articles()
        self.emit("feeds_changed")
        return True

    # ------------------------------------------------------------------
    def add_group(self, name):
        name = _clean(name)
        if not name or name in self.store.groups:
            return list(self.store.groups)
        self.store.groups.append(name)
        self.store.save_subscriptions()
        self.emit("feeds_changed")
        return list(self.store.groups)

    def rename_group(self, old, new):
        self.store.rename_group(old, _clean(new))
        self.store.save_subscriptions()
        self.emit("feeds_changed")
        return list(self.store.groups)

    def remove_group(self, name, delete_feeds=False):
        self.store.remove_group(name, delete_feeds=bool(delete_feeds))
        self.store.save_subscriptions()
        self.store.save_articles()
        self.emit("feeds_changed")
        return list(self.store.groups)

    # ==================================================================
    # 已读 / 收藏
    # ==================================================================
    def mark_read(self, article_id, read=True):
        changed = self.store.mark_read(article_id, bool(read))
        if changed:
            # 用户看过就不该再弹通知了。以前这里只改 read，
            # notified 还留着 False，于是角标和通知都还挂着这篇。
            if read:
                self.store.mark_notified([article_id])
            self.schedule_save()
            self.update_badge()
        return changed

    def toggle_star(self, article_id):
        starred = self.store.toggle_star(article_id)
        self.schedule_save()
        return starred

    def mark_all_read(self, view="all", feed_id=None, group=None):
        """按当前可见范围标记已读。

        收藏视图下只清收藏的文章——以前这里会把整个范围都清掉，
        用户在收藏页点"全部已读"结果把别的未读也清了。
        """
        touched = []
        if view == "starred":
            count = 0
            for art in self.store.articles.values():
                if art.starred and not art.read:
                    if feed_id and art.feed_id != feed_id:
                        continue
                    if group and self.feed_group(art.feed_id) != group:
                        continue
                    self.store.mark_read(art.id, True)
                    touched.append(art.id)
                    count += 1
        else:
            count = self.store.mark_feed_read(feed_id, group)
            if count:
                # mark_feed_read 内部改了 read，这里要把范围里的
                # 文章 id 取出来同步 notified
                for art in self.store.articles.values():
                    if art.read and not art.notified:
                        if feed_id and art.feed_id != feed_id:
                            continue
                        if group and self.feed_group(art.feed_id) != group:
                            continue
                        touched.append(art.id)
        if count:
            # 一并清掉通知状态，否则角标不会归零
            if touched:
                self.store.mark_notified(touched)
            self.schedule_save()
            self.update_badge()
        return count
        if count:
            self.schedule_save()
            self.update_badge()
        return count

    # ==================================================================
    # 刷新
    # ==================================================================
    def refresh(self, feed_ids=None):
        if self.refreshing:
            return False
        started = self.refresher.start(feed_ids)
        if not started:
            return False
        self.refreshing = True
        self.refresh_done = 0
        self.refresh_total = len(feed_ids) if feed_ids else len(
            [f for f in self.store.feeds if f.enabled])
        self.refresh_started = time.time()
        self.emit("refresh_start", total=self.refresh_total)
        return True

    def cancel_refresh(self):
        self.refresher.cancel()
        return True

    def poll_refresh(self):
        """把后台刷新进度搬给前端。返回本次的事件列表。"""
        events = []
        if self.refreshing:
            for event in self.refresher.poll():
                kind = event.get("kind")
                if kind == "progress":
                    self.refresh_done = event.get("done", 0)
                    events.append({"kind": "refresh_progress",
                                   "done": self.refresh_done,
                                   "total": event.get("total", 0),
                                   "title": event.get("title", "")})
                elif kind == "finish":
                    self.refreshing = False
                    new_count = event.get("new_count", 0)
                    self.store.save_articles()
                    self.store.save_subscriptions()
                    self.update_badge()
                    events.append({"kind": "refresh_finish",
                                   "new_count": new_count,
                                   "seconds": round(
                                       time.time() - self.refresh_started, 1)})
                    self.emit("feeds_changed")
        return events

    def send_notifications(self, limit=None):
        """把未通知过的新文章发出去。"""
        if not self.settings.get("notify_enabled", True):
            return 0
        fresh = self.store.unnotified_articles(limit=limit)
        if not fresh:
            return 0
        notices = build_notifications(
            fresh,
            per_feed_limit=int(self.settings.get("notify_per_feed_limit", 3)),
            max_total=int(self.settings.get("notify_max_total", 8)))
        sent = 0
        for notice in notices:
            backend = self.notifier.notify(
                notice["title"], notice["message"], notice.get("url", ""))
            if backend:
                sent += 1
        marked = [a.id for a in fresh[:len(notices)]]
        if notices:
            self.store.mark_notified([a.id for a in fresh])
            self.store.save_state()
            self.update_badge()
        return sent

    def test_notification(self):
        backend = self.notifier.notify(
            config.APP_NAME, "这是一条测试通知", "")
        return {"backend": backend, "error": self.notifier.last_error}

    # ------------------------------------------------------------------
    def auto_refresh_tick(self):
        """定时触发：到点的源自动刷新 + 发通知。"""
        now = time.time()
        interval = float(self.settings.get("auto_refresh_interval_min", 5) or 5)
        if interval <= 0:
            return
        if now - self._auto_last < interval * 60:
            return
        self._auto_last = now
        due = self.store.due_feed_ids(now)
        if due and not self.refreshing:
            self.refresh(due)
        elif not self.refreshing:
            self.refresh()

    # ==================================================================
    # OPML
    # ==================================================================
    def export_opml(self, path):
        try:
            opml.export_opml(self.store.feeds, path)
            return {"ok": True, "path": path}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def import_opml(self, path, group=""):
        try:
            items = opml.parse_opml_file(path)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "added": 0}
        added = self.add_feeds([i["url"] for i in items],
                              group=group or "")
        return {"ok": True, "added": len(added), "total": len(items)}

    def opml_text(self):
        try:
            return opml.dumps(self.store.feeds)
        except Exception as exc:
            return "<!-- 导出失败：%s -->" % exc

    # ==================================================================
    # 外部动作
    # ==================================================================
    def open_url(self, url):
        """只放行 http/https，别把 feed 里的任意协议丢给系统。"""
        url = _clean(url)
        if not url:
            return False
        if not url.startswith(("http://", "https://")):
            return False
        try:
            webbrowser.open(url)
            return True
        except Exception:
            return False

    def open_reader(self, article_id):
        return self.get_article(article_id)

    def reader_prev_next(self, article_id, delta=1):
        """阅读页翻上一篇 / 下一篇。

        跟着主窗口当前的筛选走（视图、订阅源、分组、搜索词），
        否则在"未读"里翻篇会翻到已读的文章去。
        """
        items = self.query_articles(
            getattr(self, "_reader_view", "all"),
            getattr(self, "_reader_feed", None),
            getattr(self, "_reader_group", None),
            getattr(self, "_reader_query", ""))
        ids = [item.id for item in items]
        try:
            index = ids.index(article_id)
        except ValueError:
            # 当前文章不在筛选结果里（比如刚标了未读），退回全量列表
            items = self.query_articles("all", None, None, "")
            ids = [item.id for item in items]
            try:
                index = ids.index(article_id)
            except ValueError:
                return None
        target = index + (1 if (delta or 0) >= 0 else -1)
        if target < 0 or target >= len(ids):
            return None
        return self.get_article(ids[target])

    def copy_link(self, text):
        """复制到系统剪贴板。

        不依赖 GUI 框架：Windows 用自带的 clip.exe，
        Linux 退回到 xclip / xsel，都没有就返回 False。
        """
        import subprocess
        text = _clean(text)
        if not text:
            return False
        if os.name == "nt" or sys.platform.startswith("win"):
            try:
                proc = subprocess.Popen(["clip"], stdin=subprocess.PIPE)
                proc.communicate(text.encode("utf-16-le"))
                return proc.returncode == 0
            except Exception:
                return False
        for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard"]):
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                proc.communicate(text.encode("utf-8"))
                if proc.returncode == 0:
                    return True
            except Exception:
                continue
        return False

    # ==================================================================
    # 设置与保存
    # ==================================================================
    def save_settings(self, data):
        if isinstance(data, dict):
            self.settings.update(data)
            self.settings.save()
            self.notifier.settings = self.settings
        return self.settings.as_dict()

    def schedule_save(self):
        """延迟写盘：连续点已读不会每次都全量写一次。"""
        if self._save_pending:
            return
        self._save_pending = True

        def worker():
            time.sleep(1.2)
            self._save_pending = False
            try:
                self.store.save_articles()
                self.store.save_state()
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def flush_save(self):
        try:
            self.store.save()
        except Exception:
            pass

    def stop_all(self):
        """真正退出前收尾：停托盘、停轮询、落盘。

        托盘不可用时关闭窗口走的就是这条路径——不能只把窗口藏起来，
        否则程序变成没有入口的后台进程，用户以为没关掉。
        """
        try:
            self.flush_save()
        except Exception:
            pass
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        try:
            if getattr(self, "refresher", None) is not None:
                self.refresher.cancel()
        except Exception:
            pass
        try:
            if getattr(self, "server", None) is not None:
                self.server.stop()
        except Exception:
            pass
        return True

    def clear_articles(self, keep_starred=True):
        """清空文章。

        必须同时清掉每个源的 etag / last_modified：否则下次"检查更新"
        会带着旧的条件头请求，服务器回 304 Not Modified，正文就再也
        抓不回来了 —— 清完反而变成空的。
        """
        self.store.clear_articles(keep_starred=bool(keep_starred))
        for feed in self.store.feeds:
            feed.etag = ""
            feed.last_modified = ""
            feed.last_status = ""
        self.store._dirty = True
        self.store.save_articles()
        self.store.save()
        return True

    def stats(self):
        return {
            "feeds": len(self.store.feeds),
            "articles": len(self.store.articles),
            "unread": self.store.unread_count(),
            "starred": self.store.starred_count(),
            "unnotified": self.store.unnotified_count(),
        }
