# -*- coding: utf-8 -*-
"""订阅源抓取。

- requests 负责 HTTP（带 UA、ETag / Last-Modified 条件请求、超时），
  万一环境里没有 requests 就用标准库 urllib 兜底
- feedparser 负责解析 RSS / Atom / RDF / JSON Feed
- 后台刷新用线程池 + 队列，主线程通过队列更新界面，避免界面卡死
"""

import calendar
import os
import time
import threading
import queue
from concurrent.futures import ThreadPoolExecutor

from . import config, htmlutil

try:
    import requests
    HAS_REQUESTS = True
except Exception:  # pragma: no cover
    requests = None
    HAS_REQUESTS = False

try:
    import feedparser
    HAS_FEEDPARSER = True
except Exception:  # pragma: no cover
    feedparser = None
    HAS_FEEDPARSER = False

# 单次响应最大读取量，防止异常 / 恶意 feed 把内存打满
MAX_CONTENT_BYTES = int(os.environ.get("FLUENT_RSS_MAX_BYTES", 8 * 1024 * 1024))

DEFAULT_HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, "
              "text/xml, application/json, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}


# ---------------------------------------------------------------------------
# 单源抓取
# ---------------------------------------------------------------------------


def _http_get(url, timeout=15, etag="", last_modified=""):
    """返回 (status_code, content_bytes, headers_dict)；失败抛异常。"""
    headers = dict(DEFAULT_HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    if HAS_REQUESTS:
        response = requests.get(url, headers=headers, timeout=timeout,
                                allow_redirects=True, stream=True)
        try:
            # 必须走 iter_content，不能读 response.raw：
            # raw 是未解压的原始流，gzip / br 压缩的站点会拿到二进制乱码，
            # feedparser 就会报 "not well-formed (invalid token)"。
            content = _read_capped(response.iter_content(65536))
            return response.status_code, content, dict(response.headers)
        finally:
            try:
                response.close()
            except Exception:
                pass

    import urllib.request
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as fh:
            raw = fh.read(MAX_CONTENT_BYTES)
            # urllib 同样不自动解压，按 Content-Encoding 自己处理
            return fh.getcode(), _decode_body(raw, fh.headers), \
                dict(fh.headers.items())
    except urllib.error.HTTPError as err:  # type: ignore
        return err.code, b"", dict(err.headers.items() if err.headers else {})


def _read_capped(stream):
    """按上限读取流，超过就截断（而不是无脑 read() 全部读进内存）。"""
    chunks = []
    total = 0
    try:
        for chunk in stream:
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_CONTENT_BYTES:
                break
    except Exception:
        pass
    return b"".join(chunks)


def _decode_body(raw, headers):
    """按 Content-Encoding 解压；不认识或解压失败就原样返回。"""
    encoding = ""
    try:
        encoding = (headers.get("Content-Encoding") or "").strip().lower()
    except Exception:
        encoding = ""
    if not encoding or encoding == "identity":
        return raw
    try:
        import gzip
        import zlib
        if encoding == "gzip":
            return gzip.decompress(raw)
        if encoding == "deflate":
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
        if encoding == "br":
            try:
                import brotli
                return brotli.decompress(raw)
            except Exception:
                return raw
    except Exception:
        return raw
    return raw


def looks_like_html(content):
    """判断返回的是不是网页（而不是订阅源 XML）。"""
    if not content:
        return False
    try:
        head = content[:512].lstrip(b"\xef\xbb\xbf").lstrip().lower()
    except Exception:
        return False
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def discover_feed_urls(content, base_url=""):
    """从网页里找 <link rel="alternate" type="application/rss+xml"> 的地址。

    用户经常直接粘博客首页而不是订阅地址，这时候告诉他真正的 feed 在哪，
    比甩一句"解析失败"有用得多。
    """
    import re
    from urllib.parse import urljoin
    try:
        text = content.decode("utf-8", "replace")[:400000]
    except Exception:
        return []
    pattern = re.compile(
        r"<link\b[^>]*type=[\"']?(?:application/rss\+xml|application/atom\+xml|"
        r"application/rdf\+xml|text/xml|application/xml)[\"']?[^>]*>",
        re.I)
    urls = []
    for tag in pattern.findall(text):
        match = re.search(r"href\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
                          tag, re.I)
        if not match:
            continue
        href = match.group(1) or match.group(2) or match.group(3) or ""
        href = href.strip()
        if not href:
            continue
        if base_url:
            try:
                href = urljoin(base_url, href)
            except Exception:
                pass
        if href.startswith(("http://", "https://")) and href not in urls:
            urls.append(href)
        if len(urls) >= 5:
            break
    return urls


def _entry_time(entry):
    """取条目时间戳。

    feedparser 的 *_parsed 是 UTC 的 struct_time，必须用 calendar.timegm
    按 UTC 解释；用 time.mktime 会按本地时区解释，东八区会整体偏 8 小时。
    """
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            try:
                return float(calendar.timegm(tuple(value)[:9]))
            except Exception:
                continue
    return 0.0


def _entry_date(entry):
    value = entry.get("published") or entry.get("updated") or ""
    return (value or "").strip()


def _entry_summary(entry):
    """优先取完整正文，其次取摘要。"""
    html_body = ""
    if entry.get("content"):
        try:
            html_body = entry["content"][0].get("value", "") or ""
        except Exception:
            html_body = ""
    if not html_body:
        html_body = entry.get("summary_detail", {}).get("value", "") or \
            entry.get("summary", "") or ""
    return html_body


def parse_feed_bytes(content, fallback_url="", feed_title=""):
    """用 feedparser 解析字节流，返回 (meta, entries)。"""
    if not HAS_FEEDPARSER:
        raise RuntimeError("缺少 feedparser 库，无法解析订阅源")
    parsed = feedparser.parse(content)
    if getattr(parsed, "bozo", 0) and not getattr(parsed, "entries", None):
        message = ""
        try:
            message = str(parsed.bozo_exception)
        except Exception:
            message = "无法解析的内容"
        raise ValueError(message or "无法解析的内容")

    feed_info = getattr(parsed, "feed", {}) or {}
    meta = {
        "title": (feed_info.get("title") or "").strip(),
        "site_url": (feed_info.get("link") or "").strip(),
        "description": (feed_info.get("subtitle") or
                        feed_info.get("description") or "").strip(),
        "etag": (getattr(parsed, "etag", "") or
                 feed_info.get("etag", "") or ""),
        "modified": (getattr(parsed, "modified", "") or
                     feed_info.get("updated", "") or ""),
    }
    title = meta["title"] or feed_title

    # 补全图片地址要用的基准：站点地址优先，退回 feed 地址。
    # 没有它，正文里的相对路径图片（src="/wp-content/..."）在本地
    # 页面上会解析成 127.0.0.1，永远加载不出来。
    base_url = meta.get("site_url") or fallback_url or ""

    entries = []
    for entry in getattr(parsed, "entries", []) or []:
        link = entry.get("link") or ""
        if not link and entry.get("id"):
            link = entry.get("id")
        # 原始内容：可能是 HTML，也可能是 Markdown 源文本。
        # 以前这里直接 html_to_text 压成纯文本，图片、链接、标题
        # 全没了（那段逻辑是给只能显示纯文本的 Tk 界面写的）。
        # 现在存原样，由前端按 HTML / Markdown 渲染。
        raw_content = _entry_summary(entry)
        # 先把图片地址补全成绝对 URL（相对路径在本地页面上加载不了）
        raw_content = htmlutil.absolutize_images(raw_content,
                                                 base_url or link)
        # 摘要仍用纯文本：列表页要显示一行预览，带标签会乱
        plain = htmlutil.html_to_text(raw_content)
        summary_text = htmlutil.summarize(plain, 240)
        entries.append({
            "title": (entry.get("title") or "").strip(),
            "link": link,
            "author": (entry.get("author") or "").strip(),
            "published": _entry_date(entry),
            "timestamp": _entry_time(entry),
            "summary": summary_text,
            "content": raw_content,
            "images": htmlutil.extract_images(raw_content),
            "feed_title": title,
        })
    return meta, entries


def fetch_feed(feed, timeout=15):
    """抓取单个订阅源。

    返回 dict：
        {"ok": bool, "status": "ok"/"not_modified"/"error",
         "error": str, "meta": {...}, "entries": [...]}
    """
    result = {"ok": False, "status": "error", "error": "", "meta": {},
              "entries": []}
    if not HAS_FEEDPARSER:
        result["error"] = "未安装 feedparser"
        return result
    url = (feed.url or "").strip()
    if not url:
        result["error"] = "地址为空"
        return result
    try:
        status, content, headers = _http_get(
            url, timeout=timeout, etag=getattr(feed, "etag", "") or "",
            last_modified=getattr(feed, "last_modified", "") or "")
    except Exception as exc:
        result["error"] = "网络错误：%s" % exc
        return result

    if status == 304:
        result["ok"] = True
        result["status"] = "not_modified"
        return result
    if status >= 400:
        result["error"] = "HTTP %s" % status
        return result
    if not content:
        result["error"] = "返回内容为空"
        return result

    # 站点返回的是网页而不是订阅源：直接报 XML 语法错会让用户一头雾水，
    # 这里换成一句人话，并把从网页里发现的订阅地址一并返回
    if looks_like_html(content):
        found = discover_feed_urls(content, url)
        if found:
            result["error"] = "这是网页不是订阅源；已发现订阅地址：%s" % found[0]
            result["suggested"] = found
        else:
            result["error"] = ("这是网页不是订阅源；请改用博客的 RSS / Atom 地址"
                               "（通常在页脚或 /feed、/atom.xml）")
        return result

    try:
        meta, entries = parse_feed_bytes(
            content, fallback_url=url, feed_title=getattr(feed, "title", ""))
    except Exception as exc:
        result["error"] = "解析失败：%s" % exc
        return result

    if not meta.get("etag"):
        meta["etag"] = headers.get("ETag", "") or headers.get("etag", "")
    if not meta.get("modified"):
        meta["modified"] = headers.get("Last-Modified", "") or \
            headers.get("last-modified", "")
    result["ok"] = True
    result["status"] = "ok"
    result["meta"] = meta
    result["entries"] = entries
    return result


def probe_url(url, timeout=15):
    """添加订阅前探测地址是否可用。

    返回 dict：{"ok", "title", "count", "message", "entries", "site_url"}
    entries 会带回来，这样勾选后可以直接入库，不用再抓一遍。
    """
    from .models import Feed
    blank = {"ok": False, "title": "", "count": 0, "message": "",
             "entries": [], "site_url": "", "resolved_url": url}
    feed = Feed(url=url, title=url)
    result = fetch_feed(feed, timeout=timeout)

    # 粘的是博客首页而不是订阅地址：网页里一般写了 feed 链接，
    # 自动跟着找一次，用户就不用自己去翻页脚了
    if not result["ok"] and result.get("suggested"):
        for candidate in result["suggested"][:2]:
            retry = fetch_feed(Feed(url=candidate, title=candidate),
                               timeout=timeout)
            if retry["ok"]:
                result = retry
                url = candidate
                blank["resolved_url"] = candidate
                blank["message"] = "已从网页发现订阅地址"
                break
        else:
            blank["message"] = result["error"]
            return blank
    elif not result["ok"]:
        blank["message"] = result["error"]
        return blank

    blank.update({
        "ok": True,
        "title": result["meta"].get("title") or url,
        "count": len(result["entries"]),
        "message": blank["message"] or "可用",
        "entries": result["entries"],
        "site_url": result["meta"].get("site_url", ""),
    })
    return blank


# ---------------------------------------------------------------------------
# 后台批量刷新
# ---------------------------------------------------------------------------


class Refresher(object):
    """后台刷新订阅源，主线程通过队列取进度。

    用法：
        refresher = Refresher(store, settings)
        refresher.start(feed_ids)          # 启动
        while True: 事件 = refresher.poll()
    """

    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.queue = queue.Queue()
        self._thread = None
        self._cancel = False
        self.running = False

    # ------------------------------------------------------------------
    def start(self, feed_ids=None):
        if self.running:
            return False
        # 禁用的订阅源不刷新（用户禁用就是不想刷）
        allow = set(feed_ids) if feed_ids else None
        targets = [f for f in self.store.feeds
                   if f.enabled and (allow is None or f.id in allow)]
        if not targets:
            return False
        self._cancel = False
        self.running = True
        self._thread = threading.Thread(
            target=self._run, args=(targets,), daemon=True)
        self._thread.start()
        return True

    def cancel(self):
        """取消刷新。

        除了置标志位，还会把线程池里没开始的任务直接丢掉，
        否则用户点了取消，后台还在慢慢把剩下的源抓完。
        """
        self._cancel = True
        pool = getattr(self, "_pool", None)
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:  # Python < 3.9 没有 cancel_futures
                try:
                    pool.shutdown(wait=False)
                except Exception:
                    pass
            except Exception:
                pass

    def poll(self):
        """主线程调用：取出所有待处理事件。"""
        events = []
        while True:
            try:
                events.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return events

    # ------------------------------------------------------------------
    def _run(self, targets):
        timeout = int(self.settings.get("refresh_timeout", 15) or 15)
        workers = max(1, min(8, int(self.settings.get("concurrency", 4) or 4)))
        total = len(targets)
        self.queue.put({"kind": "start", "total": total})

        def job(feed):
            if self._cancel:
                return feed, {"ok": False, "status": "cancelled",
                              "error": "已取消", "meta": {}, "entries": []}
            return feed, fetch_feed(feed, timeout=timeout)

        done = 0
        added_total = 0
        failed = []
        try:
            pool = ThreadPoolExecutor(max_workers=workers)
            self._pool = pool
            try:
                for feed, result in pool.map(job, targets):
                    if self._cancel:
                        break
                    done += 1
                    if result["status"] == "cancelled":
                        continue
                    if not result["ok"]:
                        feed.last_status = "error"
                        feed.last_error = result["error"]
                        failed.append((feed.title, result["error"]))
                        self.queue.put({
                            "kind": "progress", "done": done, "total": total,
                            "feed": feed.title, "added": 0,
                            "error": result["error"]})
                        continue
                    if result["status"] == "not_modified":
                        feed.last_status = "ok"
                        feed.last_error = ""
                        feed.last_updated = time.time()
                        self.queue.put({
                            "kind": "progress", "done": done, "total": total,
                            "feed": feed.title, "added": 0,
                            "error": "", "cached": True})
                        continue

                    meta = result["meta"] or {}
                    # 用户改过标题就不再被服务端标题覆盖
                    if meta.get("title") and not getattr(feed, "custom_title",
                                                          False):
                        feed.title = meta["title"] or feed.title
                    if meta.get("site_url"):
                        feed.site_url = meta["site_url"]
                    if meta.get("etag"):
                        feed.etag = meta["etag"]
                    if meta.get("modified"):
                        feed.last_modified = meta["modified"]
                    feed.last_status = "ok"
                    feed.last_error = ""
                    feed.last_updated = time.time()
                    added = self.store.upsert_articles(
                        feed.id, result["entries"], feed_title=feed.title)
                    added_total += added
                    self.queue.put({
                        "kind": "progress", "done": done, "total": total,
                        "feed": feed.title, "added": added, "error": ""})
            finally:
                self._pool = None
                try:
                    pool.shutdown(wait=False)
                except Exception:
                    pass
        except Exception as exc:
            self._pool = None
            self.queue.put({"kind": "error", "message": str(exc)})

        self.queue.put({
            "kind": "finish", "total": total, "added": added_total,
            "failed": failed, "cancelled": self._cancel})
        self.running = False
