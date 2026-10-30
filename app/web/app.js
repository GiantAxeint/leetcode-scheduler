/* 刷题调度 · 前端（零依赖） */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

let TODAY = null;
let CURRENT_VIEW = 'today';
let REVIEW = null;

const SLOT_LABEL = { morning: '早班', noon: '午班', evening: '晚班' };

/* 题目链接：优先用存下来的 url，其次按 slug 拼，最后退到题库搜索 */
function lcUrl(o) {
  if (!o) return '';
  if (o.url) return o.url;
  if (o.slug) return `https://leetcode.cn/problems/${o.slug}/`;
  if (o.leetcode_id) return `https://leetcode.cn/problemset/?search=${o.leetcode_id}`;
  return '';
}

/* LeetCode 官方难度色：简单 #00B8A3 / 中等 #FFB800 / 困难 #FF375F */
function diffClass(d) {
  const s = String(d || '');
  if (/简单|easy/i.test(s)) return 'd-easy';
  if (/中等|medium/i.test(s)) return 'd-medium';
  if (/困难|hard/i.test(s)) return 'd-hard';
  return '';
}

function titleLink(text, o) {
  const u = lcUrl(o);
  const cls = ('plink ' + diffClass(o && (o.difficulty || o.problem_difficulty))).trim();
  const t = esc(text);
  return u
    ? `<a class="${cls}" href="${esc(u)}" target="_blank" rel="noopener">${t}</a>`
    : `<span class="${cls} plain">${t}</span>`;
}

async function api(path, opts = {}) {
  const { silent, ...rest } = opts;
  const res = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json' },
    ...rest,
    body: rest.body ? JSON.stringify(rest.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (!silent) toast(data.error || `请求失败 ${res.status}`);
    const err = new Error(data.error || res.status);
    err.code = data.code;
    throw err;
  }
  return data;
}

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add('hidden'), 2400);
}

function modal(html) {
  $('#modal-box').innerHTML = html;
  $('#modal').classList.remove('hidden');
}
function closeModal() {
  $('#modal').classList.add('hidden');
  $('#modal-box').innerHTML = '';
  REVIEW = null;
}

/* ---------------- 顶部时钟 ---------------- */
function tickClock() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  const week = '日一二三四五六'[d.getDay()];
  $('#clock').textContent =
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} 周${week} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function startClock() {
  tickClock();
  setInterval(tickClock, 1000);
}

function renderGate(g, backlog) {
  const warn = (a, b) => (a >= b ? 'warn' : '');
  $('#gate').innerHTML = `
    <span class="chip ${warn(g.active, g.active_limit)}" onclick="openList('inflight')"
          title="正在复习的不同题目，同一题多次复习算 1">在途 ${g.active}/${g.active_limit}</span>
    <span class="chip" onclick="openList('queued')" title="还没轮到的题，出池一个补一个">候补 ${g.queued}</span>
    <span class="chip" onclick="openList('inbox')" title="还没归到题型上的题">待归纳 ${g.inbox}</span>
    <span class="chip ${backlog > 0 ? 'warn' : ''}" onclick="openList('backlog')"
          title="今天到期但没排进当日任务">积压 ${backlog}</span>`;
}

/* 点状态条看清单 */
async function openList(kind) {
  const meta = {
    inflight: { title: '在途 · 正在复习的题', url: '/inflight' },
    queued: { title: '候补队列 · 拖拽调整顺序', url: '/queued' },
    inbox: { title: '待归纳题型区', url: '/problems?status=inbox' },
    backlog: { title: '积压 · 今天没排下的', url: '/backlog' },
  }[kind];
  const rows = await api(meta.url);

  if (kind === 'queued') {
    const items = rows.map((r, i) => `
      <div class="qrow" draggable="true" data-id="${r.id}">
        <span class="qidx">${i + 1}</span>
        <span class="qmain">
          <b style="font-weight:500">${titleLink(r.title, r)}</b>
          <span class="muted">${esc([
            r.leetcode_id ? '#' + r.leetcode_id : '',
            r.difficulty || '',
            r.status === 'inbox' ? '未归题型' : (r.cards || '已归题型'),
          ].filter(Boolean).join(' · '))}</span>
        </span>
        <button class="btn ghost" onclick="activateProblem(${r.id})">提到在途</button>
        <button class="btn ghost" onclick="delProblem(${r.id});closeModal()">删除</button>
      </div>`).join('');
    modal(`<h2 style="margin:0 0 6px;font-size:17px;font-weight:500">${meta.title}</h2>
      <div class="cap">按住任意一行拖动即可排序，松手后点保存。在途出池一个，队头自动补一个。</div>
      <div id="qlist">${items || '<div class="muted" style="padding:14px 4px">候补队列是空的</div>'}</div>
      <div class="row end" style="margin-top:16px">
        <button class="btn" onclick="closeModal()">关闭</button>
        <button class="btn primary" onclick="saveQueueOrder()">保存顺序</button>
      </div>`);
    bindQueueDrag();
    return;
  }

  const body = rows.length
    ? rows.map((r) => {
      const name = r.title || r.problem_title || '';
      const sub = [
        r.leetcode_id ? '#' + r.leetcode_id : '',
        r.difficulty || '',
        r.next_due ? '到期 ' + r.next_due : '',
      ].filter(Boolean).join(' · ');
      const pid = r.id || r.problem_id || null;
      const cid = r.card_id || null;
      const label = kind === 'backlog' ? esc(name) : titleLink(name, r);
      return `<div class="recent-row">
        <span><b style="font-weight:500">${label}</b><br><span class="muted">${esc(sub)}</span></span>
        ${pid ? `<span class="row" style="gap:4px">
            ${kind === 'inflight' ? `<button class="btn ghost" onclick="deactivateProblem(${pid},'inflight')">移回候补</button>` : ''}
            <button class="btn ghost" onclick="removeProblem(${pid},'${kind}')">删除</button>
          </span>`
          : `<button class="btn ghost" onclick="removeCard(${cid},'${kind}')">归档</button>`}
      </div>`;
    }).join('')
    : '<div class="muted" style="padding:14px 4px">空的</div>';

  modal(`<h2 style="margin:0 0 14px;font-size:17px;font-weight:500">${meta.title}</h2>
    ${body}
    <div class="row end" style="margin-top:16px">
      ${kind === 'inflight' ? '<button class="btn" onclick="gotoAdd()">添加题目</button>' : ''}
      <button class="btn" onclick="closeModal()">关闭</button>
    </div>`);
}

