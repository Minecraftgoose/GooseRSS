# -*- coding: utf-8 -*-
"""本地 HTTP 后端。

为什么不直接用 pywebview 的 js_api：
    js_api 是靠向页面注入 JS 对象实现的，官方文档明确说它"不保证在
    load 事件时可用"。在 Windows 7 上尤其脆弱——那里的 WebView2
    Runtime 冻结在 109，而新版 pywebview 内置的 SDK 需要更新的 API，
    结果就是窗口能建、桥却没起来：界面出来了但后端等于没开。

改成起一个只监听 127.0.0.1 的 HTTP 服务，前端用 XHR 调它。
这样不依赖任何注入机制，只要页面能显示，后端就一定连得上。

只服务本机、端口随机，避免和别的程序抢。
"""

import json
import os
import sys
import socket
import threading

from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from http.server import ThreadingHTTPServer as _BaseServer
except ImportError:      # Python < 3.7
    from http.server import HTTPServer as _BaseServer

from . import config

def _find_web_dir():
    """定位 web/ 目录。

    打包后 src/ 被塞进 _MEIPASS，而 web/ 是作为数据文件另放的，
    直接按 __file__ 往上两级会指向 _MEIPASS 内部（那里没有 web/），
    界面就会白屏。所以必须和 config.assets_dir 一样先看 _MEIPASS。
    """
    if getattr(sys, "frozen", False):
        internal = os.path.join(getattr(sys, "_MEIPASS",
                                        os.path.dirname(sys.executable)),
                                "web")
        if os.path.isdir(internal):
            return internal
        near_exe = os.path.join(os.path.dirname(sys.executable), "web")
        if os.path.isdir(near_exe):
            return near_exe
    return os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "web")


WEB_DIR = _find_web_dir()

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def _free_port():
    """取一个空闲端口。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class ApiServer(object):
    """把 Controller 的方法暴露成 HTTP 端点，同时托管 web/ 静态文件。"""

    def __init__(self, controller, host="127.0.0.1", port=None):
        self.controller = controller
        self.host = host
        self.port = port or _free_port()
        self.server = None
        self.thread = None
        self.base_url = "http://%s:%d" % (host, self.port)

    # ------------------------------------------------------------------
    # 可调方法白名单：下划线开头的一律不放行
    # ------------------------------------------------------------------
    def _dispatch(self, name, args):
        if not name or name.startswith("_"):
            return {"error": "无效的方法名"}
        fn = getattr(self.controller, name, None)
        if fn is None or not callable(fn):
            return {"error": "没有这个方法：%s" % name}
        try:
            value = fn(*(args or []))
            return {"result": value}
        except Exception as exc:
            return {"error": "%s: %s" % (type(exc).__name__, exc)}

    # ------------------------------------------------------------------
    def start(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                pass          # 别把每个请求都打到控制台

            # ---- 安全：只允许本机 ----
            def _check_host(self):
                try:
                    return self.client_address[0] in ("127.0.0.1", "::1")
                except Exception:
                    return False

            def _send(self, code, body, ctype, extra=None):
                if isinstance(body, str):
                    body = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if extra:
                    for key, value in extra.items():
                        self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except Exception:
                    pass

            def _send_json(self, code, data):
                self._send(code, json.dumps(data, ensure_ascii=False),
                           "application/json; charset=utf-8",
                           {"Access-Control-Allow-Origin": "*"})

            def do_OPTIONS(self):
                self._send(204, "", "text/plain",
                           {"Access-Control-Allow-Origin": "*",
                            "Access-Control-Allow-Methods":
                                "POST, GET, OPTIONS",
                            "Access-Control-Allow-Headers": "Content-Type"})

            def do_POST(self):
                if not self._check_host():
                    self._send_json(403, {"error": "只允许本机访问"})
                    return
                # /api/<方法名>
                name = self.path.rsplit("/", 1)[-1].split("?")[0]
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"[]"
                    args = json.loads(raw.decode("utf-8") or "[]")
                    if not isinstance(args, list):
                        args = [args]
                except Exception:
                    args = []
                self._send_json(200, outer._dispatch(name, args))

            def do_GET(self):
                if not self._check_host():
                    self._send_json(403, {"error": "只允许本机访问"})
                    return
                path = self.path.split("?")[0]
                if path.startswith("/api/"):
                    name = path.rsplit("/", 1)[-1]
                    self._send_json(200, outer._dispatch(name, []))
                    return
                self._serve_static(path)

            def _serve_static(self, path):
                if path == "/" or path == "":
                    path = "/index.html"
                # 防目录穿越
                safe = path.replace("\\", "/")
                if ".." in safe:
                    self._send(403, "forbidden", "text/plain")
                    return
                rel = safe.lstrip("/")
                full = os.path.join(WEB_DIR, rel)
                if not os.path.isfile(full):
                    self._send(404, "not found: " + rel, "text/plain")
                    return
                _, ext = os.path.splitext(full.lower())
                ctype = MIME.get(ext, "application/octet-stream")
                try:
                    with open(full, "rb") as fh:
                        data = fh.read()
                except Exception:
                    self._send(500, "read error", "text/plain")
                    return
                self._send(200, data, ctype)

        class Server(_BaseServer):
            daemon_threads = True
            allow_reuse_address = True

        try:
            self.server = Server((self.host, self.port), Handler)
        except Exception:
            return False
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        if self.server is not None:
            try:
                self.server.shutdown()
            except Exception:
                pass
            try:
                self.server.server_close()
            except Exception:
                pass


def start_server(controller):
    """起服务并返回 (server, base_url)；失败返回 (None, "")。"""
    for _ in range(3):
        server = ApiServer(controller)
        if server.start():
            return server, server.base_url
    return None, ""
