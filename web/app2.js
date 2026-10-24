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
  return `<table><tr><th>No.</th><th>Type</th><th>Customer</th><th>Date</th><th class="num">Total</th><th>Status</th></tr>${rows.sort((a, b) => (b[1].date || '') < (a[1].date || '') ? -1 : 1).map(([coll, d]) => `<tr class="click" data-href="#/doc/${coll}/${d.id}"><td><b>${d.number ? esc(d.number) : '<span class="muted">No number yet</span>'}</b><div class="small muted">${esc(d.title || '')}</div></td><td>${coll === 'offers' ? 'Offer' : 'Invoice'}</td><td>${esc(nameOf('customers', d.customer_id))}</td><td>${fmtDate(d.date)}</td><td class="num">${eur(docTotal(d))}</td><td>${statusPill(coll, d)}</td></tr>`).join('')}</table>`;
}
function statusPill(coll, d) {
  const late = coll === 'invoices' && d.status === 'sent' && d.due_date && d.due_date < todayISO();
  const cls = late ? 'bad' : { paid: 'good', accepted: 'good', sent: 'blue', rejected: 'bad', cancelled: 'bad', draft: 'warn' }[d.status] || '';
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
      SUGGESTED_RATE = suggestedRate(DOC.customer_id, null); DOC.lines[0].price = SUGGESTED_RATE; AUTO_PRICED.add(DOC.lines[0]);
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
  return `<div class="head"><div><a href="#/sales/${coll}" class="small">← ${isInv ? 'Invoices' : 'Offers'}</a><h1>${isInv ? 'Invoice' : 'Offer'} ${esc(d.number || '')} ${d.id ? statusPill(coll, d) : '<span class="pill warn">draft – not saved yet</span>'}</h1></div>
    <div class="row tight" style="flex:none">${d.id ? `<button data-a="docPdf">Preview PDF</button>` : ''}<button class="${d.number ? '' : 'primary'}" data-a="docSave">${d.status === 'draft' ? 'Save draft' : 'Save'}</button>
    ${d.id && !d.number ? `<button class="primary" data-a="docFinalize">Finalize & number</button>` : ''}${d.id && d.number ? `<select style="width:auto" data-change="docStatus"><option value="">Set status…</option>${opt(statuses, '')}</select>` : ''}
    ${d.id && !isInv && d.number && d.status !== 'accepted' ? `<button data-a="docAccept">✓ Accepted → create project</button>` : ''}${d.id && !isInv ? `<button data-a="docToInvoice">→ Invoice</button>` : ''}</div></div>
  ${d.status === 'draft' && !d.number ? `<div class="banner info">This is a <b>draft</b>: it has no number yet, is not counted in revenue or planning, and the PDF is marked as a draft. It gets its ${isInv ? 'invoice' : 'offer'} number when you press <b>Finalize &amp; number</b>.</div>` : ''}
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
// Hourly rate for a new document: project > customer > your default (the same order the time entries use).
const AUTO_PRICED = new WeakSet();   // lines whose price was set by us, not typed by hand
let SUGGESTED_RATE = null;   // set only while editing a new, unsaved document; saved documents are never repriced
function suggestedRate(customerId, projectId) {
  const pick = x => (x === null || x === undefined || x === '' ? null : +x);
  return pick(S.d.projects.find(p => p.id === projectId)?.rate) ?? pick(S.d.customers.find(c => c.id === customerId)?.rate)
    ?? pick(S.me?.rate) ?? pick(S.me?.default_rate) ?? pick(S.settings.default_rate) ?? 0;
}
function repriceNewDoc() {
  if (!DOC || DOC.id || SUGGESTED_RATE === null) return;
  syncDoc();
  const rate = suggestedRate(DOC.customer_id, DOC.project_id);
  let changed = false;   // only lines we priced ourselves are touched, never a price typed by hand
  DOC.lines.forEach(l => { if (AUTO_PRICED.has(l) && rate !== SUGGESTED_RATE) { l.price = rate; changed = true; } });
  SUGGESTED_RATE = rate; if (changed) render();
}
const _origDoc = PAGES.doc;
PAGES.doc = async (...a) => {
  const h = await _origDoc(...a);
  render.after = () => { recalcDoc(); for (const id of ['d_cust', 'd_proj']) $('#' + id)?.addEventListener('change', repriceNewDoc); };
  return h;
};
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
document.addEventListener('input', e => {
  if (!e.target.closest('[data-l],#d_vat')) return;
  if (e.target.dataset?.k === 'price' && DOC?.lines) AUTO_PRICED.delete(DOC.lines[+e.target.dataset.l]);   // typed by hand
  recalcDoc();
});
A.addLine = () => {
  syncDoc();
  const line = { description: '', qty: 1, unit: 'h', price: DOC.id ? S.settings.default_rate || 0 : suggestedRate(DOC.customer_id, DOC.project_id) };
  if (!DOC.id) AUTO_PRICED.add(line);
  DOC.lines.push(line); render();
};
A.delLine = el => { syncDoc(); DOC.lines.splice(+el.dataset.i, 1); render(); };
/** Warnings are response-only; never echo them back into a PUT body. */
function stripWarnings(o) { delete o.sanity_warnings; delete o.planning_warnings; return o; }
async function persistDoc() {
  syncDoc(); const d = DOC;
  if (!d.customer_id) throw new Error('Choose a customer first');
  const { coll, __route, ...body } = d; stripWarnings(body);
  const r = await api.upsert(coll, body); DOC = { coll, ...stripWarnings({ ...r }) };
  await loadAll([coll]); return r;
}
A.docSave = async () => { const wasNew = !DOC.id; const r = await persistDoc(); toast('Saved'); if (wasNew) location.hash = `#/doc/${DOC.coll}/${r.id}`; else render(); };
A.docFinalize = async () => { await persistDoc(); const r = await api('POST', `${DOC.coll}/${DOC.id}/finalize`); DOC = { coll: DOC.coll, ...stripWarnings({ ...r }) }; toast('Finalized as ' + r.number); await loadAll([DOC.coll]); location.hash = `#/doc/${DOC.coll}/${r.id}`; render(); };
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
const EXP_CATS = ['Software & subscriptions', 'Hardware & equipment', 'Office & coworking', 'Travel', 'Transportation', 'Accommodation', 'Material', 'Business meals', 'Marketing', 'Insurance', 'Phone & internet', 'Training', 'Subcontractors', 'Fees & bank', 'Other'];
PAGES.expenses = async () => {
  await loadAll(['expenses', 'projects', 'trips']);   // trips: the bill dialog's trip select
  const y = new Date().getFullYear(), list = S.d.expenses.sort((a, b) => a.date < b.date ? 1 : -1), yr = list.filter(e => (e.date || '').startsWith(y));
  return `<div class="head"><div><h1>Expenses</h1><div class="muted">Bills you pay – general or tied to a project.</div></div><button class="primary" data-a="editExpense">+ Add bill</button></div>
  <div class="grid g3"><div class="card kpi"><div class="l">${y} net expenses</div><div class="v">${eur(yr.reduce((a, e) => a + (e.net || 0) * (e.deductible ?? 100) / 100, 0))}</div></div><div class="card kpi"><div class="l">Input VAT ${y}</div><div class="v">${S.settings.kleinunternehmer ? '–' : eur(yr.reduce((a, e) => a + (e.vat || 0) * (e.vat_deductible ?? e.deductible ?? 100) / 100, 0))}</div></div></div>
  <div class="card">${list.length ? `<table><tr><th>Date</th><th>Vendor</th><th>Category</th><th>Project</th><th class="num">Net</th><th class="num">VAT</th><th></th></tr>${list.map(e => `<tr class="click" data-a="editExpense" data-id="${e.id}"><td class="nowrap">${fmtDate(e.date)}</td><td><b>${esc(e.vendor || '')}</b><div class="small muted">${esc(e.description || '')}</div>${e.lines?.length ? `<div class="small muted">${e.lines.length === 1 ? '1 item' : e.lines.length + ' items'}</div>` : ''}</td><td>${esc(e.category || '')}</td><td>${esc(nameOf('projects', e.project_id) || 'General')}</td><td class="num">${eur(e.net)}</td><td class="num">${eur(e.vat)}</td><td>${e.attachment ? `<a href="${api.url('attachments/' + e.attachment)}" target="_blank" data-stop>📎</a>` : ''}</td></tr>`).join('')}</table>` : '<div class="empty">No expenses yet.</div>'}</div>`;
};
/* ----- itemised bills: lines table, live totals, bill preview (docs/specs/itemised-expenses.md) ----- */
const LINE_RATES = [19, 7, 0], MAX_LINES = 50;
const PREVIEW_IMAGE = ['png', 'jpg', 'jpeg', 'webp', 'gif'];
let LN = [];   // lines of the open dialog, as typed: {description, gross, vat_rate, deductible, vat_deductible, extra}
let PV = null; // bill preview of the open dialog: {kind, name, src, own, zoom, fit, nw}
let DRAG = null;
let PV_GEN = 0;  // bumped when the dialog closes or the chosen file changes: a late load of a stored bill then does nothing
const kindOf = name => { const ext = String(name).split('.').pop().toLowerCase(); return ext === 'pdf' ? 'pdf' : PREVIEW_IMAGE.includes(ext) ? 'image' : 'other'; };
/** MIME type of a previewable file, from its extension (the same rule as kindOf); '' for other files. */
const MIME_BY_EXT = { pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', gif: 'image/gif' };
const mimeFor = name => MIME_BY_EXT[String(name).split('.').pop().toLowerCase()] || '';
const toNum = x => { const t = String(x ?? '').trim().replace(',', '.'); return t === '' ? NaN : Number(t); };
const lineHTML = (l, i) => {
  // a stored rate outside LINE_RATES (e.g. 5.5 or 16 from the API) is offered too, so the select shows what the totals use
  const rates = LINE_RATES.map(String).includes(String(l.vat_rate)) ? LINE_RATES : [...LINE_RATES, l.vat_rate];
  return `<tr data-line="${i}">
  <td><input data-f="description" value="${esc(l.description)}" maxlength="200" aria-label="Line ${i + 1}: description"></td>
  <td><input data-f="gross" type="number" step="0.01" min="0" inputmode="decimal" value="${esc(l.gross)}" aria-label="Line ${i + 1}: amount on the bill, gross EUR"></td>
  <td><select data-f="vat_rate" aria-label="Line ${i + 1}: VAT rate">${rates.map(r => `<option value="${esc(r)}" ${String(r) === String(l.vat_rate) ? 'selected' : ''}>${esc(r)} %</option>`).join('')}</select></td>
  <td><input data-f="deductible" type="number" step="any" min="0" max="100" inputmode="decimal" value="${esc(l.deductible)}" aria-label="Line ${i + 1}: deductible %"></td>
  <td><button type="button" class="ghost sm" data-a="removeLine" data-i="${i}" aria-label="Remove line ${i + 1}" ${LN.length < 2 ? 'disabled' : ''}>✕</button></td></tr>`;
};
function renderLines() {
  $('#e_lines').innerHTML = LN.map(lineHTML).join('');
  $('#e_addline').disabled = LN.length >= MAX_LINES;
  refreshLineTotals();
}
/** Totals and the switch rule. The arithmetic is the server's (money.js mirrors money.split_gross); the server recomputes on save. */
function refreshLineTotals() {
  const many = LN.length > 1;
  $('#e_itemise_hint').hidden = !many;
  $('#f_itemise').disabled = many;
  const t = Money.lineTotals(LN.map(l => [l.gross || 0, l.vat_rate]));
  $('#e_tot_gross').textContent = eur(t.gross);
  $('#e_tot_net').textContent = eur(t.net);
  $('#e_tot_vat').textContent = eur(t.vat);
  $('#e_tot_rates').innerHTML = Object.entries(t.byRate).map(([k, v]) => `<tr><th scope="row">VAT ${esc(k)} %</th><td data-tot-vat="${esc(k)}">${eur(v)}</td></tr>`).join('');
}
function setItemised(on) {
  $('#e_itemised').hidden = !on;
  for (const k of ['gross', 'vat_rate', 'deductible']) $('#f_' + k).closest('.field').hidden = on;
  $('#modal-root .modal').classList.toggle('exp-two', on);
  $('#e_body').classList.toggle('two', on);
  if (on) renderLines();
}
function addLine() {
  if (LN.length >= MAX_LINES) return toast(`At most ${MAX_LINES} lines per bill`, true);
  const cat = $('#f_category')?.value;
  LN.push({ description: '', gross: '', vat_rate: '19', deductible: cat === 'Business meals' ? '70' : '100', vat_deductible: null, extra: {} });
  renderLines();
  $('#e_lines tr[data-line="' + (LN.length - 1) + '"] [data-f=description]').focus();
}
function removeLine(i) {
  if (LN.length < 2) return;
  LN.splice(i, 1);
  renderLines();
  $('#e_addline').focus();
}
/** Preview of the chosen or stored bill. A chosen file is shown through a blob: URL that is revoked when replaced. */
function pvRelease() {
  if (PV && PV.own) URL.revokeObjectURL(PV.src);
  PV = null; DRAG = null;
}
/** Shows a bill. src: the object URL (blob:) of the chosen or loaded file; href: the link to open (default src). */
function pvSet(kind, name, src, own, href = src, note = '') {
  pvRelease();
  const box = $('#e_preview');
  if (!kind) { box.hidden = true; box.innerHTML = ''; return; }
  PV = { kind, name, src, own, zoom: 100, fit: kind === 'image', nw: 0, nh: 0 };
  box.hidden = false;
  box.dataset.kind = kind;
  box.dataset.zoom = '100';
  const link = kind === 'other'
    ? `<a id="e_preview_open" href="${esc(href)}" download="${esc(name)}">Download ${esc(name)}</a>`
    : `<a id="e_preview_open" href="${esc(href)}" target="_blank" rel="noopener">Open in a new tab</a>`;
  // a visible line under every PDF or image: the browser may download a PDF or show it blank, and the page cannot tell
  const help = kind === 'other' ? '' : `<p id="e_preview_help" class="small">Not showing? <a id="e_preview_help_open" href="${esc(href)}" target="_blank" rel="noopener">Open</a> or <a id="e_preview_download" href="${esc(href)}" download="${esc(name)}">download</a> the bill.</p>`;
  if (kind === 'image') {
    box.innerHTML = `<div class="exp-zoombar" role="group" aria-label="Zoom">
        <button type="button" data-a="zoomOut" aria-label="Zoom out">−</button>
        <button type="button" data-a="zoomIn" aria-label="Zoom in">+</button>
        <button type="button" data-a="zoomFit" aria-label="Fit the bill into the pane">Fit</button>
        <button type="button" data-a="zoom100" aria-label="Zoom to 100 %">100 %</button>
        <span id="e_zoom_label" class="small muted" aria-live="polite"></span>${link}</div>
      ${help}
      <div id="e_stage" class="exp-stage"><img id="e_img" alt="${esc(name)}" src="${esc(src)}" draggable="false"></div>`;
    const img = $('#e_img');
    img.addEventListener('load', () => { if (!PV || PV.src !== src) return; PV.nw = img.naturalWidth; PV.nh = img.naturalHeight; if (PV.fit) pvZoomTo(fitZoom(), null, true); else pvApply(); });
  } else if (kind === 'pdf') {
    box.innerHTML = `<div class="exp-zoombar">${link}</div>
      ${help}
      <iframe id="e_pdf" class="exp-pdf" src="${esc(src)}" title="Bill preview: ${esc(name)}"></iframe>
      <p id="e_preview_fallback" class="small muted">If the preview stays blank, this browser cannot show PDFs here. Use “Open in a new tab”.</p>`;
  } else {
    box.innerHTML = `<div class="exp-zoombar">${link}</div><p class="small muted">${esc(note || 'No preview for this file type.')}</p>`;
  }
}
/** The stored bill, fetched with the token (an img or iframe cannot send it) and shown from a blob: URL. */
async function pvStored(name) {
  const gen = PV_GEN, kind = kindOf(name), link = api.url('attachments/' + encodeURIComponent(name));
  if (kind === 'other') return pvSet('other', name, null, false, link);
  let src;
  try {
    const t = api.token.get();
    const r = await fetch(link, { headers: t ? { Authorization: 'Bearer ' + t } : {}, credentials: 'include' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    src = URL.createObjectURL(await r.blob());
  } catch {
    if (gen === PV_GEN) pvSet('other', name, null, false, link, 'The preview could not be loaded. Use the link to open the bill.');
    return;
  }
  if (gen !== PV_GEN) return URL.revokeObjectURL(src);
  pvSet(kind, name, src, true, link);
}
function fitZoom() {
  const stage = $('#e_stage'), img = $('#e_img');
  if (!stage || !img || !PV.nw) return 100;
  return Math.min(100, 100 * Math.min(stage.clientWidth / PV.nw, stage.clientHeight / (PV.nh || 1)));
}
/** Sets the zoom (clamped to 25..800 %) and keeps the point under the pointer (or the centre) where it is. */
function pvZoomTo(z, anchor, fit = false) {
  const box = $('#e_preview'), stage = $('#e_stage'), img = $('#e_img');
  if (!PV || PV.kind !== 'image') return;
  const old = PV.zoom, next = Math.min(800, Math.max(25, z));
  PV.zoom = Math.round(next * 100) / 100;
  PV.fit = fit;
  box.dataset.zoom = String(PV.zoom);
  $('#e_zoom_label').textContent = PV.zoom + ' %';
  if (!img || !PV.nw) return;
  img.style.width = (PV.nw * PV.zoom / 100) + 'px';
  if (stage) {
    // keep the image point under the anchor where it was; a narrower image is centred by its margins (auto)
    const ax = anchor ? anchor.x : stage.clientWidth / 2, ay = anchor ? anchor.y : stage.clientHeight / 2;
    const offset = (w) => Math.max(0, (stage.clientWidth - w) / 2);
    const ow = PV.nw * old / 100, nw = PV.nw * PV.zoom / 100;
    const fx = (stage.scrollLeft + ax - offset(ow)) / ow, fy = (stage.scrollTop + ay) / (PV.nh * old / 100);
    stage.scrollLeft = fx * nw + offset(nw) - ax;
    stage.scrollTop = fy * (PV.nh * PV.zoom / 100) - ay;
  }
}
function pvApply() { if (PV && PV.kind === 'image') pvZoomTo(PV.zoom, null); }
function pvKey(e) {
  if (!PV || PV.kind !== 'image') return;
  const stage = $('#e_stage');
  const step = { '+': () => pvZoomTo(PV.zoom * 1.25), '=': () => pvZoomTo(PV.zoom * 1.25), '-': () => pvZoomTo(PV.zoom / 1.25),
    '0': () => pvZoomTo(fitZoom(), null, true), ArrowLeft: () => stage.scrollBy(-40, 0), ArrowRight: () => stage.scrollBy(40, 0),
    ArrowUp: () => stage.scrollBy(0, -40), ArrowDown: () => stage.scrollBy(0, 40) }[e.key];
  if (step) { e.preventDefault(); step(); }
}
function bindPreview(box) {
  box.addEventListener('keydown', pvKey);
  box.addEventListener('wheel', e => {
    if (!PV || PV.kind !== 'image') return;
    e.preventDefault();
    // proportional to the wheel distance (x1.25 per 100 px, at most 300 px per event); up zooms in, down zooms out
    const dy = Math.max(-300, Math.min(300, e.deltaY * (e.deltaMode === 1 ? 33 : 1)));
    const r = $('#e_stage').getBoundingClientRect();
    pvZoomTo(PV.zoom * Math.pow(1.25, -dy / 100), { x: e.clientX - r.left, y: e.clientY - r.top });
  }, { passive: false });
  // drag to pan (the stage scrolls); pointer events cover mouse, pen and touch
  box.addEventListener('pointerdown', e => {
    const stage = $('#e_stage');
    if (!stage || e.button !== 0 || !stage.contains(e.target)) return;
    DRAG = { id: e.pointerId, x: e.clientX, y: e.clientY, l: stage.scrollLeft, t: stage.scrollTop };
    stage.setPointerCapture(e.pointerId);
  });
  box.addEventListener('pointermove', e => {
    if (!DRAG || DRAG.id !== e.pointerId) return;
    const stage = $('#e_stage');
    stage.scrollLeft = DRAG.l - (e.clientX - DRAG.x);
    stage.scrollTop = DRAG.t - (e.clientY - DRAG.y);
  });
  const end = e => { if (DRAG && DRAG.id === e.pointerId) DRAG = null; };
  box.addEventListener('pointerup', end);
  box.addEventListener('pointercancel', end);
}
/** Buttons of the lines table and of the preview. Handled here so the page-wide [data-a] actions (A.addLine works on offers) never see them. */
function bindExpenseDialog(modalEl) {
  const typed = e => {
    const tr = e.target.closest('#e_lines tr[data-line]'); if (!tr || !e.target.dataset.f) return;
    LN[+tr.dataset.line][e.target.dataset.f] = e.target.value; refreshLineTotals();
  };
  modalEl.addEventListener('input', typed);
  modalEl.addEventListener('change', typed);
  modalEl.addEventListener('click', e => {
    const b = e.target.closest('[data-a]'); if (!b || !modalEl.contains(b)) return;
    const act = b.dataset.a;
    const zoomNow = {
      addLine: () => addLine(), removeLine: () => removeLine(+b.dataset.i),
      zoomIn: () => pvZoomTo(PV.zoom * 1.25), zoomOut: () => pvZoomTo(PV.zoom / 1.25),
      zoomFit: () => pvZoomTo(fitZoom(), null, true), zoom100: () => pvZoomTo(100),
    }[act];
    if (!zoomNow || (act.startsWith('zoom') && !PV)) return;
    e.stopPropagation(); e.preventDefault();
    zoomNow();
  });
}
function lineError(l, i) {
  const n = i + 1, g = toNum(l.gross), d = toNum(l.deductible);
  if (Number.isNaN(g)) return `Line ${n}: enter the amount on the bill`;
  if (g < 0) return `Line ${n}: the amount must not be negative (book refunds as a credit-note invoice)`;
  if (Number.isNaN(d) || d < 0 || d > 100) return `Line ${n}: the deductible share must be between 0 and 100 %`;
  return null;
}
A.editExpense = el => {
  // a new bill can be started from a business trip: data-trip, data-category and data-project preselect those fields
  const e = el?.dataset?.id ? S.d.expenses.find(x => x.id === el.dataset.id) : { date: todayISO(), deductible: 100, vat_rate: 19, category: el?.dataset?.category || EXP_CATS[0], project_id: el?.dataset?.project || '', trip_id: el?.dataset?.trip || '' };
  const cats = e.category && !EXP_CATS.includes(e.category) ? [...EXP_CATS, e.category] : EXP_CATS;   // keep a category entered elsewhere
  const stored = Array.isArray(e.lines) ? e.lines : [];
  // a stored line keeps the fields this dialog does not show (line category, unknown fields); net/vat are derived
  LN = stored.map(l => {
    const { description, gross, vat_rate, deductible, vat_deductible, net, vat, ...extra } = l;
    return { description: description ?? '', gross: String(gross ?? ''), vat_rate: String(vat_rate ?? 19), deductible: String(deductible ?? 100), vat_deductible: vat_deductible ?? null, extra };
  });
  const v0 = { ...e, gross: e.net != null && !stored.length ? Math.round((e.net + (e.vat || 0)) * 100) / 100 : '', vat_rate: e.vat_rate ?? 19, itemise: stored.length > 0 };
  openForm({
    title: e.id ? 'Edit bill' : 'Add bill', vals: v0, wide: true,
    fields: [{ k: 'date', l: 'Paid on', t: 'date', row: 1 }, { k: 'vendor', l: 'Vendor', row: 1 }, { k: 'description', l: 'Description' },
      { k: 'itemise', l: 'Itemise this bill (several lines, e.g. a hotel invoice with different VAT rates)', t: 'check' },
      { k: 'gross', l: 'Amount on the bill (gross) €', t: 'number', row: 2 }, { k: 'vat_rate', l: 'VAT %', t: 'select', o: LINE_RATES, row: 2 },
      { k: 'category', l: 'Category', t: 'select', o: cats, row: 3 }, { k: 'project_id', l: 'Project', t: 'select', o: [['', 'General expense'], ...projectOptions(false)], row: 3 },
      { k: 'trip_id', l: 'Business trip', t: 'select', o: [['', '— no trip —'], ...tripOptions(e.trip_id)], row: 4 },
      { k: 'deductible', l: 'Deductible %', t: 'number', h: '100 normally; 70 for business meals with business partners (set automatically with that category). Input VAT of business meals is reclaimed in full.' }],
    extra: `<div id="e_body" class="exp-body">
      <div id="e_itemised" class="exp-itemised" hidden>
        <div id="e_itemise_hint" class="muted small" hidden>Several lines: remove lines until one is left to switch itemising off.</div>
        <div class="exp-table-wrap"><table class="exp-lines">
          <thead><tr><th scope="col">Description</th><th scope="col">Gross €</th><th scope="col">VAT</th><th scope="col">Deductible</th><th scope="col"><span class="sr-only">Remove</span></th></tr></thead>
          <tbody id="e_lines"></tbody></table></div>
        <div class="exp-addrow"><button type="button" class="ghost sm" data-a="addLine" id="e_addline">+ Add line</button></div>
        <table class="exp-totals" aria-label="Totals of the bill"><tbody>
          <tr><th scope="row">Gross</th><td id="e_tot_gross"></td></tr>
          <tr><th scope="row">Net</th><td id="e_tot_net"></td></tr>
          <tr><th scope="row">VAT</th><td id="e_tot_vat"></td></tr></tbody>
          <tbody id="e_tot_rates"></tbody></table>
      </div>
      <div class="exp-side">
        <div class="field"><label for="e_file">Attach the bill (PDF / photo)</label><input type="file" id="e_file" accept=".pdf,.png,.jpg,.jpeg,.webp,.gif">${e.attachment ? `<div class="small"><a href="${api.url('attachments/' + esc(e.attachment))}" target="_blank" rel="noopener">Current attachment</a></div>` : ''}</div>
        <div id="e_preview" class="exp-preview" tabindex="0" aria-label="Bill preview" hidden></div>
      </div></div>`,
    save: async v => {
      const itemised = !!v.itemise, gross = v.gross;
      delete v.itemise; delete v.gross;
      const bmeal = v.category === 'Business meals';
      v.project_id = v.project_id || null;
      v.trip_id = v.trip_id || null;   // a bill belongs to one business trip (or none)
      v.vat_deductible = bmeal ? 100 : null;   // the 30 % that is not deductible does not touch the input VAT
      if (itemised) {
        if (!LN.length) throw new Error('Add at least one line');
        const bad = LN.map(lineError).find(Boolean); if (bad) throw new Error(bad);
        if (!v.date) throw new Error('Pick the date you paid');
        v.lines = LN.map(l => ({ ...l.extra, description: l.description, gross: toNum(l.gross), vat_rate: Number(l.vat_rate),
          deductible: toNum(l.deductible), vat_deductible: l.vat_deductible ?? (bmeal ? 100 : null) }));
        delete v.net; delete v.vat; delete v.vat_rate;   // derived by the server from the lines
      } else {
        delete v.lines;
        if (!gross) throw new Error('Enter the amount');
        const r = +v.vat_rate || 0;
        if (gross < 0) throw new Error('The amount must not be negative (book refunds as a credit-note invoice)');
        if (!v.date) throw new Error('Pick the date you paid');
        Object.assign(v, Money.splitGross(gross, r)); v.vat_rate = r;
      }
      const f = $('#e_file').files[0];
      if (f) v.attachment = (await api.upload(f)).name;
      await save('expenses', v); render();
    },
    del: e.id ? async () => { await api('DELETE', 'expenses/' + e.id); await loadAll(['expenses']); render(); } : null
  });
  const modalEl = $('#modal-root .modal');
  closeModal.onClose = () => { PV_GEN++; pvRelease(); LN = []; };
  bindExpenseDialog(modalEl);
  bindPreview($('#e_preview'));
  // Business meals are 70 % deductible: choosing the category proposes that, leaving it restores 100 (a hand-typed value stays)
  $('#f_category')?.addEventListener('change', ev => {
    const ded = $('#f_deductible'); if (!ded) return;
    if (ev.target.value === 'Business meals' && +ded.value === 100) ded.value = 70;
    else if (ev.target.value !== 'Business meals' && +ded.value === 70) ded.value = 100;
  });
  $('#f_itemise').addEventListener('change', ev => {
    if (ev.target.checked) {
      LN = [{ description: $('#f_description').value, gross: $('#f_gross').value, vat_rate: $('#f_vat_rate').value,
        deductible: $('#f_deductible').value || '100', vat_deductible: null, extra: {} }];
    } else {
      if (LN.length !== 1) { ev.target.checked = true; return; }
      const l = LN[0];
      $('#f_gross').value = l.gross; $('#f_vat_rate').value = l.vat_rate; $('#f_deductible').value = l.deductible;
      if (l.description) $('#f_description').value = l.description;
      LN = [];
    }
    setItemised(ev.target.checked);
  });
  $('#e_file').addEventListener('change', ev => {
    PV_GEN++;
    const f = ev.target.files[0];
    if (f) {
      // the browser's guessed type is not trusted: a previewed file is typed by its extension (an HTML file named .pdf is not text/html)
      const kind = kindOf(f.name);
      return pvSet(kind, f.name, URL.createObjectURL(kind === 'other' ? f : new Blob([f], { type: mimeFor(f.name) })), true);
    }
    if (e.attachment) pvStored(e.attachment); else pvSet(null);
  });
  if (stored.length) setItemised(true);
  if (e.attachment) pvStored(e.attachment);
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
  return `<div class="head"><div><h1>Team</h1><div class="muted">You (boss) plan; employees track their own time; accountants read the books.</div></div><button class="primary" data-a="editUser">+ Add person</button></div>
  <div class="card"><table><tr><th>Person</th><th>Role</th><th class="num">Weekly h</th><th>This week</th><th class="num">Rate</th><th class="num">Cost/h</th></tr>${dashes.map(([u, d]) => `<tr class="click" data-a="editUser" data-id="${u.id}"><td><span class="dot" style="background:${esc(u.color || '#888')}"></span><b>${esc(u.name)}</b></td><td><span class="pill ${u.role === 'boss' ? 'blue' : ''}">${u.role}</span> ${u.totp_enabled ? `<span class="pill good" title="Two-factor authentication is on">2FA</span> <button class="ghost sm" data-a="resetTotp" data-id="${u.id}">Reset 2FA</button>` : ''}</td><td class="num">${u.weekly_hours || 0}</td><td style="min-width:140px"><div class="bar"><div style="width:${d.target ? Math.min(100, d.worked / d.target * 100) : 0}%"></div></div><span class="small muted">${hm(d.worked)} / ${hm(d.target)}</span></td><td class="num">${u.default_rate ? eur(u.default_rate) : ''}</td><td class="num">${u.cost_rate ? eur(u.cost_rate) : ''}</td></tr>`).join('')}</table></div>
  <div class="hint">🔒 Passwords keep team members out of each other's data inside the app. Because the data is plain files in a synced folder, anyone with access to that folder can read everything – share it only with people you trust.</div>`;
};
A.editUser = el => {
  const u = el?.dataset?.id ? S.d.users.find(x => x.id === el.dataset.id) : { role: 'employee', weekly_hours: 40, color: COLORS[S.d.users.length % COLORS.length], active: true };
  openForm({
    title: u.id ? 'Edit person' : 'Add person', vals: u,
    fields: [{ k: 'name', l: 'Name' }, { k: 'role', l: 'Role', t: 'select', o: [['employee', 'Employee – tracks own time and time off'], ['accountant', 'Accountant – reads the books, enters expenses'], ['boss', 'Boss – plans & sees everything']], row: 1 }, { k: 'color', l: 'Colour', t: 'color', row: 1 },
      { k: 'weekly_hours', l: 'Weekly hours', t: 'number', row: 2 }, { k: 'tracking_start', l: 'Tracking since', t: 'date', row: 2, h: 'Starts the flex-time balance' },
      { k: 'work_start', l: 'Working day from', t: 'time', row: 4, h: 'Calendar busy time counts only inside this window' }, { k: 'work_end', l: 'Working day until', t: 'time', row: 4 },
      { k: 'timezone', l: 'Time zone of the working day', ph: 'Europe/Berlin', h: 'IANA name, empty = Europe/Berlin' },
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
  <div class="card"><h2>Defaults</h2><div class="row">${f('default_rate', 'Default hourly rate €', 'number')}${f('payment_days', 'Payment term (days)', 'number')}${f('offer_valid_days', 'Offers valid (days)', 'number')}${fieldHTML({ k: 'default_template', l: 'Default template', t: 'select', o: S.templates.map(t => [t.id, t.name]) }, s)}</div><p class="small">Document numbers: <a href="#/numbering">Numbering</a> (patterns, next number, preview, history). Bring documents from another tool: <a href="#/import">Import</a>. Copy all data out or into an empty instance: <a href="#/dataexport">Data export and import</a>.</p></div>
  <div class="card"><div class="row" style="align-items:center"><h2 style="margin:0">Document templates</h2><div class="right auto"><button data-a="editTemplate">+ New template</button></div></div><table>${S.templates.map(t => `<tr><td><b>${esc(t.name)}</b> <span class="muted small">${esc(t.layout)} · ${esc(t.language)}</span></td><td><span class="dot" style="background:${esc(t.accent)}"></span></td><td class="right"><button class="sm" data-a="editTemplate" data-id="${t.id}">${tpls.find(x => x.id === t.id) ? 'Edit' : 'Copy & customise'}</button></td></tr>`).join('')}</table></div>
  ${teamVisibilityCard()}
  ${await calendarsCard()}
  <div class="card"><h2>Data & sync</h2><p>Data folder on this device:<br><code>${esc(S.data_dir || '')}</code></p><p class="muted small">Put this folder inside a Syncthing-shared folder and start permitA38 with <code>permita38 --data &lt;that folder&gt;</code> on each device. See the README for details.</p></div>`;
};
A.saveSettings = async () => {
  const s = { ...S.settings };
  const txt = ['company_name', 'owner_name', 'street', 'zip', 'city', 'email', 'phone', 'website', 'tax_number', 'vat_id', 'bank_name', 'iban', 'bic', 'company_type', 'state', 'accounting', 'default_template'];
  const nums = ['hebesatz', 'social_monthly', 'church_tax', 'other_income', 'other_deductions', 'default_rate', 'payment_days', 'offer_valid_days', 'vat_rate'];
  txt.forEach(k => { const e = $('#f_' + k); if (e) s[k] = e.value; });
  nums.forEach(k => { const e = $('#f_' + k); if (e) s[k] = e.value === '' ? 0 : parseFloat(e.value); });
  ['kleinunternehmer', 'splitting'].forEach(k => s[k] = $('#f_' + k).checked);
  if ($('#f_tl_employee')) s.team_timeline_visibility = { employee: $('#f_tl_employee').value, accountant: $('#f_tl_accountant').value };
  const lf = $('#logo_file').files[0];
  if (lf) s.logo = (await api.upload(lf)).name;
  S.settings = await api('PUT', 'settings', s); delete S.settings.sanity_warnings; toast('Settings saved'); render();
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
