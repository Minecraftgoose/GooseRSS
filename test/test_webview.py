# -*- coding: utf-8 -*-
"""pywebview 界面测试。

真的把窗口开起来、真的渲染、真的走一遍 JS ↔ Python 桥接。
只测 Python 逻辑是不够的：之前 Tk 版"测试全绿但界面上输入框被压成 1 像素"
就是因为没人真的把窗口打开看一眼。
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "test"))

# 和 offline_demo 用同一个目录，否则 seed 写进去的数据我们读不到
DATA = os.path.join(ROOT, ".tmp", "demo_data")
os.environ["GOOSE_RSS_HOME"] = DATA

PASSED = []
FAILED = []


def check(condition, message):
    flag = bool(condition)
    print(("  [PASS] " if flag else "  [FAIL] ") + message)
    (PASSED if flag else FAILED).append(message)
    return flag


def prepare():
    """清空上次数据，保证结果可重复。"""
    import shutil
    if os.path.isdir(DATA):
        try:
            shutil.rmtree(DATA)
        except OSError:
            pass
    try:
        os.makedirs(DATA)
    except OSError:
        pass


def main():
    prepare()
    print("=" * 68)
    print("GooseRSS — pywebview 界面测试")
    print("=" * 68)

    from offline_demo import seed
    seed()

    from src.controller import Controller
    from src import webview_app

    controller = Controller()
    # 测试环境里不要真的弹托盘（Linux 无 GTK 托盘实现）
    controller.tray = None

    print("\n[1] 控制器（无 GUI 依赖）")
    boot = controller.bootstrap()
    check(boot["app"]["name"] == "GooseRSS", "bootstrap 返回应用信息")
    check(len(boot["feeds"]) > 0, "订阅源已加载：%d 个" % len(boot["feeds"]))
    check(len(boot["groups"]) > 0, "分组已加载：%s" % boot["groups"])
    page = controller.get_articles(view="all", offset=0, limit=20)
    check(page["total"] > 0, "文章可查询：共 %d 篇" % page["total"])
    check(len(page["items"]) > 0, "首页返回 %d 条" % len(page["items"]))
    check(isinstance(page["items"][0]["title"], str), "文章字段可 JSON 序列化")
    art = controller.get_article(page["items"][0]["id"])
    check(art is not None and "body" in art, "单篇文章可取正文")

    print("\n[2] 分页（性能相关：不能一次吐全部）")
    first = controller.get_articles(offset=0, limit=5)
    second = controller.get_articles(offset=5, limit=5)
    ids1 = [a["id"] for a in first["items"]]
    ids2 = [a["id"] for a in second["items"]]
    check(not set(ids1) & set(ids2), "相邻两页不重复")
    check(first["total"] == second["total"], "total 保持一致")

    print("\n[3] 标记已读 / 收藏")
    target = first["items"][0]
    check(controller.mark_read(target["id"], True) in (True, False),
          "mark_read 可调用")
    star = controller.toggle_star(target["id"])
    check(isinstance(star, bool), "toggle_star 返回布尔")

    print("\n[4] 启动真实窗口")
    import webview
    try:
        import gi  # noqa: F401
    except Exception:
        print("  [跳过] 当前环境没有 GTK/WebKit 绑定，无法开窗口")
        return finish()

    window = webview.create_window(
        "test", url=webview_app.index_url(), width=1000, height=680,
        js_api=webview_app.create_api(controller))
    controller.window = window

    result = {}

    def probe():
        # 等页面和 pywebview 的桥接就绪
        for _ in range(80):
            time.sleep(0.25)
            try:
                ready = window.evaluate_js(
                    "!!(window.pywebview && window.pywebview.api)")
                if ready:
                    break
            except Exception:
                pass
        time.sleep(1.0)
        try:
            result["title"] = window.evaluate_js("document.title")
            result["bodyclass"] = window.evaluate_js("document.body.className")
            result["sidebar"] = window.evaluate_js(
                "document.getElementById('sidebar').childNodes.length")
            result["rows"] = window.evaluate_js(
                "document.getElementById('list').childNodes.length")
            result["status"] = window.evaluate_js(
                "document.getElementById('status-text').textContent")
            result["text"] = window.evaluate_js(
                "document.getElementById('list').textContent.length")
            result["bound"] = window.evaluate_js(
                "(function(){var r=document.querySelector('.row-item');"
                "return r && typeof r.onclick==='function' ? 1 : 0;})()")
            # 连点两次不同视图，模拟快速切换
            window.evaluate_js(
                "var s=document.querySelectorAll('.seg');"
                "s[1].click();s[0].click();")
            time.sleep(2.0)
            result["afterSwitch"] = window.evaluate_js(
                "document.getElementById('list').childNodes.length")
        except Exception as exc:
            result["error"] = "%s: %s" % (type(exc).__name__, exc)
        try:
            window.destroy()
        except Exception:
            pass

    import threading
    threading.Thread(target=probe, daemon=True).start()
    webview.start(debug=False)
    time.sleep(0.5)

    check("error" not in result, "页面可访问（%s）" % result.get("error", "无异常"))
    check("GooseRSS" in (result.get("title") or ""),
          "窗口标题正确：%s" % result.get("title"))
    check(result.get("bodyclass", "").startswith("theme-"),
          "主题类已应用：%s" % result.get("bodyclass"))
    check((result.get("sidebar") or 0) > 0,
          "侧栏已渲染 %s 个节点" % result.get("sidebar"))
    check((result.get("rows") or 0) > 0,
          "文章列表已渲染 %s 行" % result.get("rows"))
    check((result.get("text") or 0) > 20,
          "列表有可见文本（%s 字符）" % result.get("text"))

    # 关键回归：曾经因为 childNodes 是 live 的，搬完节点后
    # bindRow 一次都没执行——列表渲染出来但完全点不动
    check(result.get("bound") == 1,
          "列表行绑定了点击事件（onclick=%s）" % result.get("bound"))
    # 快速切换视图后不能是空列表
    check((result.get("afterSwitch") or 0) > 0,
          "快速切换筛选后列表非空（%s 行）" % result.get("afterSwitch"))

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
