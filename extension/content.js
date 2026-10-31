/* 悬浮卡：抓当前题目 → 录用进本地库。主程序没开就先排队，开了自动补写。 */

(function () {
  if (window.__scjInjected) return;
  window.__scjInjected = true;

  const SLOTS = [['morning', '早班'], ['noon', '午班'], ['evening', '晚班']];
  let info = null;
  let cards = [];
  let folded = false;

  // waitMs 要覆盖「主程序没开 → 走 scj:// 拉起来 → 等服务就绪」这几秒，
  // 否则会在程序刚启动时误报「已暂存」
  const send = (msg, waitMs = 5000) =>
    new Promise((resolve) => {
      let done = false;
      const finish = (v) => { if (!done) { done = true; resolve(v || { ok: false }); } };
      // 后台 service worker 可能被浏览器休眠，兜个超时，别让按钮一直没反应
      setTimeout(() => finish({ ok: false, offline: true }), waitMs);
      try {
        chrome.runtime.sendMessage(msg, (res) => finish(res));
      } catch (e) {
        finish({ ok: false, offline: true });
      }
    });

  function slugOf() {
    const m = location.pathname.match(/\/problems\/([^/?#]+)/);
    return m ? m[1] : '';
  }

  function textOf(sel) {
    const el = document.querySelector(sel);
    return el ? el.textContent.trim() : '';
  }

  function grab() {
    const slug = slugOf();
    if (!slug || slug === 'all') return null;

    let title = textOf('[data-cy="question-title"]') || textOf('h1') || textOf('.text-title-large');
    if (!title) title = (document.title || '').split(' - ')[0].trim();

    let lid = '';
    const head = document.title.match(/^(\d+)\./);
    if (head) lid = head[1];
    if (!lid) {
      const t = title.match(/^(\d+)\.\s*/);
      if (t) { lid = t[1]; title = title.replace(/^\d+\.\s*/, ''); }
    }
    title = title.replace(/\s*-\s*力扣.*$/, '').replace(/^\d+\.\s*/, '').trim();

    let difficulty = '';
    const dEl = document.querySelector('[class*="text-difficulty-"]');
    const dText = dEl ? dEl.textContent.trim() : '';
    if (/简单|Easy/i.test(dText)) difficulty = '简单';
    else if (/中等|Medium/i.test(dText)) difficulty = '中等';
    else if (/困难|Hard/i.test(dText)) difficulty = '困难';

    const tags = [...document.querySelectorAll('a[href*="/tag/"], .topic-tag')]
      .map((a) => a.textContent.trim())
      .filter((t) => t && t.length < 20)
      .slice(0, 4);

    const solved = !!document.querySelector('.text-message.text-green-s, [data-e2e-locator="submission-result"]');

    return {
      leetcode_id: lid,
      title,
      slug,
      difficulty,
      tags: tags.join(','),
      url: location.href.split('?')[0],
      solved,
    };
  }

  function el(tag, cls, txt) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  }

  function build() {
    const root = el('div', 'scj-root');
    root.id = 'scj-root';
    root.innerHTML = `
      <div class="scj-head">
        <span class="scj-brand">刷题调度</span>
        <button class="scj-fold" title="收起">–</button>
      </div>
      <div class="scj-body">
        <div class="scj-prob"><b class="scj-id"></b><span class="scj-title"></span></div>
        <div class="scj-meta"></div>
        <div class="scj-line">
          <label>排进</label>
          <select class="scj-slot">
            <option value="auto">自动（按时间）</option>
            ${SLOTS.map(([v, t]) => `<option value="${v}">${t}</option>`).join('')}
          </select>
        </div>
        <div class="scj-line">
          <label>挂到卡</label>
          <select class="scj-card"><option value="">不挂卡，只入库</option></select>
        </div>
        <div class="scj-actions">
          <button class="scj-btn primary scj-take">录用</button>
          <button class="scj-btn scj-new">新建题型</button>
        </div>
        <div class="scj-newbox" hidden>
          <input class="scj-newtitle" placeholder="题型名，如：区间 DP">
          <button class="scj-btn scj-save-new">建题型并录用</button>
        </div>
        <div class="scj-hint" hidden></div>
      </div>`;
    document.body.appendChild(root);
    return root;
  }

  let root = null;

  function paint() {
    if (!info) return;
    root.querySelector('.scj-id').textContent = info.leetcode_id ? `#${info.leetcode_id}` : '#?';
    root.querySelector('.scj-title').textContent = info.title || '未识别标题';
    root.querySelector('.scj-meta').textContent =
      [info.difficulty, info.tags, info.solved ? '已通过' : ''].filter(Boolean).join(' · ') || '—';
  }

  async function refreshStatus() {
    const s = await send({ type: 'status' });
    status = { online: !!s.online, pending: s.pending || 0 };
    if (status.pending > 0 && status.online) {
      await send({ type: 'flush' });
    }
  }

  async function loadCards() {
    const st = await send({ type: 'status' });
    if (!st.port) return;
    try {
      const r = await fetch(`http://127.0.0.1:${st.port}/api/cards`);
      if (!r.ok) return;
      cards = await r.json();
      const sel = root.querySelector('.scj-card');
      const keep = sel.value;
      sel.innerHTML = '<option value="">不挂卡，只入库</option>' +
        cards.filter((c) => c.status === 'active')
          .map((c) => `<option value="${c.id}">${c.title.replace(/</g, '&lt;')}</option>`).join('');
      sel.value = keep;
    } catch (e) { /* 离线，先不管 */ }
  }

  function flash(msg, kind) {
    const box = document.createElement('div');
    box.className = 'scj-flash ' + (kind || '');
    box.textContent = msg;
    // 挂在 body 上并贴着卡片顶部，避免被卡片的 overflow:hidden 裁掉
    const rect = root.getBoundingClientRect();
    box.style.top = Math.max(8, rect.top - 46) + 'px';
    box.style.right = Math.max(8, window.innerWidth - rect.right) + 'px';
    document.body.appendChild(box);
    setTimeout(() => box.remove(), 2000);
  }

  async function submit(extra) {
    const body = {
      leetcode_id: info.leetcode_id,
      title: info.title,
      slug: info.slug,
      difficulty: info.difficulty,
      tags: info.tags,
      url: info.url,
      solved: info.solved,
      slot: root.querySelector('.scj-slot').value,
      ...extra,
    };
    const btn = root.querySelector('.scj-take');
    if (btn.disabled) return;
    btn.disabled = true;
    btn.textContent = '提交中…';
    try {
      const res = await send({ type: 'api', path: '/capture', method: 'POST', body }, 12000);
      if (res.ok) {
        const d = res.data || {};
        if (d.queued) flash('已录用，在途已满，排进候补', 'warn');
        else if (res.launched) flash('已启动主程序并录用', 'ok');
        else flash('录用成功', 'ok');
        refreshStatus();
      } else if (res.offline) {
        flash('已暂存，主程序启动后自动写入', 'warn');
      } else {
        flash('录用失败，' + (res.error || '未知原因'), 'err');
      }
    } finally {
      btn.disabled = false;
      btn.textContent = '录用';
    }
  }

  function mount() {
    info = grab();
    if (!info) return;
    if (!root) {
      root = build();
      root.querySelector('.scj-fold').addEventListener('click', () => {
        folded = !folded;
        root.classList.toggle('folded', folded);
        root.querySelector('.scj-fold').textContent = folded ? '+' : '–';
      });
      root.querySelector('.scj-take').addEventListener('click', () => {
        const cardId = root.querySelector('.scj-card').value;
        submit(cardId ? { card_id: Number(cardId) } : {});
      });
      root.querySelector('.scj-new').addEventListener('click', () => {
        const box = root.querySelector('.scj-newbox');
        box.hidden = !box.hidden;
        if (!box.hidden) root.querySelector('.scj-newtitle').focus();
      });
      root.querySelector('.scj-save-new').addEventListener('click', () => {
        const name = root.querySelector('.scj-newtitle').value.trim();
        if (!name) { flash('给题型起个名', 'warn'); return; }
        submit({ new_card_title: name });
      });
    }
    root.querySelector('.scj-slot').value = 'auto';
    paint();
    loadCards();
    refreshStatus();
  }

  let lastUrl = location.href;
  setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      setTimeout(mount, 800);
    }
  }, 1000);

  setInterval(refreshStatus, 20000);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(mount, 600));
  } else {
    setTimeout(mount, 600);
  }
})();