function bindQueueDrag() {
  const box = $('#qlist');
  if (!box) return;
  let dragged = null;
  box.querySelectorAll('.qrow').forEach((row) => {
    row.addEventListener('dragstart', () => {
      dragged = row;
      row.classList.add('dragging');
    });
    row.addEventListener('dragend', () => {
      row.classList.remove('dragging');
      dragged = null;
    });
    row.addEventListener('dragover', (e) => {
      e.preventDefault();
      if (!dragged || dragged === row) return;
      const rect = row.getBoundingClientRect();
      const after = e.clientY > rect.top + rect.height / 2;
      box.insertBefore(dragged, after ? row.nextSibling : row);
    });
  });
}

async function saveQueueOrder() {
  const ids = [...document.querySelectorAll('#qlist .qrow')].map((r) => Number(r.dataset.id));
  await api('/queued/order', { method: 'PUT', body: { ids } });
  toast('顺序已保存');
  refreshGate();
}

async function activateProblem(id) {
  try {
    await api(`/problems/${id}/activate`, { method: 'POST', silent: true });
  } catch (e) {
    toast(`提到在途失败，${e.message}`);
    return;
  }
  toast('已提到在途');
  refreshGate();
  openList('queued');
}

async function deactivateProblem(id, kind) {
  await api(`/problems/${id}/deactivate`, { method: 'POST' });
  toast('已移回候补，队头补位');
  refreshGate();
  openList(kind);
}

function gotoAdd() {
  closeModal();
  CURRENT_VIEW = 'problems';
  location.hash = 'problems';
  render();
}

async function removeProblem(pid, kind) {
  if (!confirm('把这题从库里移除？关联的题型挂载也会断。')) return;
  await api('/problems/' + pid, { method: 'DELETE' });
  toast('已移除');
  await refreshGate();
  openList(kind);
  if (CURRENT_VIEW === 'today') loadToday();
}

async function removeCard(cid, kind) {
  if (!confirm('把这张卡归档？归档后不再进复习队列。')) return;
  await api(`/cards/${cid}/archive`, { method: 'POST' });
  toast('已归档');
  await refreshGate();
  openList(kind);
}

/* ---------------- 今日 ---------------- */
async function loadToday() {
  TODAY = await api('/today');
  renderGate(TODAY.gate, TODAY.backlog);
  const labels = { morning: '早班', noon: '午班', evening: '晚班' };
  const caps = {
    morning: '今天新录的题，先自己做',
    noon: '到期的题，闭卷重做核心步骤',
    evening: '只口述，不写码',
  };
  const blocks = ['morning', 'noon', 'evening'].map((slot) => {
    const items = TODAY.slots[slot] || [];
    const body = items.length
      ? items.map(planItem).join('')
      : `<div class="muted" style="padding:12px 0">今天这班空的</div>`;
    return `<div class="panel dropzone" data-slot="${slot}"><h3>${labels[slot]}</h3>
      <div class="cap">${caps[slot]}</div>${body}</div>`;
  }).join('');
  const total = Object.values(TODAY.slots).flat().length;
  $('#view-today').innerHTML = `
    <div class="row" style="margin-bottom:12px">
      <h2 style="margin:0;font-size:16px;font-weight:500">当日任务</h2>
      <span class="muted">要复习的 + 今天新录的</span>
      <div class="spacer"></div>
      <span class="muted">共 ${total} 项 · ${TODAY.date}</span>
    </div>
    <div class="cols">${blocks}</div>
    <div class="row" style="margin-top:16px">
      <button class="btn" onclick="loadToday()">刷新</button>
      <button class="btn ghost" onclick="gotoAdd()">加题</button>
      <span class="muted">卡片可以直接拖到别的班次</span>
    </div>`;
  bindPlanDrag();
}

