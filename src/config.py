# -*- coding: utf-8 -*-
"""全局配置、路径与设置项。

该模块不依赖任何 GUI 组件，便于单独测试。
所有代码保持 Python 3.6+ 兼容，方便在 Windows 7 上用 Python 3.8 / 3.9 运行。
"""

import json
import os
import sys

APP_NAME = "GooseRSS 阅读器"
APP_NAME_CN = "GooseRSS 阅读器"
APP_ID = "GooseRSS"
VERSION = "1.0.0"
# GitHub 仓库：关于页展示 + 检查更新用
REPO_OWNER = "Minecraftgoose"
REPO_NAME = "GooseRSS"
REPO_URL = "https://github.com/Minecraftgoose/GooseRSS/"
RELEASES_API = ("https://api.github.com/repos/Minecraftgoose/"
                "GooseRSS/releases/latest")
USER_AGENT = (
    "Mozilla/5.0 (compatible; GooseRSS/%s; +https://github.com/) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0 Safari/537.36" % VERSION
)

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------


def _is_frozen():
    """是否被 PyInstaller / Nuitka 等工具打包。"""
    return getattr(sys, "frozen", False)


def base_dir():
    """项目根目录（src 的上一级），打包后指向临时解压目录。"""
    if _is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def assets_dir():
    """图标等资源目录，打包后从 _MEIPASS 中寻找。"""
    if _is_frozen():
        internal = os.path.join(getattr(sys, "_MEIPASS", base_dir()), "assets")
        if os.path.isdir(internal):
            return internal
    return os.path.join(base_dir(), "assets")


def icons_dir():
    return os.path.join(assets_dir(), "icons")


def licenses_dir():
    if _is_frozen():
        internal = os.path.join(getattr(sys, "_MEIPASS", base_dir()), "licenses")
        if os.path.isdir(internal):
            return internal
    return os.path.join(base_dir(), "licenses")


def set_data_dir(path):
    """覆盖数据目录（--data-dir 参数用）。

    走环境变量，这样 data_dir() 里的平台分支不用各自记一份状态。
    """
    if path:
        os.environ["GOOSE_RSS_HOME"] = os.path.abspath(
            os.path.expanduser(path))
    return data_dir()


def data_dir():
    """用户数据目录：Windows 放在 %APPDATA%，其他系统放在家目录。"""
    override = os.environ.get("GOOSE_RSS_HOME")
    if override:
        path = os.path.abspath(os.path.expanduser(override))
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            appdata = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        path = os.path.join(appdata, APP_ID)
    elif sys.platform == "darwin":
        path = os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support", APP_ID)
    else:
        path = os.path.join(os.path.expanduser("~"), ".goose-rss")
    try:
        if not os.path.isdir(path):
            os.makedirs(path)
    except OSError:
        pass
    return path


def log_path():
    return os.path.join(data_dir(), "goose-rss.log")


# ---------------------------------------------------------------------------
# 默认设置
# ---------------------------------------------------------------------------

