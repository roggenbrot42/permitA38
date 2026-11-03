/* permitA38 – scanning old offers and invoices (docs/specs/document-scan.md, "Web UI"): upload and drop on the
   Offers & Invoices page, per-file result lines, the "To check" panel and the check dialog. The page preview and the
   layer of detected fields are the shared code of receipts.js (RCPT.startPreview). Everything that comes from a
   document (names, messages, values) is put into the page with esc() or textContent. */
window.DS = (() => {
  const D = { flagged: [], rows: [], lines: [], scan: null };
  const MAX_BYTES = 15 * 1024 * 1024;
  const ACCEPT = '.pdf,.jpg,.jpeg';
  const BASE = 'document-scans';
  const RATES = [19, 7, 0];
  let rowSeq = 0, running = false;

  /* plain-language sentences of the flag codes; an unknown code shows the server's message */
  const FLAG_TEXT = {
    unreadable: 'The document could not be read. Please enter the values from the document by hand.',
    kind_unclear: 'It is not clear whether this is an offer or an invoice. Please choose.',
    kind_unsupported: 'Credit notes and cancellations cannot be added here.',
    no_number: 'No document number was found. Please enter the number printed on it.',
    ambiguous_number: 'Several numbers were found. Please check the number against the document.',
    no_date: 'No document date was found. Please enter it.',
    date_implausible: 'The date looks implausible. Please check it.',
    no_customer: 'No customer was found on the document. Pick an existing customer or enter a new one.',
    customer_ambiguous: 'The customer could not be told apart reliably. Pick an existing customer or check the new one.',
    not_my_document: 'This does not look like a document from you: your company name, name or tax number was not found on it.',
    foreign_currency: 'The document is not in euros. Amounts are not converted: please check them.',
    several_vat_rates: 'The document uses more than one VAT rate. A document here has one rate: pick it and check the lines.',
    vat_rate_unusual: 'The VAT rate is unusual. Please check it.',
    totals_mismatch: 'The lines do not add up to the totals printed on the document.',
    ambiguous_total: 'Several totals were found. Please check the amounts against the document.',
    small_business_conflict: 'Your settings say you are a small business (no VAT), but the document shows VAT. Please check the amounts and your settings.',
    invalid_values: 'Some of the values read from the document are not valid. Please check and correct them.',
  };
  const NOTE_TEXT = {
    added_open: 'The document is added as open (not paid). Mark it as paid afterwards if it was already settled.',
  };
  const noteText = n => NOTE_TEXT[n.code] || n.message || n.code || String(n);
  const flagText = f => FLAG_TEXT[f.code] || f.message || f.code;
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
  const val = id => ($('#' + id)?.value ?? '').trim();
  const numOrNull = x => { const t = String(x ?? '').trim().replace(',', '.'); return t === '' || Number.isNaN(Number(t)) ? null : Number(t); };

  /* ---------- rights and the list of scans that need a check ---------- */
  D.canUpload = () => can('invoices', 'write') && can('offers', 'write') && can('customers', 'write') && can('attachments', 'write');
  D.load = async () => { D.flagged = []; await D.refreshFlagged(); };
  D.refreshFlagged = async () => {
    if (!D.canUpload()) return;
    try { D.flagged = (await api('GET', BASE + '?status=flagged')).items || []; } catch { D.flagged = []; }
  };

  /* ---------- page pieces ---------- */
  D.uploadButton = () => !D.canUpload() ? '' : `<button type="button" data-a="dsPick">Upload old documents</button><input type="file" id="ds_file" data-change="dsFiles" multiple hidden accept="${ACCEPT}">`;
  D.pageTop = () => !D.canUpload() ? '' : `<div class="card ds-zone" id="ds_zone"><span class="small muted">Drop old offers and invoices (PDF or photo) anywhere on this page to add them with their original numbers.</span></div><div id="ds_panel">${panelInner()}</div>`;
  D.flaggedSection = () => {
    if (!D.canUpload() || !D.flagged.length) return '';
    const rows = D.flagged.map(s => {
      const d = s.draft || {};
      const read = [d.number, d.kind === 'offer' ? 'Offer' : d.kind === 'invoice' ? 'Invoice' : '', d.date ? fmtDate(d.date) : '', d.customer?.name].filter(Boolean).map(esc).join(' · ') || '<span class="muted">nothing read</span>';
      const flags = (s.flags || []).map(f => `<span class="pill warn">${esc(flagText(f))}</span>`).join(' ');
      return `<tr class="click" data-a="dsCheck" data-id="${esc(s.id)}"><td>${esc(s.filename || '')}</td><td>${read}</td><td>${flags}</td></tr>`;
    }).join('');
    return `<div class="card" id="ds_flagged"><h2>To check (${D.flagged.length})</h2>
      <div class="small muted">These documents are not in your lists yet. Open one, check it and add it.</div>
      <table><tr><th>File</th><th>What was read</th><th>Check</th></tr>${rows}</table></div>`;
  };

  /* ---------- result lines ---------- */
  function rowHTML(r) {
    const link = r.href ? ` <a href="${esc(r.href)}">${esc(r.linkText)}</a>` : r.scanId && D.flagged.some(s => s.id === r.scanId) ? ` <a href="#/sales" data-a="dsCheck" data-id="${esc(r.scanId)}">Show it under “To check”</a>` : '';
    const dismiss = r.state === 'scanning' ? '' : `<button type="button" class="ghost sm" data-a="dsDismiss" data-i="${r.id}" aria-label="Dismiss: ${esc(r.name)}">✕</button>`;
    return `<div class="rcpt-row" data-state="${esc(r.state)}"><span class="rcpt-name">${esc(r.name)}</span><span class="rcpt-msg">${esc(r.text)}${link}</span>${dismiss}</div>`;
  }
  function panelInner() { return D.rows.length ? `<div class="card rcpt-panel" aria-live="polite">${D.rows.map(rowHTML).join('')}</div>` : ''; }
  function updatePanel() { const p = $('#ds_panel'); if (p) p.innerHTML = panelInner(); }
  A.dsPick = () => { const i = $('#ds_file'); if (i) i.click(); };
  A.dsFiles = e => { const files = Array.from(e.files || []); e.value = ''; D.add(files); };
  A.dsDismiss = e => { D.rows = D.rows.filter(r => String(r.id) !== e.dataset.i); updatePanel(); };
  const extOf = name => { const m = /\.[^.]+$/.exec(String(name)); return m ? m[0].toLowerCase() : ''; };
  const docLink = ex => ex && ex.collection && ex.id ? { href: `#/doc/${ex.collection}/${ex.id}`, linkText: `Show ${ex.number || 'the existing document'}` } : {};
  D.add = files => {
    if (!D.canUpload()) return;
    for (const f of files) {
      const row = { id: ++rowSeq, name: f.name, state: 'scanning', text: 'Waiting…', file: f };
      if (!ACCEPT.split(',').includes(extOf(f.name))) { row.state = 'error'; row.text = `This file type is not accepted (${ACCEPT.split(',').join(' ')}).`; delete row.file; }
      else if (f.size > MAX_BYTES) { row.state = 'error'; row.text = 'The file is larger than 15 MB.'; delete row.file; }
      D.rows.push(row);
    }
    updatePanel();
    if (!running) run();
  };
  async function run() {
    running = true;
    try {
      for (;;) {
        const row = D.rows.find(r => r.file && r.state === 'scanning');
        if (!row) break;
        row.text = 'Scanning…'; updatePanel();
        const fd = new FormData(); fd.append('file', row.file);
        delete row.file;
        try {
          const r = await api('POST', BASE, fd);
          if (r.status === 'added' && r.record) {
            row.state = 'added';
            row.text = `${r.record.number || 'Document'} added to ${r.record.year}`;
            if ((r.scan?.notes || []).some(n => n.code === 'added_open')) row.text += ' as open (not paid)';
            Object.assign(row, docLink(r.record));
          } else {
            row.state = 'check';
            row.text = 'Flagged: ' + ((r.scan?.flags || []).map(flagText).join(' ') || 'please check this document.');
            row.scanId = r.scan?.id || null;
          }
        } catch (err) {
          const code = err.data?.code, ex = err.data?.existing;
          row.state = code === 'duplicate_file' || code === 'duplicate_number' ? 'duplicate' : 'error';
          row.text = err.message;
          if (ex) {
            if (ex.id) Object.assign(row, docLink(ex));
            else row.scanId = ex.scan_id || null;
          }
        }
        updatePanel();
        try { await loadAll(['invoices', 'offers', 'customers']); await D.refreshFlagged(); updatePanel(); } catch { /* the list refreshes on the next visit */ }
        if (/^#\/sales/.test(location.hash) && !$('#modal-root').firstChild) await render();
      }
    } finally { running = false; }
    setTimeout(() => { D.rows = D.rows.filter(r => r.state === 'scanning' || r.state === 'duplicate' || r.state === 'error'); updatePanel(); }, 8000);
  }

  /* ---------- drop target (the whole page) ---------- */
  let overlay = null;
  const hasFiles = e => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
  const dropActive = () => D.canUpload() && /^#\/sales/.test(location.hash) && !!$('#ds_panel') && !$('#modal-root').firstChild;
  function showDrop() {
    if (!overlay) { overlay = el('div', 'Drop your old documents to add them', 'rcpt-drop'); overlay.id = 'ds_drop'; overlay.setAttribute('role', 'status'); document.body.appendChild(overlay); }
    overlay.hidden = false;
  }
  const hideDrop = () => { if (overlay) overlay.hidden = true; };
  document.addEventListener('dragenter', e => { if (hasFiles(e) && dropActive()) { e.preventDefault(); showDrop(); } });
  document.addEventListener('dragover', e => { if (hasFiles(e) && dropActive()) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; showDrop(); } });
  document.addEventListener('dragleave', e => { if (!e.relatedTarget) hideDrop(); });
  document.addEventListener('dragend', hideDrop);
  document.addEventListener('drop', e => {
    hideDrop();
    if (!hasFiles(e) || !dropActive()) return;
    e.preventDefault();
    D.add(Array.from(e.dataTransfer.files));
  });

  /* ---------- totals: computed from the lines (exact, money.js) against the printed ones ---------- */
  const cents = x => Math.round(Number(x) * 100);
  function computed() {
    const rate = numOrNull(val('ds_vat')) ?? 19;
    const lines = D.lines.map(l => ({ qty: numOrNull(l.quantity) ?? 0, price: numOrNull(l.price) ?? 0 }));
    const t = Money.totals({ lines, vat_rate: rate }, {});
    return { net: Number(t.net) / 100, vat: Number(t.vatTotal) / 100, gross: Number(t.gross) / 100 };
  }
  function printed(scan) {
    const d = scan.doc || {};
    const p = k => (d[k] === null || d[k] === undefined || d[k] === '' || Number.isNaN(Number(d[k]))) ? null : Number(d[k]);
    return { net: p('printed_net'), vat: p('printed_vat'), gross: p('printed_total') };
  }
  const differs = scan => { const p = printed(scan); return p.gross !== null && cents(p.gross) !== cents(computed().gross); };
  function paintTotals() {
    const scan = D.scan, c = computed(), p = printed(scan), t = $('#ds_totals');
    if (!t) return;
    const cell = v => v === null ? '–' : eur(v);
    const diff = p.gross === null ? null : (cents(c.gross) - cents(p.gross)) / 100;
    t.innerHTML = `<thead><tr><th scope="col"></th><th scope="col" class="num">Computed from the lines</th><th scope="col" class="num">Printed</th></tr></thead><tbody>
      <tr><th scope="row">Net</th><td class="num" id="ds_c_net">${cell(c.net)}</td><td class="num" id="ds_p_net">${cell(p.net)}</td></tr>
      <tr><th scope="row">VAT</th><td class="num" id="ds_c_vat">${cell(c.vat)}</td><td class="num" id="ds_p_vat">${cell(p.vat)}</td></tr>
      <tr><th scope="row">Gross</th><td class="num" id="ds_c_gross">${cell(c.gross)}</td><td class="num" id="ds_p_gross">${cell(p.gross)}</td></tr>
      <tr><th scope="row">Difference</th><td class="num" id="ds_diff" colspan="2">${diff === null ? 'no printed total' : eur(diff)}</td></tr></tbody>`;
    const on = differs(scan) || !!scan.forceKeep;
    $('#ds_keep').hidden = !on;
    $('#ds_ck_keep').required = on;
  }

  /* ---------- lines ---------- */
  function paintLines() {
    $('#ds_lines').innerHTML = D.lines.map((l, i) => `<tr data-line="${i}">
      <td><input data-f="description" value="${esc(l.description)}" maxlength="2000" aria-label="Line ${i + 1}: description"></td>
      <td><input data-f="quantity" type="number" step="any" inputmode="decimal" value="${esc(l.quantity)}" aria-label="Line ${i + 1}: quantity"></td>
      <td><input data-f="unit" value="${esc(l.unit)}" maxlength="30" aria-label="Line ${i + 1}: unit"></td>
      <td><input data-f="price" type="number" step="any" inputmode="decimal" value="${esc(l.price)}" aria-label="Line ${i + 1}: unit price, net"></td>
      <td><button type="button" class="ghost sm" data-a="dsRemoveLine" data-i="${i}" aria-label="Remove line ${i + 1}" ${D.lines.length < 2 ? 'disabled' : ''}>✕</button></td></tr>`).join('');
    $('#ds_addline').disabled = D.lines.length >= 200;
    paintTotals();
  }
  A.dsAddLine = () => { D.lines.push({ description: '', quantity: 1, unit: '', price: '' }); paintLines(); $(`#ds_lines tr[data-line="${D.lines.length - 1}"] [data-f=description]`).focus(); };
  A.dsRemoveLine = e => { if (D.lines.length < 2) return; D.lines.splice(+e.dataset.i, 1); paintLines(); $('#ds_addline').focus(); };

  /* ---------- the check dialog ---------- */
  A.dsCheck = e => { const s = D.flagged.find(x => x.id === e.dataset.id); if (s) checkDialog(s); else toast('This document was not found', true); };
  const FORM_BOX = { ds_number: ['number'], ds_date: ['date'], ds_due: ['due_date'], ds_valid: ['valid_until'], ds_cust: ['customer'], ds_vat: ['net', 'vat'], ds_kind: ['kind'], ds_from: ['service'], ds_to: ['service'] };
  const boxValue = (scan, b) => {
    if (b.value !== undefined && b.value !== null && b.value !== '') return String(b.value);
    const d = scan.draft || {}, doc = scan.doc || {};
    const v = { number: d.number, date: d.date, due_date: d.due_date, valid_until: d.valid_until, customer: d.customer?.name, net: doc.printed_net, vat: doc.printed_vat, gross: doc.printed_total, kind: KIND_LABEL[d.kind], service: [d.service_from, d.service_to].filter(Boolean).join(' – ') }[b.field];
    if (v === undefined || v === null || v === '') return '';
    return ['net', 'vat', 'gross'].includes(b.field) && !Number.isNaN(Number(v)) ? eur(Number(v)) : String(v);
  };
  const KIND_LABEL = { invoice: 'Invoice', offer: 'Offer' };
  function checkDialog(scan) {
    D.scan = scan;
    const d = scan.draft || {}, c = d.customer || {};
    D.lines = (Array.isArray(d.lines) && d.lines.length ? d.lines : [{ description: '', quantity: 1, unit: '', price: '' }]).map(l => ({ description: l.description ?? '', quantity: l.quantity ?? 1, unit: l.unit ?? '', price: l.price ?? '' }));
    const flags = (scan.flags || []).map(f => `<li>${esc(flagText(f))}</li>`).join('');
    const notes = (scan.notes || []).map(n => `<li>${esc(noteText(n))}</li>`).join('');
    const rate = d.vat_rate ?? 19, rates = RATES.includes(Number(rate)) ? RATES : [...RATES, rate];
    const known = (S.d.customers || []).find(x => x.id === d.customer_id);
    const field = (id, label, value, type = 'text', extra = '') => `<div class="field"><label for="${id}">${esc(label)}</label><input id="${id}" type="${type}" value="${esc(value ?? '')}" ${extra}></div>`;
    modal(`<div class="modal-scroll"><h2>Check document</h2>
      ${flags ? `<div class="banner" id="ds_flags" role="alert"><b>Please check this document</b><ul>${flags}</ul></div>` : ''}
      ${notes ? `<div class="banner info small" id="ds_notes"><ul>${notes}</ul></div>` : ''}
      <div id="ds_err" class="banner bad" role="alert" hidden></div>
      <div id="e_body" class="exp-body two"><div class="ds-fields">
        <div class="row"><div class="field"><label for="ds_kind">Type</label><select id="ds_kind">${!KIND_LABEL[d.kind] ? '<option value="">– choose –</option>' : ''}${Object.entries(KIND_LABEL).map(([k, l]) => `<option value="${k}" ${d.kind === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
          ${field('ds_number', 'Number (as printed)', d.number, 'text', 'maxlength="50"')}${field('ds_date', 'Date', d.date, 'date')}</div>
        <div class="row">${field('ds_due', 'Due date (invoices)', d.due_date, 'date')}${field('ds_valid', 'Valid until (offers)', d.valid_until, 'date')}${field('ds_from', 'Service from', d.service_from, 'date')}${field('ds_to', 'Service to', d.service_to, 'date')}</div>
        <div class="field"><label for="ds_cust">Customer</label><input id="ds_cust" list="ds_cust_list" autocomplete="off" maxlength="200" value="${esc(known ? known.name : c.name)}"><datalist id="ds_cust_list">${(S.d.customers || []).map(x => `<option value="${esc(x.name)}"></option>`).join('')}</datalist>
          <div class="small muted" id="ds_cust_state"></div></div>
        <div id="ds_newcust" class="row" hidden>${field('ds_c_street', 'Street', c.street)}${field('ds_c_zip', 'ZIP', c.zip, 'text', 'maxlength="20"')}${field('ds_c_city', 'City', c.city)}${field('ds_c_country', 'Country', c.country, 'text', 'maxlength="100"')}${field('ds_c_vat', 'VAT ID', c.vat_id, 'text', 'maxlength="30"')}${field('ds_c_email', 'Email', c.email)}</div>
        <div class="field"><label for="ds_vat">VAT rate %</label><select id="ds_vat">${rates.map(r => `<option value="${esc(r)}" ${String(r) === String(rate) ? 'selected' : ''}>${esc(r)} %</option>`).join('')}</select></div>
        <div class="exp-table-wrap"><table class="exp-lines"><thead><tr><th scope="col">Description</th><th scope="col">Qty</th><th scope="col">Unit</th><th scope="col">Unit price (net) €</th><th scope="col"><span class="sr-only">Remove</span></th></tr></thead><tbody id="ds_lines"></tbody></table></div>
        <div class="exp-addrow"><button type="button" class="ghost sm" data-a="dsAddLine" id="ds_addline">+ Add line</button></div>
        <table class="rcpt-checks" id="ds_totals" aria-label="Printed and computed totals"></table>
        <div class="field" id="ds_keep" hidden><label><input type="checkbox" id="ds_ck_keep"> Keep the printed totals although the lines add up to a different amount</label></div>
      </div><div class="exp-side"><div id="e_preview" class="exp-preview" tabindex="0" aria-label="Document preview" hidden></div></div></div></div>
      <div class="actions"><button class="danger" id="ds_discard" style="margin-right:auto">Discard</button><button id="m-cancel">Cancel</button><button class="primary" id="ds_add">Add</button></div>`, true);
    const modalEl = $('#modal-root .modal'), token = MODAL_SEQ;
    modalEl.classList.add('exp-two');
    closeModal.onClose = () => { PV_GEN++; pvRelease(); LN = []; RCPT.closeDialog(); D.scan = null; };
    bindPreview($('#e_preview'));
    $('#m-cancel').onclick = closeModal;
    $('#modal-root').onkeydown = ev => { if (ev.key === 'Escape') closeModal(); };
    paintLines();
    const custState = () => {
      const name = val('ds_cust').toLowerCase(), hit = (S.d.customers || []).find(x => (x.name || '').trim().toLowerCase() === name);
      $('#ds_newcust').hidden = !!hit || !name;
      $('#ds_cust_state').textContent = !name ? 'Pick a customer or type a new name.' : hit ? 'Existing customer.' : 'A new customer is created with this document.';
      return hit || null;
    };
    custState();
    modalEl.addEventListener('input', ev => {
      const t = ev.target, row = t.closest && t.closest('#ds_lines tr[data-line]');
      if (row && t.dataset.f) { D.lines[+row.dataset.line][t.dataset.f] = t.value; paintTotals(); }
      if (t.id === 'ds_cust') custState();
      if (t.id === 'ds_vat') paintTotals();
    });
    modalEl.addEventListener('change', ev => { if (ev.target.id === 'ds_vat') paintTotals(); });
    modalEl.addEventListener('focusin', ev => {
      const id = ev.target.id, boxes = FORM_BOX[id] || (ev.target.closest && ev.target.closest('#ds_lines') ? ['item'] : null);
      RCPT.highlight(boxes);
    });
    $('#ds_discard').onclick = guard(async () => {
      if (!confirm('Discard this document? The stored file is removed.')) return;
      await api('DELETE', BASE + '/' + encodeURIComponent(scan.id));
      toast('Document discarded'); await D.refreshFlagged(); closeModal(); render();
    });
    let busy = false;
    $('#ds_add').onclick = guard(async () => {
      if (busy) return;
      const showErr = msg => { const b = $('#ds_err'); b.textContent = msg; b.hidden = false; };
      $('#ds_err').hidden = true;
      const body = collect(custState());
      if (typeof body === 'string') return showErr(body);
      if ((differs(scan) || scan.forceKeep) && !$('#ds_ck_keep').checked) return showErr('Tick “Keep the printed totals”, or correct the lines so they add up to the printed total.');
      body.accept_totals_mismatch = (differs(scan) || !!scan.forceKeep) && $('#ds_ck_keep').checked;
      busy = true;
      try {
        let r;
        try { r = await api('POST', `${BASE}/${encodeURIComponent(scan.id)}/confirm`, body); }
        catch (err) {
          if (err.status === 409 && err.data?.code === 'needs_acceptance' && (err.data.missing || []).includes('totals_mismatch')) { scan.forceKeep = true; paintTotals(); }
          showErr(err.message); return;
        }
        toast(`${r.record?.number || 'Document'} added to ${r.record?.year ?? ''}`.trim());
        await loadAll(['invoices', 'offers', 'customers']); await D.refreshFlagged();
        if (token === MODAL_SEQ) closeModal();
        render();
      } finally { busy = false; }
    });
    RCPT.startPreview(scan, { base: BASE, boxText: boxValue, unmarked: () => null });
  }
  /** The confirm body from the dialog, or an error sentence (string). */
  function collect(existing) {
    const kind = val('ds_kind'), number = val('ds_number'), date = val('ds_date');
    if (!kind) return 'Choose whether this is an offer or an invoice.';
    if (!number) return 'Enter the document number.';
    if (!date) return 'Enter the document date.';
    if (!val('ds_cust')) return 'Pick a customer or type the name of a new one.';
    const lines = [];
    for (const [i, l] of D.lines.entries()) {
      const q = numOrNull(l.quantity), p = numOrNull(l.price);
      if (!String(l.description).trim()) return `Line ${i + 1}: enter a description.`;
      if (q === null || p === null) return `Line ${i + 1}: enter a quantity and a price.`;
      lines.push({ description: String(l.description).trim(), quantity: q, unit: String(l.unit || '').trim() || null, price: p });
    }
    const body = { kind, number, date, due_date: val('ds_due') || null, valid_until: val('ds_valid') || null, service_from: val('ds_from') || null, service_to: val('ds_to') || null, vat_rate: numOrNull(val('ds_vat')) ?? 19, lines };
    if (existing) body.customer_id = existing.id;
    else {
      const c = { name: val('ds_cust') };
      for (const [k, id] of [['street', 'ds_c_street'], ['zip', 'ds_c_zip'], ['city', 'ds_c_city'], ['country', 'ds_c_country'], ['vat_id', 'ds_c_vat'], ['email', 'ds_c_email']]) if (val(id)) c[k] = val(id);
      body.customer = c;
    }
    return body;
  }

  return D;
})();