/* 三个班次之间拖拽 */
let DRAG_PLAN = null;

function bindPlanDrag() {
  $$('.item[data-plan]').forEach((el) => {
    el.addEventListener('dragstart', (e) => {
      DRAG_PLAN = el.dataset.plan;
      el.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', DRAG_PLAN);
    });
    el.addEventListener('dragend', () => {
      el.classList.remove('dragging');
      DRAG_PLAN = null;
      $$('.dropzone').forEach((b) => b.classList.remove('drop-hover'));
    });
  });

  $$('.dropzone').forEach((box) => {
    box.addEventListener('dragover', (e) => {
      if (!DRAG_PLAN) return;
      e.preventDefault();
      box.classList.add('drop-hover');
    });
    box.addEventListener('dragleave', (e) => {
      if (!box.contains(e.relatedTarget)) box.classList.remove('drop-hover');
    });
    box.addEventListener('drop', async (e) => {
      e.preventDefault();
      box.classList.remove('drop-hover');
      const id = DRAG_PLAN || e.dataTransfer.getData('text/plain');
      const slot = box.dataset.slot;
      if (!id || !slot) return;
      try {
        await api(`/plan/${id}`, { method: 'PUT', body: { slot }, silent: true });
      } catch (err) {
        toast(err.message);
        return;
      }
      toast('已移到' + SLOT_LABEL[slot]);
      loadToday();
    });
  });
}

function planItem(r) {
  const stage = r.stage || 0;
  const badge = stage ? `S${stage} · ${STAGE_LABEL[stage]}` : '新题';
  return `<div class="item ${r.done ? 'done' : ''}" draggable="true" data-plan="${r.plan_id}">
    <div class="t">${titleLink(r.title, r)}<span class="badge ${stage ? 'due' : 'new'}">${badge}</span></div>
    <div class="m">${r.done ? '<span style="color:#3b6d11">✓ 已处理</span> · ' : ''}${
      r.leetcode_id ? 'LeetCode ' + r.leetcode_id : '未绑定题号'}${
      r.cards ? ' · ' + esc(r.cards) : ' · 未归题型'}</div>
    <div class="row" style="margin-top:8px">
      <button class="btn primary" onclick="quickReview(${r.problem_id},true)">答对</button>
      <button class="btn danger" onclick="quickReview(${r.problem_id},false)">未答对</button>
      <button class="btn ghost" onclick="openReview(${r.problem_id})">细想</button>
      ${r.status === 'inbox'
        ? `<button class="btn ghost" onclick="openCardEditor(null,${r.problem_id})">归题型</button>` : ''}
    </div>
  </div>`;
}

/* ---------------- 复习（按题判，题型卡只提供参考） ---------------- */
const STAGE_LABEL = ['新题', '1 天后', '3 天后', '7 天后', '15 天后', '30 天后'];

async function quickReview(pid, correct) {
  try {
    await api('/review', { method: 'POST', body: { problem_id: pid, correct }, silent: true });
  } catch (e) {
    toast(e.message);
    return;
  }
  toast(correct ? '答对，下次推后' : '未答对，明天再来');
  loadToday();
  refreshGate();
}

async function openReview(pid) {
  const d = await api('/problems/' + pid);
  REVIEW = { problem: d.problem, cards: d.cards || [], step: 1, t0: Date.now() };
  drawReview();
}

