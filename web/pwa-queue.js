/* permitA38 – offline write queue (IndexedDB), shared by the page (window) and the service worker.
 *
 * What is queued: ONLY time tracking (timer start/stop, new time entries). Never logins, never edits or deletes.
 * Why it is safe to replay: new entries carry a client-generated id (the server returns the stored entry when it
 * sees the id again) and timer actions carry the client time `at` (a stop with nothing running is a no-op, a start
 * within 5 s of the running timer is a replay). Replaying twice therefore never creates duplicates.
 *
 * The queue items are tagged with the user they belong to and are only replayed with THAT user's session. */
(function (g) {
  'use strict';
  const DB = 'permita38-pwa', VERSION = 1, CAP = 200, LOCK = 'permita38-replay';
  let dbp = null;

  function open() {
    if (dbp) return dbp;
    dbp = new Promise((resolve, reject) => {
      const r = g.indexedDB.open(DB, VERSION);
      r.onupgradeneeded = () => {
        const db = r.result;
        db.createObjectStore('queue', { keyPath: 'seq', autoIncrement: true });
        db.createObjectStore('meta', { keyPath: 'k' });
        db.createObjectStore('snap', { keyPath: 'path' });
      };
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => { dbp = null; reject(r.error); };
    });
    return dbp;
  }
  const run = (store, mode, op) => open().then(db => new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const req = op(t.objectStore(store));
    t.oncomplete = () => resolve(req ? req.result : undefined);
    t.onerror = t.onabort = () => reject(t.error);
  }));

  const Q = {
    CAP,
    available: () => !!g.indexedDB,
    /* ---- queue ---- */
    async enqueue(item) {
      if ((await Q.list()).length >= CAP) throw new Error('The offline queue is full (' + CAP + ' items). Connect to sync first.');
      const rec = Object.assign({ created: Date.now(), attempts: 0, state: 'pending', error: '' }, item);
      delete rec.seq;
      const seq = await run('queue', 'readwrite', s => s.add(rec));
      rec.seq = seq;
      return rec;
    },
    list: () => run('queue', 'readonly', s => s.getAll()).then(a => a || []),
    put: item => run('queue', 'readwrite', s => s.put(item)),
    remove: seq => run('queue', 'readwrite', s => s.delete(seq)),
    async pending(userId) { return (await Q.list()).filter(i => i.state === 'pending' && (!userId || i.user === userId)); },
    async attention(userId) { return (await Q.list()).filter(i => i.state === 'attention' && (!userId || i.user === userId)); },
    /* ---- small key/value store (auth mirror for the service worker, last-known user, local timer) ---- */
    getMeta: k => run('meta', 'readonly', s => s.get(k)).then(r => (r ? r.v : undefined)),
    setMeta: (k, v) => run('meta', 'readwrite', s => s.put({ k, v })),
    delMeta: k => run('meta', 'readwrite', s => s.delete(k)),
    /* ---- last-known lists for the offline Today screen (no money data, see pwa.js) ---- */
    snapGet: path => run('snap', 'readonly', s => s.get(path)).then(r => (r ? r.data : undefined)),
    snapPut: (path, data) => run('snap', 'readwrite', s => s.put({ path, data, at: Date.now() })),
    snapClear: () => run('snap', 'readwrite', s => s.clear()),

    /* ---- replay (FIFO). Returns {sent, attention, stopped:'offline'|'auth'|'server'|null, left} ---- */
    async replay(opts) {
      const o = Object.assign({ base: '/api/', fetchImpl: g.fetch.bind(g) }, opts || {});
      const out = { sent: 0, attention: 0, stopped: null, left: 0 };
      const auth = await Q.getMeta('auth');
      if (!auth || !auth.token) { out.stopped = 'auth'; out.left = (await Q.pending()).length; return out; }
      for (const item of await Q.pending(auth.userId)) {
        let r;
        try {
          r = await o.fetchImpl(o.base + item.path, {
            method: item.method,
            headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + auth.token },
            body: item.body === undefined ? undefined : JSON.stringify(item.body),
          });
        } catch { out.stopped = 'offline'; break; }
        if (r.ok) { await Q.remove(item.seq); out.sent++; continue; }
        if (r.status === 401) { out.stopped = 'auth'; break; }
        if (r.status >= 500 || r.status === 429 || r.status === 408) {
          item.attempts++; await Q.put(item); out.stopped = 'server'; break;
        }
        // other 4xx: the server refused this item for good -> needs the user's attention, do not block the rest
        let detail = r.statusText || 'rejected';
        try { const j = await r.json(); detail = typeof j.detail === 'string' ? j.detail : Array.isArray(j.detail) ? j.detail.map(e => e.msg).join('; ') : detail; } catch { /* no json */ }
        item.state = 'attention'; item.error = r.status + ': ' + detail; item.attempts++;
        await Q.put(item); out.attention++;
      }
      out.left = (await Q.pending(auth.userId)).length;
      return out;
    },
    /* one replay at a time across tabs and the service worker (Web Locks where available; replays are idempotent anyway) */
    replayLocked(opts) {
      const nav = g.navigator;
      if (nav && nav.locks && nav.locks.request) return nav.locks.request(LOCK, () => Q.replay(opts));
      return Q.replay(opts);
    },
  };
  g.P38Queue = Q;
})(typeof self !== 'undefined' ? self : this);