DEFAULTS = {
    # 外观
    # 键名必须和前端一致：前端 save_settings 传的是 theme。
    # 之前这里叫 theme_mode，两边对不上，用户选的主题存进了 theme，
    # 而读 theme_mode 永远拿到默认值。
    "theme": "light",                # light / dark
    # 正文是否做安全过滤。默认 False = 完全按源站 HTML 原样渲染
    # （图片、链接、样式全保留）；True = 剥掉 script / iframe / on* 事件
    "sanitize_body": False,
    "light_theme": "arc",            # ttkthemes 浅色主题
    "dark_theme": "equilux",         # ttkthemes 深色主题
    "titlebar_style": "auto",        # auto / mica / aero / dark / light / none
    "font_family": "auto",           # auto 时按平台挑选
    "font_size": 10,
    "content_font_size": 11,
    # 订阅与刷新
    "refresh_timeout": 15,
    "auto_refresh_on_startup": True,
    "auto_refresh_interval_min": 5,  # 通知器定位：默认 5 分钟轮询一次
    "max_articles_per_feed": 200,
    "max_articles_total": 5000,
    "concurrency": 4,
    # 阅读
    "mark_read_on_select": True,
    "mark_read_delay_ms": 800,
    "open_links_in_browser": True,
    # ---- 通知（通知器核心）----
    "notify_enabled": True,          # 总开关
    "notify_system": True,           # 系统通知（Win10/11 Toast，Win7 气球）
    "notify_sound": True,            # 提示音
    "notify_tray_bubble": True,      # 托盘气泡（自绘，与系统通知互补）
    "notify_badge": True,            # 托盘图标叠加未读角标
    "notify_per_feed_limit": 3,      # 单个订阅源一次最多提醒几篇，超出合并
    "notify_max_total": 8,           # 一次轮询最多弹几条通知，防刷屏
    "notify_silent_hours": "",       # 免打扰时段，如 "23:00-07:00"
    "notify_open_on_click": True,    # 点击通知直接用浏览器打开原文
    "notify_mark_read_on_notify": False,  # 提醒后是否自动标记已读
    # 窗口
    "window_geometry": "1180x720",
    "window_maximized": False,
    "list_width": 430,
    "tree_width": 250,
    # 其它
    "last_view": "all",              # all / unread / starred
    "minimize_to_tray": True,        # 通知器定位：关闭窗口默认缩到托盘
    "start_hidden": True,            # 启动后不弹主窗口，直接常驻托盘
}


def default_font_family():
    """按平台挑选默认界面字体。"""
    if sys.platform == "win32":
        return "Microsoft YaHei UI"
    if sys.platform == "darwin":
        return "PingFang SC"
    return "Noto Sans CJK SC"


class Settings(object):
    """简单的 JSON 设置存储，带有默认值兜底。"""

    def __init__(self, path=None):
        self.path = path or os.path.join(data_dir(), "settings.json")
        self._data = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            if os.path.isfile(self.path):
                with open(self.path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    self._data.update(loaded)
        except Exception:
            pass
        return self._data

    def save(self):
        try:
            folder = os.path.dirname(self.path)
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
            tmp = self.path + ".tmp"
            # 先 dumps 再一次写入，避免 json.dump 的分块写开销
            text = json.dumps(self._data, ensure_ascii=False, indent=2)
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            if os.path.exists(self.path):
                os.remove(self.path)
            os.rename(tmp, self.path)
        except Exception:
            pass

    def get(self, key, default=None):
        if key in self._data:
            return self._data[key]
        return DEFAULTS.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def update(self, mapping):
        self._data.update(mapping or {})

    def as_dict(self):
        return dict(self._data)

    def reset(self):
        self._data = dict(DEFAULTS)


# ---------------------------------------------------------------------------
# 平台信息
# ---------------------------------------------------------------------------


def windows_version():
    """返回 (major, minor, build)；非 Windows 返回 None。

    Windows 7 => (6, 1, ...)；Windows 10 => (10, 0, ...)；Windows 11 => (10, 0, 22000+)
    """
    if sys.platform != "win32":
        return None
    try:
        ver = sys.getwindowsversion()
        return (ver.major, ver.minor, getattr(ver, "build", 0))
    except Exception:
        return None


def is_windows_7():
    ver = windows_version()
    return bool(ver and ver[0] == 6 and ver[1] == 1)


def is_windows_10_or_later():
    ver = windows_version()
    if not ver:
        return False
    if ver[0] > 6:
        return True
    return bool(ver[0] == 6 and ver[1] >= 2)  # 保守处理未知版本


def is_windows_11():
    ver = windows_version()
    return bool(ver and ver[0] == 10 and ver[2] >= 22000)


def system_prefers_dark():
    """读取系统深浅色偏好。

    Windows 10+ 读注册表 AppsUseLightTheme；Windows 7 没有该键，返回 None
    由调用方回落到浅色。Linux / macOS 不做探测。
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg  # type: ignore
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return int(value) == 0
    except Exception:
        return None
