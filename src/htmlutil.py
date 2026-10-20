# -*- coding: utf-8 -*-
"""轻量 HTML 清洗工具。

不引入 bs4 / lxml，纯标准库实现，安装体积更小，在 Windows 7 的老机器上也更快。
主要用途：把 feed 里的 HTML 摘要 / 正文转成可在 Tk Text 中显示的纯文本。
"""

import html
import re

RE_SCRIPT = re.compile(
    r"<(script|style|noscript|svg|head)\b[^>]*>.*?</\1>", re.I | re.S)
RE_COMMENT = re.compile(r"<!--.*?-->", re.S)
RE_BR = re.compile(r"<br\s*/?\s*>", re.I)
RE_BLOCK = re.compile(
    r"</?(p|div|tr|h[1-6]|li|ul|ol|pre|blockquote|section|article|figure|"
    r"figcaption|table|thead|tbody|hr)\b[^>]*>", re.I)
RE_IMG = re.compile(r"<img\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", re.I)
# src 可能是 data-src / data-original（懒加载站点很常见）
# img 标签里的 src / data-src 等属性（用于改写为绝对地址）
RE_IMG_ATTR = re.compile(
    r"\b(src|data-src|data-original|data-lazy-src)\s*=\s*"
    r"(\"|')?([^\"'\s>]*)(\"|')?", re.I)
