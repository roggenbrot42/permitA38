/* permitA38 – boss pages: customers, projects, offers/invoices, expenses, money, team, settings. */

/* ================= CUSTOMERS ================= */
PAGES.customers = async () => {
  await loadAll(['customers', 'projects', 'invoices', 'offers']);
  const cs = S.d.customers.sort((a, b) => a.name.localeCompare(b.name));
  return `<div class="head"><div><h1>Customers</h1><div class="muted">Business info, contacts and everything you did for them.</div></div><button class="primary" data-a="editCustomer">+ New customer</button></div>
  <div class="card">${cs.length ? `<table><tr><th>Name</th><th>Contact</th><th>City</th><th class="num">Rate</th><th class="num">Open</th></tr>${cs.map(c => {
    const open = S.d.invoices.filter(i => i.customer_id === c.id && i.status === 'sent').reduce((a, i) => a + docTotal(i), 0);
    return `<tr class="click" data-href="#/customer/${c.id}"><td><b>${esc(c.name)}</b></td><td>${esc(c.contact || '')}<div class="small muted">${esc(c.email || '')}</div></td><td>${esc(c.city || '')}</td><td class="num">${c.rate ? eur(c.rate) + '/h' : ''}</td><td class="num">${open ? eur(open) : ''}</td></tr>`;
  }).join('')}</table>` : '<div class="empty">No customers yet. Add your first one – you need one before you can send an offer.</div>'}</div>`;
};
const CUSTOMER_FIELDS = [
  { k: 'name', l: 'Company / name' }, { k: 'customer_no', l: 'Customer no.', row: 1 }, { k: 'rate', l: 'Default hourly rate (€)', t: 'number', row: 1 },
  { k: 'street', l: 'Street & number' }, { k: 'zip', l: 'ZIP', row: 2 }, { k: 'city', l: 'City', row: 2 }, { k: 'country', l: 'Country', row: 2, ph: 'Germany' },
  { k: 'tax_number', l: 'Tax number (Steuernr.)', row: 3 }, { k: 'vat_id', l: 'VAT ID (USt-IdNr.)', row: 3 },
  { k: 'contact', l: 'Point of contact' }, { k: 'email', l: 'E-mail', row: 4 }, { k: 'phone', l: 'Phone', row: 4 },
  { k: 'notes', l: 'Notes', t: 'area', rows: 4 }];
A.editCustomer = el => {
  const c = el?.dataset?.id ? S.d.customers.find(x => x.id === el.dataset.id) : {};
  openForm({
    title: c.id ? 'Edit customer' : 'New customer', fields: CUSTOMER_FIELDS, vals: c, wide: true, note: impressumRow(),
    save: async v => { if (!v.name) throw new Error('Name is required'); const r = await save('customers', v); if (!c.id) location.hash = '#/customer/' + r.id; else render(); },
    del: c.id ? async () => { await api('DELETE', 'customers/' + c.id); location.hash = '#/customers'; await loadAll(['customers']); render(); } : null
  });
  bindImpressum();
};

/* ---- prefill a customer from the Impressum (legal notice) of their website -------------------------------
   The server only reads and parses the page; nothing is saved. Everything that comes from the server is put into
   the page with textContent / dataset (never as HTML). Existing input is never overwritten without a click. */
