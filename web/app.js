/* permitA38 – core: state, helpers, forms, router, daily-work pages. Vanilla JS, no build step. */
const S = { me: null, settings: {}, timer: null, d: {} };
const A = {};      // click actions:  <button data-a="name" data-id="..">
const PAGES = {};  // route -> async (args) => html
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
/** localStorage can be blocked or full: a remembered filter is a convenience, never a reason to fail. */
const lsGet = (k, d = '') => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } };
const eur = n => new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' }).format(n || 0);
const num = (n, d = 1) => new Intl.NumberFormat('de-DE', { maximumFractionDigits: d }).format(n || 0);
const hm = h => { const m = Math.round(Math.abs(h) * 60); return (h < 0 ? '-' : '') + Math.floor(m / 60) + ':' + String(m % 60).padStart(2, '0'); };
const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const todayISO = () => iso(new Date());
const parseD = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const addDays = (s, n) => { const d = parseD(s); d.setDate(d.getDate() + n); return iso(d); };
const mondayOf = s => { const d = parseD(s); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return iso(d); };
const fmtDate = s => s ? parseD(s).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' }) : '';
const dayName = s => parseD(s).toLocaleDateString('en-GB', { weekday: 'short' });
const isBoss = () => S.me && S.me.role === 'boss';
/** The page of a boss-only area, shown to everybody else. */
const denyMember = () => '<div class="card" role="alert">This page is only available to the boss.</div>';
/* permissions come from the server with the user: S.me.permissions[area] = {read, write} with scope none|limited|all.
   The server enforces them; the UI only hides what a role cannot use. */
const scopeOf = (area, op = 'read') => S.me?.permissions?.[area]?.[op] || 'none';
const can = (area, op = 'read') => scopeOf(area, op) !== 'none';
const by = (arr, k = 'id') => Object.fromEntries(arr.map(x => [x[k], x]));
const nameOf = (coll, id) => (S.d[coll] || []).find(x => x.id === id)?.name || '';

api.onUnauthorized = () => { if (S.me) S.expired = true; S.me = null; render(); };
function toast(msg, err) { const t = $('#toast'); t.textContent = msg; t.className = 'on' + (err ? ' err' : ''); clearTimeout(toast.t); toast.t = setTimeout(() => t.className = '', err ? 4000 : 1800); }
const guard = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };

const COLL_AREA = { customers: 'customers', projects: 'projects', tasks: 'projects', entries: 'time', contracts: 'contracts', absences: 'absences', offers: 'offers', invoices: 'invoices', expenses: 'expenses', trips: 'trips', users: 'team' };
async function loadAll(only) {
  const colls = (only || Object.keys(COLL_AREA)).filter(c => can(COLL_AREA[c]));
  const res = await Promise.all(colls.map(c => api('GET', c)));
  colls.forEach((c, i) => S.d[c] = res[i]);
}
async function bootstrap() {
  const b = await api('GET', 'bootstrap');
  Object.assign(S, { me: b.me, settings: b.settings, timer: b.timer, states: b.states, conflicts: b.conflicts, templates: b.templates, data_dir: b.data_dir, features: b.features });
  await loadAll();
}

/* ---------- modal + forms ---------- */
function closeModal() { runCloseHook(); $('#modal-root').onkeydown = null; $('#modal-root').innerHTML = ''; }
/** A dialog can register cleanup (e.g. revoking object URLs) that runs once when it closes. */
function runCloseHook() { const h = closeModal.onClose; closeModal.onClose = null; if (h) h(); }
let MODAL_SEQ = 0;  // bumped every time a dialog opens: a save that finishes after its dialog was cancelled must not close another one
function modal(html, wide) {
  runCloseHook();
  MODAL_SEQ++;
  $('#modal-root').onkeydown = null;   // a dialog's key handler must not outlive it
  $('#modal-root').innerHTML = `<div class="modal-bg"><div class="modal ${wide ? 'wide' : ''}">${html}</div></div>`;
  const first = $('#modal-root input:not([type=hidden]), #modal-root select'); if (first) first.focus();
}
function fieldHTML(f, v) {
  const val = v?.[f.k] ?? f.def ?? '';
  const id = 'f_' + f.k;
  let inner;
  if (f.t === 'select') inner = `<select id="${id}">${(f.o || []).map(o => { const [ov, ol] = Array.isArray(o) ? o : [o, o]; return `<option value="${esc(ov)}" ${String(ov) === String(val) ? 'selected' : ''}>${esc(ol)}</option>`; }).join('')}</select>`;
  else if (f.t === 'area') inner = `<textarea id="${id}" rows="${f.rows || 3}" placeholder="${esc(f.ph || '')}">${esc(val)}</textarea>`;
  else if (f.t === 'check') return `<div class="field"><label><input type="checkbox" id="${id}" ${val === true || val === 'true' ? 'checked' : ''}> ${esc(f.l)}</label>${f.h ? `<div class="muted small">${esc(f.h)}</div>` : ''}</div>`;
  else inner = `<input id="${id}" type="${f.t || 'text'}" value="${esc(val)}" placeholder="${esc(f.ph || '')}" ${f.max ? `max="${f.max}"` : ''} ${f.step ? `step="${f.step}"` : f.t === 'number' ? 'step="any"' : ''}>`;
  return `<div class="field"><label for="${id}">${esc(f.l)}</label>${inner}${f.h ? `<div class="muted small">${esc(f.h)}</div>` : ''}</div>`;
}
function formHTML(fields, vals) {
  let out = '', i = 0;
  while (i < fields.length) {
    const f = fields[i];
    if (f.row) { let g = [f]; while (fields[i + 1]?.row === f.row) g.push(fields[++i]); out += `<div class="row">${g.map(x => fieldHTML(x, vals)).join('')}</div>`; }
    else out += fieldHTML(f, vals);
    i++;
  }
  return out;
}
function readForm(fields, base) {
  const o = { ...base };
  for (const f of fields) {
    const el = $('#f_' + f.k); if (!el) continue;
    if (f.t === 'check') o[f.k] = el.checked;
    else if (f.t === 'number') o[f.k] = el.value === '' ? null : parseFloat(el.value.replace(',', '.'));
    else o[f.k] = el.value;
  }
  return o;
}
function openForm({ title, fields, vals = {}, save, del, wide, extra = '', saveLabel = 'Save', note = '' }) {
  modal(`<div class="modal-scroll"><h2>${esc(title)}</h2>${note}${formHTML(fields, vals)}${extra}</div><div class="actions">${del ? '<button class="danger" id="m-del" style="margin-right:auto">Delete</button>' : ''}<button id="m-cancel">Cancel</button><button class="primary" id="m-save">${esc(saveLabel)}</button></div>`, wide);
  const token = MODAL_SEQ, mine = () => token === MODAL_SEQ;
  let busy = false;   // a second Enter or click while a save runs is ignored (no double bill)
  $('#m-cancel').onclick = closeModal;
  const doSave = guard(async () => {
    if (busy) return;
    busy = true;
    try { await save(readForm(fields, vals)); if (mine()) closeModal(); } finally { busy = false; }
  });
  $('#m-save').onclick = doSave;
  $('#modal-root').onkeydown = e => { if (e.key === 'Enter' && e.target.tagName === 'INPUT') doSave(); if (e.key === 'Escape') closeModal(); };
  if (del) $('#m-del').onclick = guard(async () => { if (busy) return; if (confirm('Delete this? This cannot be undone.')) { busy = true; try { await del(vals); if (mine()) closeModal(); } finally { busy = false; } } });
}
const save = guard(async (coll, obj, msg = 'Saved') => { const r = await api.upsert(coll, obj); toast(msg); await loadAll([coll]); return r; });

