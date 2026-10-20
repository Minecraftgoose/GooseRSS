# -*- coding: utf-8 -*-
"""订阅源 / 文章数据模型与本地存储。

数据放在用户目录（Windows 为 %APPDATA%\\GooseRSS）：
    subscriptions.json  订阅源与分组
    articles.json       文章缓存（含已读 / 收藏状态）
    settings.json       设置项
"""

import json
import os
import threading
import time
import uuid

from . import config


def new_id(prefix="f"):
    return prefix + "-" + uuid.uuid4().hex[:12]


def now_ts():
    return time.time()


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


class Feed(object):
    """一个订阅源（博客 / 站点 / 播客的 RSS、Atom、RDF 或 JSON Feed）。"""

    def __init__(self, url, title="", group="", site_url="", tags=None,
                 feed_id=None, note="", enabled=True, refresh_minutes=0,
                 custom_title=False):
        self.id = feed_id or new_id("f")
        self.url = (url or "").strip()
        self.title = (title or "").strip() or self.url
        self.group = (group or "").strip()
        self.site_url = (site_url or "").strip()
        self.tags = list(tags or [])
        self.note = note or ""
        self.enabled = bool(enabled)
        self.refresh_minutes = int(refresh_minutes or 0)
        # 用户手动改过标题后为 True，刷新时不再被服务端标题覆盖
        self.custom_title = bool(custom_title)
        # 运行状态
        self.etag = ""
        self.last_modified = ""
        self.last_updated = 0.0
        self.last_status = ""        # ok / error / pending
        self.last_error = ""
        self.added = now_ts()

    # ------------------------------------------------------------------
    def to_dict(self):
        return {
            "id": self.id, "url": self.url, "title": self.title,
            "group": self.group, "site_url": self.site_url, "tags": self.tags,
            "note": self.note, "enabled": self.enabled,
            "refresh_minutes": self.refresh_minutes,
            "custom_title": self.custom_title, "etag": self.etag,
            "last_modified": self.last_modified,
            "last_updated": self.last_updated, "last_status": self.last_status,
            "last_error": self.last_error, "added": self.added,
        }

    @classmethod
    def from_dict(cls, data):
        feed = cls(
            url=data.get("url", ""), title=data.get("title", ""),
            group=data.get("group", ""), site_url=data.get("site_url", ""),
            tags=data.get("tags") or [], feed_id=data.get("id"),
            note=data.get("note", ""),
            enabled=data.get("enabled", True),
            refresh_minutes=data.get("refresh_minutes", 0),
            custom_title=data.get("custom_title", False),
        )
        feed.etag = data.get("etag", "") or ""
        feed.last_modified = data.get("last_modified", "") or ""
        feed.last_updated = float(data.get("last_updated", 0) or 0)
        feed.last_status = data.get("last_status", "") or ""
        feed.last_error = data.get("last_error", "") or ""
        feed.added = float(data.get("added", now_ts()) or now_ts())
        return feed

    @property
    def label(self):
        return self.title or self.url


class Article(object):
    """一篇已抓取的文章。"""

    def __init__(self, feed_id, feed_title, title, link, author="",
                 published="", summary="", content="", article_id=None,
                 timestamp=0.0, images=None):
        self.id = article_id or new_id("a")
        self.feed_id = feed_id
        self.feed_title = feed_title or ""
        self.title = (title or "").strip() or "(无标题)"
        self.link = (link or "").strip()
        self.author = author or ""
        self.published = published or ""
        self.summary = summary or ""
        self.content = content or ""
        self.images = list(images or [])
        self.timestamp = float(timestamp or 0.0)
        self.read = False
        self.starred = False
        # 是否已经弹过通知。和 read 分开：一篇新文章要提醒一次，
        # 不能因为用户没点开就每次轮询都重复弹。
        self.notified = False
        self.added = now_ts()

    # ------------------------------------------------------------------
    def to_dict(self, include_state=True):
        data = {
            "id": self.id, "feed_id": self.feed_id,
            "feed_title": self.feed_title, "title": self.title,
            "link": self.link, "author": self.author,
            "published": self.published, "summary": self.summary,
            "content": self.content, "images": self.images,
            "timestamp": self.timestamp, "added": self.added,
        }
        if include_state:
            data["read"] = self.read
            data["starred"] = self.starred
            data["notified"] = self.notified
        return data

    @classmethod
    def from_dict(cls, data):
        art = cls(
            feed_id=data.get("feed_id", ""), feed_title=data.get("feed_title", ""),
            title=data.get("title", ""), link=data.get("link", ""),
            author=data.get("author", ""), published=data.get("published", ""),
            summary=data.get("summary", ""), content=data.get("content", ""),
            article_id=data.get("id"), timestamp=data.get("timestamp", 0),
            images=data.get("images") or [],
        )
        art.read = bool(data.get("read", False))
        art.starred = bool(data.get("starred", False))
        art.notified = bool(data.get("notified", False))
        art.added = float(data.get("added", now_ts()) or now_ts())
        return art

    @property
    def body(self):
        return self.content or self.summary or ""

    def matches(self, keywords):
        """关键词搜索：标题 / 摘要 / 正文 / 作者 / 来源（全部命中才算匹配）。"""
        if not keywords:
            return True
        haystack = " ".join([
            self.title, self.summary, self.content, self.author,
            self.feed_title,
        ]).lower()
        for word in keywords:
            if word.lower() not in haystack:
                return False
        return True


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------


