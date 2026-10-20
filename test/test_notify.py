# -*- coding: utf-8 -*-
"""通知引擎测试：不依赖真实系统通知，只验证逻辑。

用法：
    python test/test_notify.py

覆盖：
· 新文章去重（同一篇不会重复提醒）
· 通知内容按订阅源分组、超量合并
· 免打扰时段判定
· 状态位（read / starred / notified）互不干扰
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

DATA = os.path.join(ROOT, ".tmp", "notify_data")
os.environ["GOOSE_RSS_HOME"] = DATA

PASSED = []
FAILED = []


def check(condition, message):
    flag = bool(condition)
    print(("  [PASS] " if flag else "  [FAIL] ") + message)
    (PASSED if flag else FAILED).append(message)
    return flag


def prepare():
    import shutil
    if os.path.isdir(DATA):
        shutil.rmtree(DATA)
    os.makedirs(DATA)


def fake_articles(count, feed_id, feed_title, start=1000):
    return [{
        "title": "%s 第 %d 篇" % (feed_title, i),
        "link": "https://example.com/%s/%d" % (feed_id, i),
        "author": "作者", "published": "2026-10-01 10:00",
        "timestamp": 1760000000.0 + start + i,
        "summary": "摘要", "content": "正文",
        "images": [], "feed_title": feed_title,
    } for i in range(count)]


def main():
    prepare()
    print("=" * 68)
    print("GooseRSS 通知引擎测试")
    print("=" * 68)

    from src.models import Store, Feed
    from src import notifier

    print("\n[1] 后端探测与降级")
    backends = notifier.available_backends()
    check("bubble" in backends, "自绘气泡始终可用（兜底）")
    check(isinstance(notifier.is_win7(), bool), "Win7 判定不抛异常")
    check(isinstance(notifier.is_win10_or_later(), bool),
          "Win10+ 判定不抛异常")
    check(notifier.available_backends() == backends, "后端列表可重复调用")

    print("\n[2] 免打扰时段")
    class _T(object):
        def __init__(self, hour, minute=0):
            self.tm_hour = hour
            self.tm_min = minute
    check(notifier.in_quiet_hours("23:00-07:00", _T(23, 30)) is True,
          "23:30 在 23:00-07:00 内")
    check(notifier.in_quiet_hours("23:00-07:00", _T(2, 0)) is True,
          "02:00 跨零点仍在免打扰内")
    check(notifier.in_quiet_hours("23:00-07:00", _T(12, 0)) is False,
          "12:00 不在免打扰内")
    check(notifier.in_quiet_hours("09:00-18:00", _T(12, 0)) is True,
          "同日区间 09:00-18:00 生效")
    check(notifier.in_quiet_hours("", _T(23, 30)) is False,
          "留空表示全天提醒")

    print("\n[3] 新文章去重（核心）")
    store = Store(data_dir=DATA)
    store.settings.set("max_articles_total", 99999)
    store.load()
    feed_a = store.add_feed(Feed(url="https://a.example.com/rss",
                                 title="源A", group="技术"))
    feed_b = store.add_feed(Feed(url="https://b.example.com/rss",
                                 title="源B", group="新闻"))
    store.upsert_articles(feed_a.id, fake_articles(5, "a", "源A"), "源A")
    store.upsert_articles(feed_b.id, fake_articles(2, "b", "源B", 2000), "源B")
    check(store.unnotified_count() == 7, "7 篇都还没提醒过")

    candidates = store.unnotified_articles()
    notices = notifier.build_notifications(candidates, per_feed_limit=3)
    check(len(notices) == 2, "两个订阅源合成 2 条通知")
    # 通知按最新文章排序，所以不假定哪个源排第一
    joined = " ".join(n["title"] + " " + n["message"] for n in notices)
    check("源A" in joined and "源B" in joined, "通知内容含各订阅源名")

    # 标记已通知后不再重复
    ids = [a.id for n in notices for a in n["articles"]]
    store.mark_notified(ids)
    check(store.unnotified_count() == 0, "提醒后清零，不会重复弹")
    again = store.unnotified_articles()
    check(len(again) == 0, "再次取候选为空")

    print("\n[4] 超量合并（防刷屏）")
    store2 = Store(data_dir=DATA + "2")
    store2.settings.set("max_articles_total", 99999)
    store2.load()
    ids_seen = []
    for i in range(12):
        f = store2.add_feed(Feed(url="https://f%d.example.com/rss" % i,
                                 title="源%d" % i))
        store2.upsert_articles(f.id, fake_articles(1, "f%d" % i, "源%d" % i,
                                                   3000 + i * 10), "源%d" % i)
    all_notices = notifier.build_notifications(store2.unnotified_articles(),
                                               per_feed_limit=3, max_total=8)
    check(len(all_notices) == 9, "12 个源限到 8 条 + 1 条汇总 = 9 条")
    check("还有" in all_notices[-1]["title"], "最后一条是汇总：%s"
          % all_notices[-1]["title"])

    print("\n[5] 单源多篇文章的展示方式")
    store3 = Store(data_dir=DATA + "3")
    store3.settings.set("max_articles_total", 99999)
    store3.load()
    f3 = store3.add_feed(Feed(url="https://c.example.com/rss", title="源C"))
    store3.upsert_articles(f3.id, fake_articles(1, "c", "源C", 5000), "源C")
    one = notifier.build_notifications(store3.unnotified_articles(),
                                       per_feed_limit=3)
    check(len(one) == 1 and one[0]["message"] == "源C 第 0 篇",
          "单篇时标题就是文章标题")

    store4 = Store(data_dir=DATA + "4")
    store4.settings.set("max_articles_total", 99999)
    store4.load()
    f4 = store4.add_feed(Feed(url="https://d.example.com/rss", title="源D"))
    store4.upsert_articles(f4.id, fake_articles(6, "d", "源D", 6000), "源D")
    many = notifier.build_notifications(store4.unnotified_articles(),
                                        per_feed_limit=3)
    check("等共 6 篇" in many[0]["message"], "超过上限时合并显示：%r"
          % many[0]["message"][:40])

    print("\n[6] 状态位互不干扰")
    arts = list(store.articles.values())
    target = arts[0]
    store.mark_read(target.id, True)
    store.toggle_star(target.id)
    check(target.read is True and target.starred is True,
          "已读 + 收藏可同时存在")
    check(target.notified is True, "已通知状态不受影响")
    store.save()
    reloaded = Store(data_dir=DATA)
    reloaded.load()
    back = reloaded.article(target.id)
    check(back.read is True, "重载后已读保持")
    check(back.starred is True, "重载后收藏保持")
    check(back.notified is True, "重载后已通知保持（不会重复提醒）")
    check(reloaded.unnotified_count() == 0, "重载后未通知数为 0")

    print("\n[7] 通知内容安全")
    notices_check = notifier.build_notifications([])
    check(notices_check == [], "空候选不产生通知")
    from src.models import Article
    weird = Article(feed_id="x", feed_title="源", title="标题",
                    link="", timestamp=1.0)
    check(notifier.build_notifications([weird])[0]["url"] == "",
          "无链接的文章不会崩")

    print("\n" + "=" * 68)
    print("结果：%d 项通过，%d 项失败" % (len(PASSED), len(FAILED)))
    for message in FAILED:
        print("   失败：" + message)
    print("=" * 68)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
