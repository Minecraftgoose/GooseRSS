/* GooseRSS — 前端逻辑
   ES5 写法：Windows 7 上 pywebview 会降级到 IE11，那里没有
   let/const、箭头函数、模板字符串和 Promise。 */

/* ---- Promise polyfill（IE11 需要，pywebview 的调用桥依赖它） ---- */
(function () {
  if (window.Promise) { return; }
  function P(fn) {
    var self = this; self.state = 0; self.val = null; self.cbs = [];
    function settle(ok, v) {
      if (self.state) { return; }
      self.state = ok ? 1 : 2; self.val = v;
      for (var i = 0; i < self.cbs.length; i++) { self.cbs[i](); }
      self.cbs = [];
    }
    function res(v) {
      if (v && typeof v.then === 'function') { v.then(res, rej); return; }
      settle(1, v);
    }
    function rej(v) { settle(2, v); }
    try { fn(res, rej); } catch (e) { rej(e); }
  }
  P.prototype.then = function (ok, err) {
    var self = this;
    return new P(function (res, rej) {
      function run() {
        var h = self.state === 1 ? ok : err;
        if (typeof h !== 'function') { (self.state === 1 ? res : rej)(self.val); return; }
        try { res(h(self.val)); } catch (e) { rej(e); }
      }
      if (self.state) { run(); } else { self.cbs.push(run); }
    });
  };
  P.prototype['catch'] = function (e) { return this.then(null, e); };
  window.Promise = P;
})();

