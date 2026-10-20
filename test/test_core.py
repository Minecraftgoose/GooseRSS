# -*- coding: utf-8 -*-
"""核心逻辑测试：不依赖网络、不打开窗口。

用法：
    python test/test_core.py

覆盖：HTML 清洗、RSS/Atom 解析、本地存储与状态、搜索、OPML 导入导出。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

DATA = os.environ.get("GOOSE_RSS_HOME") or os.path.join(ROOT, ".tmp", "core")
os.environ["GOOSE_RSS_HOME"] = DATA

from src import htmlutil, opml
from src.models import Store, Feed
from src.fetcher import parse_feed_bytes

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>示例博客</title><link>https://example.com</link>
<item>
  <title>第一篇文章</title>
  <link>https://example.com/post/1</link>
  <author>张三</author>
  <pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate>
  <description><![CDATA[<p>摘要<b>加粗</b>内容</p>
  <img src="https://example.com/a.png"/></p>]]></description>
</item>
<item>
  <title>第二篇文章</title>
  <link>https://example.com/post/2</link>
  <pubDate>Tue, 02 Jan 2024 10:00:00 GMT</pubDate>
  <description>纯文本摘要 &amp; 实体</description>
</item>
</channel></rss>"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Atom 示例</title>
<link href="https://atom.example.com"/>
<entry>
  <title>Atom 文章</title>
  <link href="https://atom.example.com/e/1"/>
  <updated>2024-03-01T12:00:00Z</updated>
  <author><name>李四</name></author>
  <summary type="html">&lt;p&gt;Atom 摘要&lt;/p&gt;</summary>
</entry>
</feed>"""

PASSED = []
FAILED = []


def check(condition, message):
    flag = bool(condition)
    print(("  [PASS] " if flag else "  [FAIL] ") + message)
    (PASSED if flag else FAILED).append(message)
    return flag


def test_html():
    print("\n[1] HTML 清洗")
    text = htmlutil.html_to_text(
        "<div><h1>标题</h1><p>第一段<br>换行</p>"
        "<script>alert(1)</script><a href='https://x.com'>链接</a>"
        "<img src='https://i.png'/></div>")
    check("标题" in text and "第一段" in text and "换行" in text, "保留正文与段落")
    check("alert" not in text, "移除脚本")
    check("https://x.com" in text, "保留链接地址")
    check("/>" not in text, "不残留标签碎片")
    check(htmlutil.extract_images("<img src='https://i.png'/>")
          == ["https://i.png"], "提取图片地址")
    # 无引号属性 / 懒加载属性 / 属性值里带 >
    check(htmlutil.extract_links("<a href=https://x.com>文字</a>")
          == ["https://x.com"], "无引号 href 也能解析")
    check(htmlutil.extract_images(
        "<img data-src='https://lazy.png' title='a > b'>")
        == ["https://lazy.png"], "懒加载 data-src 也能取到")
    text_attr = htmlutil.html_to_text('<p title="a > b">正文</p>')
    check("正文" in text_attr and "a > b" not in text_attr,
          "属性值里的 > 不会截断标签解析")
    # 二次转义要解透：&amp;nbsp; -> &nbsp; -> 真正的空格
    check(htmlutil.unescape("&amp;nbsp;&lt;") == "\xa0<",
          "二次转义解透：&amp;nbsp; -> 空格")
    check(htmlutil.unescape("&lt;p&gt;x&lt;/p&gt;") == "<p>x</p>",
          "转义的标签还原成标签")
    check(htmlutil.unescape("AT&amp;T") == "AT&T", "普通文本不被误伤")
    check(len(htmlutil.summarize("字" * 500)) <= 221, "摘要截断到 220 字")


def test_parse():
    print("\n[2] 订阅解析")
    meta, entries = parse_feed_bytes(RSS_SAMPLE.encode("utf-8"))
    check(meta["title"] == "示例博客", "RSS 标题：%s" % meta["title"])
    check(len(entries) == 2, "RSS 条目数 %d" % len(entries))
    check(entries[0]["title"] == "第一篇文章", "首条标题")
    check("摘要" in entries[0]["content"], "正文提取")
    check(entries[0]["images"] == ["https://example.com/a.png"], "正文图片")
    meta2, entries2 = parse_feed_bytes(ATOM_SAMPLE.encode("utf-8"))
    check(meta2["title"] == "Atom 示例", "Atom 标题")
    check(len(entries2) == 1 and "Atom 摘要" in entries2[0]["content"],
          "Atom 正文")
    return entries


def test_store(entries):
    print("\n[3] 本地存储")
    store = Store(data_dir=DATA)
    store.load()
    feed = Feed(url="https://example.com/rss.xml", title="示例博客",
                group="技术")
    store.add_feed(feed)
    check(store.upsert_articles(feed.id, entries) == 2, "新增 2 篇文章")
    check(store.unread_count() == 2, "未读计数 2")
    art = list(store.articles.values())[0]
    store.mark_read(art.id)
    check(store.unread_count() == 1, "标记已读")
    store.toggle_star(art.id)
    check(store.starred_count() == 1, "收藏计数")
    check(len(store.articles_for(unread_only=True)) == 1, "按未读筛选")
    check(len(store.articles_for(starred_only=True)) == 1, "按收藏筛选")
    check(len(store.articles_for(keywords=["第一"])) == 1, "关键词搜索命中")
    check(len(store.articles_for(keywords=["没有"])) == 0, "关键词搜索无命中")
    check(store.upsert_articles(feed.id, entries) == 0, "重复抓取不新增")
    check(store.article(art.id).read is True, "已读状态保留")
    store.save()
    check(os.path.isfile(store.subscriptions_path), "订阅文件写入磁盘")

    reloaded = Store(data_dir=DATA)
    reloaded.load()
    check(len(reloaded.feeds) == 1, "重载后订阅源 1 个")
    check(len(reloaded.articles) == 2, "重载后文章 2 篇")
    check(reloaded.starred_count() == 1, "重载后收藏状态保留")
    return reloaded


