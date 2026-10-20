# -*- coding: utf-8 -*-
"""性能测试：大量文章下的响应时间。

用户抱怨"太卡"是这次换 pywebview 的直接原因，所以这里必须给出数字：
生成 5000 篇文章，分别测「取一页」「渲染一页」「切换筛选」的耗时。
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "test"))

DATA = os.path.join(ROOT, ".tmp", "perf_data")
os.environ["GOOSE_RSS_HOME"] = DATA

TOTAL_ARTICLES = 5000
PASSED = []
FAILED = []


def check(condition, message):
    flag = bool(condition)
    print(("  [PASS] " if flag else "  [FAIL] ") + message)
    (PASSED if flag else FAILED).append(message)
    return flag


def prepare(count):
    import shutil
    if os.path.isdir(DATA):
        try:
            shutil.rmtree(DATA)
        except OSError:
            pass
    os.makedirs(DATA, exist_ok=True)

    from src.models import Store, Feed
    store = Store(data_dir=DATA)
    store.load()
    base = time.time()
    feed_ids = []
    for f in range(30):
        feed = Feed(url="https://site%d.example.com/feed.xml" % f,
                    title="测试站点 %d" % f,
                    group=("技术" if f % 3 == 0 else
                           "新闻" if f % 3 == 1 else "效率"))
        saved = store.add_feed(feed)
        feed_ids.append(saved.id)
    per = count // len(feed_ids)
    for index, fid in enumerate(feed_ids):
        entries = []
        for i in range(per):
            entries.append({
                "title": "第 %d 篇文章的标题，用来测试渲染性能" % (index * per + i),
                "link": "https://site%d.example.com/post/%d" % (index, i),
                "author": "作者 %d" % index,
                "published": "2026-10-01 10:00",
                "timestamp": base - (index * per + i) * 60,
                "summary": "这是摘要，用来测试列表与搜索的表现。" * 3,
                "content": "正文内容。" * 60,
                "feed_title": "测试站点 %d" % index,
            })
        store.upsert_articles(fid, entries)
    store.save_articles()
    store.save_subscriptions()
    return store


def main():
    print("=" * 68)
    print("GooseRSS — 性能测试")
    print("=" * 68)

    print("\n[1] 准备 %d 篇文章…" % TOTAL_ARTICLES)
    t0 = time.time()
    store = prepare(TOTAL_ARTICLES)
    print("  生成耗时 %.2fs，实际入库 %d 篇" % (time.time() - t0, len(store.articles)))
    check(len(store.articles) >= TOTAL_ARTICLES * 0.9,
          "样本量足够：%d 篇" % len(store.articles))

    from src.controller import Controller
    controller = Controller()
    controller.tray = None

    print("\n[2] 取一页（后端）")
    times = []
    for offset in (0, 1000, 3000, 4500):
        t0 = time.time()
        page = controller.get_articles(view="all", offset=offset, limit=80)
        cost = (time.time() - t0) * 1000
        times.append(cost)
        print("  offset=%-5d %6.1f ms  (%d 条)" % (offset, cost, len(page["items"])))
    worst = max(times)
    check(worst < 400, "取一页最慢 %.0f ms（阈值 400ms）" % worst)

    print("\n[3] 切换筛选（后端）")
    t0 = time.time()
    controller.get_articles(view="unread", offset=0, limit=80)
    cost = (time.time() - t0) * 1000
    print("  未读视图 %.1f ms" % cost)
    check(cost < 600, "筛选耗时 %.0f ms（阈值 600ms）" % cost)

    t0 = time.time()
    controller.get_articles(view="all", query="标题", offset=0, limit=80)
    cost = (time.time() - t0) * 1000
    print("  搜索 %.1f ms" % cost)
    check(cost < 800, "搜索耗时 %.0f ms（阈值 800ms）" % cost)

    print("\n[4] 单篇读取")
    sample = list(store.articles.values())[0]
    t0 = time.time()
    controller.get_article(sample.id)
    cost = (time.time() - t0) * 1000
    print("  %.1f ms" % cost)
    check(cost < 200, "单篇读取 %.0f ms" % cost)

    print("\n[5] 标记已读（写盘是延迟的，这里只测内存操作）")
    t0 = time.time()
    for i in range(50):
        controller.mark_read(list(store.articles.keys())[i], True)
    cost = (time.time() - t0) * 1000
    print("  连续 50 次 %.1f ms（平均 %.2f ms/次）" % (cost, cost / 50))
    check(cost / 50 < 20, "单次标记 %.2f ms" % (cost / 50))

    print("\n[6] 真实窗口里渲染一页（前端）")
    try:
        import gi  # noqa: F401
    except Exception:
        print("  [跳过] 无 GTK/WebKit 绑定")
        return finish()

    import webview
    from src import webview_app
    window = webview.create_window(
        "perf", url=webview_app.index_url(), width=1000, height=680,
        js_api=webview_app.create_api(controller))
    controller.window = window

    result = {}

    def probe():
        for _ in range(60):
            time.sleep(0.3)
            try:
                if window.evaluate_js(
                        "!!(window.pywebview && window.pywebview.api)"):
                    break
            except Exception:
                pass
        time.sleep(2.0)

        # 端到端计时：走真实 UI 路径（点视图按钮 → 后端取数 → 前端渲染）。
        # 这比注入自定义脚本更能反映用户实际感受到的速度。
        try:
            window.evaluate_js("document.getElementById('list').innerHTML=''")
            window.evaluate_js("document.getElementById('list-more').style.display='none'")
            t0 = time.time()
            window.evaluate_js(
                "var s=document.querySelector('.seg');if(s){s.click();}")
            rows = 0
            for _ in range(80):
                time.sleep(0.1)
                try:
                    rows = window.evaluate_js(
                        "document.getElementById('list').childNodes.length")
                except Exception:
                    rows = 0
                if rows >= 80:
                    break
            cost = (time.time() - t0) * 1000
            result["render"] = {"ms": cost, "count": rows}
            print("  端到端（点按钮 → 渲染 80 行）：%.0f ms" % cost)
        except Exception as exc:
            result["error"] = "%s: %s" % (type(exc).__name__, exc)

        # 真实滚动加载：滚到底触发下一页
        try:
            before = window.evaluate_js(
                "document.getElementById('list').childNodes.length")
            for _ in range(6):
                window.evaluate_js(
                    "document.getElementById('list').scrollTop = "
                    "document.getElementById('list').scrollHeight")
                time.sleep(0.6)
            time.sleep(1.5)
            result["before"] = before
            result["afterScroll"] = window.evaluate_js(
                "document.getElementById('list').childNodes.length")
        except Exception as exc:
            result["scrollError"] = str(exc)
        try:
            window.destroy()
        except Exception:
            pass

    import threading
    threading.Thread(target=probe, daemon=True).start()
    webview.start(debug=False)
    time.sleep(0.5)

    if "error" in result:
        check(False, "前端渲染计时失败：%s" % result["error"])
    else:
        ms = (result.get("render") or {}).get("ms", -1)
        count = (result.get("render") or {}).get("count", 0)
        print("  渲染 80 行：%.1f ms（%d 行）" % (ms, count))
        check(0 < ms < 300, "DOM 渲染 80 行 %.1f ms（阈值 300ms）" % ms)
        check(count == 80, "渲染行数正确：%d" % count)
        after = result.get("afterScroll", 0)
        print("  滚动加载：%s -> %s 行" % (result.get("before"), after))
        check((after or 0) > 80, "滚动到底能加载更多（%s 行）" % after)

    return finish()


def finish():
    print("\n" + "=" * 68)
    print("结果：%d 项通过，%d 项失败" % (len(PASSED), len(FAILED)))
    for message in FAILED:
        print("   失败：" + message)
    print("=" * 68)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
