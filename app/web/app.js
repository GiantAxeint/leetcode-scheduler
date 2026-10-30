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

async function api(path, opts = {}) {
  const res = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    toast(data.error || `请求失败 ${res.status}`);
    throw new Error(data.error || res.status);
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
    <span class="${warn(g.active, g.active_limit)}">在途 ${g.active}/${g.active_limit}</span>
    <span class="${warn(g.inbox, g.inbox_limit)}">待归卡 ${g.inbox}/${g.inbox_limit}</span>
    <span class="${backlog > 0 ? 'warn' : ''}">积压 ${backlog}</span>`;
}

/* ---------------- 今日 ---------------- */
async function loadToday() {
  TODAY = await api('/today');
  renderGate(TODAY.gate, TODAY.backlog);
  const labels = { morning: '早班', noon: '午班', evening: '晚班' };
  const caps = {
    morning: '新题优先，闸门关了就改复习',
    noon: '到期卡，闭卷重做核心步骤',
    evening: '只口述，不写码',
  };
  const blocks = ['morning', 'noon', 'evening'].map((slot) => {
    const items = TODAY.slots[slot] || [];
    const body = items.length
      ? items.map((r) => (r.card_id ? cardItem(r) : problemItem(r))).join('')
      : `<div class="muted" style="padding:12px 0">今天这班空的</div>`;
    return `<div class="panel"><h3>${labels[slot]}</h3>
      <div class="cap">${caps[slot]}</div>${body}</div>`;
  }).join('');
  $('#view-today').innerHTML = `<div class="cols">${blocks}</div>
    <div class="row" style="margin-top:16px">
      <button class="btn" onclick="loadToday()">刷新</button>
      <span class="muted">共 ${Object.values(TODAY.slots).flat().length} 项 · ${TODAY.date}</span>
    </div>`;
}

function cardItem(r) {
  return `<div class="item ${r.done ? 'done' : ''}" onclick="openReview(${r.card_id})">
    <div class="t">${esc(r.title)}<span class="badge due">S${r.stage}</span></div>
    <div class="m">${esc(r.category || '未分类')} · 到期 ${esc(r.next_due || '')}</div>
  </div>`;
}

function problemItem(r) {
  return `<div class="item ${r.done ? 'done' : ''}">
    <div class="t">${esc(r.problem_title)}<span class="badge new">新题</span></div>
    <div class="m">${r.leetcode_id ? 'LeetCode ' + r.leetcode_id : '未绑定题号'}</div>
    <div class="row" style="margin-top:8px">
      <button class="btn" onclick="event.stopPropagation();donePlan(${r.plan_id})">标记完成</button>
      <button class="btn ghost" onclick="event.stopPropagation();openCardEditor(null,${r.problem_id})">去建题型</button>
    </div>
  </div>`;
}

async function donePlan(id) {
  await api(`/plan/${id}/done`, { method: 'POST' });
  loadToday();
}

/* ---------------- 复习三阶段 ---------------- */
async function openReview(cardId) {
  const d = await api('/cards/' + cardId);
  REVIEW = { card: d.card, step: 1, t0: Date.now(), checks: {} };
  drawReview();
}

function drawReview() {
  const c = REVIEW.card;
  const dots = Array.from({ length: 5 }, (_, i) =>
    `<div class="dot ${i < (c.stage || 1) ? 'on' : ''}"></div>`).join('');

  if (REVIEW.step === 1) {
    modal(`
      <div class="muted">阶段一 · 只给你线索</div>
      <h2 style="margin:6px 0 12px;font-size:17px;font-weight:500">${esc(c.title)}</h2>
      <div class="stage-dots">${dots}</div>
      <div class="panel" style="background:#fbfaf6;margin:16px 0">
        <div class="cap" style="margin:0 0 6px">触发信号</div>
        <div style="white-space:pre-wrap">${esc(c.trigger_signals || '（还没填触发信号）')}</div>
      </div>
      <div class="muted" style="margin-bottom:16px">先在纸上或脑子里走一遍，别急着点。</div>
      <div class="row end">
        <button class="btn danger" onclick="failFast()">想不起来</button>
        <button class="btn" onclick="skipReview()">跳过</button>
        <button class="btn primary" onclick="REVIEW.step=2;drawReview()">想起来了 · 展开</button>
      </div>`);
    return;
  }

  if (REVIEW.step === 2) {
    modal(`
      <div class="muted">阶段二 · 对答案</div>
      <h2 style="margin:6px 0 12px;font-size:17px;font-weight:500">${esc(c.title)}</h2>
      <div class="cap">骨架</div>
      <pre class="code">${esc(c.skeleton_code || '（空）')}</pre>
      <div class="cap" style="margin-top:12px">边界与坑</div>
      <div style="white-space:pre-wrap">${esc(c.pitfalls || '（空）')}</div>
      ${c.complexity ? `<div class="cap" style="margin-top:12px">复杂度</div><div>${esc(c.complexity)}</div>` : ''}
      <div class="row end" style="margin-top:20px">
        <button class="btn" onclick="REVIEW.step=1;drawReview()">返回</button>
        <button class="btn primary" onclick="REVIEW.step=3;drawReview()">进入判定</button>
      </div>`);
    return;
  }

  const checks = (TODAY?.checks || []).map((it) => `
    <label class="checkline" id="chk-${it.id}">
      <input type="checkbox" onchange="toggleCheck(${it.id}, this.checked)">
      <span><b style="font-weight:500">${esc(it.name)}</b><br>
      <span class="muted">${esc(it.hint)}</span></span>
    </label>`).join('');

  modal(`
    <div class="muted">阶段三 · 判定</div>
    <h2 style="margin:6px 0 12px;font-size:17px;font-weight:500">${esc(c.title)}</h2>
    <div class="cap">这轮你做到了哪几项</div>
    ${checks}
    <div class="grid2" style="margin-top:16px">
      <label class="field"><span>错在哪（挂了必选）</span>
        <select id="errtype">
          <option value="">—</option>
          <option value="knowledge">不会 · 知识缺口</option>
          <option value="index">会但想不到 · 索引缺口</option>
          <option value="execution">想到了写错 · 执行缺口</option>
        </select>
      </label>
      <label class="field"><span>一句话备注</span>
        <input id="note" placeholder="可空">
      </label>
    </div>
    <div class="row end">
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="submitReview()">提交</button>
    </div>`);
}

function toggleCheck(id, ok) {
  REVIEW.checks[id] = ok;
  $(`#chk-${id}`).classList.toggle('on', ok);
}