function drawReview() {
  const p = REVIEW.problem;
  const cards = REVIEW.cards;
  const dots = Array.from({ length: 5 }, (_, i) =>
    `<div class="dot ${i < (p.stage || 0) ? 'on' : ''}"></div>`).join('');

  if (REVIEW.step === 1) {
    modal(`
      <div class="muted">阶段一 · 先自己想</div>
      <h2 style="margin:6px 0 12px;font-size:17px;font-weight:500">${esc(p.title)}</h2>
      <div class="stage-dots">${dots}</div>
      <div class="panel" style="background:#fbfaf6;margin:16px 0">
        <div class="cap" style="margin:0 0 6px">这题属于</div>
        <div>${cards.length
          ? cards.map((c) => `<b style="font-weight:500">${esc(c.title)}</b>
              <div class="muted" style="margin:2px 0 8px">${esc(c.trigger_signals || '（还没写触发信号）')}</div>`).join('')
          : '<span class="muted">还没归到任何题型</span>'}</div>
      </div>
      <div class="muted" style="margin-bottom:16px">在脑子里把解法走一遍，再展开对答案。</div>
      <div class="row end">
        <button class="btn" onclick="skipReview()">跳过</button>
        <button class="btn primary" onclick="REVIEW.step=2;drawReview()">想好了 · 展开</button>
      </div>`);
    return;
  }

  if (REVIEW.step === 2) {
    const ref = cards.length
      ? cards.map((c) => `
        <div class="cap" style="margin-top:12px">${esc(c.title)} · 骨架</div>
        <pre class="code">${esc(c.skeleton_code || '（空）')}</pre>
        <div class="cap" style="margin-top:12px">边界与坑</div>
        <div style="white-space:pre-wrap">${esc(c.pitfalls || '（空）')}</div>`).join('')
      : '<div class="muted" style="padding:12px 0">这题还没归题型，没有参考骨架。对完答案记得去归一下。</div>';
    modal(`
      <div class="muted">阶段二 · 对答案</div>
      <h2 style="margin:6px 0 12px;font-size:17px;font-weight:500">${esc(p.title)}</h2>
      ${ref}
      <div class="row end" style="margin-top:20px">
        <button class="btn" onclick="REVIEW.step=1;drawReview()">返回</button>
        <button class="btn primary" onclick="REVIEW.step=3;drawReview()">判定</button>
      </div>`);
    return;
  }

  modal(`
    <div class="muted">阶段三 · 判定</div>
    <h2 style="margin:6px 0 14px;font-size:17px;font-weight:500">${esc(p.title)}</h2>
    <div class="muted" style="margin-bottom:14px">
      现在是第 ${p.stage || 0} 档。答对 → ${p.stage ? '升一档' : '直接跳到 3 天后'}；未答对 → 回到 1 天后。
    </div>
    <label class="field"><span>备注（可空）</span><input id="note" placeholder="卡在哪一步"></label>
    <div class="row end">
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn danger" onclick="submitReview(false)">未答对</button>
      <button class="btn primary" onclick="submitReview(true)">答对</button>
    </div>`);
}

async function submitReview(correct) {
  const note = $('#note')?.value || '';
  const res = await api('/review', {
    method: 'POST',
    body: {
      problem_id: REVIEW.problem.id,
      correct,
      note,
      duration_sec: Math.round((Date.now() - REVIEW.t0) / 1000),
    },
  });
  closeModal();
  if (res.out_pool) toast('答对，走完 5 档，出池了');
  else toast(correct ? `答对 → 下次 ${res.next_due}` : `未答对 → 明天再来`);
  loadToday();
  refreshGate();
}

async function skipReview() {
  await api('/review', { method: 'POST', body: { problem_id: REVIEW.problem.id, skip: true } });
  closeModal();
  toast('已跳过，不计入档位');
  loadToday();
}

/* ---------------- 题型卡 ---------------- */
async function loadCards() {
  const cards = await api('/cards');
  const rows = cards.map((c) => `
    <tr>
      <td><a href="#" onclick="event.preventDefault();openCardEditor(${c.id})">${esc(c.title)}</a></td>
      <td>${esc(c.category || '')}</td>
      <td>${c.status === 'archived' ? '已收起' : '使用中'}</td>
      <td>${c.problem_count || 0}</td>
      <td class="row">
        <button class="btn ghost" onclick="openCardEditor(${c.id})">编辑</button>
        ${c.status === 'archived'
          ? `<button class="btn ghost" onclick="reopenCard(${c.id})">回炉</button>`
          : `<button class="btn ghost" onclick="archiveCard(${c.id})">收起</button>`}
      </td>
    </tr>`).join('');
  $('#view-cards').innerHTML = `
    <div class="row" style="margin-bottom:6px">
      <button class="btn primary" onclick="openCardEditor(null)">新建题型</button>
      <span class="muted">共 ${cards.length} 张</span>
    </div>
    <div class="cap" style="margin-bottom:14px">
      题型是归纳用的容器：写清触发信号和骨架，复习时作为参考答案。它不排期，排期的是题。
    </div>
    <div class="panel"><table>
      <thead><tr><th>题型</th><th>分类</th><th>状态</th><th>挂题数</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="muted">还没有题型</td></tr>'}</tbody>
    </table></div>`;
}

async function openCardEditor(id, attachProblem) {
  const d = id ? await api('/cards/' + id) : { card: {}, problems: [] };
  const c = d.card || {};
  modal(`
    <h2 style="margin:0 0 16px;font-size:17px;font-weight:500">${id ? '编辑题型' : '新建题型'}</h2>
    <label class="field"><span>题型名</span><input id="f-title" value="${esc(c.title || '')}" placeholder="如：滑动窗口 · 最短覆盖"></label>
    <label class="field"><span>触发信号（最重要：题干出现什么就该想到它）</span>
      <textarea id="f-trigger" placeholder="连续子数组 + 求最短/最长 + 有单调性">${esc(c.trigger_signals || '')}</textarea></label>
    <label class="field"><span>一句话本质</span><input id="f-essence" value="${esc(c.essence || '')}" placeholder="用窗口吃掉不满足的部分"></label>
    <label class="field"><span>骨架代码</span><textarea id="f-code" style="min-height:120px;font-family:Consolas,monospace">${esc(c.skeleton_code || '')}</textarea></label>
    <div class="grid2">
      <label class="field"><span>边界与坑</span><textarea id="f-pitfalls">${esc(c.pitfalls || '')}</textarea></label>
      <label class="field"><span>变形族</span><textarea id="f-variants">${esc(c.variants || '')}</textarea></label>
    </div>
    <div class="grid2">
      <label class="field"><span>复杂度</span><input id="f-complexity" value="${esc(c.complexity || '')}"></label>
      <label class="field"><span>分类</span><input id="f-category" value="${esc(c.category || '')}"></label>
    </div>
    <div class="row end">
      ${id ? `<button class="btn danger" onclick="deleteCard(${id})">删除</button>` : ''}
      <div class="spacer"></div>
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="saveCard(${id || 'null'},${attachProblem || 'null'})">保存</button>
    </div>`);
}

