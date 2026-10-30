/* 后台：找端口、转发请求、离线时进队列，服务一上线自动补写。 */

const QUEUE_KEY = 'pending_jobs';
const PORT_KEY = 'cached_port';
const PORT_RANGE = [8765, 8786];

let cachedPort = null;
let lastProbe = 0;

async function findPort(force = false) {
  const now = Date.now();
  if (!force && cachedPort && now - lastProbe < 30000) return cachedPort;
  lastProbe = now;

  const stored = await chrome.storage.local.get(PORT_KEY);
  const candidates = [];
  if (stored[PORT_KEY]) candidates.push(stored[PORT_KEY]);
  for (let p = PORT_RANGE[0]; p < PORT_RANGE[1]; p++) {
    if (!candidates.includes(p)) candidates.push(p);
  }

  for (const port of candidates) {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 500);
      const res = await fetch(`http://127.0.0.1:${port}/api/health`, { signal: ctrl.signal });
      clearTimeout(timer);
      if (res.ok) {
        const data = await res.json();
        if (data && data.app === '刷题调度') {
          cachedPort = port;
          await chrome.storage.local.set({ [PORT_KEY]: port });
          return port;
        }
      }
    } catch (e) {
      /* 端口没开，继续试下一个 */
    }
  }
  cachedPort = null;
  return null;
}

async function getQueue() {
  const got = await chrome.storage.local.get(QUEUE_KEY);
  return got[QUEUE_KEY] || [];
}

async function setQueue(q) {
  await chrome.storage.local.set({ [QUEUE_KEY]: q });
  paintBadge(q.length);
}

function paintBadge(n) {
  chrome.action.setBadgeText({ text: n ? String(n) : '' });
  chrome.action.setBadgeBackgroundColor({ color: '#854F0B' });
}

async function enqueue(job) {
  const q = await getQueue();
  q.push({ ...job, at: Date.now() });
  await setQueue(q);
}

async function send(job) {
  const port = await findPort();
  if (!port) return { ok: false, offline: true };
  try {
    const res = await fetch(`http://127.0.0.1:${port}/api${job.path}`, {
      method: job.method || 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: job.body ? JSON.stringify(job.body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    return { ok: res.ok, status: res.status, data };
  } catch (e) {
    return { ok: false, offline: true };
  }
}

async function flush() {
  const q = await getQueue();
  if (!q.length) return { flushed: 0, pending: 0 };
  const port = await findPort(true);
  if (!port) {
    paintBadge(q.length);
    return { flushed: 0, pending: q.length };
  }
  const rest = [];
  let flushed = 0;
  for (const job of q) {
    const r = await send(job);
    if (r.ok) flushed += 1;
    else if (r.offline) rest.push(job);
    // 服务端明确拒绝（409 之类）的不再重试，直接丢弃
  }
  await setQueue(rest);
  return { flushed, pending: rest.length };
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    if (msg.type === 'api') {
      const method = (msg.method || 'POST').toUpperCase();
      const job = { path: msg.path, method, body: msg.body };
      const r = await send(job);
      if (r.ok) {
        reply({ ok: true, data: r.data, pending: (await getQueue()).length });
        return;
      }
      if (r.offline) {
        if (method !== 'GET') await enqueue(job);
        reply({ ok: false, offline: true, pending: (await getQueue()).length });
        return;
      }
      reply({
        ok: false,
        offline: false,
        status: r.status,
        error: (r.data && r.data.error) || `服务异常 ${r.status || ''}`.trim(),
      });
      return;
    }
    if (msg.type === 'status') {
      const port = await findPort();
      const q = await getQueue();
      reply({ online: !!port, port, pending: q.length });
      return;
    }
    if (msg.type === 'flush') {
      reply(await flush());
      return;
    }
    reply({ ok: false });
  })();
  return true; // 异步回调
});

chrome.alarms.create('flush', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === 'flush') flush();
});
chrome.runtime.onStartup.addListener(() => flush());
chrome.runtime.onInstalled.addListener(async () => {
  paintBadge((await getQueue()).length);
  flush();
});