RE_IMG_SRC = re.compile(r"\b(?:src|data-src|data-original|data-lazy-src)\s*=\s*"
                        r"(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.I)
# href 允许带引号或不带引号
RE_LINK = re.compile(r"<a\b[^>]*?href\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|"
                     r"([^\s>]+))[^>]*>(.*?)</a>", re.I | re.S)
# 匹配标签时跳过引号内的 ">"（属性值里带 > 的情况）
RE_TAG = re.compile(r"<(?:[^>\"']|\"[^\"]*\"|'[^']*')*>")
# 只开不闭的标签（有些源站 HTML 不规整）
RE_SCRIPT_OPEN = re.compile(
    r"<\s*(script|style|iframe|object|embed|form)\b[^>]*>", re.I)
# 事件属性：onclick=... / onerror=... 等
RE_ONATTR = re.compile(
    r"""(?ix)\s on\w+ \s*=\s* (?: "[^"]*" | '[^']*' | [^\s>]+ )""")
RE_SPACES = re.compile(r"[\t\r\f\v\u00a0 ]+")
RE_MULTI_NL = re.compile(r"\n{3,}")
RE_ENTITY = re.compile(r"&(#?\w+);")


def unescape(text):
    """反转义 HTML 实体。

    feed 里很常见「二次转义」：正文 HTML 被整段 escape 一遍，
    于是 `&lt;p&gt;` 要还原成 `<p>`、`&amp;nbsp;` 要还原成空格。
    所以这里一直解到不再含实体为止（最多 3 轮，避免死循环）。
    """
    if not text:
        return ""
    for _ in range(3):
        new_text = html.unescape(text)
        if new_text == text:
            break
        text = new_text
        # 还有 &xx; 形式的实体就再解一层；否则停手，防止把正常文本里的
        # "AT&T" 之类再误伤
        if not RE_ENTITY.search(text):
            break
    return text


def _attr_value(match):
    """从 (引号值 / 单引号值 / 无引号值) 三种分组里取第一个命中的。"""
    for group in match.groups():
        if group:
            return group.strip()
    return ""


def _img_src(tag):
    """取图片地址：优先 src，没有再退回 data-src 等懒加载属性。"""
    best = ""
    for match in RE_IMG_SRC.finditer(tag):
        # 先看这次匹配到的是不是真正的 src
        raw = match.group(0).lower()
        value = unescape(_attr_value(match)).strip()
        if not value:
            continue
        if raw.startswith("src"):
            return value
        if not best:
            best = value
    return best


def extract_images(html_text, limit=8):
    """提取正文中的图片地址。"""
    if not html_text:
        return []
    images = []
    for match in RE_IMG.finditer(html_text):
        url = _img_src(match.group(0))
        if url and url not in images:
            images.append(url)
        if len(images) >= limit:
            break
    return images


def absolutize_images(html_text, base_url=""):
    """把正文里图片的地址补全成绝对 URL。

    这是"图片不显示"最常见的原因：很多站的 RSS 正文里写的是
    src="/wp-content/uploads/a.jpg" 这种相对路径。放在本地页面上渲染时，
    浏览器会解析成 http://127.0.0.1:端口/wp-content/... ，当然 404。

    另外顺手处理懒加载：只有 data-src 没有 src 的 <img> 补上 src。
    """
    if not html_text:
        return ""
    base = (base_url or "").strip()
    if not base:
        return html_text
    try:
        from urllib.parse import urljoin
    except ImportError:                       # Python 2 兜底
        from urlparse import urljoin

    def fix(match):
        tag = match.group(0)

        def repl(m):
            quote = m.group(2) or m.group(4) or ""
            value = (m.group(3) or m.group(5) or "").strip()
            if not value or value.startswith(("data:", "http", "//")):
                return m.group(0)
            try:
                absolute = urljoin(base, value)
            except Exception:
                return m.group(0)
            if not absolute or absolute == value:
                return m.group(0)
            return m.group(1) + "=" + quote + absolute + quote

        return RE_IMG_ATTR.sub(repl, tag)

    return RE_IMG.sub(fix, html_text)


def extract_links(html_text, limit=20):
    """提取正文中的链接地址。"""
    if not html_text:
        return []
    links = []
    for match in RE_LINK.finditer(html_text):
        url = unescape(_attr_value(match)).strip()
        if url and url not in links:
            links.append(url)
        if len(links) >= limit:
            break
    return links


def html_to_text(html_text, keep_links=True, image_placeholder="[图片]"):
    """把 HTML 转成带段落结构的纯文本。"""
    if not html_text:
        return ""
    text = RE_SCRIPT.sub(" ", html_text)
    text = RE_COMMENT.sub(" ", text)

    if image_placeholder:
        text = RE_IMG.sub(" " + image_placeholder + " ", text)
    else:
        text = RE_IMG.sub(" ", text)

    if keep_links:
        def _link(match):
            # 分组 1/2/3 是 href 的三种写法（双引号 / 单引号 / 无引号），4 是链接文字
            href = match.group(1)
            if href is None:
                href = match.group(2)
            if href is None:
                href = match.group(3)
            url = unescape(href or "").strip()
            inner = RE_TAG.sub("", match.group(4))
            inner = unescape(inner).strip()
            if not inner:
                return url
            if url and url not in inner:
                return "%s (%s)" % (inner, url)
            return inner
        text = RE_LINK.sub(_link, text)

    text = RE_BR.sub("\n", text)
    text = RE_BLOCK.sub("\n\n", text)
    text = RE_TAG.sub("", text)
    text = unescape(text)
    text = text.replace("\u200b", "")
    text = RE_SPACES.sub(" ", text)
    lines = []
    for line in text.split("\n"):
        lines.append(line.strip())
    text = "\n".join(lines)
    text = RE_MULTI_NL.sub("\n\n", text)
    return text.strip()


def summarize(text, length=220):
    """生成用于列表显示的简短摘要。"""
    if not text:
        return ""
    flat = " ".join(text.split())
    if len(flat) <= length:
        return flat
    return flat[:length].rstrip() + "…"


def rich_body(html_text, sanitize=False):
    """正文原样输出（保留图片、链接、排版）。

    之前 article_body 用的是 strip_html_basic，那个函数会把所有标签
    剥掉只留纯文本 —— 于是 RSS 里的图片、链接、加粗全部丢失，
    阅读区只剩一堆文字。

    这里反过来做：默认保留一切能显示的东西（img / a / p / 样式），
    只在 sanitize=True 时剥掉会执行脚本的部分。

    :param sanitize: True 时剥离 script / iframe / on* 事件属性。
                     默认 False —— 完全按源站给的 HTML 原样渲染。
    """
    if not html_text:
        return ""
    text = html_text
    if sanitize:
        text = RE_SCRIPT.sub(" ", text)
        text = RE_SCRIPT_OPEN.sub(" ", text)
        text = RE_ONATTR.sub(" ", text)
    return text


def strip_html_basic(html_text):
    """只去标签、不去结构的快速清洗（用于 tooltip 等短文本）。"""
    if not html_text:
        return ""
    text = RE_SCRIPT.sub(" ", html_text)
    text = RE_TAG.sub("", text)
    return RE_SPACES.sub(" ", unescape(text)).strip()