const IMP_FIELDS = ['name', 'street', 'zip', 'city', 'country', 'email', 'phone', 'vat_id', 'tax_number', 'contact'];
const IMP_LABELS = { name: 'Company', street: 'Street', zip: 'ZIP', city: 'City', country: 'Country', email: 'E-mail', phone: 'Phone', vat_id: 'VAT ID', tax_number: 'Tax number', contact: 'Contact', notes: 'Notes' };
let IMP = null;
function impressumRow() {
  if (S.features && S.features.impressum_lookup === false) return '';
  return `<div class="hint imp-box"><label for="imp_url">Prefill from a company website (Impressum)</label>
    <div class="row" style="align-items:center"><input id="imp_url" type="text" inputmode="url" autocomplete="off" placeholder="www.example.com – or the address of the Impressum page"><div class="auto"><button type="button" id="imp_go">Look up</button></div></div>
    <div class="small muted" style="margin-top:6px">permitA38 requests this web page and reads the legal notice. Nothing is saved until you press Save.</div><div id="imp_out" class="imp-out"></div></div>`;
}
function impEl(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function impBadge(key, evidence, confidence) {
  const label = $('label[for="f_' + key + '"]'); if (!label) return;
  label.querySelectorAll('.imp-badge').forEach(b => b.remove());
  const b = impEl('span', 'from Impressum', 'pill blue imp-badge');
  b.title = (confidence != null ? 'Confidence ' + Math.round(confidence * 100) + ' % – ' : '') + (evidence || '');
  label.appendChild(b);
}
function impSet(key, value, evidence, confidence) {
  const input = $('#f_' + key); if (!input) return false;
  input.value = value; impBadge(key, evidence, confidence); return true;
}
function impNotes(res) {
  const f = res.fields, parts = [];
  const reg = [f.register_number, f.register_court].filter(Boolean).join(', ');
  if (reg) parts.push('Handelsregister: ' + reg);
  if (Array.isArray(f.managing_directors) && f.managing_directors.length) parts.push('Geschäftsführer: ' + f.managing_directors.join(', '));
  return parts.join('\n');
}
function impShow(out, res) {
  out.replaceChildren();
  const filled = [], conflicts = [];
  for (const k of IMP_FIELDS) {
    const v = res.fields[k]; if (typeof v !== 'string' || !v) continue;
    const input = $('#f_' + k); if (!input) continue;
    if (!input.value.trim()) { impSet(k, v, res.evidence[k], res.confidence[k]); filled.push(k); }
    else if (input.value.trim() !== v) conflicts.push(k);
  }
  const notes = $('#f_notes'), noteText = impNotes(res);
  if (noteText && notes && !notes.value.trim()) { notes.value = noteText; impBadge('notes', 'Register data and managing directors from the Impressum', 0.7); filled.push('notes'); }
  out.appendChild(impEl('div', filled.length ? 'Filled in: ' + filled.map(k => IMP_LABELS[k]).join(', ') + '. Please check the values before saving.' : 'Nothing new to fill in – the form already has these values.', 'imp-summary'));
  if (conflicts.length || Object.keys(res.candidates || {}).some(k => IMP_FIELDS.includes(k))) {
    const list = impEl('ul', undefined, 'imp-list');
    const addOption = (k, label, value, evidence, confidence) => {
      const li = impEl('li'); li.appendChild(impEl('span', label + ': ', 'muted')); li.appendChild(impEl('b', value));
      const btn = impEl('button', 'Use', 'sm'); btn.type = 'button'; btn.dataset.a = 'impUse'; btn.dataset.k = k; btn.dataset.v = value; btn.dataset.e = evidence || ''; btn.dataset.c = confidence != null ? String(confidence) : '';
      li.appendChild(document.createTextNode(' ')); li.appendChild(btn); list.appendChild(li);
    };
    for (const k of conflicts) addOption(k, IMP_LABELS[k] + ' (Impressum says; you typed something else)', res.fields[k], res.evidence[k], res.confidence[k]);
    for (const k of IMP_FIELDS) for (const c of (res.candidates?.[k] || []).slice(0, 3)) if (typeof c.value === 'string') addOption(k, 'Other ' + IMP_LABELS[k] + ' found', c.value, c.evidence, c.confidence);
    if (list.childNodes.length) { out.appendChild(impEl('div', 'Different or alternative values – click Use to replace the field:', 'small muted')); out.appendChild(list); }
  }
  if (res.warnings && res.warnings.length) { const w = impEl('ul', undefined, 'imp-list imp-warn'); res.warnings.forEach(t => w.appendChild(impEl('li', '⚠ ' + t))); out.appendChild(w); }
  const src = res.impressum_url || res.source_url;
  if (src && /^https?:\/\//i.test(src)) { const p = impEl('div', 'Source: ', 'small muted'); const a = impEl('a', src); a.href = src; a.target = '_blank'; a.rel = 'noopener noreferrer'; p.appendChild(a); out.appendChild(p); }
}
A.impUse = el => { impSet(el.dataset.k, el.dataset.v, el.dataset.e, el.dataset.c === '' ? null : Number(el.dataset.c)); el.disabled = true; el.textContent = 'Used'; };
function bindImpressum() {
  const go = $('#imp_go'), url = $('#imp_url'), out = $('#imp_out'); if (!go) return;
  const run = async () => {
    const value = url.value.trim(); if (!value) { url.focus(); return; }
    go.disabled = true; go.textContent = 'Looking up…'; out.replaceChildren(impEl('div', 'Reading the website …', 'small muted'));
    try { IMP = await api.post('customers/lookup-impressum', { url: value }); impShow(out, IMP); }
    catch (e) { out.replaceChildren(impEl('div', e.message || 'The lookup failed.', 'imp-error')); }
    finally { go.disabled = false; go.textContent = 'Look up'; }
  };
  go.onclick = run;
  url.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); run(); } };  // Enter must not submit the form
}
PAGES.customer = async id => {
  await loadAll(['customers', 'projects', 'invoices', 'offers', 'entries']);
  const c = S.d.customers.find(x => x.id === id); if (!c) return '<div class="card">Customer not found.</div>';
  const projects = S.d.projects.filter(p => p.customer_id === id), pids = new Set(projects.map(p => p.id));
  const offers = S.d.offers.filter(o => o.customer_id === id), invs = S.d.invoices.filter(o => o.customer_id === id);
  const unbilled = S.d.entries.filter(e => pids.has(e.project_id) && !e.invoice_id && e.billable !== false).reduce((a, e) => a + e.minutes, 0) / 60;
  const addr = [c.street, [c.zip, c.city].filter(Boolean).join(' '), c.country].filter(Boolean).map(esc).join('<br>');
  return `<div class="head"><div><a href="#/customers" class="small">← Customers</a><h1>${esc(c.name)}</h1></div><div class="row tight" style="flex:none"><button data-a="editCustomer" data-id="${id}">Edit</button><button data-a="newDoc" data-coll="offers" data-cust="${id}">+ Offer</button><button class="primary" data-a="newDoc" data-coll="invoices" data-cust="${id}">+ Invoice</button></div></div>
  <div class="grid g2"><div class="card"><h3>Business info</h3><div>${addr || '<span class="muted">No address</span>'}</div><p class="small">${c.tax_number ? 'Tax no.: ' + esc(c.tax_number) + '<br>' : ''}${c.vat_id ? 'VAT ID: ' + esc(c.vat_id) + '<br>' : ''}${c.customer_no ? 'Customer no.: ' + esc(c.customer_no) : ''}</p>
    <h3>Contact</h3><div>${esc(c.contact || '–')}<br><span class="small muted">${esc(c.email || '')} ${esc(c.phone || '')}</span></div></div>
    <div class="card"><h3>Notes</h3><div style="white-space:pre-wrap">${esc(c.notes || '–')}</div></div></div>
  ${unbilled ? `<div class="banner info">⏱ ${hm(unbilled)} h of unbilled time. <a data-a="billTime" data-cust="${id}">Create invoice from time →</a></div>` : ''}
  <div class="card"><h2>Projects</h2>${projects.length ? `<table>${projects.map(p => `<tr class="click" data-href="#/project/${p.id}"><td><span class="dot" style="background:${esc(p.color || '#888')}"></span><b>${esc(p.name)}</b></td><td><span class="pill">${esc(p.status || 'active')}</span></td></tr>`).join('')}</table>` : '<div class="empty">No projects yet.</div>'}</div>
  <div class="card"><h2>Offers & invoices</h2>${docTable([...offers.map(o => ['offers', o]), ...invs.map(o => ['invoices', o])])}</div>`;
};
function docTable(rows) {
  if (!rows.length) return '<div class="empty">Nothing here yet.</div>';
  return `<table><tr><th>No.</th><th>Type</th><th>Customer</th><th>Date</th><th class="num">Total</th><th>Status</th></tr>${rows.sort((a, b) => (b[1].date || '') < (a[1].date || '') ? -1 : 1).map(([coll, d]) => `<tr class="click" data-href="#/doc/${coll}/${d.id}"><td><b>${esc(d.number || 'Draft')}</b><div class="small muted">${esc(d.title || '')}</div></td><td>${coll === 'offers' ? 'Offer' : 'Invoice'}</td><td>${esc(nameOf('customers', d.customer_id))}</td><td>${fmtDate(d.date)}</td><td class="num">${eur(docTotal(d))}</td><td>${statusPill(coll, d)}</td></tr>`).join('')}</table>`;
}
function statusPill(coll, d) {
  const late = coll === 'invoices' && d.status === 'sent' && d.due_date && d.due_date < todayISO();
  const cls = late ? 'bad' : { paid: 'good', accepted: 'good', sent: 'blue', rejected: 'bad', cancelled: 'bad' }[d.status] || '';
  return `<span class="pill ${cls}">${late ? 'overdue' : esc(d.status)}</span>`;
}