(function () {
  'use strict';

  /* ===================== 全局状态 ===================== */
  var state = {
    view: 'all',
    feedId: null,
    group: null,
    query: '',
    offset: 0,
    total: 0,
    loading: false,
    rows: [],          // 已渲染的文章（供阅读窗口翻页 / 右键用）
    feeds: [],
    groups: [],
    counts: {},
    settings: {},
    refreshing: false,
    theme: 'light'
  };
  var PAGE = 80;
  var reqSeq = 0;      // 请求序号，用于丢弃过期的列表结果
  var el = {};

  function $(id) { return document.getElementById(id); }
  function byTag(root, tag) { return root.getElementsByTagName(tag); }

  /* ===================== 工具 ===================== */
  function esc(text) {
    if (text === null || text === undefined) { return ''; }
    return String(text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function api() { return window.pywebview && window.pywebview.api; }

  /* ===================== HTTP 通道（主通道）=====================
     页面由本地 HTTP 服务提供，和接口同源，直接用相对路径 /api/xxx。
     相比 js_api 注入，这条路不依赖内核行为：只要页面显示得出来，
     后端就一定调得到。Win7 的 WebView2 109 上 js_api 经常注入失败，
     所以把它作为主通道。 */
  function httpAvailable() {
    return location.protocol === 'http:' || location.protocol === 'https:';
  }

  function callHttp(name, args) {
    var xhr = null;
    try { xhr = new XMLHttpRequest(); } catch (e) { xhr = null; }
    if (!xhr) { return null; }
    var done = null;
    var obj = {
      then: function (cb) { done = cb; return this; },
      'catch': function () { return this; }
    };
    try {
      xhr.open('POST', '/api/' + name, true);
      xhr.setRequestHeader('Content-Type', 'application/json');
      xhr.onreadystatechange = function () {
        if (xhr.readyState !== 4) { return; }
        var data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
        if (done) {
          // 后端统一返回 {result: ...} 或 {error: "..."}
          done(data && !data.error ? data.result : undefined);
        }
      };
      xhr.send(JSON.stringify(args || []));
    } catch (e) {
      if (done) { done(undefined); }
    }
    return obj;
  }

  /* 兜底通道：window.pywebview.api 没注入时，Python 端会每 0.4 秒
     取走 window.__cmds 里的命令、把结果和事件通过 __pump 送回来。
     这样即使 JS 桥不可用，软件也只是慢半拍，不至于变成空壳。 */
  var fbSeq = 0;
  var fbPending = {};
  window.__cmds = [];
  window.__pump = function (data) {
    var results = (data && data.results) || [];
    for (var i = 0; i < results.length; i++) {
      var item = results[i];
      var cb = fbPending[item.id];
      if (cb) {
        delete fbPending[item.id];
        cb(item.error ? undefined : item.value);
      }
    }
    var events = (data && data.events) || [];
    for (var j = 0; j < events.length; j++) {
      try { handleEvent(events[j]); } catch (e) { /* 单个事件出错不该中断 */ }
    }
  };

  function callFallback(name, args) {
    var id = ++fbSeq;
    window.__cmds.push({ id: id, name: name, args: args || [] });
    return {
      then: function (cb) { fbPending[id] = cb; return this; },
      'catch': function () { return this; }
    };
  }

  /* 后端不可用时的空结果。回调必须在下一个 tick 触发并传 undefined：
     如果像以前那样直接返回 thenable 但永不调用，调用方里
     `state.loading = false` 就永远执行不到，界面会永久卡在加载中。 */
  function deadResult() {
    return {
      then: function (cb) {
        if (cb) { setTimeout(function () { cb(undefined); }, 0); }
        return this;
      },
      'catch': function () { return this; }
    };
  }

  /* 所有后端调用统一走这里：后端不可用时静默失败，不弹一堆错误打断用户 */
  function call(name, args) {
    // 1) 本地 HTTP 服务（主通道，最可靠）
    if (httpAvailable()) {
      var viaHttp = callHttp(name, args);
      if (viaHttp) { return viaHttp; }
    }
    // 2) pywebview 的 js_api（注入成功时才可用）
    var bridge = api();
    if (bridge && typeof bridge[name] === 'function') {
      var result;
      try {
        result = args === undefined ? bridge[name]() : bridge[name].apply(null, args);
      } catch (e) {
        return deadResult();
      }
      return result && typeof result.then === 'function' ? result : {
        then: function (cb) { if (cb) { cb(result); } return this; },
        'catch': function () { return this; }
      };
    }
    // 3) 轮询兜底（Python 端检测到桥不可用时会自动启动 pump）
    if (window.__runtimeReady || window.__cmds) {
      return callFallback(name, args);
    }
    return deadResult();
  }

  function fmtTime(ts) {
    if (!ts) { return ''; }
    var d = new Date(ts * 1000);
    function p(n) { return n < 10 ? '0' + n : '' + n; }
    var now = new Date();
    var sameDay = d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    if (sameDay) { return p(d.getHours()) + ':' + p(d.getMinutes()); }
    var sameYear = d.getFullYear() === now.getFullYear();
    return (sameYear ? '' : d.getFullYear() + '-') +
      p(d.getMonth() + 1) + '-' + p(d.getDate());
  }

  function toast(title, message, ms) {
    // ms=0 是合法的（立刻消失），不能用 || 兜底
    var life = (ms === undefined || ms === null) ? 4200 : ms;
    var box = el.toasts;
    var node = document.createElement('div');
    node.className = 'toast';
    node.innerHTML = '<div class="t-title">' + esc(title) + '</div>' +
      '<div class="t-msg">' + esc(message || '') + '</div>';
    box.appendChild(node);
    setTimeout(function () {
      if (node.parentNode) { node.parentNode.removeChild(node); }
    }, life);
  }

  function status(text) {
    if (el.statusText) { el.statusText.textContent = text; }
  }

  /* ===================== 主题 ===================== */
  function applyTheme(mode) {
    state.theme = (mode === 'dark') ? 'dark' : 'light';
    // 只替换主题类，别整体覆盖 body.className（会吃掉以后加的其它类）
    var rest = document.body.className.replace(/\btheme-\w+\b/g, '')
      .replace(/\s+/g, ' ').trim();
    document.body.className = (rest + ' theme-' + state.theme).trim();
    var img = byTag(el.btnTheme, 'img')[0];
    if (img) { img.src = 'icons/' + (state.theme === 'dark' ? 'sun' : 'moon') + '.png'; }
  }

  /* ===================== 字体与布局 =====================
     这些设置转 pywebview 后一度丢了：DEFAULTS 里还留着 font_family /
     font_size / tree_width，但界面从没读过它们，等于摆设。
     IE11 不支持 CSS 变量，所以只能直接改内联样式。 */
  var FONT_OPTIONS = [
    ['auto', '跟随系统'],
    ['"Microsoft YaHei UI", "Microsoft YaHei", sans-serif', '微软雅黑'],
    ['"SimSun", "宋体", serif', '宋体'],
    ['"SimHei", "黑体", sans-serif', '黑体'],
    ['"KaiTi", "楷体", serif', '楷体'],
    ['"Segoe UI", "PingFang SC", sans-serif', 'Segoe UI'],
    ['"Consolas", "Courier New", monospace', '等宽']
  ];

  function applyLayout() {
    var st = state.settings || {};
    // 界面字号
    var uiSize = parseInt(st.font_size, 10);
    if (uiSize && uiSize >= 9 && uiSize <= 24) {
      document.body.style.fontSize = uiSize + 'px';
    }
    // 字体族
    var fam = st.font_family;
    if (fam && fam !== 'auto') {
      document.body.style.fontFamily = fam;
    } else {
      document.body.style.fontFamily = '';
    }
    // 正文字号
    var bodySize = parseInt(st.content_font_size, 10);
    var rb = $('r-body');
    if (rb) {
      rb.style.fontSize = (bodySize && bodySize >= 9 && bodySize <= 32)
        ? bodySize + 'px' : '';
    }
    // 侧栏宽度
    var treeW = parseInt(st.tree_width, 10);
    if (treeW && treeW >= 150 && treeW <= 420) {
      var sb = document.querySelector('.sidebar');
      if (sb) { sb.style.flex = '0 0 ' + treeW + 'px'; }
    }
  }

  /* ===================== 侧栏 ===================== */
  function renderSidebar() {
    var html = '';
    var counts = state.counts || {};
    function unreadOf(feed) {
      var c = counts[feed.id];
      return c ? (c.unread || 0) : 0;
    }
    var totalUnread = 0;
    for (var i = 0; i < state.feeds.length; i++) {
      totalUnread += unreadOf(state.feeds[i]);
    }
    // 收藏总数只能从后端取：侧栏遍历的是订阅源，没有收藏汇总。
    // 之前这里初始化为 0 后从没赋过值，徽章永远是 0。
    var totalStarred = (state.stats && state.stats.starred) ||
      state.starredTotal || 0;

    function viewNode(key, label, icon, n) {
      var on = (state.view === key && !state.feedId && !state.group) ? ' active' : '';
      return '<div class="node' + on + '" data-view="' + key + '">' +
        '<img class="nicon" src="icons/' + icon + '.png" alt="">' +
        '<span class="nlabel">' + esc(label) + '</span>' +
        '<span class="badge' + (n ? '' : ' zero') + '">' + n + '</span></div>';
    }
    html += viewNode('all', '全部文章', 'feeds', 0);
    html += viewNode('unread', '未读', 'unread', totalUnread);
    html += viewNode('starred', '收藏', 'star', totalStarred);

    if (state.groups.length) {
      for (var g = 0; g < state.groups.length; g++) {
        var name = state.groups[g];
        var gu = 0, list = '';
        for (var k = 0; k < state.feeds.length; k++) {
          if (state.feeds[k].group === name) {
            gu += unreadOf(state.feeds[k]);
            list += feedNode(state.feeds[k], true);
          }
        }
        var gOn = (state.group === name) ? ' active' : '';
        html += '<div class="node group' + gOn + '" data-group="' + esc(name) + '">' +
          '<img class="nicon" src="icons/folder.png" alt="">' +
          '<span class="nlabel">' + esc(name) + '</span>' +
          '<span class="badge' + (gu ? '' : ' zero') + '">' + gu + '</span>' +
          '<img class="gear" src="icons/gear.png" data-groupact="rename" ' +
        'title="重命名" alt=""></div>';
        html += list;
      }
    }

    var loose = [];
    for (var m = 0; m < state.feeds.length; m++) {
      if (!state.feeds[m].group) { loose.push(state.feeds[m]); }
    }
    if (loose.length) {
      html += '<div class="node group"><img class="nicon" src="icons/folder.png" alt="">' +
        '<span class="nlabel">未分组</span></div>';
      for (var q = 0; q < loose.length; q++) { html += feedNode(loose[q], false); }
    }

    el.sidebar.innerHTML = html;
    bindSidebar();
  }

  function feedNode(feed, sub) {
    var c = state.counts[feed.id] || {};
    var unread = c.unread || 0;
    // 一个来自 getAttribute（字符串），一个来自后端（可能是数字），
    // 统一转成字符串再比，否则选中态永远不亮
    var on = (String(state.feedId) === String(feed.id)) ? ' active' : '';
    var icon = feed.enabled ? 'rss' : 'archive';
    return '<div class="node' + (sub ? ' sub' : '') + on + '" data-feed="' + esc(feed.id) + '">' +
      '<img class="nicon" src="icons/' + icon + '.png" alt="">' +
      '<span class="nlabel">' + esc(feed.title || feed.url) + '</span>' +
      '<span class="badge' + (unread ? '' : ' zero') + '">' + unread + '</span></div>';
  }

  function bindSidebar() {
    var nodes = el.sidebar.childNodes;
    for (var i = 0; i < nodes.length; i++) {
      (function (node) {
        node.onclick = function (e) {
          // 齿轮是 group div 里的子 span，事件冒泡上来后 e.target 才是它。
          // 之前判断的是外层 div 上的属性，永远取不到，点⚙变成了切换分组。
          var target = e.target || e.srcElement;
          if (target && target.className &&
              String(target.className).indexOf('gear') >= 0) {
            e.stopPropagation();
            var oldName = node.getAttribute('data-group');
            var renamed = prompt('重命名分组', oldName);
            if (renamed && renamed !== oldName) {
              call('rename_group', [oldName, renamed]).then(function (groups) {
                state.groups = groups || [];
                reloadFeeds();
              });
            }
            return;
          }
          var view = node.getAttribute('data-view');
          var feed = node.getAttribute('data-feed');
          var group = node.getAttribute('data-group');
          if (view) { state.view = view; state.feedId = null; state.group = null; }
          else if (feed) { state.feedId = feed; state.group = null; }
          else if (group) { state.group = group; state.feedId = null; }
          else { return; }
          reloadList();
        };
        node.oncontextmenu = function (e) {
          var feed = node.getAttribute('data-feed');
          var group = node.getAttribute('data-group');
          if (!feed && !group) { return; }
          e.preventDefault();
          var items = [];
          if (feed) {
            items.push(['编辑订阅源', function () { editFeed(feed); }]);
            items.push(['立即刷新', function () { call('refresh', [[feed]]); }]);
            items.push(['标为已读', function () {
              call('mark_all_read', [state.view, feed, null]).then(reloadAfterAction);
            }]);
            items.push(null);
            items.push(['删除订阅源', function () {
              if (confirm('删除这个订阅源？')) {
                call('remove_feed', [feed]).then(reloadFeeds);
              }
            }]);
          } else {
            items.push(['重命名分组', function () {
              var next = prompt('重命名分组', group);
              if (next && next !== group) {
                call('rename_group', [group, next]).then(function (g) {
                  state.groups = g || []; reloadFeeds();
                });
              }
            }]);
            items.push(['标为已读', function () {
              call('mark_all_read', [state.view, null, group]).then(reloadAfterAction);
            }]);
            items.push(null);
            items.push(['删除分组', function () {
              if (confirm('删除分组「' + group + '」？\n文章会保留。')) {
                call('remove_group', [group, false]).then(function (g) {
                  state.groups = g || []; state.group = null; reloadFeeds();
                });
              }
            }]);
          }
          showMenu(e.clientX, e.clientY, items);
        };
      })(nodes[i]);
    }
  }

  /* ===================== 文章列表 ===================== */
  function reloadList() {
    state.offset = 0;
    state.rows = [];
    el.list.innerHTML = '';
    el.listEmpty.style.display = 'none';
    // 重置加载标记：否则上一次请求没回来时，新的 reloadList 会清空
    // DOM 又被 loadPage 开头的 `if (state.loading) return` 挡住，
    // 结果列表一片空白，等旧请求回来再塞进已经对不上的视图。
    state.loading = false;
    loadPage(true);
    renderSidebar();
    updateHead();
  }

  function updateHead() {
    var label = '全部文章';
    if (state.feedId) {
      for (var i = 0; i < state.feeds.length; i++) {
        if (state.feeds[i].id === state.feedId) { label = state.feeds[i].title; break; }
      }
    } else if (state.group) { label = '分组：' + state.group; }
    else if (state.view === 'unread') { label = '未读'; }
    else if (state.view === 'starred') { label = '收藏'; }
    status(label + (state.query ? '（搜索：' + state.query + '）' : ''));
  }

  function loadPage(reset) {
    // 只有追加请求会被"正在加载"挡住；重置请求必须放行，
    // 否则快速切换视图时新请求会被上一次的追加请求挡掉。
    if (!reset && state.loading) { return; }
    state.loading = true;
    if (reset) {
      el.listMore.style.display = 'block';
      reqSeq++;                       // 作废旧请求的结果
    }
    var mine = reqSeq;
    call('get_articles', [state.view, state.feedId, state.group,
      state.query, state.offset, PAGE]).then(function (data) {
      state.loading = false;
      if (mine !== reqSeq) { return; }   // 过期结果，丢弃
      if (!data) { return; }
      state.total = data.total || 0;
      var items = data.items || [];
      if (reset && !items.length) {
        el.listEmpty.style.display = 'block';
        el.listEmpty.textContent = state.query ?
          '没有匹配的文章。' : '这个范围内还没有文章。';
      } else {
        el.listEmpty.style.display = 'none';
      }
      appendRows(items);
      state.offset += items.length;
      el.listMore.style.display =
        (state.offset < state.total) ? 'block' : 'none';
      if (state.offset < state.total) {
        el.listMore.textContent = '滚动加载更多（还有 ' +
          (state.total - state.offset) + ' 篇）';
      }
    });
  }

  /* 一次拼好整页再插入：逐条 appendChild 在几百条时明显更慢 */
  function appendRows(items) {
    if (!items.length) { return; }
    var html = '';
    for (var i = 0; i < items.length; i++) {
      var a = items[i];
      state.rows.push(a);
      var cls = 'row-item ' + (a.read ? 'read' : 'unread');
      html += '<div class="' + cls + '" data-id="' + esc(a.id) + '">' +
        '<span class="star">' + (a.starred ? starIcon() : '') + '</span>' +
        '<span class="rtitle' + (a.read ? ' title-read' : '') + '">' +
        esc(a.title || '（无标题）') + '</span>' +
        '<span class="rsource muted">' + esc(a.feed_title || '') + '</span>' +
        '<span class="rtime muted">' + esc(fmtTime(a.timestamp)) + '</span>' +
        '</div>';
    }
    var box = document.createElement('div');
    box.innerHTML = html;
    // childNodes 是 live 的：appendChild 把节点移走的同时也会把它从
    // box.childNodes 里删掉。必须先快照成普通数组，否则搬完之后
    // kids.length 已经是 0，bindRow 一次都不会执行（整张表失去交互）。
    var kids = box.childNodes;
    var moved = [];
    for (var k = 0; k < kids.length; k++) { moved.push(kids[k]); }
    for (var j = 0; j < moved.length; j++) { el.list.appendChild(moved[j]); }
    for (var b = 0; b < moved.length; b++) { bindRow(moved[b]); }
    highlightRows();
  }

  /* 3D Emoji 图标：所有界面图标统一用微软 Fluent 3D Emoji，
     不再用 ★ / ⚙ 这类 Unicode 字符（不同系统字体渲染不一致，
     深色主题下还可能出现彩色字体的怪异配色）。*/
  function iconHtml(name, cls) {
    return '<img class="' + (cls || 'inline-ico') + '" src="icons/' +
      name + '.png" alt="">';
  }

  function starIcon() {
    return iconHtml('star', 'star-ico');
  }

  /* 搜索命中高亮（mark.js 提供，缺失时静默跳过）*/
  function highlightRows() {
    if (typeof Mark === 'undefined') { return; }
    var q = state.query;
    var rows = el.list.childNodes;
    if (!q) {
      // 没搜索词时清掉残留的高亮
      for (var c = 0; c < rows.length; c++) {
        var marks = rows[c].getElementsByTagName
          ? rows[c].getElementsByTagName('mark') : [];
        for (var m = marks.length - 1; m >= 0; m--) {
          var mk = marks[m];
          if (mk.parentNode) {
            mk.parentNode.replaceChild(
              document.createTextNode(mk.textContent), mk);
          }
        }
      }
      return;
    }
    try {
      var inst = new Mark(rows.length ? el.list : document.body);
      inst.mark(q, {
        element: 'mark',
        className: 'hl',
        separateWordSearch: true,
        exclude: ['.rtime', '.rsource']
      });
    } catch (e) { /* 高亮失败不影响列表本身 */ }
  }

  function bindRow(node) {
    var id = node.getAttribute('data-id');
    node.onclick = function () { markActive(node); };
    node.ondblclick = function () { openReader(id); };
    node.oncontextmenu = function (e) {
      e.preventDefault();
      markActive(node);
      var art = findRow(id);
      if (!art) { return; }
      showMenu(e.clientX, e.clientY, [
        ['阅读', function () { openReader(id); }],
        ['在浏览器中打开', function () { call('open_url', [art.link]); }],
        ['复制链接', function () { call('copy_link', [art.link]); }],
        null,
        [art.starred ? '取消收藏' : '收藏', function () {
          call('toggle_star', [id]).then(function (star) {
            art.starred = star;
            var spans = node.getElementsByTagName('span');
            if (spans.length) {
      spans[0].innerHTML = star ? starIcon() : '';
    }
            toast('已' + (star ? '收藏' : '取消收藏'), art.title);
          });
        }],
        [art.read ? '标为未读' : '标为已读', function () {
          call('mark_read', [id, !art.read]).then(function () {
            art.read = !art.read;
            node.className = 'row-item ' + (art.read ? 'read' : 'unread');
            bumpBadge(art.feed_id, art.read ? -1 : 1);
          });
        }]
      ]);
    };
  }

  function markActive(node) {
    var rows = el.list.childNodes;
    for (var i = 0; i < rows.length; i++) {
      // 用正则全局替换：' active' 只删一个，反复点击会残留多个 active
      rows[i].className = (rows[i].className || '')
        .replace(/\s*\bactive\b/g, '').replace(/\s+/g, ' ').trim();
    }
    node.className = ((node.className || '') + ' active').trim();
  }

  function findRow(id) {
    for (var i = 0; i < state.rows.length; i++) {
      if (state.rows[i].id === id) { return state.rows[i]; }
    }
    return null;
  }

  /* 只改徽标数字，不重建侧栏——重建是之前卡顿的主要来源 */
  function bumpBadge(feedId, delta) {
    var nodes = el.sidebar.childNodes;
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var isFeed = String(node.getAttribute('data-feed')) === String(feedId);
      var isView = node.getAttribute('data-view') === 'unread';
      if (!isFeed && !isView) { continue; }
      // 按类名找，不按下标：分组节点里 span 的顺序和订阅源节点不同，
      // 用 [1] 一旦结构变动就会改错元素。
      var badge = null;
      var spans = node.getElementsByTagName('span');
      for (var k = 0; k < spans.length; k++) {
        if (spans[k].className &&
            spans[k].className.indexOf('badge') >= 0) {
          badge = spans[k];
          break;
        }
      }
      if (!badge) { continue; }
      var n = parseInt(badge.textContent, 10) || 0;
      n = Math.max(0, n + delta);
      badge.textContent = n;
      badge.className = 'badge' + (n ? '' : ' zero');
    }
  }

  /* ===================== 阅读页（SPA 内）=====================
     以前是每篇文章开一个独立窗口，窗口多了很乱、开起来也慢。
     现在改在主页面里切视图：列表和阅读互斥显示，侧栏始终在，
     返回列表不丢滚动位置和筛选条件。 */
  var readerArt = null;

  function showView(name) {
    var isReader = (name === 'reader');
    $('view-list').hidden = isReader;
    $('view-reader').hidden = !isReader;
    state.viewName = isReader ? 'reader' : 'list';
    // 工具栏在列表页才有意义，阅读页隐藏掉避免误点
    try { $('list-head').hidden = isReader; } catch (e) {}
  }

  function backToList() {
    showView('list');
    readerArt = null;
  }

  /* 阅读页要完整日期时间，列表只显示当天时间。
     两个函数不能同名——同名时后定义的会覆盖先定义的，
     列表的时间格式会被阅读页的版本悄悄顶掉。 */
  function fmtDateTime(ts) {
    if (!ts) { return ''; }
    var d = new Date(ts * 1000);
    function p(n) { return n < 10 ? '0' + n : '' + n; }
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
      ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }


  /* 正文渲染：自动判断是 HTML 还是 Markdown。

     后端现在存的是原始内容，可能是：
       · HTML（大多数博客）——直接交给浏览器渲染
       · Markdown 源文本（Hexo / Hugo 之类）——先用 marked 转成 HTML
     判断依据：含常见 HTML 标签就当 HTML；否则交给 marked。
  */
  function looksLikeHtml(text) {
    return /<(p|div|img|br|a|span|h[1-6]|ul|ol|li|table|pre|blockquote|figure|strong|em|code)(\s|>)/i
      .test(text);
  }

  function renderBody(body) {
    if (looksLikeHtml(body)) { return body; }
    // 不是 HTML，试着当 Markdown 解析
    if (typeof marked === 'undefined') { return body; }
    try {
      var parsed = (typeof marked.parse === 'function')
        ? marked.parse(body) : marked(body);
      return parsed || body;
    } catch (e) {
      // 解析失败就原样显示，至少还能读
      return body;
    }
  }

  /* 图片后处理：修好几种"加载不出来"的常见情况。

     1) 懒加载：只有 data-src 没有 src 的 <img> 永远不显示
     2) 反盗链：页面地址是 127.0.0.1，很多图床会因为 Referer 不对
        返回 403。no-referrer 不发送 Referer，多数图床会放行
     3) 加载失败：显示一个占位，而不是浏览器默认的裂图
  */
  function fixImages(root) {
    if (!root) { return; }
    var imgs = root.getElementsByTagName('img');
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      // 1) 懒加载：把 data-* 的地址挪到 src
      if (!img.getAttribute('src')) {
        var lazy = img.getAttribute('data-src') ||
          img.getAttribute('data-original') ||
          img.getAttribute('data-lazy-src') || '';
        if (lazy) { img.setAttribute('src', lazy); }
      }
      // 2) 不带 Referer，绕过大部分图床的防盗链
      if (!img.getAttribute('referrerpolicy')) {
        img.setAttribute('referrerpolicy', 'no-referrer');
      }
      // 3) 失败兜底
      if (!img.getAttribute('data-fallback')) {
        img.setAttribute('data-fallback', '1');
        img.onerror = function () {
          this.onerror = null;
          this.style.display = 'none';
          var tip = document.createElement('span');
          tip.className = 'img-fail';
          tip.textContent = '[图片加载失败]';
          if (this.parentNode) {
            this.parentNode.insertBefore(tip, this);
          }
        };
      }
    }
  }

  function renderReader(art) {
    readerArt = art;
    showView('reader');
    if (!art) {
      $('r-title').textContent = '文章不存在或已被清理';
      $('r-body').innerHTML = '';
      $('r-meta').textContent = '';
      $('r-images').innerHTML = '';
      return;
    }
    $('r-title').textContent = art.title || '（无标题）';

    var parts = [];
    if (art.feed_title) { parts.push(art.feed_title); }
    if (art.author) { parts.push('作者：' + art.author); }
    if (art.published || art.timestamp) {
      parts.push(art.published || fmtDateTime(art.timestamp));
    }
    // meta 里要塞 3D Emoji 图标，所以只能用 innerHTML。
    // 各段都是后端数据，必须先 esc，不能裸拼。
    var html = [];
    for (var pi = 0; pi < parts.length; pi++) {
      html.push(esc(parts[pi]));
    }
    if (art.starred) {
      html.push(iconHtml('star', 'meta-ico') + '已收藏');
    }
    $('r-meta').innerHTML = html.join('　·　');

    var body = art.body || '';
    if (!body.replace(/\s/g, '')) {
      body = '<p class="muted">这篇文章没有正文内容，' +
        '可以点「浏览器打开」看原文。</p>';
    } else {
      body = renderBody(body);
    }
    // 正文原样渲染：图片、链接、排版都保留
    $('r-body').innerHTML = body;
    fixImages($('r-body'));
    $('r-body').scrollTop = 0;

    var starBtn = $('r-star');
    var spans = starBtn.getElementsByTagName('span');
    if (spans.length) { spans[0].textContent = art.starred ? '取消收藏' : '收藏'; }

    // 图片已经在正文里原样显示了，底部不再列链接清单
    var imgs = art.images || [];
    $('r-images').innerHTML = imgs.length
      ? '（正文含 ' + imgs.length + ' 张图片）' : '';
  }

  function openReader(id) {
    var art = findRow(id);
    // 设置里关掉「打开即标已读」后就不该再标
    var autoRead = state.settings.mark_read_on_select !== false;
    if (art && !art.read && autoRead) {
      call('mark_read', [id, true]).then(function () {
        art.read = true;
        var rows = el.list.childNodes;
        for (var i = 0; i < rows.length; i++) {
          if (String(rows[i].getAttribute('data-id')) === String(id)) {
            rows[i].className = 'row-item read active';
            var t = rows[i].getElementsByTagName('span')[1];
            if (t) { t.className = 'rtitle title-read'; }
          }
        }
        bumpBadge(art.feed_id, -1);
      });
    }
    // 直接取数据在本页渲染，不再另开窗口
    call('get_article', [id]).then(function (data) {
      renderReader(data || art);
    });
  }

  function readerStep(delta) {
    if (!readerArt) { return; }
    var r = call('reader_prev_next', [readerArt.id, delta]);
    if (r) {
      r.then(function (next) {
        if (next) { renderReader(next); }
      });
    }
  }

  function bindReader() {
    $('r-back').onclick = backToList;
    $('r-browser').onclick = function () {
      if (readerArt && readerArt.link) {
        call('open_url', [readerArt.link]);
      }
    };
    $('r-star').onclick = function () {
      if (!readerArt) { return; }
      var r = call('toggle_star', [readerArt.id]);
      if (r) {
        r.then(function (star) {
          readerArt.starred = star;
          renderReader(readerArt);
        });
      }
    };
    $('r-unread').onclick = function () {
      if (!readerArt) { return; }
      var r = call('mark_read', [readerArt.id, false]);
      if (r) {
        r.then(function () {
          readerArt.read = false;
          renderReader(readerArt);
          reloadCounts();
        });
      }
    };
    $('r-copy').onclick = function () {
      if (readerArt && readerArt.link) {
        var r = call('copy_link', [readerArt.link]);
        var span = $('r-copy').getElementsByTagName('span')[0];
        if (span) { span.textContent = r === false ? '复制失败' : '已复制'; }
      }
    };
    $('r-prev').onclick = function () { readerStep(-1); };
    $('r-next').onclick = function () { readerStep(1); };
  }

  /* ===================== 右键菜单 ===================== */
  function showMenu(x, y, items) {
    var menu = el.ctxmenu;
    menu.innerHTML = '';
    for (var i = 0; i < items.length; i++) {
      if (!items[i]) {
        var sep = document.createElement('div');
        sep.className = 'sep';
        menu.appendChild(sep);
        continue;
      }
      (function (item) {
        var node = document.createElement('div');
        node.textContent = item[0];
        node.onclick = function () { hideMenu(); item[1](); };
        menu.appendChild(node);
      })(items[i]);
    }
    menu.hidden = false;
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    var rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth) { menu.style.left = (x - rect.width) + 'px'; }
    if (rect.bottom > window.innerHeight) { menu.style.top = (y - rect.height) + 'px'; }
  }

  function hideMenu() { el.ctxmenu.hidden = true; }

  /* ===================== 订阅源数据 ===================== */
  function reloadFeeds() {
    call('get_feeds', []).then(function (feeds) {
      state.feeds = feeds || [];
      renderSidebar();
      updateHead();
    });
    call('stats', []).then(function (s) {
      if (s) {
        el.stats.textContent = s.feeds + ' 个源 · ' + s.articles + ' 篇 · 未读 ' + s.unread;
      }
    });
  }

  function reloadCounts() {
    call('bootstrap', []).then(function (data) {
      if (!data) { return; }
      state.groups = data.groups || [];
      state.counts = data.counts || {};
      state.feeds = data.feeds || [];
      state.starredTotal = data.starred_total || 0;
      renderSidebar();
    });
  }

  function reloadAfterAction() {
    reloadCounts();
    reloadList();
  }

  /* ===================== 添加订阅源 ===================== */
  var probeRows = [];

  function openAdd() {
    probeRows = [];
    $('add-urls').value = '';
    $('add-group').value = '';
    $('probe-body').innerHTML = '';
    $('probe-hint').textContent = '等待输入地址';
    $('mask-add').hidden = false;
    $('add-urls').focus();
  }

  function runProbe() {
    var text = $('add-urls').value;
    if (!text.trim()) {
      $('probe-hint').textContent = '请先输入至少一个 http(s) 地址';
      return;
    }
    $('probe-hint').textContent = '正在检测…';
    call('probe_feeds', [text]).then(function (rows) {
      probeRows = rows || [];
      renderProbe();
      var ok = 0;
      for (var i = 0; i < probeRows.length; i++) { if (probeRows[i].ok) { ok++; } }
      $('probe-hint').textContent = '检测完成：' + ok + ' 个可用 / 共 ' +
        probeRows.length + ' 个';
    });
  }

  function renderProbe() {
    var html = '';
    for (var i = 0; i < probeRows.length; i++) {
      var r = probeRows[i];
      var checked = r.ok ? 'checked' : '';
      html += '<tr data-i="' + i + '">' +
        '<td class="c-check"><input type="checkbox" ' + checked + '></td>' +
        '<td class="c-status ' + (r.ok ? 'ok' : 'bad') + '">' + esc(r.status) + '</td>' +
        '<td>' + esc(r.ok ? (r.title || '') : (r.message || '')) + '</td>' +
        '<td>' + esc(r.url) + '</td>' +
        '<td class="c-count">' + (r.ok ? r.count : '') + '</td></tr>';
    }
    $('probe-body').innerHTML = html;
  }

  function selectAllOk() {
    var rows = byTag($('probe-body'), 'tr');
    for (var i = 0; i < rows.length; i++) {
      var idx = parseInt(rows[i].getAttribute('data-i'), 10);
      var box = byTag(rows[i], 'input')[0];
      if (box) { box.checked = !!(probeRows[idx] && probeRows[idx].ok); }
    }
  }

  function addSelected() {
    var rows = byTag($('probe-body'), 'tr');
    var urls = [];
    for (var i = 0; i < rows.length; i++) {
      var box = byTag(rows[i], 'input')[0];
      if (!box || !box.checked) { continue; }
      var idx = parseInt(rows[i].getAttribute('data-i'), 10);
      var r = probeRows[idx];
      if (!r) { continue; }
      urls.push(r.resolved_url || r.url);
    }
    if (!urls.length) {
      var text = $('add-urls').value;
      if (text.trim()) {
        // 没点「检测全部」直接粘贴地址时，把原文交给后端解析，
        // 而不是传一个空数组过去让人以为添加成功了。
        call('add_feeds', [text, $('add-group').value]).then(function (added) {
          $('mask-add').hidden = true;
          toast('已添加', (added ? added.length : 0) + ' 个订阅源，正在抓取文章…');
          reloadFeeds();
          reloadCounts();
        });
        return;
      }
      $('probe-hint').textContent = '请至少勾选一个，或先点「检测全部」';
      return;
    }
    call('add_feeds', [urls, $('add-group').value]).then(function (added) {
      $('mask-add').hidden = true;
      toast('已添加', (added ? added.length : 0) + ' 个订阅源，正在抓取文章…');
      reloadFeeds();
      reloadCounts();
    });
  }

  /* ===================== 编辑订阅源 ===================== */
  var editingId = null;

  function editFeed(id) {
    for (var i = 0; i < state.feeds.length; i++) {
      if (state.feeds[i].id === id) {
        var f = state.feeds[i];
        editingId = id;
        $('ed-title').value = f.title || '';
        $('ed-url').value = f.url || '';
        $('ed-site').value = f.site_url || '';
        $('ed-group').value = f.group || '';
        $('ed-tags').value = (f.tags || []).join(', ');
        $('ed-note').value = f.note || '';
        $('ed-minutes').value = f.refresh_minutes || 0;
        $('ed-enabled').checked = !!f.enabled;
        $('ed-status').textContent = f.last_status ?
          ('上次结果：' + f.last_status) : '';
        $('mask-edit').hidden = false;
        return;
      }
    }
  }

  function saveEdit() {
    if (!editingId) { return; }
    var tags = $('ed-tags').value.split(/[,，]/);
    var clean = [];
    for (var i = 0; i < tags.length; i++) {
      if (tags[i].trim()) { clean.push(tags[i].trim()); }
    }
    call('update_feed', [editingId, {
      title: $('ed-title').value, url: $('ed-url').value,
      site_url: $('ed-site').value, group: $('ed-group').value,
      tags: clean, note: $('ed-note').value,
      refresh_minutes: parseInt($('ed-minutes').value, 10) || 0,
      enabled: $('ed-enabled').checked
    }]).then(function () {
      $('mask-edit').hidden = true;
      editingId = null;
      reloadFeeds();
      reloadCounts();
    });
  }

  /* ===================== 设置 ===================== */
  function fillFontOptions(current) {
    var sel = $('s-fontfamily');
    if (!sel) { return; }
    var html = '';
    for (var i = 0; i < FONT_OPTIONS.length; i++) {
      var v = FONT_OPTIONS[i][0], label = FONT_OPTIONS[i][1];
      var on = (current && current === v) ? ' selected' : '';
      html += '<option value="' + esc(v) + '"' + on + '>' +
        esc(label) + '</option>';
    }
    sel.innerHTML = html;
    if (!current || current === 'auto') { sel.value = 'auto'; }
  }

  function openSettings() {
    var s = state.settings || {};
    $('s-notify-enabled').checked = s.notify_enabled !== false;
    $('s-per-feed').value = s.notify_per_feed_limit || 3;
    $('s-max-total').value = s.notify_max_total || 8;
    $('s-sound').checked = s.notify_sound !== false;
    $('s-system').checked = s.notify_system !== false;
    $('s-quiet').value = s.notify_silent_hours || '';
    $('s-interval').value = s.auto_refresh_interval_min || 5;
    $('s-timeout').value = s.refresh_timeout || 15;
    $('s-workers').value = s.refresh_workers || 6;
    $('s-autostart').checked = !!s.start_hidden;
    $('s-tray').checked = s.minimize_to_tray !== false;
    var themeValue = (s.theme === 'dark') ? 'dark' : 'light';
    $('s-theme').value = themeValue;   // 未知值兜底成浅色，否则 select 会空白
    fillFontOptions(s.font_family);
    $('s-uifontsize').value = s.font_size || 13;
    $('s-fontsize').value = s.content_font_size || 11;
    $('s-treewidth').value = s.tree_width || 232;
    // window_geometry 形如 "1180x720" 或 "1180x720+100+50"，
    // 只取宽高两段，位置部分忽略（跨显示器时恢复位置反而可能跑到屏幕外）
    var gw = 1080, gh = 700;
    var gm = String(s.window_geometry || '').match(/(\d+)\s*[x×]\s*(\d+)/i);
    if (gm) {
      gw = parseInt(gm[1], 10) || gw;
      gh = parseInt(gm[2], 10) || gh;
    }
    $('s-winw').value = gw;
    $('s-winh').value = gh;
    $('s-markread').checked = s.mark_read_on_select !== false;
    $('set-status').textContent = '';
    call('backend_info', []).then(function (info) {
      $('notify-backends').textContent = (info && info.length) ?
        ('可用通知方式：' + info.join('、')) : '未检测到系统通知能力，将使用页面内气泡';
    });
    call('stats', []).then(function (st) {
      if (st) {
        $('data-stats').textContent = '共 ' + st.feeds + ' 个订阅源、' +
          st.articles + ' 篇文章（收藏 ' + st.starred + '）';
      }
    });
    fillAbout();
    $('mask-set').hidden = false;
  }

  function saveSettings() {
    var data = {
      notify_enabled: $('s-notify-enabled').checked,
      notify_per_feed_limit: parseInt($('s-per-feed').value, 10) || 3,
      notify_max_total: parseInt($('s-max-total').value, 10) || 8,
      notify_sound: $('s-sound').checked,
      notify_system: $('s-system').checked,
      notify_silent_hours: $('s-quiet').value,
      auto_refresh_interval_min: parseInt($('s-interval').value, 10) || 5,
      refresh_timeout: parseInt($('s-timeout').value, 10) || 15,
      refresh_workers: parseInt($('s-workers').value, 10) || 6,
      start_hidden: $('s-autostart').checked,
      minimize_to_tray: $('s-tray').checked,
      theme: $('s-theme').value,
      font_family: $('s-fontfamily').value || 'auto',
      font_size: parseInt($('s-uifontsize').value, 10) || 13,
      content_font_size: parseInt($('s-fontsize').value, 10) || 11,
      tree_width: parseInt($('s-treewidth').value, 10) || 232,
      window_geometry: (parseInt($('s-winw').value, 10) || 1080) + 'x' +
        (parseInt($('s-winh').value, 10) || 700),
      mark_read_on_select: $('s-markread').checked
    };
    call('save_settings', [data]).then(function (saved) {
      state.settings = saved || data;
      applyTheme(state.settings.theme);
      // 字号 / 字体 / 侧栏宽度要立刻生效，不然得重启才看得到
      applyLayout();
      // 窗口尺寸交给后端，让 pywebview 真正 resize
      try {
        var w = parseInt($('s-winw').value, 10);
        var h = parseInt($('s-winh').value, 10);
        if (w && h) { call('resize_window', [w, h]); }
      } catch (e) { /* 失败不影响设置保存 */ }
      $('mask-set').hidden = true;
      toast('已保存', '设置已生效');
    });
  }

  /* ===================== 关于 / 检查更新 ===================== */
  function fillAbout() {
    var app = (state.boot && state.boot.app) || {};
    $('ab-name').textContent = app.name || 'GooseRSS 阅读器';
    $('ab-version').textContent = app.version || '';
    var repo = app.repo || 'https://github.com/Minecraftgoose/GooseRSS/';
    $('ab-repo-text').textContent = repo.replace(/^https?:\/\//, '');
    $('btn-repo').onclick = function () { call('open_url', [repo]); };
  }

  function renderUpdate(r) {
    var box = $('ab-update');
    if (!box) { return; }
    if (!r || !r.ok) {
      box.hidden = false;
      box.innerHTML = '<div class="up-err">检查失败：' +
        esc((r && r.error) || '未知错误') + '</div>';
      return;
    }
    var html = '';
    if (r.has) {
      html += '<div class="up-new">发现新版本 ' + esc(r.latest) +
        '（当前 ' + esc(r.current) + '）</div>';
      if (r.name && r.name !== r.latest) {
        html += '<div class="up-title">' + esc(r.name) + '</div>';
      }
      html += '<div class="row"><button class="btn primary" id="btn-go-release">' +
        '<img src="icons/globe.png" alt=""><span>前往下载</span></button></div>';
    } else {
      html += '<div class="up-ok">已是最新版本（' + esc(r.current) + '）</div>';
    }
    // 更新说明是 Markdown，用 marked 渲染（缺失时退回纯文本）
    if (r.body) {
      html += '<div class="up-body">' + renderMarkdown(r.body) + '</div>';
    }
    box.hidden = false;
    box.innerHTML = html;
    var go = $('btn-go-release');
    if (go) {
      go.onclick = function () { call('open_url', [r.url]); };
    }
  }

  function renderMarkdown(text) {
    if (typeof marked === 'undefined') {
      return '<pre>' + esc(text) + '</pre>';
    }
    try {
      return (typeof marked.parse === 'function') ? marked.parse(text)
        : marked(text);
    } catch (e) {
      return '<pre>' + esc(text) + '</pre>';
    }
  }

  function bindSettingsExtra() {
    var cu = $('btn-check-update');
    if (cu) {
      cu.onclick = function () {
        $('ab-update-status').textContent = '正在检查…';
        call('check_update', []).then(function (r) {
          $('ab-update-status').textContent = '';
          renderUpdate(r);
        });
      };
    }
    var btn = $('btn-reset-win');
    if (btn) {
      btn.onclick = function () {
        $('s-winw').value = 1080;
        $('s-winh').value = 700;
        var hint = $('win-hint');
        if (hint) { hint.textContent = '已设为 1080×700，保存后生效'; }
      };
    }
  }

  /* ===================== 轮询：后端 → 前端 ===================== */
  function poll() {
    call('poll', []).then(function (events) {
      if (!events || !events.length) { return; }
      for (var i = 0; i < events.length; i++) { handleEvent(events[i]); }
    });
  }

  function handleEvent(e) {
    var kind = e.kind;
    if (kind === 'refresh_start') {
      state.refreshing = true;
      el.progress.hidden = false;
      el.btnRefresh.disabled = true;
      setProgress(0, e.total || 0);
    } else if (kind === 'refresh_progress') {
      setProgress(e.done || 0, e.total || 0);
    } else if (kind === 'refresh_finish') {
      state.refreshing = false;
      el.progress.hidden = true;
      el.btnRefresh.disabled = false;
      var n = e.new_count || 0;
      if (n > 0) {
        status('刷新完成，新增 ' + n + ' 篇');
        call('send_notifications', []).then(function (sent) {
          if (!sent) { toast('新文章', '新增 ' + n + ' 篇（通知已暂停或处于免打扰）'); }
        });
      } else {
        status('刷新完成，没有新文章');
      }
      reloadCounts();
      reloadFeeds();
      // 后台每几分钟自动刷新一次。无条件 reloadList 会把正在翻页的
      // 用户弹回顶部——只在列表处于顶部时才重建，否则只更新计数。
      if (el.list.scrollTop <= 40) { reloadList(); }
      else if (n > 0) {
        status('刷新完成，新增 ' + n + ' 篇（滚动到顶部可查看）');
      }
    } else if (kind === 'bubble') {
      toast(e.title || '新文章', e.message || '');
    } else if (kind === 'feeds_changed') {
      reloadFeeds();
      reloadCounts();
    } else if (kind === 'tray_action') {
      if (e.action === 'show') { call('show_window', []); }
      else if (e.action === 'refresh') { call('refresh', [null]); }
      else if (e.action === 'quit') { call('quit_app', []); }
    }
  }

  function setProgress(done, total) {
    var pct = total ? Math.round(done * 100 / total) : 0;
    el.progressBar.style.width = pct + '%';
    el.progressText.textContent = done + '/' + total;
  }

  /* ===================== 绑定 ===================== */
  function bind() {
    bindReader();
    el = {
      sidebar: $('sidebar'), list: $('list'), listMore: $('list-more'),
      listEmpty: $('list-empty'), statusText: $('status-text'),
      stats: $('stats'), toasts: $('toasts'), ctxmenu: $('ctxmenu'),
      btnTheme: $('btn-theme'), btnRefresh: $('btn-refresh'),
      progress: $('progress'), progressBar: $('progress-bar'),
      progressText: $('progress-text')
    };

    $('btn-add').onclick = openAdd;
    $('btn-probe').onclick = runProbe;
    $('btn-selectok').onclick = selectAllOk;
    $('btn-add-ok').onclick = addSelected;
    $('btn-import-opml').onclick = function () {
      call('import_opml', [null, $('add-group').value]).then(function (r) {
        if (r && r.ok) { toast('导入完成', '新增 ' + r.added + ' 个订阅源'); reloadFeeds(); }
        else if (r && r.error && r.error !== '已取消') { toast('导入失败', r.error); }
        $('mask-add').hidden = true;
      });
    };

    $('btn-edit-ok').onclick = saveEdit;
    $('btn-del-feed').onclick = function () {
      if (!editingId) { return; }
      if (confirm('删除这个订阅源？它的文章也会一起删除。')) {
        call('remove_feed', [editingId]).then(function () {
          $('mask-edit').hidden = true; editingId = null; reloadFeeds();
        });
      }
    };

    $('btn-settings').onclick = openSettings;
    $('btn-set-ok').onclick = saveSettings;
    $('btn-test-notify').onclick = function () {
      var backendNames = {
        balloon: '托盘气球',
        bubble: '页面气泡'
      };
      call('test_notification', []).then(function (r) {
        if (r && r.backend) {
          $('set-status').textContent = '已通过 ' +
            (backendNames[r.backend] || r.backend) + ' 发出';
        } else {
          $('set-status').textContent = '发送失败：' +
            ((r && r.error) || '未知，可能是通知被免打扰或托盘不可用');
        }
      });
    };
    $('btn-export').onclick = function () {
      call('export_opml', [null]).then(function (r) {
        $('set-status').textContent = (r && r.ok) ? '已导出到 ' + r.path :
          ('导出失败：' + ((r && r.error) || '未知'));
      });
    };
    $('btn-import').onclick = function () {
      call('import_opml', [null, '']).then(function (r) {
        if (r && r.ok) {
          toast('导入完成', '新增 ' + r.added + ' 个');
          reloadFeeds();
          reloadCounts();
          reloadList();
        }
      });
    };
    $('btn-clear').onclick = function () {
      if (confirm('清空所有文章？收藏的会保留。')) {
        call('clear_articles', [true]).then(function () { reloadList(); reloadFeeds(); });
      }
    };

    $('btn-theme').onclick = function () {
      var next = state.theme === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      state.settings.theme = next;   // 不回写的话，设置面板里还显示旧值
      call('save_settings', [{ theme: next }]);
    };

    $('btn-notify').onclick = function () {
      var on = !(state.settings.notify_enabled !== false);
      call('save_settings', [{ notify_enabled: on }]).then(function (saved) {
        state.settings = saved || state.settings;
        toast(on ? '已开启提醒' : '已暂停提醒', on ? '有新文章会通知你' : '仍然会刷新，但不弹通知');
      });
    };

    $('btn-refresh').onclick = function () { call('refresh', [null]); };
    $('btn-readall').onclick = function () {
      call('mark_all_read', [state.view, state.feedId, state.group])
        .then(function (n) {
          toast('已标记', (n || 0) + ' 篇为已读');
          reloadCounts(); reloadList();
        });
    };
    $('btn-newgroup').onclick = function () {
      var name = prompt('新分组名称');
      if (name) {
        call('add_group', [name]).then(function (groups) {
          state.groups = groups || []; renderSidebar();
        });
      }
    };

    var segs = document.getElementsByClassName('seg');
    for (var i = 0; i < segs.length; i++) {
      (function (seg) {
        seg.onclick = function () {
          state.view = seg.getAttribute('data-view');
          state.feedId = null; state.group = null;
          for (var j = 0; j < segs.length; j++) {
            segs[j].className = 'seg' + (segs[j] === seg ? ' on' : '');
          }
          reloadList();
        };
      })(segs[i]);
    }

    var searchTimer = null;
    $('search').oninput = function () {
      var value = $('search').value;
      if (searchTimer) { clearTimeout(searchTimer); }
      searchTimer = setTimeout(function () {
        state.query = value.trim();
        reloadList();
      }, 260);
    };
    $('search-clear').onclick = function () {
      $('search').value = ''; state.query = ''; reloadList();
    };

    el.list.onscroll = function () {
      if (el.list.scrollTop + el.list.clientHeight >=
          el.list.scrollHeight - 240) {
        if (state.offset < state.total) { loadPage(false); }
      }
    };

    var closers = document.querySelectorAll('[data-close]');
    for (var c = 0; c < closers.length; c++) {
      (function (node) {
        node.onclick = function () { $(node.getAttribute('data-close')).hidden = true; };
      })(closers[c]);
    }

    var tabs = document.getElementsByClassName('tab');
    for (var t = 0; t < tabs.length; t++) {
      (function (tab) {
        tab.onclick = function () {
          for (var k = 0; k < tabs.length; k++) { tabs[k].className = 'tab'; }
          tab.className = 'tab on';
          var pages = document.getElementsByClassName('page');
          for (var p = 0; p < pages.length; p++) { pages[p].hidden = true; }
          $(tab.getAttribute('data-tab')).hidden = false;
        };
      })(tabs[t]);
    }

    var masks = document.getElementsByClassName('mask');
    for (var m = 0; m < masks.length; m++) {
      (function (mask) {
        mask.onclick = function (e) {
          if (e.target === mask) { mask.hidden = true; }
        };
      })(masks[m]);
    }

    document.onclick = function () { hideMenu(); };
    document.onkeydown = function (e) {
      // 阅读页优先：Esc 返回列表、← → 翻篇、Ctrl+S 收藏、Ctrl+O 浏览器打开
      if (state.viewName === 'reader') {
        if (e.keyCode === 27) { if (e.preventDefault) { e.preventDefault(); } backToList(); return; }
        if (e.keyCode === 37) { if (e.preventDefault) { e.preventDefault(); } readerStep(-1); return; }
        if (e.keyCode === 39) { if (e.preventDefault) { e.preventDefault(); } readerStep(1); return; }
        if (e.keyCode === 83 && e.ctrlKey) { if (e.preventDefault) { e.preventDefault(); } $('r-star').click(); return; }
        if (e.keyCode === 79 && e.ctrlKey) { if (e.preventDefault) { e.preventDefault(); } $('r-browser').click(); return; }
      }

      if (e.keyCode === 27) { hideMenu(); }
    };
    window.onbeforeunload = function () { call('hide_window', []); };
  }

  /* ===================== 启动 ===================== */
  /* pywebview 的 api 桥是在页面加载之后才注入的。
     官方文档明确说 window.pywebview.api 不保证在 load 事件时可用，
     推荐监听 pywebviewready。这里事件 + 轮询双保险：
     · 事件在支持的版本上最快
     · 轮询兜住不触发事件的版本（以及 IE11 内核）
  */
  var readyDone = false;

  function whenReady(cb) {
    function fire() {
      if (readyDone) { return; }
      readyDone = true;
      cb();
    }
    try {
      if (window.addEventListener) {
        window.addEventListener('pywebviewready', fire);
      } else if (window.attachEvent) {          // IE8-10
        window.attachEvent('onpywebviewready', fire);
      }
    } catch (e) { /* 老内核不支持就只靠轮询 */ }

    var tries = 0;
    (function check() {
      if (readyDone) { return; }
      // HTTP 模式下后端在页面加载前就已经在跑，不必等注入
      if (httpAvailable()) { fire(); return; }
      if (api()) { fire(); return; }
      // Python 端已确认活着（它推过 __runtime），但桥迟迟没注入：
      // 别再干等，直接切到轮询兜底，软件照样能用。
      if (window.__runtimeReady && tries > 70) {   // 约 3.5 秒
        window.__useFallback = true;
        fire();
        return;
      }
      if (tries++ > 200) {          // 约 10 秒
        readyDone = true;
        showBackendError();
        return;
      }
      setTimeout(check, 50);
    })();
  }

  /* 连接不上时给出可诊断的信息，而不是一句含糊的"请重启"。
     关键区分点：window.__runtime 是 Python 主动推过来的，
     如果它存在说明 Python 端活着、是 JS 桥没注入；
     如果它也不存在，说明 Python 端根本没把窗口跑起来。 */
  function showBackendError() {
    var hasPy = !!window.pywebview;
    var hasRuntime = !!window.__runtimeReady;
    var detail = '后端未连接。';
    if (hasRuntime) {
      detail += 'Python 端已运行（内核 ' +
        (window.__runtime && window.__runtime.gui) +
        '），但 JS 桥未注入——多为渲染内核与 pywebview 版本不匹配。';
    } else if (hasPy) {
      detail += 'pywebview 对象存在但 api 未就绪。';
    } else if (location.protocol === 'file:') {
      detail += '页面以 file:// 打开，本地 HTTP 服务未启动——' +
        '请检查端口是否被占用，或用 --gui 指定渲染内核。';
    } else {
      detail += '后端无响应，请查看控制台输出。';
    }
    status(detail);
    var box = document.getElementById('list-empty');
    if (box) {
      box.style.display = 'block';
      box.innerHTML = '<div style="padding:12px;line-height:1.7">' +
        '<b>后端未连接</b><br>' + esc(detail) +
        '<br><br>可尝试：<br>' +
        '· 重新安装兼容版本：<code>pip install "pywebview>=4.0,<6.0"</code><br>' +
        '· 指定渲染内核：<code>python main.py --gui edgechromium</code>' +
        '（Win7 用 <code>mshtml</code>）<br>' +
        '· 跑诊断脚本：<code>python test/check_env.py</code></div>';
    }
  }

  function boot() {
    bind();
    bindSettingsExtra();
    whenReady(startup);
  }

  function startup() {
    call('bootstrap', []).then(function (data) {
      if (!data) {
        status('未能连接到后端，界面数据不可用');
        return;
      }
      state.settings = data.settings || {};
      state.boot = data;
      state.groups = data.groups || [];
      state.feeds = data.feeds || [];
      state.counts = data.counts || {};
      applyTheme(state.settings.theme || 'light');
      applyLayout();
      var segs = document.getElementsByClassName('seg');
      for (var i = 0; i < segs.length; i++) {
        segs[i].className = 'seg' +
          (segs[i].getAttribute('data-view') === 'all' ? ' on' : '');
      }
      renderSidebar();
      reloadFeeds();
      loadPage(true);
      var guiName = (window.__runtime && window.__runtime.gui) || '';
      var channel = httpAvailable() ? '本地服务' :
        (window.__useFallback ? '轮询兜底' : '注入桥');
      status('就绪' + (guiName ? '　·　渲染内核 ' + guiName : '') +
        '　·　后端通道 ' + channel);
      // 托盘是通知器的主入口，没有它用户关了窗口就找不回来了
      var tray = data && data.tray;
      if (tray && !tray.ok) {
        toast('托盘不可用', '原因：' + (tray.reason || '未知') +
          '。关闭窗口后程序虽仍在后台运行，但无法从托盘唤回。', 9000);
      }
      // 兜底模式下由 Python 侧的 pump 主动推事件，前端再 poll 会导致
      // 事件被取两次、刷新进度重复。
      if (!window.__useFallback) { setInterval(poll, 400); }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
