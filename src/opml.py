# -*- coding: utf-8 -*-
"""OPML 1.0 / 2.0 导入导出。

导出时分组写成嵌套 <outline>，同时给每个源写上 category 属性，
这样在其他阅读器（Feedly、Inoreader、Reeder 等）里也能保留分组结构。
"""

import os
import time
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'


def _rfc822(ts=None):
    return time.strftime("%a, %d %b %Y %H:%M:%S +0000",
                         time.gmtime(ts if ts else time.time()))


def export_opml(feeds, groups=None, title="GooseRSS 订阅"):
    """把订阅源导出为 OPML 文本。

    feeds:  Feed 对象列表（需要有 title / url / site_url / group 属性）
    groups: 需要保留的分组顺序（可为空）
    """
    grouped = {}
    order = []
    for feed in feeds:
        key = feed.group or ""
        if key not in grouped:
            grouped[key] = []
            order.append(key)
    for feed in feeds:
        grouped[feed.group or ""].append(feed)

    if groups:
        known = [g for g in groups if g in grouped]
        extra = [g for g in order if g not in known]
        order = known + extra

    lines = [HEADER]
    lines.append('<opml version="2.0">')
    lines.append("  <head>")
    lines.append("    <title>%s</title>" % escape(title))
    lines.append("    <dateCreated>%s</dateCreated>" % _rfc822())
    lines.append("    <generator>GooseRSS</generator>")
    lines.append("  </head>")
    lines.append("  <body>")

    for name in order:
        items = grouped.get(name, [])
        if not items:
            continue
        if name:
            lines.append('    <outline text="%s" title="%s">' % (escape(name),
                                                                  escape(name)))
            prefix = "      "
        else:
            prefix = "    "
        for feed in items:
            attrs = [
                'type="rss"',
                'text="%s"' % escape(feed.title or feed.url),
                'title="%s"' % escape(feed.title or feed.url),
                'xmlUrl="%s"' % escape(feed.url),
                'htmlUrl="%s"' % escape(feed.site_url or ""),
                'category="%s"' % escape(feed.group or ""),
            ]
            # 标签和备注：标准 OPML 没有这俩字段，用自定义属性带出去，
            # 至少自己导自己导回来不会丢
            if getattr(feed, "tags", None):
                attrs.append('tags="%s"'
                             % escape(",".join(str(t) for t in feed.tags)))
            if getattr(feed, "note", ""):
                attrs.append('note="%s"' % escape(feed.note))
            lines.append("%s<outline %s/>" % (prefix, " ".join(attrs)))
        if name:
            lines.append("    </outline>")
    lines.append("  </body>")
    lines.append("</opml>")
    return "\n".join(lines) + "\n"


def export_to_file(feeds, path, groups=None, title="GooseRSS 订阅"):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(export_opml(feeds, groups, title))
    return path


def _split_category(value):
    """category 属性可能是 "A/B"、"A|B" 或 "A,B"，取第一段作为分组名。"""
    if not value:
        return ""
    parts = [p.strip() for p in value.replace("|", "/").split("/") if p.strip()]
    parts = [p for part in parts
             for p in (part.split(",") if "," in part else [part])]
    return parts[0] if parts else ""


def _walk(node, inherited_group, out):
    for child in node:
        if child.tag.lower() != "outline":
            continue
        attrs = {k.lower(): (v or "") for k, v in child.attrib.items()}
        xml_url = attrs.get("xmlurl", "")
        title = attrs.get("title") or attrs.get("text") or xml_url
        html_url = attrs.get("htmlurl", "")
        category = attrs.get("category", "")

        # 分组名取三处：category 属性 → 自身 text/title（嵌套分组节点）→ 继承
        group = _split_category(category) or inherited_group
        if not xml_url:
            # 没有 xmlUrl = 这是一个分组节点，它的 text/title 就是分组名
            own = (attrs.get("text") or attrs.get("title") or "").strip()
            group = _split_category(category) or own or inherited_group
            _walk(child, group, out)
            continue

        tags = [t.strip() for t in attrs.get("tags", "").split(",")
                if t.strip()]
        out.append({
            "title": title,
            "url": xml_url,
            "site_url": html_url,
            "group": group,
            "tags": tags,
            "note": attrs.get("note", ""),
        })
        # 订阅源节点一般没有子节点，但真有就继续（把当前分组传下去）
        if len(child):
            _walk(child, group, out)


def parse_opml(text):
    """解析 OPML 文本，返回 [{title, url, site_url, group}, ...]。"""
    out = []
    try:
        root = ET.fromstring(text)
    except Exception:
        return out
    body = None
    for child in root:
        if child.tag.lower() == "body":
            body = child
            break
    if body is None:
        body = root
    _walk(body, "", out)
    # 去重（按 URL）
    seen = set()
    unique = []
    for item in out:
        key = item["url"].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def parse_opml_file(path, max_bytes=8 * 1024 * 1024):
    """读文件再解析；超过 8MB 直接拒绝，避免异常大文件把内存打满。"""
    try:
        if os.path.getsize(path) > max_bytes:
            raise ValueError("OPML 文件过大（超过 %d MB），已拒绝导入"
                             % (max_bytes // 1024 // 1024))
    except OSError:
        pass
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse_opml(fh.read(max_bytes))


def parse_url_list(text):
    """把一段自由文本解析成 URL 列表。

    支持换行 / 空格 / 逗号 / 分号分隔，支持 # 或 // 开头的注释行。
    """
    urls = []
    if not text:
        return urls
    for raw_line in text.replace(",", "\n").replace(";", "\n").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        # 允许 "标题 http(s)://..." 的写法；一行里有多个地址就全部收下
        parts = line.split()
        for part in parts:
            low = part.lower()
            if low.startswith(("http://", "https://", "feed://")):
                url = "http://" + part[7:] if low.startswith("feed://") else part
                if url not in urls:
                    urls.append(url)
    return urls
