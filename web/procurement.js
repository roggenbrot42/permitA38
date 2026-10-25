/* permitA38 – Incoming offers, accept dialog, allocation cancel and supplier commitments
   (docs/contracts/subcontractors.md). Vanilla JS, no build step. The server computes hours, VAT, planned cost and
   warnings; this file lists, edits and draws what it receives. Accepting is boss only (procurement write). */
(() => {
  const STATUS_LABEL = { received: 'received', under_review: 'under review', accepted: 'accepted', rejected: 'rejected', expired: 'expired' };
  const TABS = [['open', 'Open'], ['accepted', 'Accepted'], ['rejected', 'Rejected'], ['expired', 'Expired']];
  const eff = o => o.effective_status || o.status;
  const inTab = (o, t) => (t === 'open' ? ['received', 'under_review'].includes(eff(o)) : eff(o) === t);
  const hh = x => num(x, 2) + ' h';
  const writeOk = () => can('procurement', 'write');
  const strOrNull = v => { const t = String(v ?? '').trim(); return t === '' ? null : t; };
  const CODE_TEXT = { 'allocation.exceeds_weekly_hours': 'The subcontractor would be allocated more hours in a week than they can offer.' };
  let tab = lsGet('offTab', 'open');
  if (!TABS.some(([k]) => k === tab)) tab = 'open';

  async function ensureLists() {
    await loadAll(['projects', 'contracts']).catch(() => {});
    if (!window.SUP.list.length) await window.SUP.load();
  }
  const projName = id => (S.d.projects || []).find(p => p.id === id)?.name || '';
  const pill = o => window.SUP.offerPill(o);
  const planText = p => (p ? `${fmtDate(p.start)} – ${fmtDate(p.end)}, ${p.hours_per_week != null ? hh(p.hours_per_week) + ' per week' : hh(p.total_hours) + ' in total'}` : '');

  /* ---------- list ---------- */
  async function listPage() {
    const [offers] = await Promise.all([api('GET', 'incoming-offers'), ensureLists()]);
    const rows = offers.filter(o => inTab(o, tab));
    const write = writeOk();
    return `<div class="head"><div><h1>Incoming offers</h1><div class="muted">Offers you received from suppliers and subcontractors. Accepting one books the cost; for a subcontractor it also adds their hours to the project.</div></div>${write ? '<button class="primary" data-a="offNew">+ Record offer</button>' : ''}</div>
    <div class="tabs" role="tablist">${TABS.map(([k, l]) => `<a href="#/incoming" role="tab" aria-selected="${k === tab}" data-a="offTab" data-tab="${k}" class="${k === tab ? 'on' : ''}">${l} (${offers.filter(o => inTab(o, k)).length})</a>`).join('')}</div>
    <div class="card">${rows.length ? `<div class="table-wrap"><table id="off-table"><tr><th>Reference</th><th>Supplier</th><th class="num">Hours</th><th class="num">Net</th><th>Valid to</th><th>Status</th></tr>
      ${rows.map(o => `<tr class="click" data-href="#/incoming/${esc(o.id)}" data-id="${esc(o.id)}"><td><a href="#/incoming/${esc(o.id)}"><b>${esc(o.reference || o.title || 'Offer')}</b></a></td><td>${esc(o.supplier_name || window.SUP.name(o.supplier_id))}</td><td class="num">${o.hours ? esc(hh(o.hours)) : ''}</td><td class="num">${eur(o.net_total)}</td><td class="nowrap">${esc(fmtDate(o.valid_until))}</td><td>${pill(o)}</td></tr>`).join('')}</table></div>` : '<div class="empty">No offers here.</div>'}</div>`;
  }
  A.offTab = el => { tab = el.dataset.tab; lsSet('offTab', tab); location.hash = '#/incoming'; render(); };

  /* ---------- detail ---------- */
  function warnList(list) {
    if (!list || !list.length) return '';
    return `<ul class="proc-warnings small">${list.map(w => `<li data-code="${esc(w.code)}"><span class="pill ${w.severity === 'warning' ? 'warn' : ''}">${esc(w.severity)}</span> ${esc(w.message)}</li>`).join('')}</ul>`;
  }
  function allocBlock(a) {
    if (!a) return '';
    const days = (a.days || []).map(d => `${esc(fmtDate(d.date).slice(0, 5))} ${esc(num(d.hours, 2))} h`).join(' · ');
    return `<div class="card proc-alloc"><h2>Allocation</h2><table class="sup-facts">
      <tr><th scope="row">Project</th><td>${esc(a.project_name || projName(a.project_id))}</td></tr>
      <tr><th scope="row">Period</th><td>${esc(fmtDate(a.start))} – ${esc(fmtDate(a.end))}${a.status === 'cancelled' ? ` <span class="pill warn">cancelled from ${esc(fmtDate(a.cancelled_from))}</span>${a.cancel_reason ? ' ' + esc(a.cancel_reason) : ''}` : ''}</td></tr>
      <tr><th scope="row">Planned hours</th><td>${esc(hh(a.planned_hours))}</td></tr>
      <tr><th scope="row">Planned cost (net)</th><td>${eur(a.planned_net)}</td></tr>
      <tr><th scope="row">Invoiced (net)</th><td>${eur(a.invoiced_net)}</td></tr>
      <tr><th scope="row">Open (net)</th><td>${eur(a.open_net)}</td></tr></table>
      ${days ? `<details><summary class="small">Hours per day (${a.days.length} days)</summary><div class="small">${days}</div></details>` : ''}
      ${writeOk() && a.status === 'active' ? `<div style="margin-top:10px"><button data-a="allocCancel" data-id="${esc(a.id)}" data-start="${esc(a.start)}">Cancel from a date…</button></div>` : ''}</div>`;
  }
  async function detailPage(id) {
    let o;
    try { o = await api('GET', 'incoming-offers/' + encodeURIComponent(id)); }
    catch (e) { if (e.status === 404) return `<div class="head"><h1>Incoming offer</h1></div><div class="card" role="alert">This offer does not exist (any more). <a href="#/incoming">Back to the offers</a></div>`; throw e; }
    await ensureLists();
    const alloc = o.allocation_id ? await api('GET', 'allocations/' + encodeURIComponent(o.allocation_id)).catch(() => null) : null;
    const st = eff(o), w = writeOk();
    const btn = (a, label, cls = '') => `<button class="${cls}" data-a="${a}" data-id="${esc(o.id)}">${label}</button>`;
    const actions = !w ? '' : [
      st === 'received' ? btn('offReview', 'Mark under review') : '',
      ['received', 'under_review', 'expired'].includes(st) && ['received', 'under_review'].includes(o.status) ? btn('offAccept', 'Accept…', 'primary') + btn('offReject', 'Reject…') : '',
      o.status === 'rejected' ? btn('offReopen', 'Reopen') : '',
      o.status === 'accepted' ? btn('offUnaccept', 'Unaccept') : '',
      btn('offEdit', 'Edit'),
      o.status === 'accepted' ? '' : btn('offDelete', 'Delete', 'danger'),
    ].join('');
    return `<div class="head"><div><h1>${esc(o.reference || o.title || 'Incoming offer')} ${pill(o)}</h1><div class="muted">${esc(o.title || '')} · from <a href="#/suppliers/${esc(o.supplier_id)}">${esc(o.supplier_name || window.SUP.name(o.supplier_id))}</a></div></div>
      <div class="sup-actions"><a class="btn" href="#/incoming">← All offers</a>${actions}</div></div>
    ${st === 'expired' ? `<div class="banner" role="status">This offer expired on ${esc(fmtDate(o.valid_until))}. You can still accept it after confirming.</div>` : ''}
    ${o.decision_note ? `<div class="hint">Decision note: ${esc(o.decision_note)}</div>` : ''}
    <div class="grid g2"><div class="card"><h2>Offer</h2><table class="sup-facts">
      <tr><th scope="row">Received</th><td>${esc(fmtDate(o.received_on))}</td></tr><tr><th scope="row">Valid until</th><td>${esc(fmtDate(o.valid_until) || '–')}</td></tr>
      <tr><th scope="row">Project</th><td>${esc(o.project_name || projName(o.project_id) || '–')}</td></tr>
      ${o.plan ? `<tr><th scope="row">Plan</th><td>${esc(planText(o.plan))}</td></tr>` : ''}
      ${o.attachment ? `<tr><th scope="row">Attachment</th><td><a href="${api.url('attachments/' + encodeURIComponent(o.attachment))}" target="_blank" rel="noopener">Open ${esc(o.attachment)}</a></td></tr>` : ''}
      ${o.notes ? `<tr><th scope="row">Notes</th><td>${esc(o.notes)}</td></tr>` : ''}</table></div>
    <div class="card"><h2>Amounts</h2><table class="sup-facts" id="off-amounts">
      <tr><th scope="row">Hours</th><td>${esc(hh(o.hours))}</td></tr><tr><th scope="row">Net</th><td>${eur(o.net_total)}</td></tr>
      <tr><th scope="row">VAT ${esc(String(o.vat_rate))} %</th><td>${eur(o.vat)}</td></tr><tr><th scope="row">Gross</th><td><b>${eur(o.gross_total)}</b></td></tr>
      <tr><th scope="row">VAT treatment</th><td>${esc(window.SUP.VAT_MODE[o.vat_mode] || o.vat_mode)}</td></tr></table></div></div>
    <div class="card"><h2>Lines</h2><table><tr><th>Description</th><th class="num">Quantity</th><th>Unit</th><th class="num">Unit price</th></tr>
      ${(o.lines || []).map(l => `<tr><td>${esc(l.description || '')}</td><td class="num">${esc(num(l.quantity, 2))}</td><td>${l.unit === 'h' ? 'hours' : 'flat'}</td><td class="num">${eur(l.unit_price)}</td></tr>`).join('')}</table></div>
    ${allocBlock(alloc)}`;
  }

  PAGES.incoming = async id => (id ? detailPage(id) : listPage());

  /* ---------- form ---------- */
  let LINES = [];
  const lineRow = (l, i) => `<tr data-line="${i}">
    <td><input data-f="description" value="${esc(l.description)}" maxlength="200" aria-label="Line ${i + 1}: description"></td>
    <td><input data-f="quantity" type="number" step="any" min="0" value="${esc(l.quantity)}" aria-label="Line ${i + 1}: quantity"></td>
    <td><select data-f="unit" aria-label="Line ${i + 1}: unit"><option value="h" ${l.unit === 'h' ? 'selected' : ''}>hours</option><option value="flat" ${l.unit === 'flat' ? 'selected' : ''}>flat</option></select></td>
    <td><input data-f="unit_price" type="number" step="any" min="0" value="${esc(l.unit_price)}" aria-label="Line ${i + 1}: unit price in euro"></td>
    <td><button type="button" class="ghost sm" data-a="offLineRemove" data-i="${i}" aria-label="Remove line ${i + 1}" ${LINES.length < 2 ? 'disabled' : ''}>✕</button></td></tr>`;
  function paintLines() { const b = $('#off_lines'); if (b) b.innerHTML = LINES.map(lineRow).join(''); const a = $('#off_addline'); if (a) a.disabled = LINES.length >= 50; }
  A.offLineAdd = () => { if (LINES.length < 50) { LINES.push({ description: '', quantity: '', unit: 'h', unit_price: '' }); paintLines(); } };
  A.offLineRemove = el => { if (LINES.length > 1) { LINES.splice(+el.dataset.i, 1); paintLines(); } };
  const contractOptions = pid => [['', '— no contract —'], ...(S.d.contracts || []).filter(c => c.project_id === pid).map(c => [c.id, c.title || 'Contract'])];
  const projectChoices = cur => [['', '— no project —'], ...(S.d.projects || []).filter(p => !['done', 'archived'].includes(p.status) || p.id === cur).map(p => [p.id, p.name + (['done', 'archived'].includes(p.status) ? ' (closed)' : '')])];
  const numOrNull = v => { const t = String(v ?? '').trim().replace(',', '.'); return t === '' ? NaN : Number(t); };
  // the fields of IncomingOfferIn: the response keys of the offer (hours, net_total, status, ...) are never sent back
  const OFFER_IN = ['supplier_id', 'reference', 'title', 'received_on', 'valid_until', 'lines', 'vat_rate', 'vat_mode', 'plan', 'project_id', 'contract_id', 'attachment', 'decision_note', 'notes'];

  async function openOfferForm(o = {}, presetSupplier = '') {
    await ensureLists();
    const locked = o.status === 'accepted';
    const p = o.plan || null;
    const vals = { received_on: todayISO(), supplier_id: presetSupplier, vat_mode: '', vat_rate: '', ...o,
      plan_on: !!p, plan_start: p?.start || '', plan_end: p?.end || '', plan_mode: p && p.total_hours != null ? 'total' : 'week', plan_hours: p ? (p.total_hours ?? p.hours_per_week) : '', project_id: o.project_id || '', contract_id: o.contract_id || '' };
    // the VAT treatment the server reports is derived from the supplier: preselect it only when it differs from the supplier's setting
    // (otherwise saving would pin the current treatment; '' keeps the supplier's setting)
    const supMode = window.SUP.byId(vals.supplier_id)?.vat_mode ?? null;
    if (!(o.vat_mode && o.vat_mode !== supMode)) { vals.vat_mode = ''; vals.vat_rate = ''; }
    LINES = (o.lines || [{ description: '', quantity: '', unit: 'h', unit_price: '' }]).map(l => ({ description: l.description ?? '', quantity: String(l.quantity ?? ''), unit: l.unit || 'h', unit_price: String(l.unit_price ?? '') }));
    const fields = locked ? [
      { k: 'notes', l: 'Notes', t: 'area' }, { k: 'decision_note', l: 'Decision note', t: 'area' },
    ] : [
      { k: 'supplier_id', l: 'Supplier', t: 'select', o: window.SUP.options(o.supplier_id || presetSupplier) },
      { k: 'reference', l: 'Reference (their offer number)', row: 1 }, { k: 'title', l: 'Title', row: 1 },
      { k: 'received_on', l: 'Received on', t: 'date', row: 2 }, { k: 'valid_until', l: 'Valid until', t: 'date', row: 2 },
    ];
    const lower = locked ? '' : `<div class="field"><label>Lines</label><div class="exp-table-wrap"><table class="exp-lines"><thead><tr><th scope="col">Description</th><th scope="col">Quantity</th><th scope="col">Unit</th><th scope="col">Unit price €</th><th scope="col"><span class="sr-only">Remove</span></th></tr></thead><tbody id="off_lines"></tbody></table></div>
      <button type="button" class="ghost sm" id="off_addline" data-a="offLineAdd">+ Add line</button><div class="muted small">Net, VAT and gross are computed by the server when you save.</div></div>
      <div class="row"><div><label for="f_vat_mode">VAT treatment</label><select id="f_vat_mode"><option value="">The supplier's</option>${Object.entries(window.SUP.VAT_MODE).map(([k, l]) => `<option value="${k}" ${k === vals.vat_mode ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
      <div><label for="f_vat_rate">VAT rate</label><select id="f_vat_rate"><option value="">Default</option>${[0, 5, 7, 16, 19].map(r => `<option value="${r}" ${String(r) === String(vals.vat_rate) ? 'selected' : ''}>${r} %</option>`).join('')}</select></div></div>
      <div class="field"><label><input type="checkbox" id="off_plan_on" ${vals.plan_on ? 'checked' : ''}> This offer has a capacity plan (hours of a subcontractor over a period)</label></div>
      <fieldset id="off_plan" class="sup-cap" ${vals.plan_on ? '' : 'hidden'}><legend>Plan</legend><div class="row"><div><label for="off_ps">From</label><input id="off_ps" type="date" value="${esc(vals.plan_start)}"></div><div><label for="off_pe">To</label><input id="off_pe" type="date" value="${esc(vals.plan_end)}"></div>
        <div><label for="off_pm">Hours</label><select id="off_pm"><option value="week" ${vals.plan_mode === 'week' ? 'selected' : ''}>per week</option><option value="total" ${vals.plan_mode === 'total' ? 'selected' : ''}>in total</option></select></div>
        <div><label for="off_ph">Amount of hours</label><input id="off_ph" type="number" step="any" min="0" value="${esc(vals.plan_hours)}"></div></div></fieldset>
      <div class="row"><div><label for="f_project_id">Project</label><select id="f_project_id">${projectChoices(vals.project_id).map(([v, l]) => `<option value="${esc(v)}" ${v === vals.project_id ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
      <div><label for="f_contract_id">Contract</label><select id="f_contract_id">${contractOptions(vals.project_id).map(([v, l]) => `<option value="${esc(v)}" ${v === vals.contract_id ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></div></div>
      <div class="field"><label for="off_file">Attach the offer (PDF)</label><input type="file" id="off_file" accept=".pdf,.png,.jpg,.jpeg">${o.attachment ? `<div class="small"><a href="${api.url('attachments/' + encodeURIComponent(o.attachment))}" target="_blank" rel="noopener">Current attachment</a></div>` : ''}</div>
      <div class="field"><label for="f_notes">Notes</label><textarea id="f_notes" rows="2">${esc(vals.notes || '')}</textarea></div>`;
    openForm({
      title: o.id ? 'Edit incoming offer' : 'Record incoming offer', fields, vals, wide: true, extra: lower,
      note: locked ? '<div class="banner info" role="status">An accepted offer can only change its notes, attachment and decision note.</div>' : '',
      save: async v => {
        const body = Object.fromEntries(OFFER_IN.filter(k => o[k] !== undefined).map(k => [k, o[k]]));
        if (locked) {
          body.notes = strOrNull(v.notes); body.decision_note = strOrNull(v.decision_note);
        } else {
          if (!v.supplier_id) throw new Error('Choose a supplier');
          if (!v.received_on) throw new Error('Enter the date you received the offer');
          const lines = LINES.map((l, i) => {
            const q = numOrNull(l.quantity), pr = numOrNull(l.unit_price);
            if (Number.isNaN(q) || q < 0) throw new Error(`Line ${i + 1}: enter the quantity`);
            if (Number.isNaN(pr) || pr < 0) throw new Error(`Line ${i + 1}: enter the unit price`);
            return { description: l.description, quantity: q, unit: l.unit, unit_price: pr };
          });
          Object.assign(body, { supplier_id: v.supplier_id, reference: strOrNull(v.reference), title: strOrNull(v.title), received_on: v.received_on, valid_until: v.valid_until || null, lines,
            vat_mode: $('#f_vat_mode').value || null, vat_rate: $('#f_vat_rate').value === '' ? null : Number($('#f_vat_rate').value),
            project_id: $('#f_project_id').value || null, contract_id: $('#f_contract_id').value || null, notes: strOrNull($('#f_notes').value) });
          if ($('#off_plan_on').checked) {
            const hrs = numOrNull($('#off_ph').value), mode = $('#off_pm').value;
            if (!$('#off_ps').value || !$('#off_pe').value) throw new Error('Enter both plan dates');
            if (!(hrs > 0)) throw new Error('Enter the hours of the plan');
            body.plan = { start: $('#off_ps').value, end: $('#off_pe').value, [mode === 'total' ? 'total_hours' : 'hours_per_week']: hrs };
          } else body.plan = null;
          const f = $('#off_file').files[0];
          if (f) body.attachment = (await api.upload(f)).name;
        }
        const r = o.id ? await api.put('incoming-offers/' + encodeURIComponent(o.id), body) : await api.post('incoming-offers', body);
        toast('Offer saved');
        if (!o.id && r && r.id) location.hash = '#/incoming/' + r.id; else render();
      },
    });
    if (!locked) {
      paintLines();
      const root = $('#modal-root .modal');
      const typed = e => { const tr = e.target.closest('#off_lines tr[data-line]'); if (tr && e.target.dataset.f) LINES[+tr.dataset.line][e.target.dataset.f] = e.target.value; };
      root.addEventListener('input', typed); root.addEventListener('change', typed);
      $('#off_plan_on').onchange = e => { $('#off_plan').hidden = !e.target.checked; };
      $('#f_project_id').onchange = e => { $('#f_contract_id').innerHTML = contractOptions(e.target.value).map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join(''); };
      if (vals.contract_id) $('#f_contract_id').value = vals.contract_id;
    }
  }
  A.offNew = el => openOfferForm({}, el?.dataset?.supplier || '');
  A.offEdit = async el => openOfferForm(await api('GET', 'incoming-offers/' + encodeURIComponent(el.dataset.id)));
  A.offDelete = async el => {
    if (!window.confirm('Delete this incoming offer?')) return;
    await api.del('incoming-offers/' + encodeURIComponent(el.dataset.id));
    toast('Offer deleted'); location.hash = '#/incoming'; render();
  };
  const simple = (path, msg) => async el => { await api.post(`incoming-offers/${encodeURIComponent(el.dataset.id)}/${path}`, undefined); toast(msg); render(); };
  A.offReview = simple('review', 'Marked under review');
  A.offReopen = simple('reopen', 'Offer reopened');
  A.offUnaccept = async el => {
    if (!window.confirm('Unaccept this offer? Its allocation is deleted and the offer returns to review.')) return;
    await simple('unaccept', 'Offer unaccepted')(el);
  };
  A.offReject = async el => {
    const id = el.dataset.id;
    openForm({ title: 'Reject offer', fields: [{ k: 'reason', l: 'Reason (optional)', t: 'area' }], vals: {}, saveLabel: 'Reject',
      save: async v => { await api.post(`incoming-offers/${encodeURIComponent(id)}/reject`, { reason: strOrNull(v.reason) }); toast('Offer rejected'); render(); } });
  };

  /* ---------- accept dialog ---------- */
  const AC = { offer: null, supplier: null, codes: [], error: '', result: null, busy: false };
  function acceptHTML() {
    const o = AC.offer, cap = AC.supplier && AC.supplier.kind === 'person-capacity';
    if (AC.result) {
      const r = AC.result, a = r.allocation, c = r.commitment;
      return `<h2>Offer accepted</h2><div role="status" id="ac_result">
        ${a ? `<table class="sup-facts"><tr><th scope="row">Allocation</th><td>${esc(a.project_name || projName(a.project_id))}, ${esc(fmtDate(a.start))} – ${esc(fmtDate(a.end))}</td></tr><tr><th scope="row">Planned hours</th><td>${esc(hh(a.planned_hours))}</td></tr><tr><th scope="row">Planned cost (net)</th><td>${eur(a.planned_net)}</td></tr><tr><th scope="row">Open (net)</th><td>${eur(a.open_net)}</td></tr></table>` : ''}
        ${c ? `<p>Flat commitment of ${eur(c.planned_net)} (net). No capacity is added to the planner.</p>` : ''}</div>
        ${(r.planning_warnings || []).length ? `<h3>Please check</h3>${warnList(r.planning_warnings)}` : ''}
        <div class="actions"><button type="button" class="primary" id="ac_close" data-a="offAcceptClose">Close</button></div>`;
    }
    const projects = (S.d.projects || []).filter(p => !['done', 'archived'].includes(p.status));
    const pid = AC.pid ?? o.project_id ?? '';
    return `<h2>Accept offer ${esc(o.reference || '')}</h2>
      ${AC.error ? `<div class="banner bad" role="alert" id="ac_error" tabindex="-1">${esc(AC.error)}</div>` : ''}
      <table class="sup-facts"><tr><th scope="row">Supplier</th><td>${esc(o.supplier_name || '')}</td></tr><tr><th scope="row">Hours</th><td>${esc(hh(o.hours))}</td></tr><tr><th scope="row">Net</th><td>${eur(o.net_total)}</td></tr>${o.plan ? `<tr><th scope="row">Plan</th><td>${esc(planText(o.plan))}</td></tr>` : ''}</table>
      <p class="small muted">${cap ? 'The planned hours and cost are calculated by the server when you accept; the result is shown here.' : 'This supplier has no capacity: accepting books a flat commitment of the net amount and does not change the planner.'}</p>
      <div class="row"><div><label for="ac_project">Project${cap ? ' (required)' : ''}</label><select id="ac_project" data-change="offAcceptProject">${cap ? '' : '<option value="">— none —</option>'}${projects.map(p => `<option value="${esc(p.id)}" ${p.id === pid ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></div>
      <div><label for="ac_contract">Contract (optional)</label><select id="ac_contract">${contractOptions(AC.pid ?? pid).map(([v, l]) => `<option value="${esc(v)}" ${v === (AC.cid ?? o.contract_id ?? '') ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></div></div>
      <div class="field"><label for="ac_note">Decision note (optional)</label><textarea id="ac_note" rows="2">${esc(AC.note || '')}</textarea></div>
      ${eff(o) === 'expired' ? `<div class="field"><label><input type="checkbox" id="ac_expired" ${AC.expired ? 'checked' : ''}> The offer expired on ${esc(fmtDate(o.valid_until))}; accept it anyway</label></div>` : ''}
      ${AC.codes.length ? `<fieldset class="sup-cap" id="ac_confirms"><legend>Confirm these warnings</legend>${AC.codes.map((c, i) => `<label class="sup-check"><input type="checkbox" id="ac_code${i}" data-code="${esc(c)}"> ${esc(CODE_TEXT[c] || c)}</label>`).join('<br>')}</fieldset>` : ''}
      <div class="actions"><button type="button" id="ac_cancel" data-a="offAcceptClose">Cancel</button><button type="button" class="primary" id="ac_submit" data-a="offAcceptSubmit" ${AC.busy ? 'disabled' : ''}>Accept</button></div>`;
  }
  function acceptPaint() {
    const box = $('#ac_box'); if (!box) return;
    box.innerHTML = acceptHTML();
    (box.querySelector('#ac_error') || box.querySelector('select,button'))?.focus();
  }
  A.offAccept = async el => {
    const o = await api('GET', 'incoming-offers/' + encodeURIComponent(el.dataset.id));
    await ensureLists();
    const sup = await api('GET', 'suppliers/' + encodeURIComponent(o.supplier_id)).catch(() => null);
    Object.assign(AC, { offer: o, supplier: sup, codes: [], error: '', result: null, busy: false, pid: undefined, cid: undefined, note: '', expired: false });
    modal('<div id="ac_box"></div>', true);
    $('#modal-root').onkeydown = e => { if (e.key === 'Escape') A.offAcceptClose(); };   // same as the Close button: the page refreshes when the offer was accepted
    acceptPaint();
  };
  A.offAcceptProject = el => { AC.pid = el.value; AC.cid = ''; AC.note = $('#ac_note')?.value; AC.expired = $('#ac_expired')?.checked; acceptPaint(); };
  A.offAcceptClose = () => { const done = !!AC.result; closeModal(); if (done) render(); };
  A.offAcceptSubmit = async () => {
    if (AC.busy) return;
    const o = AC.offer, token = MODAL_SEQ;
    const body = { confirm_warnings: AC.codes.filter((_, i) => $('#ac_code' + i)?.checked) };
    if ($('#ac_expired')?.checked) body.confirm_expired = true;
    const pj = $('#ac_project').value, ct = $('#ac_contract').value, note = $('#ac_note').value.trim();
    if (pj) body.project_id = pj;
    if (ct) body.contract_id = ct;
    if (note) body.decision_note = note;
    AC.pid = pj; AC.cid = ct; AC.note = note; AC.expired = !!body.confirm_expired;
    AC.busy = true; AC.error = '';
    try {
      AC.result = await api.post(`incoming-offers/${encodeURIComponent(o.id)}/accept`, body);
      if (typeof A.plnDismiss === 'function') A.plnDismiss();   // the dialog shows the warnings itself: no duplicate banner behind it
      await loadAll(['projects', 'contracts']).catch(() => {});
    } catch (e) {
      AC.error = e.message;
      const codes = e.status === 409 && e.data && Array.isArray(e.data.codes) ? e.data.codes : [];
      AC.codes = codes;
    }
    AC.busy = false;
    if (token === MODAL_SEQ) acceptPaint();
  };

  /* ---------- cancel an external allocation (offer page) ---------- */
  A.allocCancel = el => {
    const id = el.dataset.id, start = el.dataset.start;
    const day = todayISO() > start ? todayISO() : start;
    openForm({ title: 'Cancel allocation', fields: [{ k: 'effective_from', l: 'Effective from (this day and later are released)', t: 'date' }, { k: 'reason', l: 'Reason (optional)' }], vals: { effective_from: day }, saveLabel: 'Cancel allocation',
      save: async v => {
        if (!v.effective_from) throw new Error('Pick the effective date');
        await api.post(`allocations/${encodeURIComponent(id)}/cancel`, { effective_from: v.effective_from, reason: strOrNull(v.reason) });
        toast('Allocation cancelled'); render();
      } });
  };

  /* ---------- commitments report (money page block) ---------- */
  const dismissKey = h => `hintDismissed:${h.code}:${h.details?.customer_id}:${h.details?.to}`;
  A.hintDismiss = el => { lsSet(el.dataset.key, '1'); el.closest('.banner')?.remove(); };
  function hintsHTML(hints) {
    return (hints || []).filter(h => !lsGet(dismissKey(h))).map(h => `<div class="banner info proc-hint" role="status" data-code="${esc(h.code)}">${esc(h.message)} <button type="button" class="ghost sm" data-a="hintDismiss" data-key="${esc(dismissKey(h))}">Dismiss</button></div>`).join('');
  }
  window.procCommitmentsHTML = c => `<div class="card" id="proc-commitments"><h2>Open supplier commitments</h2>${hintsHTML(c.hints)}
    ${c.items.length ? `<div class="table-wrap"><table><tr><th>Supplier</th><th>Offer</th><th>Project</th><th>Period</th><th>Status</th><th class="num">Planned</th><th class="num">Invoiced</th><th class="num">Open</th></tr>
      ${c.items.map(i => `<tr data-kind="${esc(i.kind)}"><td><a href="#/suppliers/${esc(i.supplier_id)}">${esc(i.supplier_name)}</a></td><td>${i.incoming_offer_id ? `<a href="#/incoming/${esc(i.incoming_offer_id)}">${esc(i.offer_reference || 'offer')}</a>` : ''}</td><td>${esc(i.project_name || '–')}</td><td class="nowrap">${i.start ? esc(fmtDate(i.start)) + ' – ' + esc(fmtDate(i.end)) : '–'}</td><td><span class="pill">${esc(i.kind === 'flat' ? 'flat' : i.status)}</span></td><td class="num">${eur(i.planned_net)}</td><td class="num">${i.invoiced_net == null ? '–' : eur(i.invoiced_net)}</td><td class="num">${eur(i.open_net)}</td></tr>`).join('')}
      <tr><th colspan="5">Total (net, as of ${esc(fmtDate(c.as_of))})</th><th class="num">${eur(c.totals.planned_net)}</th><th class="num">${eur(c.totals.invoiced_net)}</th><th class="num">${eur(c.totals.open_net)}</th></tr></table></div>
      <div class="small muted">Commitments are planned costs from accepted offers, not expenses. They do not enter the VAT figures above.</div>` : '<div class="empty small">No open commitments.</div>'}</div>`;
  const moneyPage = PAGES.money;
  PAGES.money = async (...a) => {
    const html = await moneyPage(...a);
    if (!can('procurement')) return html;
    try { return html + window.procCommitmentsHTML(await api('GET', 'finance/commitments')); } catch { return html; }
  };
})();
