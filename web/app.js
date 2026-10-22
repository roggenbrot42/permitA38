/* permitA38 – core: state, helpers, forms, router, daily-work pages. Vanilla JS, no build step. */
const S = { me: null, settings: {}, timer: null, d: {} };
const A = {};      // click actions:  <button data-a="name" data-id="..">
const PAGES = {};  // route -> async (args) => html
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
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
const by = (arr, k = 'id') => Object.fromEntries(arr.map(x => [x[k], x]));
const nameOf = (coll, id) => (S.d[coll] || []).find(x => x.id === id)?.name || '';

api.onUnauthorized = () => { if (S.me) S.expired = true; S.me = null; render(); };
function toast(msg, err) { const t = $('#toast'); t.textContent = msg; t.className = 'on' + (err ? ' err' : ''); clearTimeout(toast.t); toast.t = setTimeout(() => t.className = '', err ? 4000 : 1800); }
const guard = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };

const BOSS_COLLS = ['customers', 'projects', 'tasks', 'entries', 'contracts', 'absences', 'offers', 'invoices', 'expenses', 'users'];
const MEMBER_COLLS = ['projects', 'tasks', 'entries', 'absences', 'users'];
async function loadAll(only) {
  const colls = only || (isBoss() ? BOSS_COLLS : MEMBER_COLLS);
  const res = await Promise.all(colls.map(c => api('GET', c)));
  colls.forEach((c, i) => S.d[c] = res[i]);
}
async function bootstrap() {
  const b = await api('GET', 'bootstrap');
  Object.assign(S, { me: b.me, settings: b.settings, timer: b.timer, states: b.states, conflicts: b.conflicts, templates: b.templates, data_dir: b.data_dir });
  await loadAll();
}