class Store(object):
    """订阅源 + 文章缓存 + 设置的一体化存储。"""

    def __init__(self, data_dir=None, settings=None):
        self.data_dir = data_dir or config.data_dir()
        if not os.path.isdir(self.data_dir):
            try:
                os.makedirs(self.data_dir)
            except OSError:
                pass
        self.subscriptions_path = os.path.join(self.data_dir, "subscriptions.json")
        self.articles_path = os.path.join(self.data_dir, "articles.json")
        self.state_path = os.path.join(self.data_dir, "state.json")
        self.settings = settings or config.Settings(
            os.path.join(self.data_dir, "settings.json"))
        self.feeds = []
        self.groups = []
        self.articles = {}      # id -> Article
        self._by_feed = {}      # feed_id -> {article_id: Article}，避免全表扫描
        self._dirty = False
        self._state_dirty = False
        self._articles_dirty = False
        # 后台刷新线程会改 Store，主线程同时读，用锁保护复合操作
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------
    def load(self):
        self.feeds = []
        raw = self._read_json(self.subscriptions_path, default={})
        for item in raw.get("feeds", []) or []:
            try:
                self.feeds.append(Feed.from_dict(item))
            except Exception:
                continue
        self.groups = list(raw.get("groups", []) or [])
        self.articles = {}
        self._by_feed = {}
        raw_articles = self._read_json(self.articles_path, default={})
        items = raw_articles.get("articles", []) if isinstance(raw_articles, dict) else []
        for item in items:
            try:
                art = Article.from_dict(item)
                self.articles[art.id] = art
                self._by_feed.setdefault(art.feed_id, {})[art.id] = art
            except Exception:
                continue
        # 已读 / 收藏状态单独存，覆盖文章文件里的值（兼容旧格式）
        raw_state = self._read_json(self.state_path, default={})
        states = raw_state.get("state", {}) if isinstance(raw_state, dict) else {}
        for art_id, mask in states.items():
            art = self.articles.get(art_id)
            if art is None:
                continue
            try:
                mask = int(mask)
            except (TypeError, ValueError):
                continue
            art.read = bool(mask & 1)
            art.starred = bool(mask & 2)
            art.notified = bool(mask & 4)
        self._normalize_groups()
        # 设置里调小上限后，旧数据也要立刻生效，不用等下次抓取
        self.prune()
        return self

    def save(self):
        """全量保存（订阅源 + 文章 + 状态 + 设置）。

        日常操作（标记已读 / 收藏）请改用 save_state()，
        它只写很小的状态文件，避免每次都重写全部文章正文。
        """
        self.save_subscriptions()
        self.save_articles()
        self.save_state()
        self.settings.save()
        self._dirty = False

    def save_subscriptions(self):
        self._write_json(self.subscriptions_path, {
            "version": 1,
            "app": config.APP_ID,
            "updated": now_ts(),
            "groups": self.groups,
            "feeds": [f.to_dict() for f in self.feeds],
        })

    def save_articles(self):
        """只写文章正文（抓取后调用）。"""
        # 正文字段不写 read / starred，它们单独存在 state.json 里，
        # 这样标记已读就不必重写几千篇正文。
        self._write_json(self.articles_path, {
            "version": 2,
            "count": len(self.articles),
            "articles": [a.to_dict(include_state=False)
                         for a in self.articles.values()],
        })
        self._articles_dirty = False

    def save_state(self):
        """只写已读 / 收藏状态（文件很小，毫秒级）。"""
        data = {}
        for art_id, art in self.articles.items():
            mask = ((1 if art.read else 0) | (2 if art.starred else 0) |
                    (4 if art.notified else 0))
            if mask:
                data[art_id] = mask
        self._write_json(self.state_path, {
            "version": 2,
            "state": data,
        })
        self._state_dirty = False

    @staticmethod
    def _read_json(path, default=None):
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
        except Exception:
            pass
        return default if default is not None else {}

    @staticmethod
    def _write_json(path, data):
        """写 JSON。

        性能要点：先 dumps 成字符串再一次写入。
        直接用 json.dump(fh) 会把 5MB 数据拆成几千次小 write，
        实测比一次性写慢近百倍（3.9s vs 43ms）。
        """
        try:
            folder = os.path.dirname(path)
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
            # 紧凑写法：不带缩进，文件更小、序列化更快
            text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            # os.replace 是原子替换：任何时刻 path 都指向一份完整数据，
            # 不会出现「旧文件已删、新文件还没改名」的窗口期
            os.replace(tmp, path)
            return True
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return False

    # ------------------------------------------------------------------
    # 订阅源
    # ------------------------------------------------------------------
    def _normalize_groups(self):
        known = list(self.groups)
        for feed in self.feeds:
            if feed.group and feed.group not in known:
                known.append(feed.group)
        self.groups = known

    def add_feed(self, feed):
        if not feed.url:
            return None
        for existing in self.feeds:
            if existing.url.rstrip("/") == feed.url.rstrip("/"):
                # 已存在则补全信息，不重复添加
                if feed.title and not existing.title:
                    existing.title = feed.title
                if feed.group:
                    existing.group = feed.group
                return existing
        self.feeds.append(feed)
        if feed.group and feed.group not in self.groups:
            self.groups.append(feed.group)
        self._dirty = True
        return feed

    def add_feeds(self, feeds):
        added = []
        for feed in feeds:
            result = self.add_feed(feed)
            if result is not None and result not in added:
                added.append(result)
        return added

    def remove_feed(self, feed_id):
        feed = self.feed_by_id(feed_id)
        if feed is None:
            return False
        self.feeds = [f for f in self.feeds if f.id != feed_id]
        # 用索引定位，避免遍历整张文章表
        bucket = self._by_feed.pop(feed_id, {})
        for art_id in bucket:
            self.articles.pop(art_id, None)
        self._dirty = True
        self._articles_dirty = True
        return True

    def _rebuild_index(self):
        """重建 feed -> 文章 索引（批量改动后调用）。"""
        self._by_feed = {}
        for art in self.articles.values():
            self._by_feed.setdefault(art.feed_id, {})[art.id] = art

    def feed_by_id(self, feed_id):
        for feed in self.feeds:
            if feed.id == feed_id:
                return feed
        return None

    def feed_by_url(self, url):
        target = (url or "").rstrip("/")
        for feed in self.feeds:
            if feed.url.rstrip("/") == target:
                return feed
        return None

    def rename_group(self, old, new):
        if not new or old == new:
            return
        for feed in self.feeds:
            if feed.group == old:
                feed.group = new
        self.groups = [new if g == old else g for g in self.groups]
        if new not in self.groups:
            self.groups.append(new)
        self._dirty = True

    def remove_group(self, name, delete_feeds=False):
        if delete_feeds:
            for feed in list(self.feeds):
                if feed.group == name:
                    self.remove_feed(feed.id)
        else:
            for feed in self.feeds:
                if feed.group == name:
                    feed.group = ""
        self.groups = [g for g in self.groups if g != name]
        self._dirty = True

    # ------------------------------------------------------------------
    # 文章
    # ------------------------------------------------------------------
    def upsert_articles(self, feed_id, entries, feed_title=""):
        """写入抓取到的文章，返回新增条数（供后台线程调用，带锁）。"""
        with self._lock:
            return self._upsert_locked(feed_id, entries, feed_title)

    def _upsert_locked(self, feed_id, entries, feed_title):
        added = 0
        seen = set()
        for entry in entries:
            link = (entry.get("link") or "").strip()
            title = (entry.get("title") or "").strip()
            key = link or title
            if not key:
                continue
            art_id = self._article_id(feed_id, key)
            seen.add(art_id)
            existing = self.articles.get(art_id)
            if existing is not None:
                # 已存在：只更新可能被修正的字段，保留已读 / 收藏状态
                if title:
                    existing.title = title
                if entry.get("summary"):
                    existing.summary = entry["summary"]
                if entry.get("content"):
                    existing.content = entry["content"]
                if entry.get("published"):
                    existing.published = entry["published"]
                if entry.get("timestamp"):
                    existing.timestamp = float(entry["timestamp"])
                if entry.get("author"):
                    existing.author = entry["author"]
                if feed_title:
                    existing.feed_title = feed_title
                continue
            self.articles[art_id] = Article(
                feed_id=feed_id,
                feed_title=entry.get("feed_title") or feed_title,
                title=title, link=link, author=entry.get("author", ""),
                published=entry.get("published", ""),
                summary=entry.get("summary", ""),
                content=entry.get("content", ""),
                article_id=art_id, timestamp=entry.get("timestamp", 0),
                images=entry.get("images") or [],
            )
            self._by_feed.setdefault(feed_id, {})[art_id] = self.articles[art_id]
            added += 1
        self.prune(feed_id)
        self._dirty = True
        self._articles_dirty = True
        return added

    @staticmethod
    def _article_id(feed_id, key):
        import hashlib
        digest = hashlib.md5((feed_id + "|" + key).encode("utf-8", "replace")).hexdigest()
        return "a-" + digest[:20]

    def prune(self, feed_id=None):
        """按设置裁剪缓存，避免无限增长。

        性能要点：只有真的超限时才排序。以前每次抓取后都无条件全表排序，
        订阅一多就成了 O(n²)。
        """
        per_feed = int(self.settings.get("max_articles_per_feed", 200) or 200)
        total = int(self.settings.get("max_articles_total", 5000) or 5000)
        if feed_id and per_feed > 0:
            bucket = self._by_feed.get(feed_id, {})
            if len(bucket) > per_feed:
                items = self._prune_sorted(bucket.values(), per_feed)
                for art in items:
                    self._discard(art)
        if total > 0 and len(self.articles) > total:
            items = self._prune_sorted(self.articles.values(), total)
            for art in items:
                self._discard(art)

    @staticmethod
    def _prune_sorted(articles, keep):
        """按时间倒序挑出要删除的文章，收藏的永远不动。

        清理缓存时保留收藏，自动裁剪却把收藏删掉，属于逻辑分裂；
        这里统一：收藏过的文章不会被裁剪。
        """
        items = sorted(articles, key=lambda a: (a.timestamp or a.added),
                       reverse=True)
        doomed = [a for a in items[keep:] if not a.starred]
        # 如果收藏件数本身就超过上限，就只能按时间保留最新的那批
        if len(items) - len(doomed) > keep and not doomed:
            doomed = items[keep:]
        return doomed

    def _discard(self, art):
        """从主表和索引里同时移除一篇文章。"""
        self.articles.pop(art.id, None)
        bucket = self._by_feed.get(art.feed_id)
        if bucket is not None:
            bucket.pop(art.id, None)

    def articles_for(self, feed_id=None, group=None, unread_only=False,
                     starred_only=False, keywords=None, limit=None):
        """按条件查询文章，默认按时间倒序。"""
        if group is not None:
            feed_ids = set(f.id for f in self.feeds
                           if (f.group or "") == group and f.enabled)
        elif feed_id:
            feed_ids = set([feed_id])
        else:
            feed_ids = set(f.id for f in self.feeds if f.enabled)
        result = []
        # 走 feed 索引，避免每次都扫描整张文章表
        for fid in feed_ids:
            for art in self._by_feed.get(fid, {}).values():
                if unread_only and art.read:
                    continue
                if starred_only and not art.starred:
                    continue
                if keywords and not art.matches(keywords):
                    continue
                result.append(art)
        result.sort(key=lambda a: (a.timestamp or a.added, a.added), reverse=True)
        if limit:
            result = result[:limit]
        return result

    def article(self, article_id):
        with self._lock:
            return self.articles.get(article_id)

    def unnotified_articles(self, limit=None):
        """取出还没弹过通知的文章，按时间倒序。

        这些是新文章提醒的候选集。注意和「未读」是两回事：
        未读 = 用户没看；未通知 = 还没提醒过。
        """
        with self._lock:
            items = [a for a in self.articles.values() if not a.notified]
            items.sort(key=lambda a: (a.timestamp or a.added, a.added),
                       reverse=True)
            return items[:limit] if limit else items

    def mark_notified(self, article_ids):
        """把一批文章标记为已通知（已经弹过提醒，之后不再重复弹）。"""
        with self._lock:
            count = 0
            for art_id in article_ids:
                art = self.articles.get(art_id)
                if art is not None and not art.notified:
                    art.notified = True
                    count += 1
            if count:
                self._dirty = True
                self._state_dirty = True
            return count

    def unnotified_count(self):
        """托盘角标用的「新文章」数。

        notified 只表示"弹过通知"，read 表示"用户看过了"。
        两者是独立状态，但角标必须同时排除：用户在界面里读了文章
        却没弹过通知时，notified 仍是 False，只看 notified 的话
        角标会一直挂着降不下来（点"全部已读"也不清零）。
        """
        with self._lock:
            return sum(1 for a in self.articles.values()
                       if not a.notified and not a.read)

    def due_feed_ids(self, now=None):
        """返回「到点该刷新」的订阅源 id。

        每个源可以有自己的周期（refresh_minutes，0 表示跟随全局、每次都刷）。
        以前这个字段只是个装饰品，现在自动刷新会真的按它来。
        """
        now = now if now is not None else now_ts()
        due = []
        for feed in self.feeds:
            if not feed.enabled:
                continue
            minutes = int(getattr(feed, "refresh_minutes", 0) or 0)
            if minutes <= 0:
                due.append(feed.id)
                continue
            elapsed = now - (feed.last_updated or 0)
            if elapsed >= minutes * 60:
                due.append(feed.id)
        return due

    def mark_read(self, article_id, read=True):
        art = self.articles.get(article_id)
        if art is None:
            return False
        if art.read != bool(read):
            art.read = bool(read)
            self._dirty = True
            self._state_dirty = True
        return True

    def toggle_star(self, article_id):
        art = self.articles.get(article_id)
        if art is None:
            return False
        art.starred = not art.starred
        self._dirty = True
        self._state_dirty = True
        return art.starred

    def mark_feed_read(self, feed_id=None, group=None):
        count = 0
        for art in self.articles_for(feed_id=feed_id, group=group,
                                     unread_only=True):
            art.read = True
            count += 1
        if count:
            self._dirty = True
            self._state_dirty = True
        return count

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    def unread_count(self, feed_id=None, group=None):
        return len(self.articles_for(feed_id=feed_id, group=group,
                                     unread_only=True))

    def starred_count(self):
        return len([a for a in self.articles.values() if a.starred])

    def counts_by_feed(self):
        """返回 {feed_id: (总数, 未读数)}。"""
        stats = {}
        for feed in self.feeds:
            stats[feed.id] = [0, 0]
        for feed_id, bucket in self._by_feed.items():
            if feed_id not in stats:
                stats[feed_id] = [0, 0]
            entry = stats[feed_id]
            entry[0] += len(bucket)
            entry[1] += sum(1 for a in bucket.values() if not a.read)
        return stats

    def group_stats(self):
        """返回 [(分组名, 订阅源数, 未读数)]。"""
        stats = {}
        for feed in self.feeds:
            key = feed.group or ""
            if key not in stats:
                stats[key] = [0, 0]
            stats[key][0] += 1
        counts = self.counts_by_feed()
        for feed in self.feeds:
            key = feed.group or ""
            stats[key][1] += counts.get(feed.id, [0, 0])[1]
        return [(key, value[0], value[1]) for key, value in stats.items()]

    def clear_articles(self, keep_starred=True):
        if keep_starred:
            self.articles = {k: v for k, v in self.articles.items() if v.starred}
        else:
            self.articles = {}
        self._rebuild_index()
        self._dirty = True
        self._articles_dirty = True
