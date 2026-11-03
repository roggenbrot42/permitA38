/* permitA38 – receipt scan UI (docs/specs/receipt-scan.md): upload and drop, progress panel, "Needs your check",
   the "Check receipt" dialog (it reuses the bill dialog of app2.js) and the layer of detected fields on the preview.
   Everything that comes from a receipt (vendor, messages, values) is put into the page with esc() or textContent. */
window.RCPT = (() => {
  const R = { caps: null, flagged: [], rows: [], ctx: null };
  const LS_BOXES = 'permita38.receipts.boxes';
  const MAX_BYTES = 15 * 1024 * 1024;
  let rowSeq = 0, running = false;

  /* ---------- fixed texts ---------- */
  const BOX_LABEL = { vendor: 'Vendor', date: 'Date', invoice_no: 'Invoice no.', vat_id: 'VAT ID', subtotal: 'Subtotal', total: 'Total', vat_group: 'VAT', item: 'Item', due_date: 'Due date', paid_date: 'Paid date', tendered: 'Tendered', change: 'Change', net_total: 'Net total', vat_total: 'VAT total', qr: 'QR code' };
  const CHECK_LABEL = { group_sum: 'Net plus VAT of a rate', group_vat: 'VAT of a rate', subtotal: 'Subtotal', groups_total: 'All rates together', item_line: 'Item line', items_group: 'Items of a rate', items_total: 'Items together', due: 'Amount due', tendered: 'Amount tendered', card: 'Card amount', net_total: 'Net of all rates', vat_total: 'VAT of all rates', qr_payment: 'QR code: rates and payment', qr_total: 'QR code: total' };
  // which box a form field of the dialog belongs to (focusing the field highlights the box)
  const FORM_BOX = { f_vendor: ['vendor'], f_date: ['date'], f_invoice_no: ['invoice_no'], f_gross: ['total'], f_paid_on: ['paid_date'], f_vat_rate: ['vat_group'] };
  const num = x => (x === null || x === undefined || x === '' || Number.isNaN(Number(x))) ? null : Number(x);
  const cents = x => Math.round(x * 100);
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };

  /* ---------- capabilities and the list that needs a check ---------- */
  const rightsOk = () => can('expenses', 'write') && can('attachments', 'write');
  R.canUpload = () => !!(R.caps && R.caps.enabled) && rightsOk();
  R.load = async () => {
    R.caps = null; R.flagged = [];
    if (!rightsOk()) return;
    try { R.caps = await api('GET', 'receipts/capabilities'); } catch { R.caps = null; return; }   // switched off or not available
    if (!R.caps || !R.caps.enabled) return;
    await R.refreshFlagged();
  };
  R.refreshFlagged = async () => {
    if (!R.canUpload()) return;
    try { R.flagged = (await api('GET', 'receipts?status=flagged')).items || []; } catch { R.flagged = []; }
  };
  R.isScanned = e => typeof e?.external_id === 'string' && e.external_id.startsWith('scan:');
  R.pills = e => !R.isScanned(e) ? '' : ' <span class="pill blue">scanned</span>' + (e.paid_on ? '' : ' <span class="pill" title="No paid date was found: the bill date counts.">paid date assumed</span>');

  /* ---------- page pieces ---------- */
  R.uploadButton = () => !R.canUpload() ? '' : `<button type="button" data-a="rcptPick">Upload receipts</button><input type="file" id="rcpt_file" data-change="rcptFiles" multiple hidden accept="${esc((R.caps.accepted || []).join(','))}">`;
  const mb = n => (n / 1048576).toFixed(n < 10485760 ? 1 : 0) + ' MB';
  const ocrNote = () => {
    if (R.caps.ocr !== false) return '';
    const offer = isBoss() && R.caps.ocr_install === 'available'
      ? ` <button type="button" class="sm" id="rcpt_ocr_install" data-a="rcptOcrInstall">Download OCR</button>` : '';
    return `<div class="small muted rcpt-note" id="rcpt_ocr_note">Photos are kept and flagged for your check because OCR (text recognition) is not installed on this device.${offer}</div>`;
  };
  R.pageTop = () => !R.canUpload() ? '' : `${ocrNote()}<div id="rcpt_panel">${panelInner()}</div>`;
  A.rcptOcrInstall = async el => {
    const size = Number(R.caps.ocr_download_size) || 0, host = String(R.caps.ocr_download_host || '');
    if (!confirm(`Download the OCR program (${size ? mb(size) : 'size unknown'}) from ${host}? It is installed for this device only and checked against a fixed checksum.`)) return;
    el.disabled = true; el.textContent = 'Downloading…';
    try {
      await api('POST', 'receipts/ocr/install');
      toast('OCR installed');
      await R.load();
      render();
    } catch (e) {
      el.disabled = false; el.textContent = 'Download OCR';
      toast(e.message || 'The OCR download failed', true);
    }
  };
  const amountOf = d => {
    if (!d) return null;
    if (Array.isArray(d.lines) && d.lines.length) return d.lines.reduce((a, l) => a + (num(l.gross) || 0), 0);
    return num(d.gross);
  };
  R.flaggedSection = () => {
    if (!R.canUpload() || !R.flagged.length) return '';
    const rows = R.flagged.map(s => {
      const d = s.draft || {}, amt = amountOf(d);
      const read = [d.vendor, d.date ? fmtDate(d.date) : '', amt !== null ? eur(amt) : ''].filter(Boolean).map(esc).join(' · ') || '<span class="muted">nothing read</span>';
      const flags = (s.flags || []).map(f => `<span class="pill warn">${esc(f.message || f.code)}</span>`).join(' ');
      return `<tr class="click" data-a="rcptCheck" data-id="${esc(s.id)}"><td>${esc(s.filename || '')}</td><td>${read}</td><td>${flags}</td></tr>`;
    }).join('');
    return `<div class="card" id="rcpt_flagged"><h2>Needs your check (${R.flagged.length})</h2>
      <div class="small muted">These bills are not counted in your figures yet. Open one, check it and save it to book it.</div>
      <table><tr><th>File</th><th>What was read</th><th>Check</th></tr>${rows}</table></div>`;
  };

  /* ---------- progress panel ---------- */
  function rowHTML(r) {
    const link = r.expenseId ? ` <a href="#/expenses" data-a="editExpense" data-id="${esc(r.expenseId)}">Show the existing bill</a>`
      : r.scanId ? ` <a href="#/expenses" data-a="rcptCheck" data-id="${esc(r.scanId)}">Show it under “Needs your check”</a>` : '';
    const dismiss = r.state === 'scanning' ? '' : `<button type="button" class="ghost sm" data-a="rcptDismiss" data-i="${r.id}" aria-label="Dismiss: ${esc(r.name)}">✕</button>`;
    return `<div class="rcpt-row" data-state="${esc(r.state)}"><span class="rcpt-name">${esc(r.name)}</span><span class="rcpt-msg">${esc(r.text)}${link}</span>${dismiss}</div>`;
  }
  function panelInner() {
    return R.rows.length ? `<div class="card rcpt-panel" aria-live="polite">${R.rows.map(rowHTML).join('')}</div>` : '';
  }
  function updatePanel() { const p = $('#rcpt_panel'); if (p) p.innerHTML = panelInner(); }
  A.rcptPick = () => { const i = $('#rcpt_file'); if (i) i.click(); };
  A.rcptFiles = el => { const files = Array.from(el.files || []); el.value = ''; R.add(files); };
  A.rcptDismiss = el => { R.rows = R.rows.filter(r => String(r.id) !== el.dataset.i); updatePanel(); };

  const extOf = name => { const m = /\.[^.]+$/.exec(String(name)); return m ? m[0].toLowerCase() : ''; };
  R.add = files => {
    if (!R.canUpload()) return;
    for (const f of files) {
      const row = { id: ++rowSeq, name: f.name, state: 'scanning', text: 'Waiting…', file: f };
      const accepted = R.caps.accepted || [];
      if (accepted.length && !accepted.includes(extOf(f.name))) { row.state = 'error'; row.text = `This file type is not accepted (${accepted.join(' ')}).`; delete row.file; }
      else if (f.size > MAX_BYTES) { row.state = 'error'; row.text = 'The file is larger than 15 MB.'; delete row.file; }
      R.rows.push(row);
    }
    updatePanel();
    if (!running) run();
  };
  const money = e => {
    const g = Array.isArray(e.lines) && e.lines.length ? e.lines.reduce((a, l) => a + (num(l.gross) || 0), 0) : (e.net || 0) + (e.vat || 0);
    return eur(g);
  };
  async function run() {
    running = true;
    try {
      for (;;) {
        const row = R.rows.find(r => r.file && r.state === 'scanning');
        if (!row) break;
        row.text = 'Scanning…'; updatePanel();
        const fd = new FormData(); fd.append('file', row.file);
        delete row.file;
        try {
          const r = await api('POST', 'receipts', fd);
          if (r.outcome === 'booked' && r.expense) { row.state = 'added'; row.text = `Added: ${r.expense.vendor || 'bill'}, ${money(r.expense)}`; }
          else { row.state = 'check'; row.text = 'Check needed: ' + ((r.scan?.flags || []).map(f => f.message || f.code).join(' ') || 'please check this bill.'); }
        } catch (err) {
          row.state = err.data?.code === 'duplicate' ? 'duplicate' : 'error';
          row.text = err.message;
          if (row.state === 'duplicate') { row.expenseId = err.data.expense_id || null; row.scanId = row.expenseId ? null : (err.data.scan_id || null); }
        }
        updatePanel();
        try { await loadAll(['expenses']); await R.refreshFlagged(); } catch { /* the list refreshes on the next visit */ }
        if (/^#\/expenses/.test(location.hash) && !$('#modal-root').firstChild) await render();
      }
    } finally { running = false; }
    setTimeout(() => { R.rows = R.rows.filter(r => r.state === 'scanning' || r.state === 'duplicate' || r.state === 'error'); updatePanel(); }, 8000);
  }

  /* ---------- drop target ---------- */
  let overlay = null;
  const hasFiles = e => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
  const dropActive = () => R.canUpload() && /^#\/expenses/.test(location.hash) && !!$('#rcpt_panel') && !$('#modal-root').firstChild;
  function showDrop() {
    if (!overlay) { overlay = el('div', 'Drop your receipts to upload them', 'rcpt-drop'); overlay.id = 'rcpt_drop'; overlay.setAttribute('role', 'status'); document.body.appendChild(overlay); }
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
    R.add(Array.from(e.dataTransfer.files));
  });

  /* ---------- the "Check receipt" dialog ---------- */
  A.rcptCheck = el => { const s = R.flagged.find(x => x.id === el.dataset.id); if (s) expenseDialog(R.draftToExpense(s), s); else toast('This receipt was not found', true); };
  /** The draft as a bill for the dialog; values may be null. draft.lines means an itemised bill. */
  R.draftToExpense = scan => {
    const d = scan.draft || {}, cat = d.category || 'Other', ded = cat === 'Business meals' ? 70 : 100;
    const lines = Array.isArray(d.lines) && d.lines.length ? d.lines.map(l => ({ ...l, deductible: l.deductible ?? ded })) : undefined;
    return { date: d.date || '', paid_on: d.paid_on || '', invoice_no: d.invoice_no || '', vendor: d.vendor || '', description: d.description || '', category: cat, deductible: ded,
      vat_rate: d.vat_rate ?? 19, gross: lines ? '' : (d.gross ?? ''), lines, project_id: '', trip_id: '', trip_item_id: '' };
  };
  R.dialogTop = scan => {
    const flags = (scan.flags || []).map(f => `<li>${esc(f.message || f.code)}</li>`).join('');
    const checks = (scan.doc?.checks || []).map(c => `<tr class="${c.ok ? '' : 'rcpt-failed'}"><td>${esc(CHECK_LABEL[c.name] || String(c.name).replace(/_/g, ' '))}</td><td class="num">${esc(c.expected ?? '')}</td><td class="num">${esc(c.found ?? '')}</td><td>${c.ok ? 'OK' : '<b>Does not match</b>'}</td></tr>`).join('');
    return `${flags ? `<div class="banner" id="rcpt_flags" role="alert"><b>Please check this bill</b><ul>${flags}</ul></div>` : ''}
      ${checks ? `<table class="rcpt-checks" id="rcpt_checks"><caption class="small muted">Checks: calculated from the bill against what is printed on it</caption><thead><tr><th scope="col">Check</th><th scope="col" class="num">Calculated</th><th scope="col" class="num">Printed</th><th scope="col">Result</th></tr></thead><tbody>${checks}</tbody></table>` : ''}
      <div id="rcpt_err" class="banner bad" role="alert" hidden></div>`;
  };
  R.dialogAccept = scan => {
    const pt = num(scan.doc?.printed_total);
    return `<div class="rcpt-accept">
      <div class="field" id="rcpt_acc_no_invoice_number" hidden><label><input type="checkbox" id="rcpt_ck_no_invoice_number"> This bill has no invoice number</label></div>
      <div class="field" id="rcpt_acc_total_differs" hidden><label><input type="checkbox" id="rcpt_ck_total_differs"> Book this although it differs from the printed total of ${pt === null ? '' : esc(eur(pt))}</label></div></div>`;
  };
  function currentGross() {
    if ($('#e_itemised') && !$('#e_itemised').hidden) return Money.lineTotals(LN.map(l => [toNum(l.gross) || 0, l.vat_rate])).gross;
    const g = toNum($('#f_gross')?.value);
    return Number.isNaN(g) ? null : g;
  }
  const forced = new Set();
  function refreshAccept(scan) {
    const g = currentGross(), pt = num(scan.doc?.printed_total);
    const noInv = (forced.has('no_invoice_number') || (g !== null && g > 250)) && !($('#f_invoice_no')?.value || '').trim();
    const differs = forced.has('total_differs') || (pt !== null && g !== null && cents(g) !== cents(pt));
    for (const [code, on] of [['no_invoice_number', noInv], ['total_differs', differs]]) {
      const box = $('#rcpt_acc_' + code); if (!box) continue;
      box.hidden = !on;
      $('#rcpt_ck_' + code).required = on;
    }
  }
  const showErr = msg => { const b = $('#rcpt_err'); if (b) { b.textContent = msg; b.hidden = false; } };
  R.confirm = async (scan, v) => {
    refreshAccept(scan);
    const accepted = [];
    for (const [code, text] of [['no_invoice_number', 'Tick “This bill has no invoice number”, or enter the invoice number.'], ['total_differs', 'Tick “Book this although it differs from the printed total”, or correct the amount.']]) {
      if ($('#rcpt_acc_' + code).hidden) continue;
      if (!$('#rcpt_ck_' + code).checked) { showErr(text); throw new Error(text); }
      accepted.push(code);
    }
    try { $('#rcpt_err').hidden = true; await api('POST', `receipts/${encodeURIComponent(scan.id)}/confirm`, { ...v, accepted }); }
    catch (err) {
      if (err.status === 409 && err.data?.code === 'needs_acceptance') { for (const m of err.data.missing || []) forced.add(m); refreshAccept(scan); showErr(err.message); }
      else if (err.status === 409) showErr(err.message);
      throw err;
    }
    toast('Saved');
    await loadAll(['expenses']); await R.refreshFlagged(); render();
  };
  R.bindDialog = (modalEl, scan) => {
    forced.clear();
    modalEl.addEventListener('focusin', ev => {
      const id = ev.target.id, boxes = FORM_BOX[id] || (ev.target.closest && ev.target.closest('#e_lines') ? ['item'] : null);
      highlight(boxes);
    });
    if (!scan) return;
    const onChange = () => refreshAccept(scan);
    modalEl.addEventListener('input', onChange); modalEl.addEventListener('change', onChange);
    modalEl.addEventListener('click', onChange);
    refreshAccept(scan);
    const discard = el('button', 'Discard receipt', 'danger'); discard.type = 'button'; discard.id = 'rcpt_discard'; discard.style.marginRight = 'auto';
    discard.onclick = guard(async () => {
      if (!confirm('Discard this receipt? The stored file is removed.')) return;
      await api('DELETE', 'receipts/' + encodeURIComponent(scan.id));
      toast('Receipt discarded'); await R.refreshFlagged(); closeModal(); render();
    });
    $('#m-cancel').parentElement.prepend(discard);
    // a flagged photo may be one of several: "Add another photo" (up to 5, JPEG)
    const photo = /\.(jpe?g|pdf)$/i.test(scan.attachment || '') && (/\.jpe?g$/i.test(scan.attachment || '') || (scan.doc?.part_hashes || []).length > 0);
    if (photo && R.canUpload() && (scan.doc?.part_hashes || []).length < 5) {
      const input = document.createElement('input'); input.type = 'file'; input.accept = '.jpg,.jpeg,image/jpeg'; input.hidden = true; input.id = 'rcpt_part_file';
      const add = el('button', 'Add another photo'); add.type = 'button'; add.id = 'rcpt_add_photo';
      add.onclick = () => input.click();
      input.onchange = guard(async () => {
        const f = input.files && input.files[0]; input.value = '';
        if (!f) return;
        if (f.size > MAX_BYTES) { toast('The file is larger than 15 MB.', true); return; }
        const fd = new FormData(); fd.append('file', f);
        let r;
        try { r = await api('POST', 'receipts/' + encodeURIComponent(scan.id) + '/parts', fd); }
        catch (err) { toast(err.message, true); return; }
        await loadAll(['expenses']); await R.refreshFlagged(); closeModal();
        if (r.outcome === 'booked') { toast('Added: the receipt is complete'); render(); return; }
        toast('Photo added. This receipt still needs a check.');
        const next = R.flagged.find(x => x.id === scan.id);
        if (next) expenseDialog(R.draftToExpense(next), next); else render();
      });
      discard.after(add, input);
    }
  };
  R.closeDialog = () => { R.ctx = null; };

  /* ---------- preview and layer of detected fields ---------- */
  const showPref = dflt => { try { const v = localStorage.getItem(LS_BOXES); if (v === '1') return true; if (v === '0') return false; } catch { /* storage unavailable */ } return dflt; };
  const savePref = on => { try { localStorage.setItem(LS_BOXES, on ? '1' : '0'); } catch { /* storage unavailable */ } };
  async function fetchBlob(path) {
    try {
      const t = api.token.get();
      const r = await fetch(api.url(path), { headers: t ? { Authorization: 'Bearer ' + t } : {}, credentials: 'include' });
      if (!r.ok) return null;
      return await r.blob();
    } catch { return null; }
  }
  const startCtx = (scan, dflt) => { R.ctx = { scan, page: 0, show: showPref(dflt) }; R.showPage(0); };
  R.openPreview = scan => startCtx(scan, true);   // "Check receipt": detected fields on by default
  R.openBooked = async e => {
    const gen = PV_GEN, id = e.external_id.slice(5);
    let scan = null;
    try { scan = await api('GET', 'receipts/' + encodeURIComponent(id)); } catch { scan = null; }
    if (gen !== PV_GEN) return;
    if (!scan) { if (e.attachment) pvStored(e.attachment); return; }   // behaves like a normal bill
    startCtx({ ...scan, attachment: scan.attachment || e.attachment }, false);
  };
  R.showPage = async n => {
    const c = R.ctx; if (!c) return;
    const gen = PV_GEN, s = c.scan, name = s.attachment || '', pages = Number(s.doc?.pages) || 0;
    if ((kindOf(name) === 'pdf' || s.doc?.rotation !== undefined) && pages > 0) {
      const blob = await fetchBlob(`receipts/${encodeURIComponent(s.id)}/pages/${n}`);
      if (gen !== PV_GEN || R.ctx !== c) return;
      if (blob) { c.page = n; c.pdf = true; return pvSet('image', `${s.filename || name} – page ${n + 1}`, URL.createObjectURL(blob), true, api.url('attachments/' + encodeURIComponent(name))); }
      c.pdf = false;   // no page image: the plain PDF preview, without a layer
    }
    c.page = 0;
    if (name) pvStored(name); else pvSet(null);
  };
  A.rcptPage = el => R.showPage(+el.dataset.n);
  A.rcptToggle = () => {
    const c = R.ctx; if (!c) return;
    c.show = !c.show; savePref(c.show);
    const layer = $('#e_layer'), t = $('#rcpt_toggle');
    if (layer) layer.hidden = !c.show;
    if (t) t.setAttribute('aria-pressed', String(c.show));
  };
  const valueOf = (scan, b) => {
    if (b.value !== undefined && b.value !== null && b.value !== '') return String(b.value);
    const d = scan.draft || {}, doc = scan.doc || {};
    const v = { vendor: d.vendor, date: d.date, invoice_no: d.invoice_no, vat_id: doc.vat_id, total: doc.printed_total ?? d.gross, due_date: doc.due_date, paid_date: d.paid_on }[b.field];
    if (v === undefined || v === null || v === '') return '';
    return b.field === 'total' && num(v) !== null ? eur(num(v)) : String(v);
  };
  const pct = x => (Math.min(1, Math.max(0, Number(x) || 0)) * 100).toFixed(3) + '%';
  function buildLayer(c) {
    const layer = $('#e_layer'); if (!layer) return;
    layer.replaceChildren(); layer.hidden = !c.show;
    for (const b of c.scan.doc?.boxes || []) {
      if ((b.page || 0) !== c.page || !BOX_LABEL[b.field]) continue;
      const failed = b.check === 'failed', value = valueOf(c.scan, b);
      const box = el('button', undefined, 'rcpt-box ' + (failed ? 'failed' : b.check === 'ok' ? 'ok' : 'none'));
      box.type = 'button'; box.dataset.field = b.field;
      box.style.left = pct(b.x); box.style.top = pct(b.y); box.style.width = pct(b.w); box.style.height = pct(b.h);
      const label = BOX_LABEL[b.field] + (failed ? ' – check failed' : '');
      const lab = el('span', undefined, 'rcpt-lab' + (b.y < 0.05 ? ' below' : ''));
      lab.appendChild(el('span', label, 'rcpt-name'));
      if (value) lab.appendChild(el('span', ': ' + value, 'rcpt-tip'));
      box.appendChild(lab);
      box.setAttribute('aria-label', label + (value ? ': ' + value : ''));
      layer.appendChild(box);
    }
  }
  function highlight(fields) {
    const layer = $('#e_layer'); if (!layer) return;
    layer.querySelectorAll('.rcpt-box.hl').forEach(b => b.classList.remove('hl'));
    if (!fields) return;
    const hit = Array.from(layer.querySelectorAll('.rcpt-box')).filter(b => fields.includes(b.dataset.field));
    hit.forEach(b => b.classList.add('hl'));
    const stage = $('#e_stage');
    if (!hit.length || layer.hidden || !stage) return;
    const s = stage.getBoundingClientRect(), r = hit[0].getBoundingClientRect();
    if (r.top < s.top || r.bottom > s.bottom) stage.scrollTop += r.top - s.top - (s.height - r.height) / 2;
    if (r.left < s.left || r.right > s.right) stage.scrollLeft += r.left - s.left - (s.width - r.width) / 2;
  }
  function unmarked(c) {
    const have = new Set((c.scan.doc?.boxes || []).map(b => b.field)), d = c.scan.draft || {}, doc = c.scan.doc || {}, out = [];
    const expected = [['vendor', 'Vendor', d.vendor], ['date', 'Date', d.date], ['invoice_no', 'Invoice no.', d.invoice_no], ['total', 'Total', doc.printed_total ?? d.gross], ['vat_id', 'VAT ID', doc.vat_id]];
    for (const [f, label, v] of expected) {
      if (have.has(f)) continue;
      const has = v !== undefined && v !== null && v !== '';
      if (has) out.push(`${label}: ${f === 'total' && num(v) !== null ? eur(num(v)) : v}`);
      else if (f !== 'vat_id') out.push(`${label}: not found`);
    }
    for (const [k, label] of [['prepaid', 'Prepaid'], ['amount_due', 'Amount due'], ['card_amount', 'Card amount']]) if (num(doc[k]) !== null) out.push(`${label}: ${eur(num(doc[k]))}`);
    return out;
  }
  R.afterPreview = () => {
    const c = R.ctx, box = $('#e_preview');
    $('#rcpt_unmarked')?.remove(); $('#rcpt_pages')?.remove();
    if (!c || !box || !PV || PV.kind !== 'image') return;   // PDF iframe, other files, .xml: no layer
    const bar = box.querySelector('.exp-zoombar');
    const t = el('button', 'Show detected fields', 'sm'); t.type = 'button'; t.id = 'rcpt_toggle'; t.dataset.a = 'rcptToggle'; t.setAttribute('aria-pressed', String(c.show));
    bar.insertBefore(t, $('#e_preview_open'));
    const pages = Number(c.scan.doc?.pages) || 0;
    if (c.pdf && pages > 1) {
      const row = el('div', undefined, 'exp-zoombar'); row.id = 'rcpt_pages'; row.setAttribute('role', 'group'); row.setAttribute('aria-label', 'Pages');
      for (let i = 0; i < pages; i++) {
        const b = el('button', 'Page ' + (i + 1), 'sm'); b.type = 'button'; b.dataset.a = 'rcptPage'; b.dataset.n = String(i);
        b.setAttribute('aria-pressed', String(i === c.page)); row.appendChild(b);
      }
      bar.after(row);
    }
    buildLayer(c);
    const list = unmarked(c);
    if (list.length) {
      const u = el('div', undefined, 'rcpt-unmarked'); u.id = 'rcpt_unmarked';
      u.appendChild(el('h3', 'Not marked on the bill'));
      const ul = el('ul'); for (const t2 of list) ul.appendChild(el('li', t2)); u.appendChild(ul);
      box.after(u);
    }
  };

  return R;
})();
