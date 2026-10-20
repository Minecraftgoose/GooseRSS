# -*- coding: utf-8 -*-
"""微软 Fluent 3D Emoji 图标管理。

所有界面图标都使用微软 Fluent 3D Emoji（assets/icons/*.png），
运行时按需要的尺寸缩放并缓存，避免重复解码带来的卡顿。

如果图标文件或 Pillow 缺失，会自动回退到 Unicode 符号，保证界面仍然可用。
"""

import os
import threading

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except Exception:  # pragma: no cover - Pillow 缺失时走文本回退
    HAS_PIL = False

from . import config

# 图标名称 -> 文件名（assets/icons 下）
ICON_FILES = {
    "app": "app.png",            # 报纸：应用标识
    "add": "add.png",            # 加号：添加订阅
    "refresh": "refresh.png",    # 循环箭头：刷新
    "delete": "delete.png",      # 垃圾桶：删除
    "search": "search.png",      # 放大镜：搜索
    "star": "star.png",          # 星标：收藏
    "moon": "moon.png",          # 月亮：深色模式
    "sun": "sun.png",            # 太阳：浅色模式
    "settings": "settings.png",  # 齿轮：设置
    "folder": "folder.png",      # 文件夹：分组
    "folder_open": "folder_open.png",
    "globe": "globe.png",        # 地球：打开站点
    "check": "check.png",        # 对勾：已读
    "import": "import.png",      # 收件箱：导入 OPML
    "export": "export.png",      # 发件箱：导出 OPML
    "read": "read.png",          # 打开的书：阅读
    "memo": "memo.png",          # 便签：备注 / 编辑
    "link": "link.png",          # 链接
    "warning": "warning.png",    # 警告
    "home": "home.png",          # 主页：全部文章
    "bell": "bell.png",          # 铃铛：未读提醒
    "clean": "clean.png",        # 扫帚：清理缓存
    "stats": "stats.png",        # 图表：统计
    "palette": "palette.png",    # 调色板：外观
    "edit": "edit.png",          # 铅笔：编辑
    "time": "time.png",          # 沙漏：时间
    "feeds": "feeds.png",        # 书堆：订阅源
    "rss": "rss.png",            # 卫星天线：RSS
    "sparkles": "sparkles.png",  # 闪光：关于
    "close": "close.png",        # 叉号：关闭
    "save": "save.png",          # 软盘：保存
    "unread": "unread.png",      # 邮箱：未读
    "heart": "heart.png",        # 红心：喜欢
    "left": "left.png",          # 左箭头：上一篇
    "right": "right.png",        # 右箭头：下一篇
    "eye": "eye.png",            # 眼睛：查看
    "archive": "archive.png",    # 卡片盒：归档
    "page": "page.png",          # 文档：文章
    "bookmark": "bookmark.png",  # 书签
    "tag": "tag.png",            # 标签
    "clipboard": "clipboard.png",  # 剪贴板：复制
    "bulb": "bulb.png",          # 灯泡：关于 / 提示
    "info": "info.png",          # 信息
}

# 图标缺失时的 Unicode 回退
ICON_FALLBACK = {
    "app": "\U0001F4F0", "add": "\u2795", "refresh": "\U0001F504",
    "delete": "\U0001F5D1", "search": "\U0001F50D", "star": "\u2b50",
    "moon": "\U0001F319", "sun": "\u2600\ufe0f", "settings": "\u2699\ufe0f",
    "folder": "\U0001F4C1", "folder_open": "\U0001F4C2",
    "globe": "\U0001F310", "check": "\u2705", "import": "\U0001F4E5",
    "export": "\U0001F4E4", "read": "\U0001F4D6", "memo": "\U0001F4DD",
    "link": "\U0001F517", "warning": "\u26a0\ufe0f", "home": "\U0001F3E0",
    "bell": "\U0001F514", "clean": "\U0001F9F9", "stats": "\U0001F4CA",
    "palette": "\U0001F3A8", "edit": "\u270f\ufe0f", "time": "\u231b",
    "feeds": "\U0001F4DA", "rss": "\U0001F4E1", "sparkles": "\u2728",
    "close": "\u274c", "save": "\U0001F4BE", "unread": "\U0001F4EC",
    "heart": "\u2764\ufe0f", "left": "\u2b05\ufe0f", "right": "\u27a1\ufe0f",
    "eye": "\U0001F441", "archive": "\U0001F5C3", "page": "\U0001F4C4",
    "bookmark": "\U0001F516", "tag": "\U0001F3F7", "clipboard": "\U0001F4CB",
    "bulb": "\U0001F4A1", "info": "\u2139\ufe0f",
}