async function saveCard(id, attachProblem) {
  const body = {
    title: $('#f-title').value,
    trigger_signals: $('#f-trigger').value,
    essence: $('#f-essence').value,
    skeleton_code: $('#f-code').value,
    pitfalls: $('#f-pitfalls').value,
    variants: $('#f-variants').value,
    complexity: $('#f-complexity').value,
    category: $('#f-category').value,
  };
  if (!body.title.trim()) { toast('题型名不能为空'); return; }
  if (id) {
    await api('/cards/' + id, { method: 'PUT', body });
  } else {
    const r = await api('/cards', { method: 'POST', body: { ...body, problem_ids: attachProblem ? [attachProblem] : [] } });
    id = r.id;
  }
  closeModal();
  toast('已保存');
  render();
}

async function archiveCard(id) { await api(`/cards/${id}/archive`, { method: 'POST' }); toast('已归档'); render(); }
async function reopenCard(id) { await api(`/cards/${id}/reopen`, { method: 'POST' }); toast('已回炉到第 3 档'); render(); }
async function deleteCard(id) {
  if (!confirm('删了连带复习记录一起没了，确定？')) return;
  await api('/cards/' + id, { method: 'DELETE' });
  closeModal(); toast('已删除'); render();
}

/* ---------------- 题目 ---------------- */
function autoSlot() {
  const h = new Date().getHours();
  return h < 12 ? 'morning' : h < 18 ? 'noon' : 'evening';
}

async function loadProblems() {
  const [inbox, all] = await Promise.all([api('/problems?status=inbox'), api('/problems')]);
  const inboxRows = inbox.map((p) => `
    <tr><td>${p.leetcode_id || ''}</td>
    <td>${titleLink(p.title, p)}</td>
    <td><button class="btn ghost" onclick="openCardEditor(null,${p.id})">归题型</button></td>
    <td><button class="btn ghost" onclick="delProblem(${p.id})">删除</button></td></tr>`).join('');
  const PHASE = { active: '在途', queued: '候补', done: '完成' };
  const allRows = all.slice(0, 100).map((p) => `
    <tr><td>${p.leetcode_id || ''}</td>
    <td>${titleLink(p.title, p)}</td>
    <td>${p.difficulty
      ? `<span class="dif ${diffClass(p.difficulty).replace('d-', '')}">${esc(p.difficulty)}</span>`
      : ''}</td>
    <td>${p.status === 'linked' ? '已归题型' : '待归纳'}</td>
    <td><span class="badge ${p.phase === 'active' ? 'due' : ''}">${PHASE[p.phase] || '候补'}</span></td>
    <td><button class="btn ghost" onclick="delProblem(${p.id})">删除</button></td></tr>`).join('');
  $('#view-problems').innerHTML = `
    <div class="panel" style="margin-bottom:16px">
      <h3>录入一道题</h3><div class="cap">题号、标题、难度 + 排到哪个班，一次说完</div>
      <div class="grid2">
        <label class="field"><span>LeetCode 题号</span><input id="p-id" placeholder="1"></label>
        <label class="field"><span>标题</span><input id="p-title" placeholder="Two Sum"></label>
      </div>
      <div class="grid2">
        <label class="field"><span>难度</span>
          <select id="p-diff"><option value="">—</option><option>简单</option><option>中等</option><option>困难</option></select></label>
        <label class="field"><span>排进今天哪一班</span>
          <select id="p-slot">
            <option value="">不排（只进待归卡区）</option>
            <option value="morning">早班</option>
            <option value="noon">午班</option>
            <option value="evening">晚班</option>
          </select></label>
      </div>
      <label class="field"><span>标签</span><input id="p-tags" placeholder="数组,哈希"></label>
      <button class="btn primary" onclick="addProblem()">录入</button>
    </div>
    <div class="panel" style="margin-bottom:16px">
      <h3>待归纳题型区 <span class="badge ${inbox.length ? 'due' : ''}">${inbox.length}</span></h3>
      <div class="cap">录了但还没归到题型上的题。不设上限，但堆着不归纳等于没学</div>
      <table><thead><tr><th>题号</th><th>标题</th><th></th><th></th></tr></thead>
      <tbody>${inboxRows || '<tr><td colspan="4" class="muted">空的，很好</td></tr>'}</tbody></table>
    </div>
    <div class="panel">
      <h3>全部题目</h3><div class="cap">最近 100 条</div>
      <table><thead><tr><th>题号</th><th>标题</th><th>难度</th><th>归纳</th><th>阶段</th><th></th></tr></thead>
      <tbody>${allRows || '<tr><td colspan="6" class="muted">还没有题</td></tr>'}</tbody></table>
    </div>`;
  $('#p-slot').value = autoSlot();
}

