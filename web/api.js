/* permitA38 – API client. All HTTP access to the backend lives here (REST under /api, JSON errors {detail}). */
(() => {
  const TOKEN_KEY = 'permita38.token';
  const base = () => (window.PERMITA38_API_BASE || '').replace(/\/$/, '') + '/api/';
  const store = {
    get() { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } },
    set(t) { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch { /* storage unavailable */ } },
  };
  function detailText(d, fallback) {
    if (!d) return fallback;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map(e => { const f = (e.loc || []).filter(x => x !== 'body' && x !== 'query').join('.'); return (f ? f + ': ' : '') + (e.msg || 'invalid'); }).join('; ');
    return fallback;
  }
  async function request(method, path, body, opts = {}) {
    const headers = {}; const t = store.get();
    if (t) headers.Authorization = 'Bearer ' + t;
    let payload;
    if (body instanceof FormData) payload = body;
    else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
    let r;
    try { r = await fetch(base() + path, { method, headers, body: payload, credentials: 'include' }); }
    catch { throw new Error('Cannot reach the permitA38 server. Is it running?'); }
    const data = await r.json().catch(() => ({}));
    if (r.status === 401 && !opts.noAuthRedirect) {
      store.set(null);
      if (typeof api.onUnauthorized === 'function') api.onUnauthorized();
      const err = new Error(detailText(data.detail, 'Please sign in'));
      err.status = 401;
      throw err;
    }
    if (!r.ok) {
      const err = new Error(detailText(data.detail, r.statusText || 'Request failed'));
      err.status = r.status;
      err.data = data;   // the whole error body (e.g. the item list of a refused import)
      err.retryAfter = r.headers.get('Retry-After');
      throw err;
    }
    if (data && typeof api.onWarnings === 'function') {
      const list = [].concat(Array.isArray(data.planning_warnings) ? data.planning_warnings : [], Array.isArray(data.sanity_warnings) ? data.sanity_warnings : []);
      if (list.length) api.onWarnings(list, method, path);
    }
    return data;
  }
  // legacy-compatible signature: api(method, path, body)
  const api = (method, path, body, opts) => request(method, path, body, opts);
  api.token = store;
  api.get = (p, o) => request('GET', p, undefined, o);
  api.post = (p, b, o) => request('POST', p, b === undefined ? {} : b, o);
  api.put = (p, b, o) => request('PUT', p, b, o);
  api.del = (p, o) => request('DELETE', p, undefined, o);
  api.upsert = (coll, obj) => (obj.id ? api.put(`${coll}/${obj.id}`, obj) : api.post(coll, obj));
  api.upload = file => { const fd = new FormData(); fd.append('file', file); return request('POST', 'attachments', fd); };
  api.url = path => base() + path;   // for plain links (cookie-authenticated downloads)
  api.onUnauthorized = null;
  api.onWarnings = null;   // (warnings, method, path): non-blocking planning and sanity warnings from a saved record
  window.api = api;
})();