class IconManager(object):
    """按需加载并缓存 3D Emoji 图标。

    性能要点：
    · 源图（256×256 PNG）只解码一次并降采样到 64×64 缓存，
      后续各尺寸都从这张小图缩放，避免重复解码大图
    · 默认懒加载，只有真正用到的图标才解码
    """

    # 源图统一降到这个尺寸再缓存，兼顾清晰度与速度
    SOURCE_SIZE = 64

    def __init__(self, root=None, icons_dir=None):
        self.root = root
        self.icons_dir = icons_dir or config.icons_dir()
        self._cache = {}          # (name, size) -> PhotoImage
        self._source = {}         # name -> 降采样后的 RGBA 源图
        self._missing = set()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    def available(self, name):
        """图标是否可用（文件或回退符号存在）。"""
        if name in self._missing:
            return name in ICON_FALLBACK
        return name in ICON_FILES

    def path(self, name):
        fname = ICON_FILES.get(name)
        if not fname:
            return None
        return os.path.join(self.icons_dir, fname)

    def _source_image(self, name):
        """解码并降采样源图，结果缓存复用（每个图标只读一次盘）。"""
        cached = self._source.get(name)
        if cached is not None:
            return cached
        file_path = self.path(name)
        if not file_path or not os.path.isfile(file_path):
            return None
        try:
            image = Image.open(file_path)
            image.load()
            image = image.convert("RGBA")
            if image.size[0] > self.SOURCE_SIZE:
                # 大图先降采样，之后再缩到 16/18/20 就很便宜了
                image = image.resize(
                    (self.SOURCE_SIZE, self.SOURCE_SIZE), Image.BILINEAR)
            self._source[name] = image
            return image
        except Exception:
            return None

    def get(self, name, size=20):
        """获取 PhotoImage；没有 Pillow 或图片时返回 None（调用方用文本回退）。"""
        if not HAS_PIL or self.root is None:
            return None
        key = (name, int(size))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if name in self._missing:
            return None
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            source = self._source_image(name)
            if source is None:
                self._missing.add(name)
                return None
            try:
                target = int(size)
                if source.size[0] == target:
                    image = source
                else:
                    # 从 64px 源图缩放，比从 256px 直接缩快一个数量级
                    image = source.resize((target, target), Image.BILINEAR)
                photo = ImageTk.PhotoImage(image, master=self.root)
                self._cache[key] = photo
                return photo
            except Exception:
                self._missing.add(name)
                return None

    def text(self, name):
        """图标的 Unicode 回退文本。"""
        return ICON_FALLBACK.get(name, "")

    def preload(self, names, sizes=(18,)):
        """预加载常用图标。

        注意：预加载是启动阶段的主要开销（每个图标都要解码 PNG），
        所以默认只加载真正会立刻显示的那一两个尺寸，其余尺寸懒加载。
        """
        for name in names:
            for size in sizes:
                self.get(name, size)

    # ------------------------------------------------------------------
    def make_ico(self, target_path, name="app",
                 sizes=(16, 24, 32, 48, 64, 128, 256)):
        """把某个 3D Emoji 导出为 .ico，用于 Windows 窗口 / 任务栏图标。"""
        if not HAS_PIL:
            return False
        file_path = self.path(name)
        if not file_path or not os.path.isfile(file_path):
            return False
        try:
            source = Image.open(file_path).convert("RGBA")
            images = [(s, source.resize((s, s), Image.LANCZOS)) for s in sizes]
            images[0][1].save(target_path, format="ICO",
                              sizes=[(s, s) for s in sizes],
                              append_images=[img for _, img in images[1:]])
            return True
        except Exception:
            return False


_MANAGER = None


def get_manager(root=None):
    """全局共享的图标管理器。"""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = IconManager(root)
    elif root is not None and _MANAGER.root is None:
        _MANAGER.root = root
    return _MANAGER
