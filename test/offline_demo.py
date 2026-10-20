# -*- coding: utf-8 -*-
"""离线演示脚本：用内置样例数据启动完整界面，不需要联网。

用法：
    python test/offline_demo.py
    python test/offline_demo.py --dark      # 直接以深色模式启动

它会把几个示例博客与文章写入一个独立的数据目录，然后打开主窗口，
方便在没有网络 / 不想污染正式数据时预览界面效果。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

DATA = os.path.join(ROOT, ".tmp", "demo_data")
os.environ["GOOSE_RSS_HOME"] = DATA

from src.models import Store, Feed  # noqa: E402
from src import config  # noqa: E402

SAMPLES = [
    ("阮一峰的网络日志", "技术", [
        ("每周分享第 300 期", "阮一峰", "2026-10-17 08:00"),
        ("科技爱好者周刊：SVG 入门教程", "阮一峰", "2026-10-15 08:00"),
        ("如何用 Python 写一个 RSS 阅读器", "阮一峰", "2026-10-12 08:00"),
        ("关于异步编程的一点思考", "阮一峰", "2026-10-09 08:00"),
    ]),
    ("少数派", "效率", [
        ("我用 RSS 重建了信息流", "少数派编辑部", "2026-10-16 10:30"),
        ("桌面美化：从图标到主题", "Lim", "2026-10-14 09:20"),
    ]),
    ("Linux 中国", "技术", [
        ("Linux 内核 6.18 发布", "Linux中国", "2026-10-13 20:00"),
        ("在 Windows 7 上跑 Python 3.8", "Linux中国", "2026-10-11 19:00"),
    ]),
    ("科普中国", "新闻", [
        ("为什么秋天容易犯困", "科普中国", "2026-10-10 09:00"),
    ]),
]


def seed():
    store = Store(data_dir=DATA)
    store.load()
    if store.feeds:
        return store
    base = 1760700000.0
    index = 0
    for title, group, items in SAMPLES:
        feed = Feed(url="https://%s.example.com/feed.xml" % title,
                    title=title, group=group)
        feed.last_status = "ok"
        feed.last_updated = base
        saved = store.add_feed(feed)
        entries = []
        for i, (art_title, author, date) in enumerate(items):
            index += 1
            entries.append({
                "title": art_title,
                "link": "https://%s.example.com/post/%d" % (title, i),
                "author": author, "published": date,
                "timestamp": base - index * 3600,
                "summary": "这是《%s》的摘要内容，用于离线演示界面效果。" % art_title,
                "content": "<p>这是《%s》的正文段落，用于演示阅读窗口的排版效果。</p>"
                           "<p>离线演示不会联网抓取，所有内容都是内置的示例数据。</p>"
                           % art_title,
                "feed_title": title,
            })
        store.upsert_articles(saved.id, entries, feed_title=title)
    # 留两篇未读，方便看未读徽标
    arts = sorted(store.articles.values(), key=lambda a: -(a.timestamp or 0))
    for art in arts[2:]:
        art.read = True
    store.save()
    return store


def main():
    dark = "--dark" in sys.argv
    store = seed()
    print("示例数据：%d 个订阅源，%d 篇文章（数据目录：%s）"
          % (len(store.feeds), len(store.articles), DATA))
    if dark:
        from src import config as cfg
        # 直接改配置文件，窗口启动时就会读到深色
        settings = cfg.Settings()
        settings.set("theme", "dark")
        settings.save()

    from src.controller import Controller
    from src.webview_app import run
    controller = Controller()
    controller.tray = None       # 演示脚本不需要托盘
    print("窗口已打开：左侧订阅导航 + 右侧文章列表。")
    print("双击文章会在独立窗口打开阅读；右键可用浏览器打开原文。")
    print("关闭窗口即退出。")
    run(controller, hidden=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