async function addProblem() {
  const body = {
    leetcode_id: $('#p-id').value.trim(),
    title: $('#p-title').value.trim(),
    difficulty: $('#p-diff').value,
    tags: $('#p-tags').value.trim(),
    slot: $('#p-slot').value,
  };
  if (!body.leetcode_id && !body.title) { toast('题号或标题至少给一个'); return; }
  let r;
  try {
    r = await api('/problems', { method: 'POST', body, silent: true });
  } catch (e) {
    toast(`录用失败，${e.message}`);
    return;
  }
  if (r.queued) toast('已录用，在途已满，排进候补');
  else toast(body.slot ? '录用成功，已排进' + SLOT_LABEL[body.slot] : '录用成功，已进在途');
  loadProblems();
  refreshGate();
  if (CURRENT_VIEW === 'today') loadToday();
}

async function delProblem(id) {
  if (!confirm('从库里删掉这道题？它和题型的挂载关系、今日任务里的一项也会一起没了。')) return;
  await api('/problems/' + id, { method: 'DELETE' });
  toast('已删除');
  loadProblems();
  refreshGate();
}

/* ---------------- 统计 ---------------- */
function relTime(s) {
  if (!s) return '';
  const t = new Date(String(s).replace(' ', 'T'));
  const diff = (Date.now() - t.getTime()) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
  if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
  const d = Math.floor(diff / 86400);
  if (d < 30) return d + ' 天前';
  if (d < 365) return Math.floor(d / 30) + ' 个月前';
  return Math.floor(d / 365) + ' 年前';
}

function buildDonut(progress, total) {
  const R = 86, C = 2 * Math.PI * R, CX = 115;
  const sum = progress.reduce((a, b) => a + b.total, 0) || 1;
  const colors = ['#1D9E75', '#EF9F27', '#E24B4A'];
  let acc = 0;
  const segs = progress.map((p, i) => {
    const len = (p.total / sum) * C;
    const seg = `<circle cx="${CX}" cy="${CX}" r="${R}" fill="none" stroke="${colors[i]}"
      stroke-opacity="0.30" stroke-width="13"
      stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-acc}"
      transform="rotate(-90 ${CX} ${CX})"/>`;
    acc += len;
    return seg;
  }).join('');
  const ratio = total.total ? Math.min(1, total.solved / total.total) : 0;
  const arc = ratio > 0.001
    ? `<circle cx="${CX}" cy="${CX}" r="${R}" fill="none" stroke="#185FA5" stroke-width="13"
        stroke-linecap="round" stroke-dasharray="${C * ratio} ${C}" transform="rotate(-90 ${CX} ${CX})"/>`
    : '';
  return `<svg viewBox="0 0 230 230" width="230" height="230" role="img">
    <title>解题进度</title>${segs}${arc}</svg>`;
}

function buildHeat(cal) {
  const end = new Date(); end.setHours(0, 0, 0, 0);
  const start = new Date(end); start.setDate(start.getDate() - 364);
  start.setDate(start.getDate() - start.getDay());
  const cells = [];
  const d = new Date(start);
  while (d <= end) {
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const n = cal[key] || 0;
    const lv = n === 0 ? '' : n <= 2 ? 'l1' : n <= 4 ? 'l2' : 'l3';
    cells.push(`<i class="${lv}" title="${key}　${n} 次"></i>`);
    d.setDate(d.getDate() + 1);
  }
  return `<div class="heat">${cells.join('')}</div>`;
}

let STATS_YEAR = new Date().getFullYear();

function buildHeat(cal, year) {
  const end = new Date(year, 11, 31);
  const first = new Date(year, 0, 1);
  first.setDate(first.getDate() - first.getDay());   // 对齐到周日
  const cells = [];
  const d = new Date(first);
  while (d <= end) {
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const inYear = d.getFullYear() === year;
    const n = inYear ? (cal[key] || 0) : 0;
    const lv = !inYear ? 'blank' : n === 0 ? '' : n <= 2 ? 'l1' : n <= 4 ? 'l2' : 'l3';
    cells.push(`<i class="${lv}" title="${inYear ? key + '　' + n + ' 次' : ''}"></i>`);
    d.setDate(d.getDate() + 1);
  }
  return `<div class="heat">${cells.join('')}</div>`;
}

