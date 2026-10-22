# -*- coding: utf-8 -*-
"""入口回归测试：确保 `python main.py` 能真正跑起来。

曾经出过的问题：main.py 把 src/ 塞进 sys.path 后用 `from app import main`
按顶层模块导入，导致 src 包内的 `from . import config` 报
"attempted relative import with no known parent package"。
这个测试用子进程真实启动 main.py，守住这条路径不再回归。

用法：
    python test/test_entry.py
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASSED = []
FAILED = []


def check(condition, message):
    flag = bool(condition)
    print(("  [PASS] " if flag else "  [FAIL] ") + message)
    (PASSED if flag else FAILED).append(message)
    return flag


def run_script(args, timeout=25):
    """跑一个脚本，返回 (returncode, stdout, stderr)。

    主程序会打开窗口并进入主循环，因此用超时终止，
    重点看终止前有没有抛异常。
    """
    env = dict(os.environ)
    env["GOOSE_RSS_HOME"] = os.path.join(ROOT, ".tmp", "entry_data")
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u"] + args, cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as exc:
        return -1, "", str(exc)
    try:
        out, err = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        code = -9   # 被超时终止 = 窗口已打开并在正常运行
    text_out = out.decode("utf-8", "replace")
    text_err = err.decode("utf-8", "replace")
    return code, text_out, text_err


def main():
    print("=" * 68)
    print("GooseRSS 入口测试")
    print("=" * 68)

    print("\n[1] python main.py 能启动")
    code, out, err = run_script(["main.py"])
    # 退出码 -9 表示被超时杀掉：说明窗口已成功打开、主循环正常
    # 退出码 0 表示正常退出（例如用户关闭窗口）
    started = code in (-9, 0)
    check(started, "main.py 启动（退出码 %s）" % code)
    check("Traceback" not in err, "启动过程无异常"
          if "Traceback" not in err else "异常内容：\n" + err[-600:])
    check("attempted relative import" not in err, "无相对导入错误")

    print("\n[2] 依赖检查提示可用")
    # 用 -c 屏蔽真实模块，验证缺依赖时给出提示而不是崩溃
    code2, out2, err2 = run_script(
        ["-c",
         "import sys,os;"
         "sys.path.insert(0,%r);"
         "sys.modules['feedparser']=None;"
         "import main; print(main._check_dependencies())" % ROOT],
        timeout=20)
    check("缺少依赖" in out2 or "缺少依赖" in err2,
          "缺依赖时给出提示（输出：%r）" % (out2.strip()[:60] or err2.strip()[:60]))

    print("\n[3] 前端文件齐全（缺一个界面就打不开）")
    web = os.path.join(ROOT, "web")
    # reader.html / reader.js 已在 SPA 改造时删除，阅读页并入 index.html
    for name in ("index.html", "app.css", "app.js"):
        check(os.path.isfile(os.path.join(web, name)),
              "web/%s 存在" % name)
    index_html = open(os.path.join(web, "index.html"), encoding="utf-8").read()
    check('id="view-reader"' in index_html, "index.html 含 SPA 阅读页")
    icons = os.path.join(web, "icons")
    count = len([n for n in os.listdir(icons)
                 if n.lower().endswith(".png")]) if os.path.isdir(icons) else 0
    check(count >= 10, "web/icons 里有 %d 个图标" % count)

    print("\n" + "=" * 68)
    print("结果：%d 项通过，%d 项失败" % (len(PASSED), len(FAILED)))
    for message in FAILED:
        print("   失败：" + message)
    print("=" * 68)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