/* ---------- time parsing ---------- */
function parseDur(s) {            // "1:30", "1.5", "90m", "2h 15m" -> minutes
  s = String(s || '').trim().toLowerCase().replace(',', '.'); if (!s) return null;
  let m;
  if ((m = s.match(/^(\d+):(\d{1,2})$/))) return +m[1] * 60 + +m[2];
  if ((m = s.match(/^(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?$/)) && (m[1] || m[2])) return Math.round((+m[1] || 0) * 60 + (+m[2] || 0));
  if (/^\d+(\.\d+)?$/.test(s)) return Math.round(parseFloat(s) * 60);
  return NaN;
}
// Project choices. customerId undefined = no customer concept next to the picker (all projects). Otherwise only the
// projects of that customer plus general projects without a customer; '' (no customer chosen) gives the general ones.
// currentId is the stored project of a record: when it belongs to another customer (legacy data) it stays listed,
// marked, so it is not silently lost; no other mismatching project is offered. billable = for an offer or invoice:
// internal projects are never billed, so they are left out (a stored one is kept, marked, like a mismatch).
const projectOptions = (withNone = true, customerId = undefined, currentId = null, billable = false) => {
  const live = (S.d.projects || []).filter(p => p.status !== 'archived');
  const isInt = p => (p.kind || (p.customer_id ? 'customer' : 'internal')) === 'internal';
  const label = p => p.name + (isInt(p) ? ' (internal)' : '');
  const fits = p => (!billable || !isInt(p)) && (customerId === undefined || !p.customer_id || p.customer_id === customerId);
  const out = [...(withNone ? [['', '— no project —']] : []), ...live.filter(fits).map(p => [p.id, label(p)])];
  const cur = currentId && live.find(p => p.id === currentId);
  if (cur && !fits(cur)) out.push([cur.id, label(cur) + (billable && isInt(cur) ? '' : ' (other customer)')]);
  return out;
};
// Rebuilds a project <select> after the customer next to it changed; a project that is no longer listed is cleared.
function refreshProjectSelect(sel, customerId, billable = false) {
  if (!sel) return;
  const cur = sel.value;
  const opts = projectOptions(true, customerId || '', null, billable);
  sel.innerHTML = opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
  sel.value = opts.some(([v]) => v === cur) ? cur : '';
}
const taskOptions = pid => [['', '— no task —'], ...(S.d.tasks || []).filter(t => t.project_id === pid).map(t => [t.id, t.name])];

function entryForm(e = {}, after) {
  const vals = { date: todayISO(), billable: true, user_id: S.me.id, ...e, dur: e.minutes ? hm(e.minutes / 60) : '' };
  const users = scopeOf('time', 'write') === 'all' ? [{ k: 'user_id', l: 'Who', t: 'select', o: (S.d.users || []).filter(u => u.active !== false).map(u => [u.id, u.name]), def: S.me.id }] : [];
  const fields = [...users,
    { k: 'date', l: 'Date', t: 'date', row: 1, max: todayISO() }, { k: 'start', l: 'From', t: 'time', row: 1 }, { k: 'end', l: 'To', t: 'time', row: 1 }, { k: 'dur', l: 'or duration (1:30, 1.5, 90m)', row: 1 },
    { k: 'project_id', l: 'Project', t: 'select', o: projectOptions() },
    { k: 'task_id', l: 'Task', t: 'select', o: taskOptions(vals.project_id) },
    { k: 'note', l: 'What did you do?', t: 'area', rows: 2 },
    { k: 'billable', l: 'Billable', t: 'check' }];
  openForm({
    title: e.id ? 'Edit time' : 'Add time', fields, vals,
    save: async v => {
      let min = parseDur(v.dur);
      if ((min === null || isNaN(min)) && v.start && v.end) { const [a, b] = [v.start, v.end].map(x => +x.slice(0, 2) * 60 + +x.slice(3)); min = b - a; if (min <= 0) min += 1440; }
      if (!v.date) throw new Error('Pick a date');
      if (v.date > todayISO()) throw new Error('Time cannot be added for a future date');
      if (min === null || isNaN(min) || min <= 0) throw new Error('Enter from/to times or a duration');
      if (min > 1440) throw new Error('A time entry can be at most 24 hours');
      v.minutes = min; delete v.dur;
      await save('entries', { ...v, project_id: v.project_id || null, task_id: v.task_id || null });
      after && after(v);
    },
    del: e.id ? async () => { await api('DELETE', 'entries/' + e.id); await loadAll(['entries']); after && after(); } : null
  });
  $('#f_project_id').onchange = () => { $('#f_task_id').innerHTML = taskOptions($('#f_project_id').value).map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join(''); };
  if (vals.task_id) $('#f_task_id').value = vals.task_id;
}

/* ---------- layout + router ---------- */
/* nav: [route, icon, label, area needed to see it, 'write' if it needs write access (or 'all' when the read scope must be all, not a restricted view)] */
const NAV = [['today', '⏱', 'Today', 'timer'], ['time', '🗓', 'Timesheet', 'time', 'write'], ['timeoff', '🏖', 'Time off', 'absences'], ['plan', '📈', 'Planning', 'planning'], ['customers', '👥', 'Customers', 'customers'], ['projects', '📁', 'Projects', 'projects', 'all'],
  ['sales', '🧾', 'Offers & Invoices', 'invoices'], ['expenses', '💳', 'Expenses', 'expenses'], ['suppliers', '🏭', 'Suppliers', 'suppliers'], ['incoming', '📨', 'Incoming offers', 'procurement'], ['trips', '🧳', 'Business trips', 'trips'], ['money', '💶', 'Money & Taxes', 'finance'], ['team', '🧑‍🤝‍🧑', 'Team', 'team', 'write'], ['timeline', '📅', 'Team timeline', 'team_timeline'], ['settings', '⚙️', 'Business settings', 'settings', 'all'], ['me', '👤', 'My settings', '*'], ['tokens', '🔑', 'API tokens', '*']];
const navAllowed = ([, , , area, need]) => (area === '*' ? true : need === 'write' ? can(area, 'write') : need === 'all' ? scopeOf(area) === 'all' : can(area));
const navFor = () => NAV.filter(n => navAllowed(n) && !(n[0] === 'timeoff' && can('planning')));
/* which area (and, for the read-only lock, which write area) a page belongs to */
function pageArea(p, args) {
  if (p === 'doc') return args[0] === 'offers' ? 'offers' : 'invoices';
  if (p === 'sales') return can('invoices') ? 'invoices' : 'offers';
  return { customer: 'customers', project: 'projects', numbering: 'settings', import: 'settings', dataexport: 'settings' }[p] || NAV.find(n => n[0] === p)?.[3] || null;
}
const ROLE_LABEL = { boss: 'Boss', employee: 'Employee', accountant: 'Accountant' };
function route() { const [p, ...a] = (location.hash.replace(/^#\/?/, '') || 'today').split('/'); return [p, a]; }
function deny() { toast('Not available for your role'); const first = navFor()[0]; location.hash = '#/' + (first ? first[0] : 'today'); }
/* a page whose area the role may read but not write: take the write controls away */
const WRITE_ACTIONS = ['editCustomer', 'newDoc', 'billTime', 'billTimeInto', 'editProject', 'editTask', 'supNew', 'supEdit', 'supDelete', 'supAdopt', 'offNew', 'offEdit', 'offDelete', 'offReview', 'offAccept', 'offReject', 'offReopen', 'offUnaccept', 'allocCancel', 'docSave', 'docFinalize', 'docAccept', 'docToInvoice', 'addLine', 'saveSettings', 'editTemplate', 'editUser', 'delLine', 'docPdfDelete', 'newTrip', 'timerStart', 'timerStop', 'timerAdd'];
function lockPage(root, p) {
  root.querySelectorAll('[data-a]').forEach(el => { if (WRITE_ACTIONS.includes(el.dataset.a)) { if (el.tagName === 'TR') { delete el.dataset.a; el.classList.remove('click'); } else el.remove(); } });
  root.querySelectorAll('[data-change="docStatus"]').forEach(el => el.remove());
  if (p === 'doc' || p === 'settings') root.querySelectorAll('input,select,textarea').forEach(el => { el.disabled = true; });
  root.insertAdjacentHTML('afterbegin', '<div class="banner info" role="status">Read-only for your role.</div>');
}
async function render() {
  const app = $('#app');
  clearInterval(render.tick);
  const seq = render.seq = (render.seq || 0) + 1;   // a slower, older render must not overwrite a newer page
  if (!S.me) return renderLogin(app);
  const [p, args] = route();
  if (p !== 'doc' && typeof DOC !== 'undefined') DOC = null;
  const nav = navFor();
  const active = { customer: 'customers', project: 'projects', doc: 'sales' }[p] || p;
  const area = pageArea(p, args), item = NAV.find(n => n[0] === p);
  const ok = item ? navAllowed(item) : ['customer', 'project', 'doc'].includes(p) ? !!area && scopeOf(area) === 'all'
    : ['numbering', 'import', 'dataexport'].includes(p) && can('settings', 'write') && scopeOf('settings') === 'all';   // boss-only management pages without a menu entry
  if (!PAGES[p] || !ok) {
    const first = nav[0];
    if (first && (!PAGES[p] || p === 'today')) { location.hash = '#/' + first[0]; return; }  // unknown page or default landing
    return deny();
  }
  let html;
  try { html = await PAGES[p](...args); } catch (e) {
    if (!S.me) return;
    if (e.status === 403 && !isBoss()) return deny();
    if (e.status) { toast(e.message, true); html = `<div class="card">Something went wrong: ${esc(e.message)}</div>`; }  // API error: shown, not a crash
    else { html = `<div class="card">Something went wrong: ${esc(e.message)}</div>`; console.error(e); }
  }
  if (!S.me || seq !== render.seq) return;
  app.innerHTML = `<div class="shell"><nav class="side"><div class="brand"><i></i>permitA38</div>
    ${nav.map(([k, ic, l]) => `<a class="nav ${k === active ? 'on' : ''}" href="#/${k}"><span>${ic}</span>${l}</a>`).join('')}
    <div class="spacer"></div><div class="me"><b>${esc(S.me.name)}</b><br>${esc(ROLE_LABEL[S.me.role] || S.me.role)}</div>
    <a class="nav" data-a="changePassword">🔑 Change password</a><a class="nav" data-a="twoFactor">🛡 Two-factor authentication</a><a class="nav" data-a="logout">↩ Sign out</a></nav><main id="main">${S.conflicts?.length ? `<div class="banner">⚠ ${S.conflicts.length} sync conflict file(s) found in your data folder (e.g. <code>${esc(S.conflicts[0])}</code>). Compare and delete the copy you do not need.</div>` : ''}${html}</main></div>`;
  if (area && area !== 'team_timeline' && can(area) && !can(area, 'write') && !(p === 'sales' && can('offers', 'write'))) lockPage($('#main'), p);   // the timeline is read-only by design: no banner
  if (render.after) { const f = render.after; render.after = null; f(); }
}
window.addEventListener('hashchange', guard(render));
window.addEventListener('unhandledrejection', e => { e.preventDefault(); toast((e.reason && e.reason.message) || 'Something went wrong', true); });

document.addEventListener('click', e => {
  const el = e.target.closest('[data-a]'); if (!el) return;
  e.preventDefault(); const fn = A[el.dataset.a]; if (fn) guard(fn)(el, e);
});
document.addEventListener('change', e => { const el = e.target.closest('[data-change]'); if (el && A[el.dataset.change]) guard(A[el.dataset.change])(el, e); });
document.addEventListener('click', e => { const tr = e.target.closest('tr[data-href]'); if (tr && !e.target.closest('button,a')) location.hash = tr.dataset.href; });

/* ---------- login / setup ---------- */
const BRAND = `<div class="auth-brand"><svg viewBox="0 0 32 32" width="34" height="34" aria-hidden="true"><circle cx="16" cy="16" r="14" fill="var(--accent)"/><path d="M16 8v8l5 3" stroke="var(--accent-ink)" stroke-width="3" fill="none" stroke-linecap="round"/></svg><span>permit<b>A38</b></span></div>`;
const pwField = (id, label, auto, extra = '') => `<div class="field"><label for="${id}">${label}</label><div class="pw"><input id="${id}" type="password" autocomplete="${auto}" ${extra}><button type="button" class="ghost sm pw-toggle" data-a="togglePw" data-for="${id}" aria-label="Show password" tabindex="-1">👁</button></div></div>`;
A.togglePw = el => { const i = $('#' + el.dataset.for); const show = i.type === 'password'; i.type = show ? 'text' : 'password'; el.setAttribute('aria-label', show ? 'Hide password' : 'Show password'); el.textContent = show ? '🙈' : '👁'; };
function authError(msg) { const e = $('#auth-err'); if (e) { e.textContent = msg; e.hidden = !msg; } }
function authBusy(btn, busy, label) { btn.disabled = busy; btn.textContent = busy ? 'Please wait…' : label; }
async function renderLogin(app) {
  let st;
  try { st = await api.get('auth/status', { noAuthRedirect: true }); }
  catch (e) { app.innerHTML = `<div class="auth-wrap"><div class="auth-card">${BRAND}<div class="banner">${esc(e.message)}</div></div></div>`; return; }
  const expired = S.expired;
  const foot = `<p class="small muted auth-foot">Your data stays on your own server or computer – no cloud.${st.version ? ' · v' + esc(st.version) : ''}</p>`;
  if (st.setup_needed) {
    app.innerHTML = `<div class="auth-wrap"><form class="auth-card" id="setup-form" autocomplete="on" novalidate>${BRAND}<h1>Welcome 👋</h1><p class="muted">Create your boss account. You can add team members later.</p>
      <div class="field"><label for="s_name">Your name</label><input id="s_name" autocomplete="name" maxlength="200" required autofocus></div>
      <div class="row"><div class="field"><label for="s_user">Username</label><input id="s_user" autocomplete="username" autocapitalize="none" spellcheck="false" maxlength="64" required></div><div class="field"><label for="s_h">Weekly hours</label><input id="s_h" type="number" value="40" min="0" max="168" step="any"></div></div>
      ${pwField('s_pw', 'Password (min. 8 characters)', 'new-password', 'maxlength="1000" required')}${pwField('s_pw2', 'Repeat password', 'new-password')}
      <div class="field"><label for="s_co">Business name (optional)</label><input id="s_co" autocomplete="organization"></div>
      <div class="banner" id="auth-err" role="alert" hidden></div><button class="primary" id="s_go" type="submit" style="width:100%;justify-content:center">Create account</button>${foot}</form></div>`;
    $('#setup-form').onsubmit = async e => {
      e.preventDefault(); const pw = $('#s_pw').value; const btn = $('#s_go');
      if (!$('#s_name').value.trim() || !$('#s_user').value.trim()) return authError('Please enter your name and a username.');
      if (pw.length < 8) return authError('The password must be at least 8 characters long.');
      if (pw.length > 1000) return authError('The password must be at most 1000 characters long.');
      if (pw !== $('#s_pw2').value) return authError('The passwords do not match.');
      authError(''); authBusy(btn, true);
      try {
        await api.post('auth/setup', { name: $('#s_name').value.trim(), username: $('#s_user').value.trim(), password: pw, company_name: $('#s_co').value.trim(), weekly_hours: Number.isFinite(parseFloat($('#s_h').value)) ? parseFloat($('#s_h').value) : 40 }, { noAuthRedirect: true });
        const r = await api.post('auth/login', { username: $('#s_user').value.trim(), password: pw }, { noAuthRedirect: true });
        await enterApp(r.token);
      } catch (err) { authError(err.message); authBusy(btn, false, 'Create account'); }
    };
    return;
  }
  app.innerHTML = `<div class="auth-wrap"><form class="auth-card" id="login-form" autocomplete="on" novalidate>${BRAND}<h1>Sign in</h1><p class="muted">Time, offers, invoices and taxes – in one place.</p>
    ${expired ? '<div class="banner info" role="status">Your session expired. Please sign in again.</div>' : ''}
    <div class="field"><label for="l_user">Username</label><input id="l_user" autocomplete="username" autocapitalize="none" spellcheck="false" autofocus></div>
    ${pwField('l_pw', 'Password', 'current-password')}
    <div class="banner" id="auth-err" role="alert" hidden></div><button class="primary" id="l_go" type="submit" style="width:100%;justify-content:center">Sign in</button>${foot}</form></div>`;
  $('#login-form').onsubmit = async e => {
    e.preventDefault(); const btn = $('#l_go'), u = $('#l_user').value.trim(), p = $('#l_pw').value;
    if (!u || !p) return authError('Please enter your username and password.');
    authError(''); authBusy(btn, true);
    try {
      const r = await api.post('auth/login', { username: u, password: p }, { noAuthRedirect: true });
      if (r.mfa_required) return renderMfa(app, r.mfa_token);   // second step, see twofactor.js
      await enterApp(r.token);
    } catch (err) { authError(err.message); authBusy(btn, false, 'Sign in'); $('#l_pw').select(); }
  };
}
async function enterApp(token) {
  api.token.set(token); S.expired = false; await bootstrap();
  if (route()[0] === 'login') location.hash = '#/today';
  render();
}
A.logout = async () => { try { await api.post('auth/logout', {}, { noAuthRedirect: true }); } catch { /* ignore */ } api.token.set(null); S.expired = false; S.me = null; S.d = {}; render(); };
A.changePassword = () => openForm({
  title: 'Change password', saveLabel: 'Change password', fields: [], vals: {},
  extra: `${pwField('cp_old', 'Current password', 'current-password')}${pwField('cp_new', 'New password (min. 8 characters)', 'new-password')}${pwField('cp_new2', 'Repeat new password', 'new-password')}`,
  save: async () => {
    const o = $('#cp_old').value, n = $('#cp_new').value;
    if (n.length < 8) throw new Error('The new password must be at least 8 characters long.');
    if (n !== $('#cp_new2').value) throw new Error('The new passwords do not match.');
    const r = await api.post('auth/change-password', { old_password: o, new_password: n }); if (r.token) api.token.set(r.token); toast('Password changed');
  }
});

/* ================= TODAY ================= */
function timerElapsed() { return S.timer?.running ? Math.max(0, Date.now() / 1000 - S.timer.started) : 0; }
const clockStr = s => { s = Math.floor(s); return [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60].map(n => String(n).padStart(2, '0')).join(':'); };
PAGES.today = async () => {
  const dash = await api('GET', 'dashboard');
  S.timer = await api('GET', 'timer');
  await loadAll(['entries']);
  const now = todayISO(), entries = S.d.entries.filter(e => e.date === now && e.user_id === S.me.id);
  const pct = dash.target ? Math.min(100, dash.worked / dash.target * 100) : 0;
  const projects = by(S.d.projects);
  const hello = new Date().getHours() < 12 ? 'Good morning' : new Date().getHours() < 18 ? 'Hello' : 'Good evening';
  let extras = '';
  moneyView = false;   // a fresh view starts on time, not money
  if (isBoss()) {
    const inv = S.d.invoices, off = S.d.offers, rep = await api('GET', 'project-report');
    const overdue = inv.filter(i => i.status === 'sent' && i.due_date && i.due_date < now);
    const unbilled = Object.values(rep).reduce((a, r) => a + r.unbilled_revenue, 0);
    const openOff = off.filter(o => o.status === 'sent');
    extras = `<div class="grid g4"><div class="card kpi ${unbilled ? 'warn' : ''}"><div class="l">Unbilled work</div><div class="v">${eur(unbilled)}</div><a href="#/sales" class="small">Create invoice →</a></div>
      <div class="card kpi ${overdue.length ? 'bad' : ''}"><div class="l">Overdue invoices</div><div class="v">${overdue.length}</div><span class="small muted">${eur(overdue.reduce((a, i) => a + docTotal(i), 0))}</span></div>
      <div class="card kpi"><div class="l">Open offers</div><div class="v">${openOff.length}</div><span class="small muted">${eur(openOff.reduce((a, i) => a + docTotal(i), 0))}</span></div>
      <div class="card kpi"><div class="l">Flex-time balance</div><div class="v">${dash.balance === null ? '–' : (dash.balance >= 0 ? '+' : '') + hm(dash.balance)}</div><span class="small muted">${dash.balance === null ? 'set "tracking since" in Team' : 'worked − target'}</span></div></div>`;
  }
  if (isBoss() && (!S.d.customers?.length || !S.d.projects?.length)) {
    const numLog = await api('GET', 'numbering/log'), batches = await api('GET', 'import/batches');
    const st = S.settings, steps = [[numLog.length > 0 || S.d.invoices.some(i => i.number), 'Check your document numbering (start number, pattern)', '#/numbering'], [batches.length > 0 || S.d.invoices.some(i => i.imported), 'Import existing offers and invoices (optional)', '#/import'], [!!st.company_name && !!(st.tax_number || st.vat_id), 'Fill in your business details (address, tax number, bank)', '#/settings'], [S.d.customers.length > 0, 'Add your first customer', '#/customers'], [S.d.projects.length > 0, 'Create a project (customer work or internal)', '#/projects'], [entries.length > 0 || S.d.entries.length > 0, 'Track your first hour with the Start button above', '#/today']];
    extras = `<div class="card" style="border-color:var(--accent)"><h2>👋 Getting started</h2><div class="muted small" style="margin-bottom:8px">Four steps to your first tracked hour. Full guide: <code>docs/getting-started.md</code></div>${steps.map(([done, t, h]) => `<div style="padding:4px 0">${done ? '✅' : '⬜'} <a href="${h}">${t}</a></div>`).join('')}</div>` + extras;
  }
  render.after = () => { timerUpd(); timerTick(); };
  return `<div class="head"><div><h1>${hello}, ${esc(S.me.name.split(' ')[0])}</h1><div class="muted">${new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })}</div></div><button data-a="addTime">+ Add time manually</button></div>
  ${timerCard()}
  <div class="card"><div class="row" style="align-items:center"><div><h2 style="margin:0">This week</h2><div class="muted">${hm(dash.worked)} of ${hm(dash.target)} h</div></div>
    <div class="right" style="flex:2"><div style="font-size:26px;font-weight:700" class="${dash.remaining ? '' : 'free-good'}">${dash.remaining ? hm(dash.remaining) + ' h to go' : '🎉 Weekly goal reached' + (dash.overtime ? ' (+' + hm(dash.overtime) + ')' : '')}</div>
    <div class="muted small">${dash.remaining && dash.remaining_days ? `≈ ${hm(dash.per_day_needed)} h per remaining working day (${dash.remaining_days} day${dash.remaining_days > 1 ? 's' : ''} incl. today)` : dash.remaining ? 'No working days left this week' : ''}</div></div></div>
    <div class="bar ${pct >= 100 ? 'good' : ''}" style="margin:12px 0"><div style="width:${pct}%"></div></div>
    <div class="days">${dash.days.map(d => { const mx = Math.max(8, ...dash.days.map(x => Math.max(x.target, x.worked))); return `<div class="day ${d.date === now ? 'today' : ''} ${!d.target ? 'off' : ''}" title="${esc(d.holiday || d.absence || '')}"><div class="col"><div style="height:${d.worked / mx * 100}%"></div>${d.target ? `<span style="bottom:${d.target / mx * 100}%"></span>` : ''}</div><b>${dayName(d.date)}</b><br>${d.worked ? hm(d.worked) : d.holiday ? '🎌' : d.absence ? '🏖' : '–'}</div>`; }).join('')}</div></div>
  ${extras}
  <div class="card"><h2>Today · ${hm(dash.today_worked)} h</h2>${entries.length ? `<table>${entries.map(e => entryRow(e, projects)).join('')}</table>` : '<div class="empty">Nothing tracked yet today. Hit <b>Start</b> when you begin working.</div>'}</div>`;
};
const docTotal = d => Money.docGross(d, S.settings);
function entryRow(e, projects) {
  const p = projects[e.project_id];
  return `<tr class="click" data-a="editEntry" data-id="${e.id}"><td>${p ? `<span class="dot" style="background:${esc(p.color || '#888')}"></span><b>${esc(p.name)}</b>` : '<span class="muted">no project</span>'}${e.task_id ? ' · ' + esc(nameOf('tasks', e.task_id)) : ''}<div class="small muted">${esc(e.note || '')}</div></td>
    <td class="small muted nowrap">${e.start ? esc(e.start) + '–' + esc(e.end || '') : ''}</td><td class="num"><b>${hm(e.minutes / 60)}</b></td><td>${e.invoice_id ? '<span class="pill good">billed</span>' : e.billable === false ? '<span class="pill">non-billable</span>' : ''}</td></tr>`;
}
A.addTime = () => entryForm({}, v => {   // show the new entry: Today lists only today's own entries, so jump to its week otherwise
  const to = !v || (v.date === todayISO() && v.user_id === S.me.id) ? null : `#/time/${mondayOf(v.date)}/${v.user_id}`;
  if (to && location.hash !== to) location.hash = to; else render();
});
A.editEntry = el => entryForm(S.d.entries.find(e => e.id === el.dataset.id), render);
A.timerProj = () => { localStorage.lastProj = projIdOf(); $('#t_task').innerHTML = taskOptions(localStorage.lastProj).map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join(''); timerUpd(); };
A.timerTask = () => timerUpd();
/* One request per action at a time: a second click while it is pending is ignored (no duplicate entry or timer call). */
const timerInFlight = {};
const timerOnce = (key, fn) => async (...a) => { if (timerInFlight[key]) return; timerInFlight[key] = true; try { return await fn(...a); } finally { timerInFlight[key] = false; } };
A.timerStart = timerOnce('timer', async () => { const body = { project_id: projIdOrThrow() || null, task_id: $('#t_task').value || null, note: $('#t_note').value }; S.timer = await api('POST', 'timer/start', body); render(); });
/* ---- time tracking card (Today page) ---- */
let moneyView = false;   // per view, not stored: tracker shows money earned instead of the clock
const projLabel = p => p.name + ((p.kind || (p.customer_id ? 'customer' : 'internal')) === 'internal' ? ' (internal)' : '');
const labelById = id => { const p = (S.d.projects || []).find(x => x.id === id); return p ? projLabel(p) : ''; };
/* Project id from the typed label or name (case-insensitive); '' when nothing matches. */
function projIdOf() {
  const v = ($('#t_proj')?.value || '').trim().toLowerCase();
  if (!v) return '';
  return (S.d.projects || []).find(p => p.status !== 'archived' && (projLabel(p).toLowerCase() === v || p.name.toLowerCase() === v))?.id || '';
}
/* Like projIdOf, but typed text that matches no project is an error, so time is never booked to no project by accident. */
function projIdOrThrow() {
  const id = projIdOf();
  if (!id && ($('#t_proj')?.value || '').trim()) throw new Error('Pick a project from the list');
  return id;
}
/* Hourly rate the app uses for the project (same chain as suggestedRate: project, customer, user, settings); null when the whole chain is empty. */
function rateOf(pid) {
  const p = pid && (S.d.projects || []).find(x => x.id === pid);
  return p ? effectiveRate(p.customer_id, pid) : null;
}
/* Elapsed whole seconds x hourly rate, exact decimals, rounded half-up to cents. */
function earned(rate) {
  const r = Money.parse(rate);
  return eur(Money.toNumber(Money.roundDiv(BigInt(Math.floor(timerElapsed())) * r.n * 100n, 3600n * 10n ** BigInt(r.s))));
}
const activePid = () => (S.timer?.running ? S.timer.project_id : projIdOf());
const hhmm = m => String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
const toMin = s => { const m = /^(\d{1,2}):(\d{2})$/.exec(s || ''); return m ? +m[1] * 60 + +m[2] : null; };
/* Refresh the hint and the clock/money text in place (no re-render, so typed values stay). */
function timerUpd() {
  const pid = activePid(), rate = rateOf(pid);
  if (rate === null) moneyView = false;
  const h = $('#t_hint'); if (h) h.textContent = pid && rate === null ? 'no rate' : '';
  const c = $('#clock'); if (c) c.textContent = moneyView ? earned(rate) : !S.timer?.running ? '0:00' : clockStr(timerElapsed());
}
function timerTick() { clearInterval(render.tick); if (S.timer?.running) render.tick = setInterval(timerUpd, 1000); }
function timerModeHtml() {
  const run = !!S.timer?.running;   // a running timer always shows its clock and Stop, whatever the stored mode
  if (!run && localStorage.timeMode !== 'tracker') return `<div class="field" style="margin:0"><label for="t_date">Date</label><input type="date" id="t_date" value="${todayISO()}"></div>
    <div class="field" style="margin:0"><label for="t_start">Start</label><input type="time" id="t_start" data-change="timerTimes"></div>
    <div class="field" style="margin:0"><label for="t_end">Stop</label><input type="time" id="t_end" data-change="timerTimes"></div>
    <div class="field" style="margin:0"><label for="t_dur">Duration</label><input id="t_dur" placeholder="hh:mm" style="width:90px" data-change="timerDur"></div>
    <button class="primary" data-a="timerAdd">Add</button>`;
  return `<button type="button" class="clock" id="clock" data-a="timerMoney" aria-label="Show time or money earned">${run ? clockStr(timerElapsed()) : '0:00'}</button>
    <div class="small muted" id="t_hint"></div>
    ${run ? '<button class="stop big" data-a="timerStop">■ Stop</button>' : '<button class="primary big" data-a="timerStart">▶ Start</button>'}`;
}
function timerCard() {
  const t = S.timer, run = !!t?.running, tracker = localStorage.timeMode === 'tracker';
  const pid = run ? t.project_id : (localStorage.lastProj || ''), tid = run ? t.task_id : '';
  const modeBtn = (m, glyph, label) => `<button type="button" class="ghost" data-a="timeMode" data-mode="${m}" aria-label="${label}" title="${label}" aria-pressed="${tracker === (m === 'tracker')}">${glyph}</button>`;
  return `<div class="card"><div class="timer">
    <div class="field" style="flex:2;min-width:200px;margin:0"><label for="t_note">Description</label><input id="t_note" value="${esc(run ? t.note || '' : '')}" placeholder="What are you working on?"></div>
    <div class="field" style="margin:0"><label for="t_proj">Project</label><input id="t_proj" list="t_proj_list" value="${esc(labelById(pid))}" placeholder="Search projects" autocomplete="off" data-change="timerProj" ${run ? 'disabled' : ''}><datalist id="t_proj_list">${projectOptions(false).map(([, l]) => `<option value="${esc(l)}"></option>`).join('')}</datalist></div>
    <div class="field" style="margin:0"><label for="t_task">Task</label><select id="t_task" data-change="timerTask" ${run ? 'disabled' : ''}>${taskOptions(pid).map(([v, l]) => `<option value="${esc(v)}" ${v === tid ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
    <div id="t_mode_area" class="timer" style="flex:none">${timerModeHtml()}</div>
    <div class="row tight" style="flex:none">${modeBtn('manual', '✎', 'Manual')}${modeBtn('tracker', '⏱', 'Time tracker')}</div>
  </div></div>`;
}
A.timeMode = el => {
  localStorage.timeMode = el.dataset.mode;
  $('#t_mode_area').innerHTML = timerModeHtml();
  document.querySelectorAll('[data-a="timeMode"]').forEach(b => b.setAttribute('aria-pressed', String(b === el)));
  timerUpd(); timerTick();
};
A.timerMoney = () => { if (rateOf(activePid()) === null) return; moneyView = !moneyView; timerUpd(); };
A.timerTimes = () => { const s = toMin($('#t_start').value), e = toMin($('#t_end').value); $('#t_dur').value = s !== null && e !== null && e > s ? hm((e - s) / 60) : ''; };
A.timerDur = () => {
  const s = toMin($('#t_start').value), d = parseDur($('#t_dur').value);
  if (s === null) { toast('Enter a start time first', true); return; }
  if (!d || d <= 0 || isNaN(d)) { toast('Enter a duration like 1:30', true); return; }
  if (s + d >= 1440) { toast('An entry must end on the same day', true); return; }
  $('#t_end').value = hhmm(s + d); $('#t_dur').value = hm(d / 60);
};
A.timerAdd = timerOnce('add', async () => {
  const date = $('#t_date').value, s = toMin($('#t_start').value), e = toMin($('#t_end').value);
  if (!date) throw new Error('Pick a date');
  if (s === null || e === null) throw new Error('Enter start and stop times');
  if (e <= s) throw new Error('Stop must be after start');
  // no guard() wrapper here: a failed save throws to the click handler, which toasts it, and the form keeps the typed values
  await api.upsert('entries', { user_id: S.me.id, date, start: $('#t_start').value, end: $('#t_end').value, minutes: e - s, project_id: projIdOrThrow() || null, task_id: $('#t_task').value || null, note: $('#t_note').value, billable: true });
  toast('Saved');
  await loadAll(['entries']);
  render();
});
A.timerStop = timerOnce('timer', async () => { const r = await api('POST', 'timer/stop', { note: $('#t_note').value }); S.timer = { running: false }; moneyView = false; toast('Saved ' + hm((r.entry?.minutes || 0) / 60) + ' h'); await loadAll(['entries']); render(); });

/* ================= TIMESHEET ================= */
PAGES.time = async (week, uid) => {
  week = week || mondayOf(todayISO()); uid = scopeOf('time', 'write') === 'all' ? (uid || S.me.id) : S.me.id;
  await loadAll(['entries', 'absences']);
  const dash = await api('GET', 'dashboard' + (uid !== S.me.id ? '?user_id=' + uid : '')), ws = mondayOf(week);
  const prev = addDays(ws, -7), next = addDays(ws, 7), projects = by(S.d.projects);
  const days = [...Array(7)].map((_, i) => addDays(ws, i));
  const mine = S.d.entries.filter(e => e.user_id === uid);
  const total = days.reduce((a, d) => a + mine.filter(e => e.date === d).reduce((x, e) => x + e.minutes, 0), 0) / 60;
  const sel = scopeOf('time', 'write') === 'all' ? `<select style="width:auto" data-change="timeUser" data-week="${ws}">${S.d.users.filter(u => u.active !== false).map(u => `<option value="${u.id}" ${u.id === uid ? 'selected' : ''}>${esc(u.name)}</option>`).join('')}</select>` : '';
  return `<div class="head"><div><h1>Timesheet</h1><div class="muted">Week of ${fmtDate(ws)} · ${hm(total)} of ${hm(dash.target)} h</div></div>
    <div class="row tight" style="flex:none;align-items:center">${sel}<button href="#" data-a="goto" data-to="time/${prev}/${uid}">←</button><button data-a="goto" data-to="time/${mondayOf(todayISO())}/${uid}">This week</button><button data-a="goto" data-to="time/${next}/${uid}">→</button><button class="primary" data-a="addTime">+ Add time</button></div></div>
    ${days.map((d, i) => { const es = mine.filter(e => e.date === d), dd = dash.days[i], sum = es.reduce((a, e) => a + e.minutes, 0) / 60;
      return `<div class="card" style="padding:12px 16px"><div class="row" style="align-items:center"><div><b>${dayName(d)} ${fmtDate(d).slice(0, 5)}</b> ${dd.holiday ? `<span class="pill blue">${esc(dd.holiday)}</span>` : ''}${dd.absence ? `<span class="pill blue">${esc(dd.absence)}</span>` : ''}</div><div class="right muted">${sum ? hm(sum) : '–'}${dd.target ? ' / ' + hm(dd.target) : ''}</div></div>${es.length ? `<table>${es.map(e => entryRow(e, projects)).join('')}</table>` : ''}</div>`; }).join('')}`;
};
A.goto = el => { location.hash = '#/' + el.dataset.to; };
A.timeUser = el => { location.hash = `#/time/${el.dataset.week}/${el.value}`; };

/* ================= PLANNING (boss) ================= */
PAGES.plan = async (tab = 'forecast') => {
  const tabs = [['forecast', 'Overview'], ['vacation', 'Plan & vacation'], ['contracts', 'Contracts'], ['timeoff', 'Time off & holidays']];
  const head = `<div class="head"><div><h1>Planning</h1><div class="muted">Where is your time going – and what is still free?</div></div></div><div class="tabs">${tabs.map(([k, l]) => `<a href="#/plan/${k}" class="${k === tab ? 'on' : ''}">${l}</a>`).join('')}</div>`;
  if (tab === 'vacation') return head + await PAGES.planVacation();
  await loadAll(['contracts', 'absences', 'customers', 'users', 'offers', 'projects']);
  if (tab === 'contracts') return head + contractsView();
  if (tab === 'timeoff') return head + await timeoffView(true);
  const OV_WEEKS = [4, 12, 26, 52];
  const weeks = OV_WEEKS.includes(+lsGet('fcWeeks')) ? +lsGet('fcWeeks') : 12, uid = lsGet('fcUser');
  let pid = lsGet('fcProject');
  const query = () => `plan?weeks=${weeks}${uid ? '&user_id=' + encodeURIComponent(uid) : ''}${pid ? '&project_id=' + encodeURIComponent(pid) : ''}`;
  let pl;
  try { pl = await api('GET', query()); }
  catch (e) { if (e.status !== 404 || !pid) throw e; pid = ''; lsSet('fcProject', ''); pl = await api('GET', query()); }   // the remembered project is gone
  const filters = `<div class="row" style="margin-bottom:12px"><div class="auto"><label>Horizon</label><select data-change="fcWeeks">${OV_WEEKS.map(n => `<option value="${n}" ${n === weeks ? 'selected' : ''}>${n} weeks</option>`).join('')}</select></div>
    <div class="auto"><label>Who</label><select data-change="fcUser"><option value="">Whole team</option>${S.d.users.filter(u => u.active !== false).map(u => `<option value="${esc(u.id)}" ${u.id === uid ? 'selected' : ''}>${esc(u.name)}</option>`).join('')}</select></div>
    <div class="auto"><label>Project</label><select data-change="fcProject"><option value="">All projects</option>${S.d.projects.filter(p => p.status !== 'archived' || p.id === pid).map(p => `<option value="${esc(p.id)}" ${p.id === pid ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}<option value="unassigned" ${pid === 'unassigned' ? 'selected' : ''}>Unassigned</option></select></div></div>`;
  let rows = [];
  try { rows = await api('GET', 'allocations'); } catch (e) { rows = []; }
  const cnt = { project: {}, user: {} };
  for (const r of Array.isArray(rows) ? rows : []) {
    if (r.project_id) cnt.project[r.project_id] = (cnt.project[r.project_id] || 0) + 1;
    if (r.user_id) cnt.user[r.user_id] = (cnt.user[r.user_id] || 0) + 1;
  }
  const sk = Array.isArray(pl.skipped) ? pl.skipped : [], skipped = sk.length || (+pl.skipped || 0);
  return head + filters + ovProjects(pl.projects || [], cnt.project) + ovPeople(pl.people || [], cnt.user) + ovWeeks(pl.weeks || []) + ppWarnings(pl.warnings) +
    (isBoss() && skipped ? `<details class="small muted ov-skipped"><summary>${skipped} damaged record${skipped === 1 ? ' was' : 's were'} skipped</summary><ul>${sk.map(s => `<li>${esc(s.coll)} ${esc(s.id)}: ${esc(s.reason)}</li>`).join('')}</ul></details>` : '');
};
/* ---- planner overview: everything below only draws numbers computed by the server (GET /api/plan) ---- */
const OV = { open: {} };
const ppH = x => num(x, 1) + ' h';
const OV_STATUS = { ok: ['good', 'ok'], at_risk: ['warn', 'at risk'], short: ['bad', 'short'], over_budget: ['bad', 'over budget'] };
const ovPill = s => { const [c, l] = OV_STATUS[s] || ['', s || '']; return `<span class="pill ${c}">${esc(l)}</span>`; };
function ovItem(it) {
  const offer = it.kind === 'offer', writable = can('planning', 'write');
  const money = offer
    ? `net ${esc(eur(it.value))}${it.expected_net != null ? ` · expected ${esc(eur(it.expected_net))}` : ''}${it.win_probability != null ? ` · ${esc(num(it.win_probability, 0))} %` : ''}`
    : `value ${esc(eur(it.value))} · invoiced ${esc(eur(it.invoiced))} · open ${esc(eur(it.open))}`;
  const sugg = (it.suggestions || []).map(s => `<button type="button" class="primary sm" data-a="ovAssign" data-project="${esc(it._pid)}" data-user="${esc(s.user_id)}" data-from="${esc(s.from)}" data-to="${esc(s.to)}" data-hours="${esc(s.hours)}">Assign ${esc(s.name)} (${esc(num(s.hours, 1))} h)</button>`).join(' ');
  return `<tr class="ov-item" data-item="${esc(it.id)}"><td><span class="pill ${offer ? 'blue' : ''}">${offer ? 'offer' : 'contract'}</span> <b>${esc(it.title)}</b><div class="small muted">${esc(fmtDate(it.start))} – ${esc(fmtDate(it.end))}${it.type === 'weekly' ? ' · weekly' : ''}</div></td>
    <td class="num" title="Promised hours">${esc(ppH(it.promised))}</td><td class="num" title="Booked hours">${esc(ppH(it.booked))}</td><td class="num" title="Promised − booked">${esc(ppH(it.remaining))}</td><td class="num" title="Remaining hours covered by assigned time">${esc(ppH(it.covered))}</td>
    <td class="num ${it.shortfall > 0 ? 'pp-gap' : ''}" title="Remaining − covered">${esc(ppH(it.shortfall))}</td><td class="small">${money}</td></tr>
    <tr class="ov-expl"><td colspan="7"><div class="small ov-explanation">${esc(it.explanation || '')}</div>${writable && sugg ? `<div class="pp-remedy">${sugg}</div>` : ''}</td></tr>`;
}
function ovAssignBtn(n, attr, label) {   // the count is always shown; the button only with planning write
  const text = `Assignments (${n})`;
  return can('planning', 'write') ? `<button type="button" class="ghost sm" data-a="allocations" ${attr} aria-label="${esc(text)} of ${esc(label)}">${text}</button>` : `<span class="small muted">${text}</span>`;
}
function ovPeople(list, counts) {
  if (!list.length) return '';
  return `<div class="card"><h2>People</h2><table class="ov-people"><tr><th>Person</th><th></th></tr>${list.map(u => `<tr data-person-row="${esc(u.user_id)}"><td><b>${esc(u.name)}</b></td><td class="num">${ovAssignBtn(counts[u.user_id] || 0, `data-user="${esc(u.user_id)}"`, u.name)}</td></tr>`).join('')}</table></div>`;
}
function ovProjects(list, counts = {}) {
  if (!list.length) return '';
  const sorted = [...list].sort((a, b) => (a.kind === 'customer' ? 0 : 1) - (b.kind === 'customer' ? 0 : 1));
  const rows = sorted.map(p => {
    const internal = p.kind === 'internal', open = p.project_id in OV.open ? OV.open[p.project_id] : (!internal && p.status !== 'ok');
    const items = (p.items || []).map(it => ovItem({ ...it, _pid: p.project_id })).join('');
    const sum = internal
      ? `<span title="Assigned hours in the period">assigned ${esc(ppH(p.assigned))}</span> · <span title="Booked hours">booked ${esc(ppH(p.booked))}</span> · <span title="Budget hours">budget ${esc(ppH(p.budget_hours))}</span>`
      : `<span title="Remaining contract hours">remaining ${esc(ppH(p.remaining_contracts))}</span> · <span class="${p.shortfall_contracts > 0 ? 'pp-gap' : ''}" title="Contract hours not covered by assigned time">shortfall ${esc(ppH(p.shortfall_contracts))}</span>${p.shortfall_worst_case > p.shortfall_contracts ? ` · <span title="Including all offers">worst case ${esc(ppH(p.shortfall_worst_case))}</span>` : ''}`;
    return `<tr class="ov-project" data-project-row="${esc(p.project_id)}"><td><button type="button" class="ghost sm pp-toggle" data-a="ovToggle" data-project="${esc(p.project_id)}" aria-expanded="${open}" aria-label="Items of ${esc(p.name)}">${open ? '▾' : '▸'}</button> <b>${esc(p.name)}</b> ${ovPill(p.status)}${internal ? ' <span class="small muted">internal</span>' : ''}</td><td class="small" colspan="5">${sum}</td><td class="small">${internal ? '' : `<span title="Contract value of the project">${esc(eur(p.contract_value))}</span> `}${p.project_id && p.project_id !== 'unassigned' ? ovAssignBtn(counts[p.project_id] || 0, `data-project="${esc(p.project_id)}"`, p.name) : ''}</td></tr>${open ? items : ''}`;
  }).join('');
  return `<div class="card"><h2>Projects</h2><table class="ov-table"><tr><th>Project / item</th><th class="num">Promised</th><th class="num">Booked</th><th class="num">Remaining</th><th class="num">Covered</th><th class="num">Shortfall</th><th>Value</th></tr>${rows}</table></div>`;
}
function ovWeeks(W) {
  if (!W.length) return '';
  const pct = v => v == null ? '–' : num(v, 0) + ' %';
  return `<div class="card"><h2>Week by week</h2><table class="ov-weeks"><tr><th>Week of</th><th class="num" title="Workday hours − holidays − time off − busy">Capacity</th><th class="num">Customer projects</th><th class="num">Internal</th><th class="num" title="Per day the capacity not assigned, summed">Unassigned</th><th class="num" title="Per day the assigned hours above capacity, summed">Over-assigned</th><th class="num" title="Customer projects / capacity">Billable share</th><th class="num">Subcontractors</th></tr>
    ${W.map(w => `<tr class="${w.over_assigned > 0 ? 'pp-over' : ''}" data-week="${esc(w.week)}"><td class="nowrap">${esc(fmtDate(w.week))}<div class="small muted ov-calendar">holiday ${esc(ppH(w.holiday_hours))} · vacation ${esc(ppH(w.vacation_hours))} · absence ${esc(ppH(w.absence_hours))} · busy ${esc(ppH(w.busy_hours))}</div></td>
      <td class="num" data-col="capacity">${esc(num(w.capacity, 1))}</td><td class="num" data-col="customer">${esc(num(w.assigned_customer, 1))}</td><td class="num" data-col="internal">${esc(num(w.assigned_internal, 1))}</td><td class="num" data-col="unassigned">${esc(num(w.unassigned, 1))}</td>
      <td class="num ${w.over_assigned > 0 ? 'pp-gap' : ''}" data-col="over">${esc(num(w.over_assigned, 1))}</td><td class="num" data-col="share">${esc(pct(w.billable_share))}</td><td class="num" data-col="subs">${esc(num(w.subcontractor_hours, 1))}</td></tr>`).join('')}</table></div>`;
}
A.ovToggle = el => { OV.open[el.dataset.project] = el.getAttribute('aria-expanded') !== 'true'; render(); };
A.ovAssign = el => {
  const d = el.dataset;
  window.openAllocations?.({ projectId: d.project, userId: d.user, start: d.from, end: d.to, total_hours: +d.hours });
};
/* a remedy line for a planning warning about a shortfall or an over-allocation (empty for other codes) */
function ppRemedy(w) {
  const d = w.details || {}, writable = can('planning', 'write');
  const open = attrs => writable ? ` <button type="button" class="ghost sm" data-a="allocations" ${attrs}>Open allocations</button>` : '';
  if (w.code === 'allocation.employee_over_allocated' && d.user_id) return `<div class="small pp-remedy">Reduce or move allocated hours.${open(`data-user="${esc(d.user_id)}"`)}</div>`;
  if (w.code === 'capacity.gap' && d.project_id) return `<div class="small pp-remedy">Allocate more capacity to this project.${open(`data-project="${esc(d.project_id)}"`)}</div>`;
  return '';
}
function ppWarnings(list) {
  if (!list || !list.length) return '';
  const shown = list.slice(0, 20);
  return `<div class="card pp-warnings"><h2>Planning warnings (${list.length})</h2><ul class="small">${shown.map(w => `<li data-code="${esc(w.code)}"><span class="pill ${w.severity === 'warning' ? 'warn' : ''}">${esc(w.severity)}</span> ${esc(w.message)}${ppRemedy(w)}</li>`).join('')}</ul>${list.length > shown.length ? `<div class="small muted">and ${list.length - shown.length} more</div>` : ''}</div>`;
}
A.fcProject = el => { lsSet('fcProject', el.value); render(); };
A.fcWeeks = el => { localStorage.fcWeeks = el.value; render(); };
A.fcUser = el => { localStorage.fcUser = el.value; render(); };

/* shared by the Planning > Contracts tab and the project page */
const contractPeriod = c => `${fmtDate(c.start)} – ${fmtDate(c.end)}`;
const contractHours = c => c.hours_per_week ? c.hours_per_week + ' h/week' : (c.total_hours || '') + ' h total';
const contractPill = c => `<span class="pill ${c.status === 'done' ? '' : 'good'}">${esc(c.status || 'confirmed')}</span>`;
function contractsView() {
  const cs = S.d.contracts;
  return `<div class="hint">Enter confirmed work as <b>hours per week</b> or a <b>total hour budget</b> between two dates. Open offers (with a plan) show up in the forecast automatically; when you accept an offer, it becomes a contract.</div>
  <div class="card"><div class="row" style="align-items:center"><h2 style="margin:0">Contracts</h2><div class="right auto"><button class="primary" data-a="editContract">+ New contract</button></div></div>
  ${cs.length ? `<table><tr><th>Contract</th><th>Customer</th><th>Period</th><th class="num">Hours</th><th class="num">Rate</th><th>Status</th></tr>${cs.sort((a, b) => a.start < b.start ? -1 : 1).map(c => `<tr class="click" data-a="editContract" data-id="${c.id}"><td><b>${esc(c.title)}</b></td><td>${esc(nameOf('customers', c.customer_id))}</td><td class="nowrap">${contractPeriod(c)}</td><td class="num">${contractHours(c)}</td><td class="num">${c.rate ? eur(c.rate) : ''}</td><td>${contractPill(c)}</td></tr>`).join('')}</table>` : '<div class="empty">No contracts yet.</div>'}</div>`;
}
A.editContract = el => {
  const proj = el.dataset.proj ? S.d.projects.find(x => x.id === el.dataset.proj) : null;   // "+ New contract" on a project page
  const c = el.dataset.id ? S.d.contracts.find(x => x.id === el.dataset.id) : { status: 'confirmed', start: todayISO(), ...(proj ? { project_id: proj.id, customer_id: proj.customer_id || '' } : {}) };
  openForm({
    title: c.id ? 'Edit contract' : 'New contract', vals: c,
    fields: [{ k: 'title', l: 'Title' }, { k: 'customer_id', l: 'Customer', t: 'select', o: [['', '—'], ...S.d.customers.map(x => [x.id, x.name])] },
      { k: 'project_id', l: 'Project', t: 'select', o: projectOptions(c.id && !c.project_id, undefined, c.project_id || null, true) },
      { k: 'start', l: 'Start', t: 'date', row: 1 }, { k: 'end', l: 'End', t: 'date', row: 1 },
      { k: 'hours_per_week', l: 'Hours per week', t: 'number', row: 2, h: 'Either this… (entering one clears the other)' }, { k: 'total_hours', l: 'Total hours', t: 'number', row: 2, h: '…or this (spread over the period)' }, { k: 'rate', l: 'Hourly rate (€)', t: 'number', row: 2 },
      { k: 'status', l: 'Status', t: 'select', o: ['confirmed', 'done'] }],
    save: async v => { if (!v.title || !v.start || !v.end) throw new Error('Title, start and end are required'); if (!v.project_id) throw new Error('Choose a project'); if (!(v.hours_per_week > 0) && !(v.total_hours > 0)) throw new Error('Enter hours per week or total hours'); if (v.hours_per_week > 0) v.total_hours = null; else v.hours_per_week = null; await save('contracts', v); render(); },
    del: c.id ? async () => { await api('DELETE', 'contracts/' + c.id); await loadAll(['contracts']); render(); } : null
  });
  const hpw = $('#f_hours_per_week'), tot = $('#f_total_hours');   // exclusive: entering one clears the other
  if (hpw && tot) { hpw.addEventListener('input', () => { if (hpw.value !== '') tot.value = ''; }); tot.addEventListener('input', () => { if (tot.value !== '') hpw.value = ''; }); }
};

/* ---- time off ---- */
const ABS_KINDS = [['vacation', 'Vacation'], ['holiday', 'Personal holiday / day off'], ['sick', 'Sick'], ['other', 'Other']];
async function timeoffView(boss) {
  await loadAll(['absences', 'users']);
  const y = new Date().getFullYear(), hol = await api('GET', 'holidays?year=' + y), hol2 = await api('GET', 'holidays?year=' + (y + 1));
  const list = S.d.absences.filter(a => boss || a.user_id === S.me.id).sort((a, b) => a.start < b.start ? -1 : 1);
  return `<div class="card"><div class="row" style="align-items:center"><h2 style="margin:0">Time off</h2><div class="right auto"><button class="primary" data-a="editAbsence">+ Add time off</button></div></div>
  ${list.length ? `<table>${list.map(a => `<tr class="click" data-a="editAbsence" data-id="${a.id}"><td>${boss ? `<b>${esc(nameOf('users', a.user_id))}</b>` : ''}</td><td class="nowrap">${fmtDate(a.start)}${a.end && a.end !== a.start ? ' – ' + fmtDate(a.end) : ''}</td><td><span class="pill blue">${esc(ABS_KINDS.find(k => k[0] === a.kind)?.[1] || a.kind)}</span></td><td class="muted">${esc(a.note || '')}</td></tr>`).join('')}</table>` : '<div class="empty">No time off planned. Add vacation or personal days – they reduce your capacity in the forecast.</div>'}</div>
  <div class="card"><h2>Public holidays ${S.settings.state ? '(' + esc(S.states?.[S.settings.state] || S.settings.state) + ')' : '(nationwide – pick your state in Settings)'}</h2><div class="small">${Object.entries({ ...hol, ...hol2 }).sort().map(([d, n]) => `<span class="nowrap" style="display:inline-block;width:300px"><span class="muted">${dayName(d)} ${fmtDate(d)}</span> ${esc(n)}</span>`).join('')}</div></div>`;
}
PAGES.timeoff = async () => `<div class="head"><div><h1>Time off</h1><div class="muted">Plan holidays and days off.</div></div></div>` + await timeoffView(false);
A.editAbsence = el => {
  const a = el.dataset.id ? S.d.absences.find(x => x.id === el.dataset.id) : { kind: 'vacation', start: todayISO(), end: todayISO(), user_id: S.me.id };
  openForm({
    title: a.id ? 'Edit time off' : 'Add time off', vals: a,
    fields: [...(scopeOf('absences', 'write') === 'all' ? [{ k: 'user_id', l: 'Who', t: 'select', o: S.d.users.filter(u => u.active !== false).map(u => [u.id, u.name]) }] : []), { k: 'kind', l: 'Type', t: 'select', o: ABS_KINDS },
      { k: 'start', l: 'From', t: 'date', row: 1 }, { k: 'end', l: 'To (inclusive)', t: 'date', row: 1 }, { k: 'note', l: 'Note' }],
    save: async v => { if (!v.end || v.end < v.start) v.end = v.start; await save('absences', v); render(); },
    del: a.id ? async () => { await api('DELETE', 'absences/' + a.id); await loadAll(['absences']); render(); } : null
  });
};