async function loadStats() {
  const s = await api('/stats?year=' + STATS_YEAR);
  if (s.gate) renderGate(s.gate, s.backlog || 0);

  const p = s.progress_total;
  const diffRows = s.progress.map((x, i) => `
    <div class="diff-row ${['easy', 'medium', 'hard'][i]}">
      <span class="nm">${x.name}</span><span class="val">${x.solved}/${x.total}</span>
    </div>`).join('');

  const cs = s.calendar_stats;
  const recent = s.recent_pass.length
    ? s.recent_pass.map((r) => `
      <div class="recent-row">
        <span>${titleLink(r.title, { difficulty: r.difficulty, leetcode_id: null })}
        <span class="muted">${r.stage_to ? 'S' + r.stage_to : ''}</span></span>
        <span class="when">${relTime(r.reviewed_at)}</span>
      </div>`).join('')
    : '<div class="muted" style="padding:14px 4px">这年还没有答对记录</div>';

  const yearOpts = (s.years || []).map((y) =>
    `<option value="${y}" ${y === cs.year ? 'selected' : ''}>${y} 年</option>`).join('');

  const maxDay = Math.max(1, ...s.daily.map((d) => d.n));
  const days = s.daily.map((d) => `
    <div class="row" style="margin-bottom:6px">
      <span class="muted" style="width:84px">${d.d}</span>
      <div class="bar" style="flex:1"><i style="width:${(d.n / maxDay) * 100}%"></i></div>
      <span class="muted" style="width:32px;text-align:right">${d.n}</span>
    </div>`).join('');
  const errs = s.errors.map((e) => `<tr><td>${esc(e.t)}</td><td>${e.n}</td></tr>`).join('');
  const stages = s.by_stage.map((b) => {
    const rate = b.n ? Math.round((b.p / b.n) * 100) : 0;
    return `<tr><td>${b.stage ? 'S' + b.stage : '新题'}</td><td>${b.n}</td><td>${rate}%</td></tr>`;
  }).join('');

  $('#view-stats').innerHTML = `
    <div class="stats-top">
      <div class="panel donut-card">
        <div class="donut-wrap">
          ${buildDonut(s.progress, p)}
          <div class="donut-center">
            <div class="big">${p.solved}<small>/${p.total}</small></div>
            <div class="tag">✓ 已解答</div>
            <div class="sub">在途 ${s.active} · 出池 ${s.done}</div>
          </div>
        </div>
      </div>
      <div class="diff-col">${diffRows}</div>
    </div>

    <div class="panel" style="margin-bottom:16px">
      <div class="row">
        <h3 style="margin:0">${cs.year} 年共完成 ${cs.total} 次</h3>
        <div class="spacer"></div>
        <span class="muted">累计 ${cs.active_days} 天${cs.year === new Date().getFullYear() ? ` · 连续 ${cs.streak} 天` : ''}</span>
        <select style="width:auto" onchange="STATS_YEAR=Number(this.value);loadStats()">${yearOpts}</select>
      </div>
      ${buildHeat(s.calendar, cs.year)}
      <div class="muted">颜色越深，当天完成次数越多；悬停看具体日期</div>
    </div>

    <div class="panel" style="margin-bottom:16px">
      <h3>最近答对</h3><div class="cap">判定为答对的题，最近的在前</div>
      ${recent}
    </div>

    <div class="cols">
      <div class="panel"><h3>最近 14 天</h3><div class="cap">每天完成了几次</div>${days || '<div class="muted">还没数据</div>'}</div>
      <div class="panel"><h3>各档通过率</h3><div class="cap">哪一档最容易答错</div>
        <table><thead><tr><th>档</th><th>次数</th><th>答对率</th></tr></thead><tbody>${stages || '<tr><td colspan="3" class="muted">—</td></tr>'}</tbody></table></div>
      <div class="panel"><h3>答错的时候</h3><div class="cap">不会 / 想不到 / 写错</div>
        <table><thead><tr><th>类型</th><th>次数</th></tr></thead><tbody>${errs || '<tr><td colspan="2" class="muted">—</td></tr>'}</tbody></table></div>
    </div>`;
}

