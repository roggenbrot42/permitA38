/* permitA38 – Suppliers and subcontractors (docs/contracts/subcontractors.md). Vanilla JS, no build step.
   List, detail, form (with the capacity fields of a subcontractor) and the vendor adoption dialog. The server computes
   every number (open commitments, dependency hint); this file lists, edits and draws. An employee gets the limited
   view {id, name, kind, tags, active} and no controls. */
(() => {
  const KIND = { company: 'Company', person: 'Person', 'person-capacity': 'Subcontractor' };
  const VAT_MODE = {
    domestic: 'Domestic (VAT charged)',
    eu_reverse_charge: 'EU reverse charge (verify)',
    third_country_reverse_charge: 'Third-country reverse charge (verify)',
    small_business: 'Small business (no VAT)',
  };
  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const F = { kind: lsGet('supKind', ''), tag: lsGet('supTag', ''), active: lsGet('supActive', '1') !== '0', q: '' };
  const hh = x => num(x, 2) + ' h';
  const strOrNull = v => { const t = String(v ?? '').trim(); return t === '' ? null : t; };
  const canWrite = () => can('suppliers', 'write');
  const isRC = m => m === 'eu_reverse_charge' || m === 'third_country_reverse_charge';

  /* shared with procurement.js and the expense dialog */
  const SUP = window.SUP = {
    list: [], KIND, VAT_MODE, DAYS, isRC,
    async load() {
      if (!can('suppliers')) { SUP.list = []; return SUP.list; }
      try { SUP.list = await api('GET', 'suppliers'); } catch { SUP.list = []; }   // an older server has no suppliers route
      return SUP.list;
    },
    byId: id => SUP.list.find(s => s.id === id),
    name: id => (SUP.list.find(s => s.id === id) || {}).name || '',
    /** <option>s of a supplier select: active suppliers, plus the selected one even when it is inactive (saving must not reassign it). */
    options(selected, { capacityOnly = false } = {}) {
      const rows = SUP.list.filter(s => (s.active !== false || s.id === selected) && (!capacityOnly || s.kind === 'person-capacity'));
      if (selected && !rows.some(s => s.id === selected)) rows.push({ id: selected, name: 'Unknown supplier', active: false });
      return rows.map(s => [s.id, s.name + (s.active === false ? ' (inactive)' : '')]);
    },
  };

  /* ---------- list ---------- */
  function filterQuery() {
    const q = [];
    if (F.kind) q.push('kind=' + encodeURIComponent(F.kind));
    if (F.active) q.push('active=true');
    if (F.tag) q.push('tag=' + encodeURIComponent(F.tag));
    if (F.q.trim()) q.push('q=' + encodeURIComponent(F.q.trim()));
    return q.length ? '?' + q.join('&') : '';
  }
  const kindPill = k => `<span class="pill ${k === 'person-capacity' ? 'blue' : ''}">${esc(KIND[k] || k)}</span>`;
  const tagPills = tags => (tags || []).map(t => `<span class="pill sup-tag">${esc(t)}</span>`).join(' ');

  async function openByScope() {   // supplier id -> open_net; null when the role cannot read commitments
    if (!can('procurement')) return null;
    try {
      const c = await api('GET', 'finance/commitments');
      return Object.fromEntries((c.by_supplier || []).map(s => [s.supplier_id, s.open_net]));
    } catch { return null; }
  }

  async function listPage() {
    const filtered = !!(F.kind || F.tag || F.q.trim() || F.active);
    const [all, rows, open] = await Promise.all([api('GET', 'suppliers'), filtered ? api('GET', 'suppliers' + filterQuery()) : null, openByScope()]);
    SUP.list = all;
    const list = rows || all;
    const tags = [...new Set(all.flatMap(s => s.tags || []))].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
    if (F.tag && !tags.includes(F.tag)) tags.push(F.tag);
    const limited = scopeOf('suppliers') === 'limited';
    const write = canWrite();
    const boss = can('settings', 'write') && scopeOf('settings') === 'all';
    return `<div class="head"><div><h1>Suppliers</h1><div class="muted">${limited ? 'Companies and subcontractors the business buys from (names only).' : 'Companies, people and subcontractors you buy from.'}</div></div>
      <div class="sup-actions">${write ? '<button class="primary" data-a="supNew">+ New supplier</button>' : ''}${write ? '<button data-a="supAdopt">Link expense vendors</button>' : ''}${boss ? '<a class="btn" href="#/import">Import</a><a class="btn" href="#/dataexport">Export</a>' : ''}</div></div>
    <div class="card sup-filter"><div class="row">
      <div class="auto"><label for="sup_kind">Kind</label><select id="sup_kind" data-change="supKind"><option value="">All kinds</option>${Object.entries(KIND).map(([k, l]) => `<option value="${k}" ${k === F.kind ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
      <div class="auto"><label for="sup_tag">Tag</label><select id="sup_tag" data-change="supTag"><option value="">All tags</option>${tags.map(t => `<option value="${esc(t)}" ${t === F.tag ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></div>
      <div><label for="sup_q">Search</label><input id="sup_q" type="search" value="${esc(F.q)}" placeholder="${limited ? 'name' : 'name, number, contact'}" data-change="supSearch"></div>
      <div class="auto"><label class="sup-check"><input type="checkbox" id="sup_active" data-change="supActive" ${F.active ? 'checked' : ''}> active only</label></div>
    </div></div>
    <div class="card">${list.length ? `<div class="table-wrap"><table id="sup-table"><tr>${limited ? '' : '<th>No.</th>'}<th>Name</th><th>Kind</th><th>Tags</th>${open ? '<th class="num">Open commitments</th>' : ''}</tr>
      ${list.map(s => `<tr class="click" data-href="#/suppliers/${esc(s.id)}" data-id="${esc(s.id)}">${limited ? '' : `<td class="nowrap">${esc(s.supplier_no || '')}</td>`}<td><a href="#/suppliers/${esc(s.id)}"><b>${esc(s.name)}</b></a>${s.active === false ? ' <span class="pill">inactive</span>' : ''}</td><td>${kindPill(s.kind)}</td><td>${tagPills(s.tags)}</td>${open ? `<td class="num">${open[s.id] ? eur(open[s.id]) : '–'}</td>` : ''}</tr>`).join('')}</table></div>`
      : `<div class="empty">${filtered ? 'No supplier matches the filter.' : 'No suppliers yet.'}</div>`}</div>`;
  }
  A.supKind = el => { F.kind = el.value; lsSet('supKind', F.kind); render(); };
  A.supTag = el => { F.tag = el.value; lsSet('supTag', F.tag); render(); };
  A.supSearch = el => { F.q = el.value; render(); };
  A.supActive = el => { F.active = el.checked; lsSet('supActive', F.active ? '1' : '0'); render(); };

  /* ---------- detail ---------- */
  const line = (label, v) => (v == null || v === '' ? '' : `<tr><th scope="row">${esc(label)}</th><td>${v}</td></tr>`);
  function dependencyBox(d) {
    if (!d) return '';
    const text = d.raised
      ? `<b>Dependency hint (verify):</b> ${esc(num(d.average_hours_per_week, 1))} h per week on average for ${esc(String(d.longest_run_weeks))} weeks in a row (${esc(fmtDate(d.from))} – ${esc(fmtDate(d.to))}). The hint starts at ${esc(num(d.threshold_hours_per_week, 0))} h per week for ${esc(String(d.threshold_weeks))} weeks. A long, full-time engagement can point to false self-employment; ask your tax adviser. This is a hint, not legal advice.`
      : `Dependency check (verify): the longest run of weeks with at least ${esc(num(d.threshold_hours_per_week, 0))} h is ${esc(String(d.longest_run_weeks))} week${d.longest_run_weeks === 1 ? '' : 's'}; the hint starts at ${esc(String(d.threshold_weeks))}.`;
    return `<div class="${d.raised ? 'banner' : 'hint'} sup-dependency" role="status" data-raised="${d.raised ? '1' : '0'}">${text}</div>`;
  }
  async function detailPage(id) {
    let s;
    try { s = await api('GET', 'suppliers/' + encodeURIComponent(id)); }
    catch (e) { if (e.status === 404) return `<div class="head"><h1>Supplier</h1></div><div class="card" role="alert">This supplier does not exist (any more). <a href="#/suppliers">Back to the suppliers</a></div>`; throw e; }
    SUP.list = SUP.list.some(x => x.id === s.id) ? SUP.list : [...SUP.list, s];
    const limited = scopeOf('suppliers') === 'limited';
    const write = canWrite();
    const proc = can('procurement');
    const [dep, comm, offers] = await Promise.all([
      proc && s.kind === 'person-capacity' ? api('GET', `suppliers/${encodeURIComponent(id)}/dependency`).catch(() => null) : null,
      proc ? api('GET', 'finance/commitments?supplier_id=' + encodeURIComponent(id)).catch(() => null) : null,
      proc ? api('GET', 'incoming-offers?supplier_id=' + encodeURIComponent(id)).catch(() => null) : null,
    ]);
    const address = [s.street, [s.zip, s.city].filter(Boolean).join(' '), s.country].filter(Boolean).map(esc).join('<br>');
    const master = limited ? '' : `<div class="grid g2"><div class="card"><h2>Master data</h2><table class="sup-facts">
        ${line('Number', esc(s.supplier_no))}${line('Address', address)}${line('Contact', esc(s.contact))}${line('E-mail', s.email ? `<a href="mailto:${esc(s.email)}">${esc(s.email)}</a>` : '')}${line('Phone', esc(s.phone))}
        ${line('VAT ID', esc(s.vat_id))}${line('Tax number', esc(s.tax_number))}${line('VAT treatment', esc(VAT_MODE[s.vat_mode] || s.vat_mode))}${line('Notes', esc(s.notes))}</table></div>
      <div class="card"><h2>Bank and payment</h2><table class="sup-facts">
        ${line('IBAN', esc(s.iban))}${line('BIC', esc(s.bic))}${line('Bank', esc(s.bank_name))}${line('Payment terms', s.payment_terms_days != null ? esc(s.payment_terms_days + ' days') + (s.payment_terms_note ? ' – ' + esc(s.payment_terms_note) : '') : esc(s.payment_terms_note))}</table>
        ${s.iban || s.bic ? '' : '<div class="muted small">No bank data.</div>'}</div></div>`;
    const cap = s.kind === 'person-capacity' && !limited ? `<div class="card"><h2>Capacity</h2><table class="sup-facts">
        ${line('Hours per week', s.weekly_hours != null ? esc(hh(s.weekly_hours)) : '')}${line('Working days', (s.workdays || []).map(d => esc(DAYS[d])).join(', '))}${line('Rate', s.rate != null ? esc(eur(s.rate)) + ' / h' : '')}
        ${line('Not available', (s.unavailable || []).map(u => `${esc(fmtDate(u.from || u.start))} – ${esc(fmtDate(u.to || u.end))}${u.note ? ' (' + esc(u.note) + ')' : ''}`).join('<br>'))}</table></div>` : '';
    const commBlock = comm ? `<div class="card"><h2>Commitments</h2>${comm.items.length ? `<div class="table-wrap"><table><tr><th>Offer</th><th>Project</th><th>Period</th><th>Status</th><th class="num">Planned</th><th class="num">Invoiced</th><th class="num">Open</th></tr>
        ${comm.items.map(i => `<tr><td>${i.incoming_offer_id ? `<a href="#/incoming/${esc(i.incoming_offer_id)}">${esc(i.offer_reference || 'offer')}</a>` : ''}</td><td>${esc(i.project_name || '–')}</td><td class="nowrap">${i.start ? esc(fmtDate(i.start)) + ' – ' + esc(fmtDate(i.end)) : '–'}</td><td><span class="pill">${esc(i.kind === 'flat' ? 'flat' : i.status)}</span></td><td class="num">${eur(i.planned_net)}</td><td class="num">${i.invoiced_net == null ? '–' : eur(i.invoiced_net)}</td><td class="num">${eur(i.open_net)}</td></tr>`).join('')}
        <tr><th>Total</th><td colspan="3"></td><th class="num">${eur(comm.totals.planned_net)}</th><th class="num">${eur(comm.totals.invoiced_net)}</th><th class="num">${eur(comm.totals.open_net)}</th></tr></table></div>`
      : '<div class="empty small">No open commitments.</div>'}</div>` : '';
    const offerBlock = offers ? `<div class="card"><h2>Incoming offers</h2>${offers.length ? `<table><tr><th>Reference</th><th>Received</th><th class="num">Net</th><th>Status</th></tr>
        ${offers.map(o => `<tr class="click" data-href="#/incoming/${esc(o.id)}"><td><a href="#/incoming/${esc(o.id)}">${esc(o.reference || o.title || 'Offer')}</a></td><td class="nowrap">${esc(fmtDate(o.received_on))}</td><td class="num">${eur(o.net_total)}</td><td>${offerPill(o)}</td></tr>`).join('')}</table>` : '<div class="empty small">No incoming offers from this supplier.</div>'}
      ${can('procurement', 'write') && s.active !== false ? `<div style="margin-top:10px"><button data-a="offNew" data-supplier="${esc(s.id)}">+ Record offer</button></div>` : ''}</div>` : '';
    return `<div class="head"><div><h1>${esc(s.name)} ${s.active === false ? '<span class="pill">inactive</span>' : ''}</h1><div class="muted">${kindPill(s.kind)} ${tagPills(s.tags)}</div></div>
      <div class="sup-actions"><a class="btn" href="#/suppliers">← All suppliers</a>${write ? `<button class="primary" data-a="supEdit" data-id="${esc(s.id)}">Edit</button><button class="danger" data-a="supDelete" data-id="${esc(s.id)}">Delete</button>` : ''}</div></div>
    ${limited ? '<div class="banner info" role="status">You see the limited view: name, kind and tags.</div>' : ''}
    ${dependencyBox(dep)}${master}${cap}${commBlock}${offerBlock}`;
  }
  const offerPill = o => { const st = o.effective_status || o.status; return `<span class="pill ${st === 'accepted' ? 'good' : st === 'rejected' ? 'bad' : st === 'expired' ? 'warn' : ''}">${esc(String(st).replace('_', ' '))}</span>`; };
  SUP.offerPill = offerPill;

  PAGES.suppliers = async id => (id ? detailPage(id) : listPage());

  /* ---------- form ---------- */
  const unavailRow = (u = {}, i = 0) => `<div class="row sup-win" data-win="${i}">
      <div><label for="sup_uf${i}">From</label><input id="sup_uf${i}" type="date" value="${esc(u.from || u.start || '')}"></div>
      <div><label for="sup_ut${i}">To</label><input id="sup_ut${i}" type="date" value="${esc(u.to || u.end || '')}"></div>
      <div><label for="sup_un${i}">Note</label><input id="sup_un${i}" type="text" maxlength="200" value="${esc(u.note || '')}"></div>
      <div class="auto"><button type="button" class="ghost sm" data-a="supWinRemove" aria-label="Remove unavailable window ${i + 1}">✕</button></div></div>`;
  function capacityBlock(s) {
    const wd = s.workdays || [0, 1, 2, 3, 4];
    return `<fieldset id="sup_cap" class="sup-cap" ${s.kind === 'person-capacity' ? '' : 'hidden'}><legend>Capacity of the subcontractor</legend>
      <div class="row"><div><label for="sup_weekly">Hours per week they can offer</label><input id="sup_weekly" type="number" step="any" min="0" value="${esc(s.weekly_hours ?? '')}"></div>
      <div><label for="sup_rate">Rate € / h</label><input id="sup_rate" type="number" step="any" min="0" value="${esc(s.rate ?? '')}"></div></div>
      <div class="field"><label>Working days</label><div class="sup-days">${DAYS.map((d, i) => `<label class="sup-check"><input type="checkbox" id="sup_wd${i}" ${wd.includes(i) ? 'checked' : ''}> ${d}</label>`).join('')}</div></div>
      <div class="field"><label>Not available (inclusive)</label><div id="sup_wins">${(s.unavailable || []).map((u, i) => unavailRow(u, i)).join('')}</div>
      <button type="button" class="ghost sm" data-a="supWinAdd">+ Add window</button></div></fieldset>`;
  }
  A.supWinAdd = () => {
    const box = $('#sup_wins'); if (!box) return;
    const n = box.querySelectorAll('.sup-win').length;
    box.insertAdjacentHTML('beforeend', unavailRow({}, n));
    $('#sup_uf' + n)?.focus();
  };
  A.supWinRemove = el => el.closest('.sup-win')?.remove();
  function readCapacity() {
    const num0 = id => { const t = $(id).value.trim().replace(',', '.'); return t === '' ? null : Number(t); };
    const windows = [...document.querySelectorAll('#sup_wins .sup-win')].map(r => {
      const [f, t, n] = r.querySelectorAll('input');
      return { from: f.value, to: t.value, note: strOrNull(n.value) };
    }).filter(w => w.from || w.to);
    for (const w of windows) {
      if (!w.from || !w.to) throw new Error('An unavailable window needs both dates');
      if (w.to < w.from) throw new Error('An unavailable window ends before it starts');
    }
    const weekly = num0('#sup_weekly');
    if (!(weekly > 0)) throw new Error('A subcontractor needs the hours per week they can offer');
    const workdays = DAYS.map((_, i) => i).filter(i => $('#sup_wd' + i).checked);
    if (!workdays.length) throw new Error('Pick at least one working day');
    return { weekly_hours: weekly, rate: num0('#sup_rate'), workdays, unavailable: windows };
  }
  function openSupplierForm(s = {}) {
    const vals = { kind: 'company', active: true, country: 'DE', ...s, tags: (s.tags || []).join(', '), vat_mode: s.vat_mode || '' };
    const fields = [
      { k: 'name', l: 'Name' },
      { k: 'kind', l: 'Kind', t: 'select', o: Object.entries(KIND), row: 1 }, { k: 'supplier_no', l: 'Supplier number', row: 1 },
      { k: 'active', l: 'Active (inactive suppliers get no new offers)', t: 'check' },
      { k: 'street', l: 'Street' },
      { k: 'zip', l: 'ZIP', row: 2 }, { k: 'city', l: 'City', row: 2 }, { k: 'country', l: 'Country (2 letters)', row: 2 },
      { k: 'contact', l: 'Contact person', row: 3 }, { k: 'email', l: 'E-mail', t: 'email', row: 3 }, { k: 'phone', l: 'Phone', row: 3 },
      { k: 'vat_id', l: 'VAT ID', row: 4 }, { k: 'tax_number', l: 'Tax number', row: 4 },
      { k: 'vat_mode', l: 'VAT treatment', t: 'select', o: [['', 'Derived from the country'], ...Object.entries(VAT_MODE)], h: 'Reverse-charge treatments are shown as a note on bills (verify with your tax adviser).' },
      { k: 'tags', l: 'Tags (comma separated)' },
      { k: 'iban', l: 'IBAN', row: 5 }, { k: 'bic', l: 'BIC', row: 5 }, { k: 'bank_name', l: 'Bank', row: 5 },
      { k: 'payment_terms_days', l: 'Payment terms (days)', t: 'number', row: 6 }, { k: 'payment_terms_note', l: 'Payment terms note', row: 6 },
      { k: 'notes', l: 'Notes', t: 'area' },
    ];
    openForm({
      title: s.id ? 'Edit supplier' : 'New supplier', fields, vals, wide: true, extra: capacityBlock(vals),
      save: async v => {
        const body = { ...v };
        for (const k of ['supplier_no', 'street', 'zip', 'city', 'contact', 'email', 'phone', 'vat_id', 'tax_number', 'iban', 'bic', 'bank_name', 'payment_terms_note', 'notes', 'vat_mode']) body[k] = strOrNull(v[k]);
        if (!strOrNull(v.name)) throw new Error('Enter a name');
        body.name = v.name.trim();
        body.country = (strOrNull(v.country) || 'DE').toUpperCase();
        body.tags = String(v.tags || '').split(',').map(t => t.trim()).filter(Boolean);
        body.payment_terms_days = v.payment_terms_days == null || Number.isNaN(v.payment_terms_days) ? null : v.payment_terms_days;
        if (v.kind === 'person-capacity') Object.assign(body, readCapacity());
        else for (const k of ['weekly_hours', 'workdays', 'rate']) delete body[k];
        if (v.kind !== 'person-capacity') body.unavailable = [];
        delete body.sanity_warnings;
        const r = await api.upsert('suppliers', body);
        toast('Supplier saved');
        await SUP.load();
        if (!s.id && r && r.id) location.hash = '#/suppliers/' + r.id; else render();
      },
    });
    $('#f_kind').onchange = ev => { $('#sup_cap').hidden = ev.target.value !== 'person-capacity'; };
  }
  A.supNew = () => openSupplierForm({});
  A.supEdit = async el => openSupplierForm(await api('GET', 'suppliers/' + encodeURIComponent(el.dataset.id)));
  A.supDelete = async el => {
    if (!window.confirm('Delete this supplier? Suppliers that are in use cannot be deleted, archive them instead.')) return;
    await api.del('suppliers/' + encodeURIComponent(el.dataset.id));
    toast('Supplier deleted');
    location.hash = '#/suppliers';
  };

  /* ---------- adoption of expense vendors ---------- */
  const AD = { groups: [], preview: null, busy: false };
  function adoptItems() {
    const items = [];
    AD.groups.forEach((g, i) => {
      if (!$('#ad_use' + i)?.checked) return;
      const sel = $('#ad_sup' + i).value;
      if (sel === '__new__') items.push({ vendors: g.variants, create: { name: ($('#ad_name' + i).value.trim() || g.variants[0]), kind: 'company' } });
      else items.push({ vendors: g.variants, supplier_id: sel });
    });
    return items;
  }
  function adoptHTML() {
    const known = SUP.options(null);
    const pv = AD.preview;
    const resultBox = pv ? `<div class="banner info" role="status" id="ad_result">${pv.dry_run ? `Check: ${esc(String(pv.expenses_changed))} expense${pv.expenses_changed === 1 ? '' : 's'} would be linked, ${esc(String(pv.items.filter(x => x.would_create).length))} supplier${pv.items.filter(x => x.would_create).length === 1 ? '' : 's'} created.` : `Linked ${esc(String(pv.expenses_changed))} expense${pv.expenses_changed === 1 ? '' : 's'}.`}</div>` : '';
    if (!AD.groups.length) return resultBox + '<div class="empty">All expense vendors are linked to a supplier.</div>';
    return `<div class="small muted">Expenses keep their vendor text. Linking only sets the supplier. Check first, then link.</div>
      <div class="table-wrap"><table class="sup-adopt"><tr><th scope="col">Link</th><th scope="col">Vendor</th><th scope="col" class="num">Bills</th><th scope="col">Supplier</th></tr>
      ${AD.groups.map((g, i) => `<tr><td><input type="checkbox" id="ad_use${i}" data-change="supAdoptChange" ${AD.use?.[i] === false ? '' : 'checked'} aria-label="Link ${esc(g.variants.join(', '))}"></td>
        <td>${g.variants.map(v => `<div>${esc(v)}</div>`).join('')}</td><td class="num">${esc(String(g.count))}<div class="small muted">${eur(g.net_total)}</div></td>
        <td><label class="sr-only" for="ad_sup${i}">Supplier for ${esc(g.variants[0])}</label><select id="ad_sup${i}" data-change="supAdoptChange"><option value="__new__" ${g.supplier ? '' : 'selected'}>Create a new supplier</option>${known.map(([id, n]) => `<option value="${esc(id)}" ${g.supplier && g.supplier.id === id ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select>
          <input id="ad_name${i}" type="text" maxlength="200" value="${esc(g.variants[0])}" aria-label="Name of the new supplier for ${esc(g.variants[0])}" data-change="supAdoptChange" ${g.supplier ? 'hidden' : ''}></td></tr>`).join('')}</table></div>
      ${resultBox}`;
  }
  function adoptPaint() {
    $('#ad_body').innerHTML = adoptHTML();
    $('#ad_check').disabled = AD.busy || !AD.groups.length;
    $('#ad_go').disabled = AD.busy || !(AD.preview && AD.preview.dry_run && AD.preview.expenses_changed > 0);
    const a = document.activeElement;   // a button that just became disabled must not keep the focus: Escape would be lost
    if (!a || a === document.body || a.disabled || !$('#modal-root .modal')?.contains(a)) $('#ad_close')?.focus();
  }
  A.supAdoptChange = el => {
    AD.use = AD.groups.map((_, i) => $('#ad_use' + i)?.checked !== false);
    AD.groups.forEach((g, i) => { const sel = $('#ad_sup' + i), nm = $('#ad_name' + i); if (sel && nm) nm.hidden = sel.value !== '__new__'; });
    AD.preview = null;
    $('#ad_go').disabled = true;
    $('#ad_result')?.remove();
  };
  A.supAdopt = async () => {
    const r = await api('GET', 'suppliers/vendor-suggestions');
    await SUP.load();
    Object.assign(AD, { groups: r.groups || [], preview: null, busy: false, use: null });
    modal(`<h2>Link expense vendors to suppliers</h2><div id="ad_body"></div><div class="actions"><button type="button" id="ad_close" data-a="supAdoptClose">Close</button><button type="button" id="ad_check" data-a="supAdoptRun" data-dry="1">Check</button><button type="button" class="primary" id="ad_go" data-a="supAdoptRun" data-dry="0" disabled>Link expenses</button></div>`, true);
    $('#modal-root').onkeydown = e => { if (e.key === 'Escape') A.supAdoptClose(); };   // same as Close: the list refreshes after linking
    adoptPaint();
  };
  A.supAdoptClose = () => { closeModal(); render(); };
  A.supAdoptRun = async el => {
    if (AD.busy) return;
    const dry = el.dataset.dry === '1', items = adoptItems();
    if (!items.length) return toast('Nothing selected', true);
    AD.busy = true; adoptPaint();
    const token = MODAL_SEQ;
    try {
      const r = await api.post('suppliers/adopt-vendors', { dry_run: dry, items });
      if (token !== MODAL_SEQ) return;
      if (!dry) {
        await SUP.load();
        AD.groups = (await api('GET', 'suppliers/vendor-suggestions')).groups || [];
        AD.use = null;
      }
      AD.preview = r;
    } finally { AD.busy = false; if (token === MODAL_SEQ) adoptPaint(); }
  };


  /* ---------- expense dialog: supplier, allocation, reverse charge ---------- */
  const fieldOf = k => $('#f_' + k)?.closest('.field');
  SUP.bindExpense = e => {
    const sel = $('#f_supplier_id'), alloc = $('#f_allocation_id'), vendor = $('#f_vendor'), rc = $('#f_reverse_charge');
    if (!sel) return;
    const rcBox = fieldOf('rc_vat');
    if (rcBox) rcBox.insertAdjacentHTML('beforeend', '<button type="button" class="ghost sm" data-a="supRcPropose">Propose from the VAT % above</button>');
    let seq = 0;
    const sync = async (initial) => {
      const s = SUP.byId(sel.value), mine = ++seq;
      vendor.readOnly = !!s;
      if (s && !initial) vendor.value = s.name;
      const showRc = !!s && (isRC(s.vat_mode) || rc.checked);
      fieldOf('reverse_charge').hidden = !showRc; rcBox.hidden = !showRc || !rc.checked;
      if (s && isRC(s.vat_mode) && !initial && !rc.checked) { rc.checked = true; rcBox.hidden = false; }
      const af = fieldOf('allocation_id');
      af.hidden = true; alloc.innerHTML = '<option value="">— none —</option>';
      if (!s || s.kind !== 'person-capacity' || !can('procurement')) return;
      let c; try { c = await api('GET', 'finance/commitments?supplier_id=' + encodeURIComponent(s.id)); } catch { return; }
      if (mine !== seq) return;
      const items = c.items.filter(i => i.kind === 'allocation');
      const cur = e.allocation_id;
      alloc.innerHTML = '<option value="">— none —</option>' + items.map(i => `<option value="${esc(i.allocation_id)}">${esc((i.offer_reference || 'Allocation') + ' · ' + (i.project_name || '') + ' · ' + fmtDate(i.start) + ' – ' + fmtDate(i.end))}</option>`).join('');
      if (cur && !items.some(i => i.allocation_id === cur)) alloc.insertAdjacentHTML('beforeend', `<option value="${esc(cur)}">(allocation no longer open)</option>`);
      if (cur && sel.value === e.supplier_id) alloc.value = cur;
      af.hidden = !items.length && !cur;
    };
    sel.onchange = () => sync(false);
    rc.onchange = () => sync(true);
    sync(true);
  };
  A.supRcPropose = () => {
    const gross = $('#f_gross')?.value, rate = $('#f_vat_rate')?.value;
    if (!gross) return toast('Enter the amount on the bill first', true);
    $('#f_rc_vat').value = Money.toNumber(Money.vatCents(Money.lineCents(1, gross), rate));
  };

  /* banner on the expenses page: how many bills have a vendor without a supplier */
  SUP.vendorBanner = async () => {
    if (!can('suppliers', 'write')) return '';
    let r;
    try { r = await api('GET', 'suppliers/vendor-suggestions'); } catch { return ''; }
    if (!r || !r.unlinked_expenses) return '';
    return `<div class="banner info sup-vendor-banner" role="status"><b>${esc(String(r.unlinked_expenses))} expense${r.unlinked_expenses === 1 ? ' has' : 's have'} a vendor without a supplier.</b> <button type="button" class="sm" data-a="supAdopt">Review and link…</button></div>`;
  };
})();