async function submitReview() {
  const checks = Object.entries(REVIEW.checks).map(([id, ok]) => ({ id: Number(id), ok }));
  const err = $('#errtype')?.value || null;
  const note = $('#note')?.value || '';
  if (checks.length && checks.every((c) => !c.ok) && !err) {
    toast('全挂了的话，选一下错在哪');
    return;
  }
  const res = await api('/review', {
    method: 'POST',
    body: {
      card_id: REVIEW.card.id,
      checks,
      error_type: err,
      note,
      duration_sec: Math.round((Date.now() - REVIEW.t0) / 1000),
    },
  });
  closeModal();
  const label = { pass: '过 · 升档', hold: '持平 · 原地', fail: '挂 · 退档' }[res.result] || res.result;
  toast(`${label} → ${res.archived ? '已归档出池' : '下次 ' + res.next_due}`);
  loadToday();
}

async function skipReview() {
  await api('/review', { method: 'POST', body: { card_id: REVIEW.card.id, skip: true } });
  closeModal();
  toast('已跳过，不计入档位');
  loadToday();
}

function failFast() {
  REVIEW.step = 3;
  drawReview();
  toast('已记为挂，勾一下你现在还剩哪几项会');
}

/* ---------------- 题型卡 ---------------- */
async function loadCards() {
  const cards = await api('/cards');
  const rows = cards.map((c) => `
    <tr>
      <td><a href="#" onclick="event.preventDefault();openCardEditor(${c.id})">${esc(c.title)}</a></td>
      <td>${esc(c.category || '')}</td>
      <td>${c.status === 'archived' ? '已归档' : 'S' + c.stage}</td>
      <td>${esc(c.next_due || '')}</td>
      <td>${c.problem_count || 0}</td>
      <td class="row">
        <button class="btn ghost" onclick="openCardEditor(${c.id})">编辑</button>
        ${c.status === 'archived'
          ? `<button class="btn ghost" onclick="reopenCard(${c.id})">回炉</button>`
          : `<button class="btn ghost" onclick="archiveCard(${c.id})">归档</button>`}
      </td>
    </tr>`).join('');
  $('#view-cards').innerHTML = `
    <div class="row" style="margin-bottom:14px">
      <button class="btn primary" onclick="openCardEditor(null)">新建题型</button>
      <span class="muted">共 ${cards.length} 张</span>
    </div>
    <div class="panel"><table>
      <thead><tr><th>题型</th><th>分类</th><th>档位</th><th>下次到期</th><th>挂题数</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="6" class="muted">还没有卡</td></tr>'}</tbody>
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
    <tr><td>${p.leetcode_id || ''}</td><td>${esc(p.title)}</td>
    <td>${p.solved ? '<span class="badge" style="background:var(--green-bg);color:var(--green)">已通过</span>' : ''}</td>
    <td><button class="btn ghost" onclick="openCardEditor(null,${p.id})">归卡</button></td></tr>`).join('');
  const allRows = all.slice(0, 100).map((p) => `
    <tr><td>${p.leetcode_id || ''}</td><td>${esc(p.title)}</td><td>${esc(p.difficulty || '')}</td>
    <td>${p.solved ? '已通过' : '未通过'}</td><td>${p.status === 'linked' ? '已挂卡' : '待归卡'}</td></tr>`).join('');
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
      <label class="row" style="gap:8px;margin-bottom:12px">
        <input type="checkbox" id="p-solved" style="width:auto"> <span class="muted">这题我已经通过了</span>
      </label>
      <button class="btn primary" onclick="addProblem()">录入</button>
    </div>
    <div class="panel" style="margin-bottom:16px">
      <h3>待归卡区 <span class="badge ${inbox.length ? 'due' : ''}">${inbox.length}</span></h3>
      <div class="cap">这些题还没挂到任何题型卡上，多了会堵住入口</div>
      <table><thead><tr><th>题号</th><th>标题</th><th>状态</th><th></th></tr></thead>
      <tbody>${inboxRows || '<tr><td colspan="4" class="muted">空的，很好</td></tr>'}</tbody></table>
    </div>
    <div class="panel">
      <h3>全部题目</h3><div class="cap">最近 100 条</div>
      <table><thead><tr><th>题号</th><th>标题</th><th>难度</th><th>通过</th><th>状态</th></tr></thead>
      <tbody>${allRows || '<tr><td colspan="5" class="muted">还没有题</td></tr>'}</tbody></table>
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
    solved: $('#p-solved').checked,
  };
  if (!body.leetcode_id && !body.title) { toast('题号或标题至少给一个'); return; }
  const r = await api('/problems', { method: 'POST', body });
  toast(r.duplicate ? '这题已经在库里了' : (body.slot ? '已排进' + SLOT_LABEL[body.slot] : '已进待归卡区'));
  loadProblems();
  if (CURRENT_VIEW === 'today') loadToday();
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