/* ---------------- 设置 ---------------- */
async function loadSettings() {
  const s = await api('/settings');
  const st = s.settings;
  const checks = await api('/checks');
  const checkRows = checks.map((c) => `
    <div class="row" style="padding:6px 0;border-bottom:1px solid var(--line)">
      <span style="flex:1">${esc(c.name)} <span class="muted">${esc(c.hint || '')}</span></span>
      <button class="btn ghost" onclick="dropCheck(${c.id})">移除</button>
    </div>`).join('');
  $('#view-settings').innerHTML = `
    <div class="panel" style="margin-bottom:16px">
      <h3>节奏</h3><div class="cap">改完立刻生效，不用重启</div>
      <div class="grid2">
        <label class="field"><span>在途题目上限（当月要复习的不同题目）</span><input id="s-queue" type="number" value="${st.queue_limit}"></label>
        <label class="field"><span>每日时长上限（分钟）</span><input id="s-minutes" type="number" value="${st.daily_minutes}"></label>
        <label class="field"><span>早班新题数</span><input id="s-morning" type="number" value="${st.morning_new}"></label>
        <label class="field"><span>午班到期数</span><input id="s-noon" type="number" value="${st.noon_due}"></label>
        <label class="field"><span>晚班到期数</span><input id="s-evening" type="number" value="${st.evening_due}"></label>
        <label class="field"><span>通过率阈值</span><input id="s-threshold" type="number" step="0.05" value="${st.pass_threshold}"></label>
        <label class="field"><span>连续持平几次降档</span><input id="s-hold" type="number" value="${st.hold_limit}"></label>
      </div>
      <label class="field"><span>间隔阶梯（天，逗号分隔）</span>
        <input id="s-intervals" value="${st.intervals.join(',')}"></label>
      <button class="btn primary" onclick="saveSettings()">保存</button>
    </div>
    <div class="panel" style="margin-bottom:16px">
      <h3>题库总数</h3><div class="cap">只影响统计页那个进度环，随便改</div>
      <div class="cols">
        <label class="field"><span>简单</span><input id="s-te" type="number" value="${st.total_easy}"></label>
        <label class="field"><span>中等</span><input id="s-tm" type="number" value="${st.total_medium}"></label>
        <label class="field"><span>困难</span><input id="s-th" type="number" value="${st.total_hard}"></label>
      </div>
      <button class="btn" onclick="saveTotals()">保存总数</button>
    </div>
    <div class="panel" style="margin-bottom:16px">
      <h3>检验项</h3><div class="cap">数量、名称自己定；判定用通过率，所以增减不影响算法</div>
      ${checkRows || '<div class="muted">没有启用的检验项</div>'}
      <div class="row" style="margin-top:12px">
        <input id="c-name" placeholder="新检验项名称" style="flex:1">
        <input id="c-hint" placeholder="一句提示" style="flex:1">
        <button class="btn" onclick="addCheck()">加一项</button>
      </div>
    </div>
    <div class="panel">
      <h3>数据目录</h3><div class="cap">整个目录可以搬到任何盘，搬完在这里改一下就行</div>
      <div class="row">
        <input id="s-root" value="${esc(s.data_root)}" style="flex:1">
        <button class="btn" onclick="changeRoot()">迁移到此路径</button>
        <button class="btn" onclick="exportCards()">导出卡片为 Markdown</button>
      </div>
    </div>`;
}

async function saveSettings() {
  const body = {
    queue_limit: $('#s-queue').value,
    morning_new: $('#s-morning').value,
    noon_due: $('#s-noon').value,
    evening_due: $('#s-evening').value,
    daily_minutes: $('#s-minutes').value,
    pass_threshold: $('#s-threshold').value,
    hold_limit: $('#s-hold').value,
    interval_days: $('#s-intervals').value,
  };
  await api('/settings', { method: 'PUT', body });
  toast('已保存');
  loadToday();
}

async function saveTotals() {
  await api('/settings', {
    method: 'PUT',
    body: { total_easy: $('#s-te').value, total_medium: $('#s-tm').value, total_hard: $('#s-th').value },
  });
  toast('已保存');
}

async function addCheck() {
  const name = $('#c-name').value.trim();
  if (!name) { toast('给个名字'); return; }
  await api('/checks', { method: 'POST', body: { name, hint: $('#c-hint').value } });
  loadSettings();
}
async function dropCheck(id) { await api('/checks/' + id, { method: 'DELETE' }); loadSettings(); }

async function changeRoot() {
  const path = $('#s-root').value.trim();
  if (!confirm(`把数据复制到：\n${path}\n\n原目录会保留。确认？`)) return;
  const r = await api('/data-root', { method: 'POST', body: { path, migrate: true } });
  toast('已切换：' + r.data_root);
  loadSettings();
}

async function exportCards() {
  const r = await api('/export/cards', { method: 'POST' });
  toast(`导出 ${r.count} 张卡 → ${r.dir}`);
}

/* ---------------- 路由 ---------------- */
const VIEWS = {
  today: loadToday, cards: loadCards, problems: loadProblems,
  stats: loadStats, settings: loadSettings,
};

function render() {
  $$('.tab').forEach((b) => b.classList.toggle('active', b.dataset.view === CURRENT_VIEW));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + CURRENT_VIEW));
  Promise.resolve(VIEWS[CURRENT_VIEW]()).catch((e) => {
    const host = $('#view-' + CURRENT_VIEW);
    if (host) {
      host.innerHTML = `<div class="panel"><h3>这个页面出错了</h3>
        <pre class="code">${esc((e && e.stack) || e)}</pre></div>`;
    }
  });
  refreshGate();
}

/* 切页时顶部状态条不该消失 */
async function refreshGate() {
  try {
    const t = await api('/today');
    TODAY = t;
    renderGate(t.gate, t.backlog);
  } catch (e) { /* 离线就先不更新 */ }
}

$('#tabs').addEventListener('click', (e) => {
  const b = e.target.closest('.tab');
  if (!b) return;
  CURRENT_VIEW = b.dataset.view;
  location.hash = CURRENT_VIEW;
  render();
});

if (VIEWS[location.hash.slice(1)]) CURRENT_VIEW = location.hash.slice(1);

$('#modal').addEventListener('click', (e) => {
  if (e.target.id === 'modal') closeModal();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeModal();
});

startClock();
render();