/* ================= PROJECTS ================= */
const COLORS = ['#2563eb', '#16a34a', '#d97706', '#dc2626', '#7c3aed', '#0891b2', '#db2777', '#4b5563'];
PAGES.projects = async () => {
  await loadAll(['projects', 'customers', 'tasks']);
  const rep = await api('GET', 'project-report');
  const ps = S.d.projects.filter(p => p.status !== 'archived');
  return `<div class="head"><div><h1>Projects</h1><div class="muted">Customer work and personal projects – hours, rates and margin.</div></div><button class="primary" data-a="editProject">+ New project</button></div>
  <div class="card">${ps.length ? `<table><tr><th>Project</th><th>Customer</th><th class="num">Hours</th><th>Budget</th><th class="num">Revenue</th><th class="num">Unbilled</th><th class="num">Margin</th></tr>${ps.map(p => {
    const r = rep[p.id] || {}, pct = p.budget_hours ? Math.min(100, r.hours / p.budget_hours * 100) : 0;
    return `<tr class="click" data-href="#/project/${p.id}"><td><span class="dot" style="background:${esc(p.color || '#888')}"></span><b>${esc(p.name)}</b>${p.personal ? ' <span class="pill">personal</span>' : ''}</td><td>${esc(nameOf('customers', p.customer_id))}</td><td class="num">${hm(r.hours || 0)}</td>
    <td style="min-width:110px">${p.budget_hours ? `<div class="bar ${pct > 100 ? '' : ''}"><div style="width:${pct}%;${r.hours > p.budget_hours ? 'background:var(--bad)' : ''}"></div></div><span class="small muted">${num(r.hours, 0)} / ${p.budget_hours} h</span>` : '<span class="muted small">–</span>'}</td>
    <td class="num">${p.personal ? '' : eur(r.revenue)}</td><td class="num">${r.unbilled_revenue ? eur(r.unbilled_revenue) : ''}</td><td class="num ${r.margin < 0 ? 'free-bad' : ''}">${p.personal ? '' : eur(r.margin)}</td></tr>`;
  }).join('')}</table>` : '<div class="empty">No projects yet. Create one to start tracking time.</div>'}</div>`;
};
A.editProject = el => {
  const p = el?.dataset?.id ? S.d.projects.find(x => x.id === el.dataset.id) : { status: 'active', color: COLORS[S.d.projects.length % COLORS.length] };
  if (el?.dataset?.cust) p.customer_id = el.dataset.cust;
  openForm({
    title: p.id ? 'Edit project' : 'New project', vals: p,
    fields: [{ k: 'name', l: 'Project name' }, { k: 'personal', l: 'Personal project (no customer, never billed)', t: 'check' },
      { k: 'customer_id', l: 'Customer', t: 'select', o: [['', '— none —'], ...S.d.customers.map(c => [c.id, c.name])] },
      { k: 'rate', l: 'Hourly rate (€)', t: 'number', row: 1, h: 'Overrides the customer rate' }, { k: 'budget_hours', l: 'Budget (hours)', t: 'number', row: 1 },
      { k: 'start', l: 'Start (period)', t: 'date', row: 3, h: 'Required for customer projects' }, { k: 'end', l: 'End (period)', t: 'date', row: 3 },
      { k: 'status', l: 'Status', t: 'select', o: ['active', 'paused', 'done', 'archived'], row: 2 }, { k: 'color', l: 'Colour', t: 'color', row: 2 },
      { k: 'notes', l: 'Notes', t: 'area' }],
    extra: `<div class="field"><label>Team (leave empty = everyone)</label>${S.d.users.filter(u => u.active !== false).map(u => `<label style="display:inline-block;margin-right:14px"><input type="checkbox" class="pm" value="${u.id}" ${(p.member_ids || []).includes(u.id) ? 'checked' : ''}> ${esc(u.name)}</label>`).join('')}</div>`,
    save: async v => { if (!v.name) throw new Error('Name is required'); if (!v.personal && v.customer_id && (!v.start || !v.end)) throw new Error('Customer projects need a start and an end date'); v.member_ids = [...document.querySelectorAll('.pm:checked')].map(x => x.value); v.customer_id = v.customer_id || null; if (v.personal) v.customer_id = null; const r = await save('projects', v); if (!p.id) location.hash = '#/project/' + r.id; else render(); },
    del: p.id ? async () => { await api('DELETE', 'projects/' + p.id); location.hash = '#/projects'; await loadAll(['projects']); render(); } : null
  });
};
PAGES.project = async id => {
  await loadAll(['projects', 'customers', 'tasks', 'entries', 'expenses']);
  const p = S.d.projects.find(x => x.id === id); if (!p) return '<div class="card">Not found.</div>';
  const r = (await api('GET', 'project-report'))[id] || {}, tasks = S.d.tasks.filter(t => t.project_id === id);
  const th = t => S.d.entries.filter(e => e.task_id === t.id).reduce((a, e) => a + e.minutes, 0) / 60;
  const ex = S.d.expenses.filter(e => e.project_id === id);
  return `<div class="head"><div><a href="#/projects" class="small">← Projects</a><h1><span class="dot" style="background:${esc(p.color || '#888')}"></span>${esc(p.name)}</h1><div class="muted">${esc(nameOf('customers', p.customer_id) || (p.personal ? 'Personal project' : ''))}</div></div><button data-a="editProject" data-id="${id}">Edit</button></div>
  <div class="grid g4"><div class="card kpi"><div class="l">Hours</div><div class="v">${hm(r.hours || 0)}</div></div><div class="card kpi"><div class="l">Revenue (at rates)</div><div class="v">${eur(r.revenue)}</div></div><div class="card kpi"><div class="l">Costs</div><div class="v">${eur((r.cost || 0) + (r.expenses || 0))}</div><span class="small muted">team ${eur(r.cost)} · expenses ${eur(r.expenses)}</span></div><div class="card kpi ${r.margin < 0 ? 'bad' : 'good'}"><div class="l">Margin</div><div class="v">${eur(r.margin)}</div></div></div>
  <div class="card"><div class="row" style="align-items:center"><h2 style="margin:0">Tasks & task rates</h2><div class="right auto"><button data-a="editTask" data-proj="${id}">+ Task</button></div></div>
  ${tasks.length ? `<table><tr><th>Task</th><th class="num">Hours</th><th class="num">Rate</th></tr>${tasks.map(t => `<tr class="click" data-a="editTask" data-id="${t.id}" data-proj="${id}"><td>${esc(t.name)}</td><td class="num">${hm(th(t))}</td><td class="num">${t.rate ? eur(t.rate) : '<span class="muted">project rate</span>'}</td></tr>`).join('')}</table>` : '<div class="empty">No tasks. Tasks are optional – use them when different work has a different rate (e.g. "Consulting" vs "Development").</div>'}</div>
  ${ex.length ? `<div class="card"><h2>Expenses on this project</h2><table>${ex.map(e => `<tr><td>${fmtDate(e.date)}</td><td>${esc(e.vendor || '')}</td><td class="num">${eur(e.net)}</td></tr>`).join('')}</table></div>` : ''}`;
};
A.editTask = el => {
  const t = el.dataset.id ? S.d.tasks.find(x => x.id === el.dataset.id) : { project_id: el.dataset.proj };
  openForm({ title: t.id ? 'Edit task' : 'New task', vals: t, fields: [{ k: 'name', l: 'Task name' }, { k: 'rate', l: 'Hourly rate (€) – optional', t: 'number', h: 'Overrides project and customer rate' }],
    save: async v => { if (!v.name) throw new Error('Name required'); await save('tasks', v); render(); },
    del: t.id ? async () => { await api('DELETE', 'tasks/' + t.id); await loadAll(['tasks']); render(); } : null });
};

/* ================= OFFERS & INVOICES ================= */
PAGES.sales = async (tab = 'invoices') => {
  await loadAll(['offers', 'invoices', 'customers', 'projects', 'entries']);
  const rep = await api('GET', 'project-report'), unbilled = Object.values(rep).reduce((a, r) => a + r.unbilled_revenue, 0);
  const rows = S.d[tab].map(d => [tab, d]);
  const sum = (f) => S.d.invoices.filter(f).reduce((a, i) => a + docTotal(i), 0);
  return `<div class="head"><div><h1>Offers & Invoices</h1><div class="muted">Offer → accepted → project → invoice.</div></div><div class="row tight" style="flex:none"><button data-a="newDoc" data-coll="offers">+ Offer</button><button class="primary" data-a="newDoc" data-coll="invoices">+ Invoice</button></div></div>
  <div class="grid g3"><div class="card kpi warn"><div class="l">Unbilled work</div><div class="v">${eur(unbilled)}</div><a data-a="billTime" class="small">Invoice tracked time →</a></div><div class="card kpi"><div class="l">Outstanding</div><div class="v">${eur(sum(i => i.status === 'sent'))}</div></div><div class="card kpi bad"><div class="l">Overdue</div><div class="v">${eur(sum(i => i.status === 'sent' && i.due_date && i.due_date < todayISO()))}</div></div></div>
  <div class="tabs"><a href="#/sales/invoices" class="${tab === 'invoices' ? 'on' : ''}">Invoices</a><a href="#/sales/offers" class="${tab === 'offers' ? 'on' : ''}">Offers</a></div><div class="card">${docTable(rows)}</div>`;
};
A.newDoc = el => { location.hash = `#/doc/${el.dataset.coll}/new${el.dataset.cust ? '-' + el.dataset.cust : ''}`; };
A.billTime = el => {
  openForm({
    title: 'Create invoice from tracked time', saveLabel: 'Create draft',
    vals: { customer_id: el?.dataset?.cust || '' }, fields: [{ k: 'customer_id', l: 'Customer', t: 'select', o: [['', 'All customers'], ...S.d.customers.map(c => [c.id, c.name])] }, { k: 'from', l: 'From', t: 'date', row: 1 }, { k: 'to', l: 'To', t: 'date', row: 1 }],
    note: '<p class="muted small">All unbilled, billable time of the selection is grouped by project, task and rate. Personal projects are skipped.</p>',
    save: async v => { const inv = await api('POST', 'invoices/from-time', { customer_id: v.customer_id || null, from: v.from || null, to: v.to || null }); location.hash = '#/doc/invoices/' + inv.id; }
  });
};