async function loadStats() {
  const s = await api('/stats');
  if (s.gate) renderGate(s.gate, s.backlog || 0);
  const p = s.progress_total;
  const diffRows = s.progress.map((x, i) => `
    <div class="diff-row ${['easy', 'medium', 'hard'][i]}">
      <span class="nm">${x.name}</span><span class="val">${x.solved}/${x.total}</span>
    </div>`).join('');
  const cs = s.calendar_stats;
  const recent = s.recent_pass.length
    ? s.recent_pass.map((r) => `
      <div class="recent-row"><span>${esc(r.title)}</span>
      <span class="when">${relTime(r.reviewed_at)}</span></div>`).join('')
    : '<div class="muted" style="padding:14px 4px">还没有通过记录</div>';

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
    return `<tr><td>S${b.stage}</td><td>${b.n}</td><td>${rate}%</td></tr>`;
  }).join('');

  $('#view-stats').innerHTML = `
    <div class="stats-top">
      <div class="panel donut-card">
        <div class="donut-wrap">
          ${buildDonut(s.progress, p)}
          <div class="donut-center">
            <div class="big">${p.solved}<small>/${p.total}</small></div>
            <div class="tag">✓ 已解答</div>
            <div class="sub">在途 ${s.active} · 归档 ${s.archived}</div>
          </div>
        </div>
      </div>
      <div class="diff-col">${diffRows}</div>
    </div>

    <div class="panel" style="margin-bottom:16px">
      <div class="row">
        <h3 style="margin:0">过去一年共复习 ${cs.total} 次</h3>
        <div class="spacer"></div>
        <span class="muted">累计 ${cs.active_days} 天 · 连续 ${cs.streak} 天</span>
      </div>
      ${buildHeat(s.calendar)}
      <div class="muted">颜色越深，当天复习次数越多；悬停看具体日期</div>
    </div>

    <div class="panel" style="margin-bottom:16px">
      <h3>最近通过</h3><div class="cap">阅卷过了的卡，最近的在前</div>
      ${recent}
    </div>

    <div class="cols">
      <div class="panel"><h3>最近 14 天</h3><div class="cap">每天复习了几次</div>${days || '<div class="muted">还没数据</div>'}</div>
      <div class="panel"><h3>各档通过率</h3><div class="cap">哪一档最容易挂</div>
        <table><thead><tr><th>档</th><th>次数</th><th>通过率</th></tr></thead><tbody>${stages || '<tr><td colspan="3" class="muted">—</td></tr>'}</tbody></table></div>
      <div class="panel"><h3>挂的时候错在哪</h3><div class="cap">不会 / 想不到 / 写错</div>
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
        <label class="field"><span>在途卡上限</span><input id="s-queue" type="number" value="${st.queue_limit}"></label>
        <label class="field"><span>待归卡区上限</span><input id="s-inbox" type="number" value="${st.inbox_limit}"></label>
        <label class="field"><span>早班新题数</span><input id="s-morning" type="number" value="${st.morning_new}"></label>
        <label class="field"><span>午班到期数</span><input id="s-noon" type="number" value="${st.noon_due}"></label>
        <label class="field"><span>晚班到期数</span><input id="s-evening" type="number" value="${st.evening_due}"></label>
        <label class="field"><span>每日时长上限（分钟）</span><input id="s-minutes" type="number" value="${st.daily_minutes}"></label>
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
    inbox_limit: $('#s-inbox').value,
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
  VIEWS[CURRENT_VIEW]();
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