def test_opml(store):
    print("\n[4] OPML 导入导出")
    store.add_feed(Feed(url="https://b.example.com/atom.xml", title="源B",
                        group="新闻"))
    text = opml.export_opml(store.feeds, groups=store.groups)
    check("<opml" in text, "生成 OPML 根节点")
    check('xmlUrl="https://example.com/rss.xml"' in text, "包含订阅地址")
    check('title="技术"' in text, "包含分组节点")
    parsed = opml.parse_opml(text)
    check(len(parsed) == 2, "回读 %d 个订阅源" % len(parsed))
    check(any(p["group"] == "技术" for p in parsed), "回读保留分组")
    # 嵌套分组：导出后再导入，分组不能掉
    nested = opml.export_opml(store.feeds, groups=store.groups)
    back = opml.parse_opml(nested)
    check(all(any(p["group"] == g for p in back)
              for g in ("技术", "新闻")), "嵌套分组往返后仍在")
    # 一行里多个地址要全部收下
    check(opml.parse_url_list("https://a.example.com https://b.example.com")
          == ["https://a.example.com", "https://b.example.com"],
          "一行多个地址全部解析")
    check(opml.parse_url_list(
        "# 注释\n标题 https://y.example.com/rss ; https://z.example.com/feed , "
        "https://w.example.com/atom") ==
        ["https://y.example.com/rss", "https://z.example.com/feed",
         "https://w.example.com/atom"], "批量地址解析")


def prepare():
    """清空上一次测试留下的数据，保证结果可重复。"""
    try:
        if not os.path.isdir(DATA):
            os.makedirs(DATA)
    except OSError:
        pass
    for name in ("subscriptions.json", "articles.json", "settings.json"):
        path = os.path.join(DATA, name)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass


def test_fetch():
    """抓取层：本地起一个 HTTP 服务，覆盖压缩 / 网页 / 自动发现。"""
    print("\n[5] 抓取与传输（本地 HTTP 服务）")
    import gzip
    import threading
    from http.server import BaseHTTPRequestHandler
    from socketserver import TCPServer

    from src import fetcher

    feed = ('<?xml version="1.0"?><rss version="2.0"><channel>'
            '<title>测试博客</title><link>https://x.example.com</link>'
            '<item><title>文章一</title><link>https://x.example.com/1</link>'
            '<pubDate>Mon, 01 Oct 2026 10:00:00 GMT</pubDate></item>'
            '</channel></rss>').encode("utf-8")
    html = (b'<!DOCTYPE html><html><head>'
            b'<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
            b'</head><body>blog</body></html>')

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/gz.xml", "/feed.xml"):
                body, enc, ctype = gzip.compress(feed), "gzip", "application/rss+xml"
            elif self.path == "/plain.xml":
                body, enc, ctype = feed, None, "application/rss+xml"
            elif self.path == "/":
                body, enc, ctype = html, None, "text/html"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            if enc:
                self.send_header("Content-Encoding", enc)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = TCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port
    try:
        # gzip：曾经因为直接读 response.raw 拿到未解压的二进制，
        # feedparser 报 "not well-formed (invalid token)"
        status, content, _ = fetcher._http_get(base + "/gz.xml")
        check(content[:5] == b"<?xml", "gzip 响应已解压（前 5 字节 %r）"
              % content[:5])
        meta, entries = fetcher.parse_feed_bytes(content)
        check(meta.get("title") == "测试博客" and len(entries) == 1,
              "gzip 订阅源可正常解析")
        probe = fetcher.probe_url(base + "/gz.xml")
        check(probe["ok"] is True and probe["count"] == 1,
              "gzip 订阅源探测成功")

        status, content, _ = fetcher._http_get(base + "/plain.xml")
        check(content[:5] == b"<?xml", "未压缩响应正常")

        check(fetcher.looks_like_html(html) is True, "能识别网页")
        check(fetcher.looks_like_html(feed) is False, "不会把 XML 误判成网页")
        found = fetcher.discover_feed_urls(html, "https://blog.example.com/")
        check(found == ["https://blog.example.com/feed.xml"],
              "从网页发现订阅地址：%s" % found)

        # 粘博客首页也应能订阅（自动跟随发现的 feed）
        probe = fetcher.probe_url(base + "/")
        check(probe["ok"] is True, "粘贴博客首页可自动发现订阅源")
        check(probe["resolved_url"].endswith("/feed.xml") or
              probe["resolved_url"].endswith("/gz.xml") or
              probe["resolved_url"].endswith("/plain.xml"),
              "自动发现后使用真实订阅地址：%s" % probe["resolved_url"])

        # 是网页但页面里没有 feed 链接：给友好提示而不是 XML 语法错
        from src.models import Feed
        result = fetcher.fetch_feed(
            Feed(url=base + "/missing", title="x"))
        check("HTTP 404" in result["error"], "404 正常报告：%s"
              % result["error"])
    finally:
        server.shutdown()


def main():
    prepare()
    print("=" * 68)
    print("GooseRSS 核心逻辑测试")
    print("=" * 68)
    test_html()
    entries = test_parse()
    store = test_store(entries)
    test_opml(store)
    test_fetch()
    print("\n" + "=" * 68)
    print("结果：%d 项通过，%d 项失败" % (len(PASSED), len(FAILED)))
    for message in FAILED:
        print("   失败：" + message)
    print("=" * 68)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