/* ---------- modal + forms ---------- */
function closeModal() { $('#modal-root').innerHTML = ''; }
function modal(html, wide) {
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
  else inner = `<input id="${id}" type="${f.t || 'text'}" value="${esc(val)}" placeholder="${esc(f.ph || '')}" ${f.step ? `step="${f.step}"` : f.t === 'number' ? 'step="any"' : ''}>`;
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
  modal(`<h2>${esc(title)}</h2>${note}${formHTML(fields, vals)}${extra}<div class="actions">${del ? '<button class="danger" id="m-del" style="margin-right:auto">Delete</button>' : ''}<button id="m-cancel">Cancel</button><button class="primary" id="m-save">${esc(saveLabel)}</button></div>`, wide);
  $('#m-cancel').onclick = closeModal;
  const doSave = guard(async () => { await save(readForm(fields, vals)); closeModal(); });
  $('#m-save').onclick = doSave;
  $('#modal-root').onkeydown = e => { if (e.key === 'Enter' && e.target.tagName === 'INPUT') doSave(); if (e.key === 'Escape') closeModal(); };
  if (del) $('#m-del').onclick = guard(async () => { if (confirm('Delete this? This cannot be undone.')) { await del(vals); closeModal(); } });
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
const projectOptions = (withNone = true) => [...(withNone ? [['', '— no project —']] : []), ...(S.d.projects || []).filter(p => p.status !== 'archived').map(p => [p.id, p.name + (p.personal ? ' (personal)' : '')])];
const taskOptions = pid => [['', '— no task —'], ...(S.d.tasks || []).filter(t => t.project_id === pid).map(t => [t.id, t.name])];

function entryForm(e = {}, after) {
  const vals = { date: todayISO(), billable: true, ...e, dur: e.minutes ? hm(e.minutes / 60) : '' };
  const users = isBoss() ? [{ k: 'user_id', l: 'Who', t: 'select', o: (S.d.users || []).filter(u => u.active !== false).map(u => [u.id, u.name]), def: S.me.id }] : [];
  const fields = [...users,
    { k: 'date', l: 'Date', t: 'date', row: 1 }, { k: 'start', l: 'From', t: 'time', row: 1 }, { k: 'end', l: 'To', t: 'time', row: 1 }, { k: 'dur', l: 'or duration (1:30, 1.5, 90m)', row: 1 },
    { k: 'project_id', l: 'Project', t: 'select', o: projectOptions() },
    { k: 'task_id', l: 'Task', t: 'select', o: taskOptions(vals.project_id) },
    { k: 'note', l: 'What did you do?', t: 'area', rows: 2 },
    { k: 'billable', l: 'Billable', t: 'check' }];
  openForm({
    title: e.id ? 'Edit time' : 'Add time', fields, vals,
    save: async v => {
      let min = parseDur(v.dur);
      if ((min === null || isNaN(min)) && v.start && v.end) { const [a, b] = [v.start, v.end].map(x => +x.slice(0, 2) * 60 + +x.slice(3)); min = b - a; if (min <= 0) min += 1440; }
      if (min === null || isNaN(min) || min <= 0) throw new Error('Enter from/to times or a duration');
      v.minutes = min; delete v.dur;
      await save('entries', { ...v, project_id: v.project_id || null, task_id: v.task_id || null });
      after && after();
    },
    del: e.id ? async () => { await api('DELETE', 'entries/' + e.id); await loadAll(['entries']); after && after(); } : null
  });
  $('#f_project_id').onchange = () => { $('#f_task_id').innerHTML = taskOptions($('#f_project_id').value).map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join(''); };
  if (vals.task_id) $('#f_task_id').value = vals.task_id;
}

/* ---------- layout + router ---------- */
const NAV_BOSS = [['today', '⏱', 'Today'], ['time', '🗓', 'Timesheet'], ['plan', '📈', 'Planning'], ['customers', '👥', 'Customers'], ['projects', '📁', 'Projects'], ['sales', '🧾', 'Offers & Invoices'], ['expenses', '💳', 'Expenses'], ['money', '💶', 'Money & Taxes'], ['team', '🧑‍🤝‍🧑', 'Team'], ['settings', '⚙️', 'Settings']];
const NAV_MEMBER = [['today', '⏱', 'Today'], ['time', '🗓', 'Timesheet'], ['timeoff', '🏖', 'Time off']];
function route() { const [p, ...a] = (location.hash.replace(/^#\/?/, '') || 'today').split('/'); return [p, a]; }
const MEMBER_PAGES = ['today', 'time', 'timeoff'];
function denyMember() { toast('Not available for your role'); location.hash = '#/today'; }
async function render() {
  const app = $('#app');
  clearInterval(render.tick);
  if (!S.me) return renderLogin(app);
  const [p, args] = route();
  if (p !== 'doc' && typeof DOC !== 'undefined') DOC = null;
  const nav = isBoss() ? NAV_BOSS : NAV_MEMBER;
  const active = { customer: 'customers', project: 'projects', doc: 'sales' }[p] || p;
  if (!isBoss() && !MEMBER_PAGES.includes(p)) return denyMember();
  let html;
  try { html = await (PAGES[p] || PAGES.today)(...args); } catch (e) {
    if (!S.me) return;
    if (e.status === 403 && !isBoss()) return denyMember();
    if (e.status) { toast(e.message, true); html = `<div class="card">Something went wrong: ${esc(e.message)}</div>`; }  // API error: shown, not a crash
    else { html = `<div class="card">Something went wrong: ${esc(e.message)}</div>`; console.error(e); }
  }
  if (!S.me) return;
  app.innerHTML = `<div class="shell"><nav class="side"><div class="brand"><i></i>permitA38</div>
    ${nav.map(([k, ic, l]) => `<a class="nav ${k === active ? 'on' : ''}" href="#/${k}"><span>${ic}</span>${l}</a>`).join('')}
    <div class="spacer"></div><div class="me"><b>${esc(S.me.name)}</b><br>${S.me.role === 'boss' ? 'Boss' : 'Member'}</div>
    <a class="nav" data-a="changePassword">🔑 Change password</a><a class="nav" data-a="logout">↩ Sign out</a></nav><main>${S.conflicts?.length ? `<div class="banner">⚠ ${S.conflicts.length} sync conflict file(s) found in your data folder (e.g. <code>${esc(S.conflicts[0])}</code>). Compare and delete the copy you do not need.</div>` : ''}${html}</main></div>`;
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
      <div class="field"><label for="s_name">Your name</label><input id="s_name" autocomplete="name" autofocus></div>
      <div class="row"><div class="field"><label for="s_user">Username</label><input id="s_user" autocomplete="username" autocapitalize="none" spellcheck="false"></div><div class="field"><label for="s_h">Weekly hours</label><input id="s_h" type="number" value="40" min="1" max="80"></div></div>
      ${pwField('s_pw', 'Password (min. 8 characters)', 'new-password')}${pwField('s_pw2', 'Repeat password', 'new-password')}
      <div class="field"><label for="s_co">Business name (optional)</label><input id="s_co" autocomplete="organization"></div>
      <div class="banner" id="auth-err" role="alert" hidden></div><button class="primary" id="s_go" type="submit" style="width:100%;justify-content:center">Create account</button>${foot}</form></div>`;
    $('#setup-form').onsubmit = async e => {
      e.preventDefault(); const pw = $('#s_pw').value; const btn = $('#s_go');
      if (!$('#s_name').value.trim() || !$('#s_user').value.trim()) return authError('Please enter your name and a username.');
      if (pw.length < 8) return authError('The password must be at least 8 characters long.');
      if (pw !== $('#s_pw2').value) return authError('The passwords do not match.');
      authError(''); authBusy(btn, true);
      try {
        await api.post('auth/setup', { name: $('#s_name').value.trim(), username: $('#s_user').value.trim(), password: pw, company_name: $('#s_co').value.trim(), weekly_hours: parseFloat($('#s_h').value) || 40 }, { noAuthRedirect: true });
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
    try { const r = await api.post('auth/login', { username: u, password: p }, { noAuthRedirect: true }); await enterApp(r.token); }
    catch (err) { authError(err.message); authBusy(btn, false, 'Sign in'); $('#l_pw').select(); }
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
  const t = S.timer, run = t?.running;
  const projects = by(S.d.projects);
  const hello = new Date().getHours() < 12 ? 'Good morning' : new Date().getHours() < 18 ? 'Hello' : 'Good evening';
  let extras = '';
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
    const st = S.settings, steps = [[!!st.company_name && !!(st.tax_number || st.vat_id), 'Fill in your business details (address, tax number, bank)', '#/settings'], [S.d.customers.length > 0, 'Add your first customer', '#/customers'], [S.d.projects.length > 0, 'Create a project (customer work or personal)', '#/projects'], [entries.length > 0 || S.d.entries.length > 0, 'Track your first hour with the Start button above', '#/today']];
    extras = `<div class="card" style="border-color:var(--accent)"><h2>👋 Getting started</h2><div class="muted small" style="margin-bottom:8px">Four steps to your first tracked hour. Full guide: <code>docs/getting-started.md</code></div>${steps.map(([done, t, h]) => `<div style="padding:4px 0">${done ? '✅' : '⬜'} <a href="${h}">${t}</a></div>`).join('')}</div>` + extras;
  }
  if (run) render.after = () => { render.tick = setInterval(() => { const c = $('#clock'); if (c) c.textContent = clockStr(timerElapsed()); }, 1000); };
  return `<div class="head"><div><h1>${hello}, ${esc(S.me.name.split(' ')[0])}</h1><div class="muted">${new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })}</div></div><button data-a="addTime">+ Add time manually</button></div>
  <div class="card"><div class="timer">${run ? `<div class="clock" id="clock">${clockStr(timerElapsed())}</div>
      <div style="flex:1;min-width:200px"><div><b>${esc(projects[t.project_id]?.name || 'No project')}</b></div><input id="t_note" value="${esc(t.note || '')}" placeholder="What are you working on?" style="margin-top:6px"></div>
      <button class="stop big" data-a="timerStop">■ Stop</button>`
      : `<div style="flex:1;min-width:200px"><div class="row"><div><label>Project</label><select id="t_proj" data-change="timerProj">${projectOptions().map(([v, l]) => `<option value="${v}" ${v === localStorage.lastProj ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
      <div><label>Task</label><select id="t_task">${taskOptions(localStorage.lastProj || '').map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join('')}</select></div></div>
      <input id="t_note" placeholder="What are you working on? (optional)" style="margin-top:8px"></div><button class="primary big" data-a="timerStart">▶ Start</button>`}</div></div>
  <div class="card"><div class="row" style="align-items:center"><div><h2 style="margin:0">This week</h2><div class="muted">${hm(dash.worked)} of ${hm(dash.target)} h</div></div>
    <div class="right" style="flex:2"><div style="font-size:26px;font-weight:700" class="${dash.remaining ? '' : 'free-good'}">${dash.remaining ? hm(dash.remaining) + ' h to go' : '🎉 Weekly goal reached' + (dash.overtime ? ' (+' + hm(dash.overtime) + ')' : '')}</div>
    <div class="muted small">${dash.remaining && dash.remaining_days ? `≈ ${hm(dash.per_day_needed)} h per remaining working day (${dash.remaining_days} day${dash.remaining_days > 1 ? 's' : ''} incl. today)` : dash.remaining ? 'No working days left this week' : ''}</div></div></div>
    <div class="bar ${pct >= 100 ? 'good' : ''}" style="margin:12px 0"><div style="width:${pct}%"></div></div>
    <div class="days">${dash.days.map(d => { const mx = Math.max(8, ...dash.days.map(x => Math.max(x.target, x.worked))); return `<div class="day ${d.date === now ? 'today' : ''} ${!d.target ? 'off' : ''}" title="${esc(d.holiday || d.absence || '')}"><div class="col"><div style="height:${d.worked / mx * 100}%"></div>${d.target ? `<span style="bottom:${d.target / mx * 100}%"></span>` : ''}</div><b>${dayName(d.date)}</b><br>${d.worked ? hm(d.worked) : d.holiday ? '🎌' : d.absence ? '🏖' : '–'}</div>`; }).join('')}</div></div>
  ${extras}
  <div class="card"><h2>Today · ${hm(dash.today_worked)} h</h2>${entries.length ? `<table>${entries.map(e => entryRow(e, projects)).join('')}</table>` : '<div class="empty">Nothing tracked yet today. Hit <b>Start</b> when you begin working.</div>'}</div>`;
};
const docTotal = d => (d.lines || []).reduce((a, l) => a + (l.amount || 0) * (1 + (S.settings.kleinunternehmer ? 0 : (l.vat ?? d.vat_rate ?? S.settings.vat_rate ?? 19) / 100)), 0);
function entryRow(e, projects) {
  const p = projects[e.project_id];
  return `<tr class="click" data-a="editEntry" data-id="${e.id}"><td>${p ? `<span class="dot" style="background:${esc(p.color || '#888')}"></span><b>${esc(p.name)}</b>` : '<span class="muted">no project</span>'}${e.task_id ? ' · ' + esc(nameOf('tasks', e.task_id)) : ''}<div class="small muted">${esc(e.note || '')}</div></td>
    <td class="small muted nowrap">${e.start ? esc(e.start) + '–' + esc(e.end || '') : ''}</td><td class="num"><b>${hm(e.minutes / 60)}</b></td><td>${e.invoice_id ? '<span class="pill good">billed</span>' : e.billable === false ? '<span class="pill">non-billable</span>' : ''}</td></tr>`;
}
A.addTime = () => entryForm({}, render);
A.editEntry = el => entryForm(S.d.entries.find(e => e.id === el.dataset.id), render);
A.timerProj = el => { localStorage.lastProj = el.value; $('#t_task').innerHTML = taskOptions(el.value).map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join(''); };
A.timerStart = async () => { S.timer = await api('POST', 'timer/start', { project_id: $('#t_proj').value || null, task_id: $('#t_task').value || null, note: $('#t_note').value }); render(); };
A.timerStop = async () => { const r = await api('POST', 'timer/stop', { note: $('#t_note').value }); S.timer = { running: false }; toast('Saved ' + hm((r.entry?.minutes || 0) / 60) + ' h'); await loadAll(['entries']); render(); };

/* ================= TIMESHEET ================= */
PAGES.time = async (week, uid) => {
  week = week || mondayOf(todayISO()); uid = isBoss() ? (uid || S.me.id) : S.me.id;
  await loadAll(['entries', 'absences']);
  const dash = await api('GET', 'dashboard' + (uid !== S.me.id ? '?user_id=' + uid : '')), ws = mondayOf(week);
  const prev = addDays(ws, -7), next = addDays(ws, 7), projects = by(S.d.projects);
  const days = [...Array(7)].map((_, i) => addDays(ws, i));
  const mine = S.d.entries.filter(e => e.user_id === uid);
  const total = days.reduce((a, d) => a + mine.filter(e => e.date === d).reduce((x, e) => x + e.minutes, 0), 0) / 60;
  const sel = isBoss() ? `<select style="width:auto" data-change="timeUser" data-week="${ws}">${S.d.users.filter(u => u.active !== false).map(u => `<option value="${u.id}" ${u.id === uid ? 'selected' : ''}>${esc(u.name)}</option>`).join('')}</select>` : '';
  return `<div class="head"><div><h1>Timesheet</h1><div class="muted">Week of ${fmtDate(ws)} · ${hm(total)} of ${hm(dash.target)} h</div></div>
    <div class="row tight" style="flex:none;align-items:center">${sel}<button href="#" data-a="goto" data-to="time/${prev}/${uid}">←</button><button data-a="goto" data-to="time/${mondayOf(todayISO())}/${uid}">This week</button><button data-a="goto" data-to="time/${next}/${uid}">→</button><button class="primary" data-a="addTime">+ Add time</button></div></div>
    ${days.map((d, i) => { const es = mine.filter(e => e.date === d), dd = dash.days[i], sum = es.reduce((a, e) => a + e.minutes, 0) / 60;
      return `<div class="card" style="padding:12px 16px"><div class="row" style="align-items:center"><div><b>${dayName(d)} ${fmtDate(d).slice(0, 5)}</b> ${dd.holiday ? `<span class="pill blue">${esc(dd.holiday)}</span>` : ''}${dd.absence ? `<span class="pill blue">${esc(dd.absence)}</span>` : ''}</div><div class="right muted">${sum ? hm(sum) : '–'}${dd.target ? ' / ' + hm(dd.target) : ''}</div></div>${es.length ? `<table>${es.map(e => entryRow(e, projects)).join('')}</table>` : ''}</div>`; }).join('')}`;
};
A.goto = el => { location.hash = '#/' + el.dataset.to; };
A.timeUser = el => { location.hash = `#/time/${el.dataset.week}/${el.value}`; };

/* ================= PLANNING (boss) ================= */
PAGES.plan = async (tab = 'forecast') => {
  const tabs = [['forecast', 'Forecast'], ['contracts', 'Contracts'], ['timeoff', 'Time off & holidays']];
  const head = `<div class="head"><div><h1>Planning</h1><div class="muted">Where is your time going – and what is still free?</div></div></div><div class="tabs">${tabs.map(([k, l]) => `<a href="#/plan/${k}" class="${k === tab ? 'on' : ''}">${l}</a>`).join('')}</div>`;
  await loadAll(['contracts', 'absences', 'customers', 'users', 'offers']);
  if (tab === 'contracts') return head + contractsView();
  if (tab === 'timeoff') return head + await timeoffView(true);
  const weeks = +(localStorage.fcWeeks || 26), uid = localStorage.fcUser || '';
  const fc = await api('GET', `forecast?weeks=${weeks}&user_id=${uid}`);
  const W = fc.weeks, mx = Math.max(10, ...W.map(w => Math.max(w.capacity, w.confirmed + w.tentative)));
  const bw = 900 / W.length, H = 200, y = v => H - v / mx * H;
  const chart = `<svg class="chart" viewBox="0 0 940 ${H + 30}"><g transform="translate(36,6)">
    ${[0, .5, 1].map(f => `<line x1="0" x2="900" y1="${y(mx * f)}" y2="${y(mx * f)}" stroke="var(--line)"/><text x="-6" y="${y(mx * f) + 4}" text-anchor="end">${Math.round(mx * f)}h</text>`).join('')}
    ${W.map((w, i) => `<g><title>${fmtDate(w.date || w.week)}: ${w.confirmed}h booked, ${w.tentative}h offers, capacity ${w.capacity}h</title>
      <rect x="${i * bw + 2}" y="${y(w.confirmed)}" width="${bw - 4}" height="${H - y(w.confirmed)}" fill="var(--accent)" rx="2"/>
      <rect x="${i * bw + 2}" y="${y(w.confirmed + w.tentative)}" width="${bw - 4}" height="${y(w.confirmed) - y(w.confirmed + w.tentative)}" fill="var(--accent)" opacity=".35" rx="2"/>
      <line x1="${i * bw}" x2="${(i + 1) * bw}" y1="${y(w.capacity)}" y2="${y(w.capacity)}" stroke="var(--good)" stroke-width="3"/>
      ${i % Math.ceil(W.length / 12) === 0 ? `<text x="${i * bw + bw / 2}" y="${H + 16}" text-anchor="middle">${fmtDate(w.week).slice(0, 5)}</text>` : ''}</g>`).join('')}</g></svg>`;
  const totals = W.reduce((a, w) => ({ cap: a.cap + w.capacity, conf: a.conf + w.confirmed, ten: a.ten + w.tentative, rev: a.rev + w.revenue, trev: a.trev + w.tentative_revenue }), { cap: 0, conf: 0, ten: 0, rev: 0, trev: 0 });
  return head + `<div class="row" style="margin-bottom:12px"><div class="auto"><label>Horizon</label><select data-change="fcWeeks">${[13, 26, 52].map(n => `<option value="${n}" ${n === weeks ? 'selected' : ''}>${n} weeks</option>`).join('')}</select></div>
    <div class="auto"><label>Who</label><select data-change="fcUser"><option value="">Whole team</option>${S.d.users.filter(u => u.active !== false).map(u => `<option value="${u.id}" ${u.id === uid ? 'selected' : ''}>${esc(u.name)}</option>`).join('')}</select></div></div>
    <div class="grid g4"><div class="card kpi"><div class="l">Capacity</div><div class="v">${num(totals.cap, 0)} h</div></div><div class="card kpi"><div class="l">Booked (contracts)</div><div class="v">${num(totals.conf, 0)} h</div></div>
      <div class="card kpi ${totals.cap - totals.conf > 0 ? 'good' : 'bad'}"><div class="l">Free capacity</div><div class="v">${num(totals.cap - totals.conf, 0)} h</div></div><div class="card kpi"><div class="l">Expected revenue</div><div class="v">${eur(totals.rev)}</div><span class="small muted">+ ${eur(totals.trev)} if open offers are won</span></div></div>
    <div class="card"><h2>Weekly load</h2>${chart}<div class="small muted"><span class="dot" style="background:var(--accent)"></span>booked <span class="dot" style="background:var(--accent);opacity:.35;margin-left:10px"></span>open offers <span class="dot" style="background:var(--good);margin-left:10px"></span>capacity (after holidays & time off)</div></div>
    <div class="card"><h2>Week by week</h2><table><tr><th>Week of</th><th class="num">Capacity</th><th class="num">Booked</th><th class="num">Offers</th><th class="num">Free</th><th class="num">Revenue</th><th>Notes</th></tr>
    ${W.map(w => `<tr><td class="nowrap">${fmtDate(w.week)}</td><td class="num">${w.capacity}</td><td class="num">${w.confirmed}</td><td class="num muted">${w.tentative || ''}</td><td class="num ${w.free < 0 ? 'free-bad' : w.free > 0 ? 'free-good' : ''}">${w.free}</td><td class="num">${w.revenue ? eur(w.revenue) : ''}</td><td class="small muted">${w.events.map(esc).join(', ')}</td></tr>`).join('')}</table></div>`;
};
A.fcWeeks = el => { localStorage.fcWeeks = el.value; render(); };
A.fcUser = el => { localStorage.fcUser = el.value; render(); };

function contractsView() {
  const cs = S.d.contracts;
  return `<div class="hint">Enter confirmed work as <b>hours per week</b> or a <b>total hour budget</b> between two dates. Open offers (with a plan) show up in the forecast automatically; when you accept an offer, it becomes a contract.</div>
  <div class="card"><div class="row" style="align-items:center"><h2 style="margin:0">Contracts</h2><div class="right auto"><button class="primary" data-a="editContract">+ New contract</button></div></div>
  ${cs.length ? `<table><tr><th>Contract</th><th>Customer</th><th>Period</th><th class="num">Hours</th><th class="num">Rate</th><th>Status</th></tr>${cs.sort((a, b) => a.start < b.start ? -1 : 1).map(c => `<tr class="click" data-a="editContract" data-id="${c.id}"><td><b>${esc(c.title)}</b></td><td>${esc(nameOf('customers', c.customer_id))}</td><td class="nowrap">${fmtDate(c.start)} – ${fmtDate(c.end)}</td><td class="num">${c.hours_per_week ? c.hours_per_week + ' h/week' : (c.total_hours || '') + ' h total'}</td><td class="num">${c.rate ? eur(c.rate) : ''}</td><td><span class="pill ${c.status === 'done' ? '' : 'good'}">${esc(c.status || 'confirmed')}</span></td></tr>`).join('')}</table>` : '<div class="empty">No contracts yet.</div>'}</div>`;
}
A.editContract = el => {
  const c = el.dataset.id ? S.d.contracts.find(x => x.id === el.dataset.id) : { status: 'confirmed', start: todayISO(), user_ids: [S.me.id] };
  const users = S.d.users.filter(u => u.active !== false);
  openForm({
    title: c.id ? 'Edit contract' : 'New contract', vals: c,
    fields: [{ k: 'title', l: 'Title' }, { k: 'customer_id', l: 'Customer', t: 'select', o: [['', '—'], ...S.d.customers.map(x => [x.id, x.name])] },
      { k: 'project_id', l: 'Project', t: 'select', o: projectOptions() },
      { k: 'start', l: 'Start', t: 'date', row: 1 }, { k: 'end', l: 'End', t: 'date', row: 1 },
      { k: 'hours_per_week', l: 'Hours per week', t: 'number', row: 2, h: 'Either this…' }, { k: 'total_hours', l: 'Total hours', t: 'number', row: 2, h: '…or this (spread over the period)' }, { k: 'rate', l: 'Hourly rate (€)', t: 'number', row: 2 },
      { k: 'status', l: 'Status', t: 'select', o: ['confirmed', 'done'] }],
    extra: `<div class="field"><label>Who works on it</label>${users.map(u => `<label style="display:inline-block;margin-right:14px"><input type="checkbox" class="cu" value="${u.id}" ${(c.user_ids || []).includes(u.id) ? 'checked' : ''}> ${esc(u.name)}</label>`).join('')}</div>`,
    save: async v => { if (!v.title || !v.start || !v.end) throw new Error('Title, start and end are required'); if (!v.hours_per_week && !v.total_hours) throw new Error('Enter hours per week or total hours'); v.user_ids = [...document.querySelectorAll('.cu:checked')].map(x => x.value); await save('contracts', v); render(); },
    del: c.id ? async () => { await api('DELETE', 'contracts/' + c.id); await loadAll(['contracts']); render(); } : null
  });
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
    fields: [...(isBoss() ? [{ k: 'user_id', l: 'Who', t: 'select', o: S.d.users.filter(u => u.active !== false).map(u => [u.id, u.name]) }] : []), { k: 'kind', l: 'Type', t: 'select', o: ABS_KINDS },
      { k: 'start', l: 'From', t: 'date', row: 1 }, { k: 'end', l: 'To (inclusive)', t: 'date', row: 1 }, { k: 'note', l: 'Note' }],
    save: async v => { if (!v.end || v.end < v.start) v.end = v.start; await save('absences', v); render(); },
    del: a.id ? async () => { await api('DELETE', 'absences/' + a.id); await loadAll(['absences']); render(); } : null
  });
};