let DOC = null;
PAGES.doc = async (coll, id) => {
  await loadAll(['customers', 'projects', 'users', 'offers', 'invoices']);
  const s = S.settings, isInv = coll === 'invoices';
  const route = coll + '/' + id;
  if (id.startsWith('new')) {
    if (!DOC || DOC.__route !== route) {   // keep the in-progress draft across re-renders (add/delete line)
      DOC = { __route: route, coll, customer_id: id.slice(4) || '', date: todayISO(), status: 'draft', lines: [{ description: '', qty: 1, unit: 'h', price: s.default_rate || 0 }], template_id: s.default_template, vat_rate: s.vat_rate };
      const cu = S.d.customers.find(c => c.id === DOC.customer_id); if (cu?.rate) DOC.lines[0].price = cu.rate;
    }
  } else if (!DOC || DOC.id !== id || DOC.coll !== coll) {
    const rec = (S.d[coll] || []).find(x => x.id === id);
    if (!rec) { DOC = null; return `<div class="card"><h2>Not found</h2><p class="muted">This ${isInv ? 'invoice' : 'offer'} does not exist (any more).</p><a href="#/sales/${isInv ? 'invoices' : 'offers'}">← Back to ${isInv ? 'invoices' : 'offers'}</a></div>`; }
    DOC = { coll, ...rec, lines: rec.lines || [] };
  }
  const d = DOC, locked = d.number && d.status !== 'draft', isNew = !d.id;
  const opt = (arr, cur) => arr.map(([v, l]) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(l)}</option>`).join('');
  const klein = s.kleinunternehmer;
  const statuses = isInv ? [['sent', 'Sent'], ['paid', 'Paid'], ['cancelled', 'Cancelled']] : [['sent', 'Sent'], ['accepted', 'Accepted'], ['rejected', 'Rejected']];
  const plan = d.plan || {};
  return `<div class="head"><div><a href="#/sales/${coll}" class="small">← ${isInv ? 'Invoices' : 'Offers'}</a><h1>${isInv ? 'Invoice' : 'Offer'} ${esc(d.number || '(draft)')} ${d.id ? statusPill(coll, d) : ''}</h1></div>
    <div class="row tight" style="flex:none">${d.id ? `<button data-a="docPdf">Preview PDF</button>` : ''}<button class="${d.number ? '' : 'primary'}" data-a="docSave">Save</button>
    ${d.id && !d.number ? `<button class="primary" data-a="docFinalize">Finalize & number</button>` : ''}${d.id && d.number ? `<select style="width:auto" data-change="docStatus"><option value="">Set status…</option>${opt(statuses, '')}</select>` : ''}
    ${d.id && !isInv && d.number && d.status !== 'accepted' ? `<button data-a="docAccept">✓ Accepted → create project</button>` : ''}${d.id && !isInv ? `<button data-a="docToInvoice">→ Invoice</button>` : ''}</div></div>
  ${locked ? '<div class="banner">This document has a number and was probably sent. Changing it now changes the PDF – only edit for corrections.</div>' : ''}
  <div class="card"><div class="grid g3"><div class="field"><label>Customer</label><select id="d_cust">${opt([['', '— choose —'], ...S.d.customers.map(c => [c.id, c.name])], d.customer_id)}</select></div>
    <div class="field"><label>Project</label><select id="d_proj">${opt(projectOptions(), d.project_id || '')}</select></div><div class="field"><label>Title / subject</label><input id="d_title" value="${esc(d.title || '')}"></div>
    <div class="field"><label>Date</label><input type="date" id="d_date" value="${esc(d.date || '')}"></div>
    ${isInv ? `<div class="field"><label>Due date</label><input type="date" id="d_due" value="${esc(d.due_date || '')}"></div><div class="field"><label>Paid on</label><input type="date" id="d_paid" value="${esc(d.paid_date || '')}"></div>` : `<div class="field"><label>Valid until</label><input type="date" id="d_valid" value="${esc(d.valid_until || '')}"></div>`}
    <div class="field"><label>Service period from</label><input type="date" id="d_sf" value="${esc(d.service_from || '')}"></div><div class="field"><label>to</label><input type="date" id="d_st" value="${esc(d.service_to || '')}"></div>
    <div class="field"><label>Your reference / PO number</label><input id="d_ref" value="${esc(d.reference || '')}"></div>
    <div class="field"><label>Template</label><select id="d_tpl">${opt(S.templates.map(t => [t.id, t.name]), d.template_id)}</select></div>
    <div class="field"><label>Language</label><select id="d_lang">${opt([['', 'Template default'], ['de', 'Deutsch'], ['en', 'English']], d.language || '')}</select></div>
    ${klein ? '' : `<div class="field"><label>Default VAT %</label><input type="number" id="d_vat" value="${esc(d.vat_rate ?? s.vat_rate)}"></div>`}</div></div>
  <div class="card"><h2>Items</h2><table class="lines"><tr><th>Description</th><th style="width:80px">Qty</th><th style="width:70px">Unit</th><th style="width:110px">Unit price €</th>${klein ? '' : '<th style="width:70px">VAT %</th>'}<th class="num" style="width:110px">Amount</th><th></th></tr>
    ${d.lines.map((l, i) => `<tr><td><input data-l="${i}" data-k="description" value="${esc(l.description)}" placeholder="What did you deliver?"></td><td><input data-l="${i}" data-k="qty" type="number" step="any" value="${esc(l.qty)}"></td><td><input data-l="${i}" data-k="unit" value="${esc(l.unit)}"></td><td><input data-l="${i}" data-k="price" type="number" step="any" value="${esc(l.price)}"></td>${klein ? '' : `<td><input data-l="${i}" data-k="vat" type="number" value="${esc(l.vat ?? '')}" placeholder="${esc(d.vat_rate ?? s.vat_rate)}"></td>`}<td class="num" id="amt${i}">${eur(l.qty * l.price)}</td><td><button class="ghost sm" data-a="delLine" data-i="${i}">✕</button></td></tr>`).join('')}</table>
    <button class="sm" data-a="addLine" style="margin-top:8px">+ Add line</button>${isInv ? ' <button class="sm" data-a="billTimeInto">+ Add unbilled time…</button>' : ''}
    <div class="right" id="d_totals" style="margin-top:12px"></div></div>
  <div class="card"><div class="grid g2"><div class="field"><label>Intro text (empty = from template)</label><textarea id="d_intro" rows="4">${esc(d.intro || '')}</textarea></div><div class="field"><label>Closing text (empty = from template)</label><textarea id="d_outro" rows="4">${esc(d.outro || '')}</textarea></div></div>
    <div class="field"><label>Internal notes (not printed)</label><textarea id="d_notes" rows="2">${esc(d.notes || '')}</textarea></div></div>
  ${isInv ? '' : `<div class="card"><h2>Planning (optional)</h2><div class="muted small" style="margin-bottom:8px">When should this work happen? While the offer is open, the Planning forecast shows it as tentative; once accepted it becomes a contract.</div><div class="row"><div><label>Start</label><input type="date" id="p_s" value="${esc(plan.start || '')}"></div><div><label>End</label><input type="date" id="p_e" value="${esc(plan.end || '')}"></div><div><label>Hours per week</label><input type="number" id="p_hpw" value="${esc(plan.hours_per_week ?? '')}"></div><div><label>or total hours</label><input type="number" id="p_tot" value="${esc(plan.total_hours ?? '')}"></div></div></div>`}`;
};
const _origDoc = PAGES.doc;
PAGES.doc = async (...a) => { const h = await _origDoc(...a); render.after = recalcDoc; return h; };
function syncDoc() {
  if (!DOC || !$('#d_cust')) return;
  const g = i => $('#' + i)?.value, d = DOC;
  Object.assign(d, { customer_id: g('d_cust'), project_id: g('d_proj') || null, title: g('d_title'), date: g('d_date'), service_from: g('d_sf') || null, service_to: g('d_st') || null, reference: g('d_ref'), template_id: g('d_tpl'), language: g('d_lang'), intro: g('d_intro'), outro: g('d_outro'), notes: g('d_notes') });
  if (d.coll === 'invoices') Object.assign(d, { due_date: g('d_due') || null, paid_date: g('d_paid') || null }); else Object.assign(d, { valid_until: g('d_valid') || null, plan: g('p_s') && g('p_e') ? { start: g('p_s'), end: g('p_e'), hours_per_week: g('p_hpw') ? +g('p_hpw') : null, total_hours: g('p_tot') ? +g('p_tot') : null } : null });
  if ($('#d_vat')) d.vat_rate = +g('d_vat');
  document.querySelectorAll('[data-l]').forEach(inp => { const l = d.lines[+inp.dataset.l], k = inp.dataset.k; l[k] = ['qty', 'price', 'vat'].includes(k) ? (inp.value === '' ? (k === 'vat' ? null : 0) : parseFloat(inp.value)) : inp.value; });
}
function recalcDoc() {
  syncDoc(); const d = DOC; if (!d || !$('#d_totals')) return;
  const klein = S.settings.kleinunternehmer, t = Money.totals(d, S.settings);   // exact cents, half-up VAT per rate
  t.lines.forEach((c, i) => { const el = $('#amt' + i); if (el) el.textContent = eur(Money.toNumber(c)); });
  const vat = Object.entries(t.vat).filter(([r]) => +r);
  $('#d_totals').innerHTML = `Net <b>${eur(Money.toNumber(t.net))}</b>${vat.map(([r, c]) => ` · VAT ${r}% ${eur(Money.toNumber(c))}`).join('')} · <span style="font-size:20px">Total <b>${eur(Money.toNumber(t.gross))}</b></span>${klein ? '<div class="small muted">Kleinunternehmer (§19 UStG): no VAT charged.</div>' : ''}`;
}
document.addEventListener('input', e => { if (e.target.closest('[data-l],#d_vat')) recalcDoc(); });
A.addLine = () => { syncDoc(); DOC.lines.push({ description: '', qty: 1, unit: 'h', price: S.settings.default_rate || 0 }); render(); };
A.delLine = el => { syncDoc(); DOC.lines.splice(+el.dataset.i, 1); render(); };
async function persistDoc() {
  syncDoc(); const d = DOC;
  if (!d.customer_id) throw new Error('Choose a customer first');
  const { coll, __route, ...body } = d; const r = await api.upsert(coll, body); DOC = { coll, ...r };
  await loadAll([coll]); return r;
}
A.docSave = async () => { const wasNew = !DOC.id; const r = await persistDoc(); toast('Saved'); if (wasNew) location.hash = `#/doc/${DOC.coll}/${r.id}`; else render(); };
A.docFinalize = async () => { await persistDoc(); const r = await api('POST', `${DOC.coll}/${DOC.id}/finalize`); DOC = { coll: DOC.coll, ...r }; toast('Finalized as ' + r.number); await loadAll([DOC.coll]); location.hash = `#/doc/${DOC.coll}/${r.id}`; render(); };
A.docPdf = async () => { await persistDoc(); window.open(api.url(`${DOC.coll}/${DOC.id}/pdf`), '_blank'); };
A.docStatus = async el => {
  if (!el.value) return;
  syncDoc(); DOC.status = el.value;
  if (el.value === 'paid' && !DOC.paid_date) { DOC.paid_date = todayISO(); const pd = $('#d_paid'); if (pd) pd.value = DOC.paid_date; }  // before persistDoc() re-reads the form
  await persistDoc(); toast('Status: ' + el.value); render();
};
A.docAccept = async () => { await persistDoc(); const r = await api('POST', `offers/${DOC.id}/accept`); await loadAll(['offers', 'projects', 'contracts']); DOC = null; toast('Accepted – project' + (r.contract ? ' and contract' : '') + ' created'); location.hash = '#/project/' + r.project.id; };
A.docToInvoice = async () => { await persistDoc(); const r = await api('POST', `offers/${DOC.id}/to-invoice`); DOC = null; await loadAll(['invoices']); location.hash = '#/doc/invoices/' + r.id; };
A.billTimeInto = () => A.billTime({ dataset: { cust: DOC.customer_id } });

/* ================= EXPENSES ================= */
const EXP_CATS = ['Software & subscriptions', 'Hardware & equipment', 'Office & coworking', 'Travel', 'Marketing', 'Insurance', 'Phone & internet', 'Training', 'Subcontractors', 'Fees & bank', 'Other'];
PAGES.expenses = async () => {
  await loadAll(['expenses', 'projects']);
  const y = new Date().getFullYear(), list = S.d.expenses.sort((a, b) => a.date < b.date ? 1 : -1), yr = list.filter(e => (e.date || '').startsWith(y));
  return `<div class="head"><div><h1>Expenses</h1><div class="muted">Bills you pay – general or tied to a project.</div></div><button class="primary" data-a="editExpense">+ Add bill</button></div>
  <div class="grid g3"><div class="card kpi"><div class="l">${y} net expenses</div><div class="v">${eur(yr.reduce((a, e) => a + (e.net || 0) * (e.deductible ?? 100) / 100, 0))}</div></div><div class="card kpi"><div class="l">Input VAT ${y}</div><div class="v">${S.settings.kleinunternehmer ? '–' : eur(yr.reduce((a, e) => a + (e.vat || 0) * (e.deductible ?? 100) / 100, 0))}</div></div></div>
  <div class="card">${list.length ? `<table><tr><th>Date</th><th>Vendor</th><th>Category</th><th>Project</th><th class="num">Net</th><th class="num">VAT</th><th></th></tr>${list.map(e => `<tr class="click" data-a="editExpense" data-id="${e.id}"><td class="nowrap">${fmtDate(e.date)}</td><td><b>${esc(e.vendor || '')}</b><div class="small muted">${esc(e.description || '')}</div></td><td>${esc(e.category || '')}</td><td>${esc(nameOf('projects', e.project_id) || 'General')}</td><td class="num">${eur(e.net)}</td><td class="num">${eur(e.vat)}</td><td>${e.attachment ? `<a href="${api.url('attachments/' + e.attachment)}" target="_blank" data-stop>📎</a>` : ''}</td></tr>`).join('')}</table>` : '<div class="empty">No expenses yet.</div>'}</div>`;
};
A.editExpense = el => {
  const e = el?.dataset?.id ? S.d.expenses.find(x => x.id === el.dataset.id) : { date: todayISO(), deductible: 100, vat_rate: 19, category: EXP_CATS[0] };
  const v0 = { ...e, gross: e.net != null ? Math.round((e.net + (e.vat || 0)) * 100) / 100 : '', vat_rate: e.vat_rate ?? 19 };
  openForm({
    title: e.id ? 'Edit bill' : 'Add bill', vals: v0,
    fields: [{ k: 'date', l: 'Paid on', t: 'date', row: 1 }, { k: 'vendor', l: 'Vendor', row: 1 }, { k: 'description', l: 'Description' },
      { k: 'gross', l: 'Amount on the bill (gross) €', t: 'number', row: 2 }, { k: 'vat_rate', l: 'VAT %', t: 'select', o: [19, 7, 0], row: 2 },
      { k: 'category', l: 'Category', t: 'select', o: EXP_CATS, row: 3 }, { k: 'project_id', l: 'Project', t: 'select', o: [['', 'General expense'], ...projectOptions(false)], row: 3 },
      { k: 'deductible', l: 'Deductible %', t: 'number', h: '100 normally; 70 for business meals' }],
    extra: `<div class="field"><label>Attach the bill (PDF / photo)</label><input type="file" id="e_file">${e.attachment ? `<div class="small"><a href="${api.url('attachments/' + e.attachment)}" target="_blank">Current attachment</a></div>` : ''}</div>`,
    save: async v => {
      if (!v.gross) throw new Error('Enter the amount'); const r = +v.vat_rate || 0;
      if (v.gross < 0) throw new Error('The amount must not be negative (book refunds as a credit-note invoice)');
      if (!v.date) throw new Error('Pick the date you paid');
      Object.assign(v, Money.splitGross(v.gross, r)); v.vat_rate = r; delete v.gross; v.project_id = v.project_id || null;
      const f = $('#e_file').files[0];
      if (f) v.attachment = (await api.upload(f)).name;
      await save('expenses', v); render();
    },
    del: e.id ? async () => { await api('DELETE', 'expenses/' + e.id); await loadAll(['expenses']); render(); } : null
  });
};

/* ================= MONEY & TAXES ================= */
PAGES.money = async (year) => {
  // the API accepts 2000..2100 only: clamp a typed or stale route instead of showing a validation error
  year = Math.min(2100, Math.max(2000, Math.trunc(+year) || new Date().getFullYear()));
  const f = await api('GET', 'finance?year=' + year), s = S.settings, m = new Date().getMonth(), cur = year === new Date().getFullYear() ? f.months[m] : null;
  const T = f.totals, ta = f.tax_actual, tp = f.tax_projected;
  const MN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const mx = Math.max(100, ...f.months.map(x => Math.max(x.revenue, x.expenses, 0))), H = 170, bw = 900 / 12;
  const chart = `<svg class="chart" viewBox="0 0 940 ${H + 28}"><g transform="translate(36,6)">${[0, .5, 1].map(k => `<line x1="0" x2="900" y1="${H - H * k}" y2="${H - H * k}" stroke="var(--line)"/><text x="-6" y="${H - H * k + 4}" text-anchor="end">${num(mx * k, 0)}</text>`).join('')}
    ${f.months.map((x, i) => `<g><title>${MN[i]}: revenue ${eur(x.revenue)}, expenses ${eur(x.expenses)}, profit ${eur(x.profit)}</title><rect x="${i * bw + 8}" y="${H - Math.max(0, x.revenue) / mx * H}" width="${bw / 2 - 9}" height="${Math.max(0, x.revenue) / mx * H}" fill="var(--accent)" rx="2"/><rect x="${i * bw + bw / 2}" y="${H - Math.max(0, x.expenses) / mx * H}" width="${bw / 2 - 9}" height="${Math.max(0, x.expenses) / mx * H}" fill="var(--warn)" rx="2"/><text x="${i * bw + bw / 2}" y="${H + 16}" text-anchor="middle">${MN[i]}</text></g>`).join('')}</g></svg>`;
  const typeName = { freiberufler: 'Freiberufler (EÜR, no trade tax)', gewerbe: 'Gewerbetreibender / Einzelunternehmen', gmbh: 'UG / GmbH' }[f.company_type];
  return `<div class="head"><div><h1>Money & Taxes</h1><div class="muted">${esc(typeName)}${f.kleinunternehmer ? ' · Kleinunternehmer' : ''} · ${s.accounting === 'accrual' ? 'by invoice date' : 'by payment date (EÜR)'}</div></div>
    <div class="row tight" style="flex:none;align-items:center"><button data-a="goto" data-to="money/${year - 1}" ${year <= 2000 ? 'disabled' : ''}>←</button><b>${year}</b><button data-a="goto" data-to="money/${year + 1}" ${year >= 2100 ? 'disabled' : ''}>→</button></div></div>
  ${f.warnings.map(w => `<div class="banner">⚠ ${esc(w)}</div>`).join('')}
  ${cur ? `<div class="card" style="border-color:var(--accent)"><h2>Set aside for ${MN[m]} (so far)</h2><div class="grid g4"><div><div class="muted small">Profit this month</div><div style="font-size:22px;font-weight:700">${eur(cur.profit)}</div></div><div><div class="muted small">Tax reserve (${num(f.effective_rate * 100, 1)} %)</div><div style="font-size:22px;font-weight:700">${eur(cur.tax_set_aside)}</div></div><div><div class="muted small">VAT to pay</div><div style="font-size:22px;font-weight:700">${eur(cur.vat_set_aside)}</div></div><div><div class="muted small">Social insurance</div><div style="font-size:22px;font-weight:700">${eur(cur.social)}</div></div></div>
    <div style="margin-top:10px">→ You can take home about <b class="${cur.take_home >= 0 ? 'free-good' : 'free-bad'}" style="font-size:20px">${eur(cur.take_home)}</b> this month.</div></div>` : ''}
  <div class="grid g4"><div class="card kpi"><div class="l">Revenue (net)</div><div class="v">${eur(T.revenue)}</div></div><div class="card kpi"><div class="l">Expenses (net)</div><div class="v">${eur(T.expenses)}</div></div><div class="card kpi ${T.profit < 0 ? 'bad' : 'good'}"><div class="l">Profit</div><div class="v">${eur(T.profit)}</div></div><div class="card kpi ${f.open_receivables ? 'warn' : ''}"><div class="l">Open receivables</div><div class="v">${eur(f.open_receivables)}</div></div></div>
  <div class="grid g2"><div class="card"><h2>Tax burden ${year}</h2><table>
    ${f.company_type === 'gmbh' ? `<tr><td>Körperschaftsteuer (15 %)</td><td class="num">${eur(ta.kst)}</td></tr><tr><td>Solidaritätszuschlag</td><td class="num">${eur(ta.soli)}</td></tr>` : `<tr><td>Einkommensteuer</td><td class="num">${eur(ta.est)}</td></tr><tr><td>Solidaritätszuschlag</td><td class="num">${eur(ta.soli)}</td></tr>${ta.church ? `<tr><td>Kirchensteuer</td><td class="num">${eur(ta.church)}</td></tr>` : ''}`}
    ${f.company_type !== 'freiberufler' ? `<tr><td>Gewerbesteuer (Hebesatz ${s.hebesatz || 400} %)${ta.gewst_credit ? `<div class="small muted">incl. ${eur(ta.gewst_credit)} credited against income tax (§35 EStG)</div>` : ''}</td><td class="num">${eur(ta.gewst)}</td></tr>` : ''}
    <tr><td><b>Total income-based taxes</b></td><td class="num"><b>${eur(ta.total)}</b></td></tr><tr><td>Umsatzsteuer (VAT) payable</td><td class="num">${f.kleinunternehmer ? '–' : eur(T.vat_due)}</td></tr><tr><td>Health / pension insurance (your estimate)</td><td class="num">${eur(f.social_year)}</td></tr>
    <tr><td><b>What stays with you</b></td><td class="num"><b class="${f.take_home >= 0 ? 'free-good' : 'free-bad'}">${eur(f.take_home)}</b></td></tr></table>
    <p class="small muted">Based on profit to date. Projected full-year profit: ${eur(f.projected_profit)} → tax rate ${num(f.effective_rate * 100, 1)} % used for the monthly reserve. Estimate only – not tax advice.</p></div>
  <div class="card"><h2>Expenses by category</h2>${Object.keys(f.categories).length ? `<table>${Object.entries(f.categories).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${eur(v)}</td></tr>`).join('')}</table>` : '<div class="empty">No expenses this year.</div>'}</div></div>
  <div class="card"><h2>Month by month</h2>${chart}<div class="small muted"><span class="dot" style="background:var(--accent)"></span>revenue <span class="dot" style="background:var(--warn);margin-left:10px"></span>expenses</div>
  <table style="margin-top:12px"><tr><th>Month</th><th class="num">Revenue</th><th class="num">Expenses</th><th class="num">Profit</th><th class="num">VAT due</th><th class="num">Tax reserve</th><th class="num">Take-home</th></tr>${f.months.map((x, i) => `<tr><td>${MN[i]}</td><td class="num">${eur(x.revenue)}</td><td class="num">${eur(x.expenses)}</td><td class="num ${x.profit < 0 ? 'free-bad' : ''}">${eur(x.profit)}</td><td class="num">${eur(x.vat_due)}</td><td class="num">${eur(x.tax_set_aside)}</td><td class="num"><b>${eur(x.take_home)}</b></td></tr>`).join('')}</table></div>`;
};

/* ================= TEAM ================= */
const WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
PAGES.team = async () => {
  await loadAll(['users', 'entries']);
  const dashes = await Promise.all(S.d.users.filter(u => u.active !== false).map(async u => [u, await api('GET', 'dashboard?user_id=' + u.id)]));
  return `<div class="head"><div><h1>Team</h1><div class="muted">You (boss) plan; members track their own time.</div></div><button class="primary" data-a="editUser">+ Add person</button></div>
  <div class="card"><table><tr><th>Person</th><th>Role</th><th class="num">Weekly h</th><th>This week</th><th class="num">Rate</th><th class="num">Cost/h</th></tr>${dashes.map(([u, d]) => `<tr class="click" data-a="editUser" data-id="${u.id}"><td><span class="dot" style="background:${esc(u.color || '#888')}"></span><b>${esc(u.name)}</b></td><td><span class="pill ${u.role === 'boss' ? 'blue' : ''}">${u.role}</span> ${u.totp_enabled ? `<span class="pill good" title="Two-factor authentication is on">2FA</span> <button class="ghost sm" data-a="resetTotp" data-id="${u.id}">Reset 2FA</button>` : ''}</td><td class="num">${u.weekly_hours || 0}</td><td style="min-width:140px"><div class="bar"><div style="width:${d.target ? Math.min(100, d.worked / d.target * 100) : 0}%"></div></div><span class="small muted">${hm(d.worked)} / ${hm(d.target)}</span></td><td class="num">${u.default_rate ? eur(u.default_rate) : ''}</td><td class="num">${u.cost_rate ? eur(u.cost_rate) : ''}</td></tr>`).join('')}</table></div>
  <div class="hint">🔒 Passwords keep team members out of each other's data inside the app. Because the data is plain files in a synced folder, anyone with access to that folder can read everything – share it only with people you trust.</div>`;
};
A.editUser = el => {
  const u = el?.dataset?.id ? S.d.users.find(x => x.id === el.dataset.id) : { role: 'member', weekly_hours: 40, color: COLORS[S.d.users.length % COLORS.length], active: true };
  openForm({
    title: u.id ? 'Edit person' : 'Add person', vals: u,
    fields: [{ k: 'name', l: 'Name' }, { k: 'role', l: 'Role', t: 'select', o: [['member', 'Member – tracks own time'], ['boss', 'Boss – plans & sees everything']], row: 1 }, { k: 'color', l: 'Colour', t: 'color', row: 1 },
      { k: 'weekly_hours', l: 'Weekly hours', t: 'number', row: 2 }, { k: 'tracking_start', l: 'Tracking since', t: 'date', row: 2, h: 'Starts the flex-time balance' },
      { k: 'default_rate', l: 'Billing rate €/h', t: 'number', row: 3, h: 'Fallback when project/customer have none' }, { k: 'cost_rate', l: 'Cost €/h', t: 'number', row: 3, h: 'For project margins' },
      { k: 'username', l: 'Username', h: 'Used to sign in (letters, digits, . - _)' }, { k: 'password', l: u.id ? 'New password (leave empty to keep)' : 'Password (min. 8 characters)', t: 'password', h: 'At least 8 characters' }, { k: 'active', l: 'Active', t: 'check' }],
    extra: `<div class="field"><label>Working days</label>${WD.map((d, i) => `<label style="display:inline-block;margin-right:12px"><input type="checkbox" class="wd" value="${i}" ${(u.workdays || [0, 1, 2, 3, 4]).includes(i) ? 'checked' : ''}> ${d}</label>`).join('')}</div>`,
    save: async v => { if (!v.name) throw new Error('Name required'); if (!v.username) throw new Error('Username required'); if (!u.id && (!v.password || v.password.length < 8)) throw new Error('A password with at least 8 characters is required'); if (v.password && v.password.length < 8) throw new Error('The password must be at least 8 characters long'); v.workdays = [...document.querySelectorAll('.wd:checked')].map(x => +x.value); if (!v.workdays.length) throw new Error('Pick at least one working day'); if (!v.password) delete v.password; delete v.has_password; await save('users', v); if (u.id === S.me.id) await bootstrap(); render(); },
    del: u.id && u.id !== S.me.id ? async () => { await api('DELETE', 'users/' + u.id); await loadAll(['users']); render(); } : null
  });
};

/* ================= SETTINGS ================= */
PAGES.settings = async () => {
  S.settings = await api('GET', 'settings'); await loadAll(['templates']);
  const st = await api.get('auth/status', { noAuthRedirect: true }), s = S.settings;
  const f = (k, l, t = 'text', extra = '') => fieldHTML({ k, l, t, ...extra }, s);
  const tpls = S.d.templates || [];
  return `<div class="head"><div><h1>Settings</h1></div><button class="primary" data-a="saveSettings">Save settings</button></div>
  <div class="grid g2"><div class="card"><h2>Your business</h2>${f('company_name', 'Business name')}${f('owner_name', 'Your name (signature)')}${f('street', 'Street & number')}<div class="row">${f('zip', 'ZIP')}${f('city', 'City')}</div><div class="row">${f('email', 'E-mail')}${f('phone', 'Phone')}</div>${f('website', 'Website')}<div class="row">${f('tax_number', 'Tax number (Steuernr.)')}${f('vat_id', 'VAT ID (USt-IdNr.)')}</div>
    <h3 style="margin-top:12px">Bank</h3>${f('bank_name', 'Bank')}<div class="row">${f('iban', 'IBAN')}${f('bic', 'BIC')}</div>
    <div class="field"><label>Logo for documents (JPEG)</label><input type="file" id="logo_file" accept="image/jpeg">${s.logo ? '<div class="small muted">A logo is set. Choose a new file to replace it.</div>' : ''}</div></div>
  <div class="card"><h2>Company type & taxes (Germany)</h2>
    ${fieldHTML({ k: 'company_type', l: 'Company type', t: 'select', o: [['freiberufler', 'Freiberufler (EÜR, no trade tax)'], ['gewerbe', 'Gewerbetreibender / Einzelunternehmen'], ['gmbh', 'UG / GmbH']] }, s)}
    ${fieldHTML({ k: 'state', l: 'Federal state (public holidays)', t: 'select', o: Object.entries(S.states || {}) }, s)}
    ${f('kleinunternehmer', 'Kleinunternehmer (§19 UStG) – no VAT on invoices', 'check')}
    <div class="row">${fieldHTML({ k: 'vat_rate', l: 'Default VAT %', t: 'select', o: [19, 7, 0] }, s)}${f('hebesatz', 'Gewerbesteuer Hebesatz %', 'number')}</div>
    ${fieldHTML({ k: 'accounting', l: 'Revenue counted…', t: 'select', o: [['eur', 'when paid (EÜR / Ist-Versteuerung)'], ['accrual', 'when invoiced (Soll)']] }, s)}
    <div class="row">${f('social_monthly', 'Health/care/pension per month €', 'number')}${f('church_tax', 'Church tax %', 'number', { h: '0, 8 or 9' })}</div>
    <div class="row">${f('other_income', 'Other taxable income / year €', 'number')}${f('other_deductions', 'Other deductions / year €', 'number')}</div>${f('splitting', 'Joint assessment (Ehegattensplitting)', 'check')}
    <p class="small muted">Tax figures are planning estimates (Einkommensteuer tariff §32a, Soli, Gewerbesteuer incl. §35 credit). Always confirm with your Steuerberater.</p></div></div>
  <div class="card"><h2>Defaults</h2><div class="row">${f('default_rate', 'Default hourly rate €', 'number')}${f('payment_days', 'Payment term (days)', 'number')}${f('offer_valid_days', 'Offers valid (days)', 'number')}${fieldHTML({ k: 'default_template', l: 'Default template', t: 'select', o: S.templates.map(t => [t.id, t.name]) }, s)}</div><div class="row">${f('invoice_pattern', 'Invoice numbers', 'text', { h: '{year} and {n:04d} are replaced' })}${f('offer_pattern', 'Offer numbers')}</div></div>
  <div class="card"><div class="row" style="align-items:center"><h2 style="margin:0">Document templates</h2><div class="right auto"><button data-a="editTemplate">+ New template</button></div></div><table>${S.templates.map(t => `<tr><td><b>${esc(t.name)}</b> <span class="muted small">${esc(t.layout)} · ${esc(t.language)}</span></td><td><span class="dot" style="background:${esc(t.accent)}"></span></td><td class="right"><button class="sm" data-a="editTemplate" data-id="${t.id}">${tpls.find(x => x.id === t.id) ? 'Edit' : 'Copy & customise'}</button></td></tr>`).join('')}</table></div>
  <div class="card"><h2>Data & sync</h2><p>Data folder on this device:<br><code>${esc(S.data_dir || '')}</code></p><p class="muted small">Put this folder inside a Syncthing-shared folder and start permitA38 with <code>permita38 --data &lt;that folder&gt;</code> on each device. See the README for details.</p></div>`;
};
A.saveSettings = async () => {
  const s = { ...S.settings };
  const txt = ['company_name', 'owner_name', 'street', 'zip', 'city', 'email', 'phone', 'website', 'tax_number', 'vat_id', 'bank_name', 'iban', 'bic', 'invoice_pattern', 'offer_pattern', 'company_type', 'state', 'accounting', 'default_template'];
  const nums = ['hebesatz', 'social_monthly', 'church_tax', 'other_income', 'other_deductions', 'default_rate', 'payment_days', 'offer_valid_days', 'vat_rate'];
  txt.forEach(k => { const e = $('#f_' + k); if (e) s[k] = e.value; });
  nums.forEach(k => { const e = $('#f_' + k); if (e) s[k] = e.value === '' ? 0 : parseFloat(e.value); });
  ['kleinunternehmer', 'splitting'].forEach(k => s[k] = $('#f_' + k).checked);
  const lf = $('#logo_file').files[0];
  if (lf) s.logo = (await api.upload(lf)).name;
  S.settings = await api('PUT', 'settings', s); toast('Settings saved'); render();
};
A.editTemplate = el => {
  const all = S.templates, src = el?.dataset?.id ? all.find(t => t.id === el.dataset.id) : { layout: 'classic', accent: '#1f4e79', language: 'de', show_logo: true };
  const custom = (S.d.templates || []).find(t => t.id === src.id), t = custom ? src : { ...src, id: undefined, name: src.name ? src.name + ' (copy)' : '' };
  openForm({
    title: custom ? 'Edit template' : 'New template', vals: t, wide: true,
    note: '<p class="muted small">Placeholders: {customer_name} {contact} {number} {date} {valid_until} {due_date} {company} {owner}</p>',
    fields: [{ k: 'name', l: 'Template name' }, { k: 'layout', l: 'Layout', t: 'select', o: [['classic', 'Classic – colour bar'], ['modern', 'Modern – header band'], ['minimal', 'Minimal – black & white']], row: 1 }, { k: 'accent', l: 'Accent colour', t: 'color', row: 1 }, { k: 'language', l: 'Language', t: 'select', o: [['de', 'Deutsch'], ['en', 'English']], row: 1 },
      { k: 'intro_offer', l: 'Offer – intro', t: 'area' }, { k: 'outro_offer', l: 'Offer – closing', t: 'area', rows: 2 }, { k: 'intro_invoice', l: 'Invoice – intro', t: 'area' }, { k: 'outro_invoice', l: 'Invoice – closing / payment note', t: 'area', rows: 2 }, { k: 'closing', l: 'Sign-off', row: 2 }, { k: 'show_logo', l: 'Show logo', t: 'check', row: 2 }],
    save: async v => { if (!v.name) throw new Error('Name required'); await api.upsert('templates', v); toast('Template saved'); await bootstrap(); render(); },
    del: custom ? async () => { await api('DELETE', 'templates/' + t.id); await bootstrap(); render(); } : null
  });
};
document.addEventListener('click', e => { if (e.target.closest('[data-stop]')) e.stopPropagation(); }, true);
