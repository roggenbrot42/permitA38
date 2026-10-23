/* permitA38 – import wizard: pick a file, check it (dry run), fix the column mapping, import, undo.
   Talks only to /api/import (see docs/import.md). */
(() => {
  const FIELDS = {
    invoices: ['number', 'date', 'due_date', 'paid_date', 'status', 'customer_name', 'customer_no', 'vat_id', 'tax_number', 'email', 'net', 'vat', 'vat_rate', 'gross', 'currency', 'title', 'reference', 'external_id', 'line_description', 'line_qty', 'line_price'],
    offers: ['number', 'date', 'due_date', 'paid_date', 'status', 'customer_name', 'customer_no', 'vat_id', 'tax_number', 'email', 'net', 'vat', 'vat_rate', 'gross', 'currency', 'title', 'reference', 'external_id', 'line_description', 'line_qty', 'line_price'],
    customers: ['name', 'customer_no', 'vat_id', 'tax_number', 'email', 'phone', 'street', 'zip', 'city', 'country', 'contact', 'notes'],
    expenses: ['date', 'vendor', 'description', 'net', 'vat', 'vat_rate', 'gross', 'category', 'currency', 'external_id'],
  };
  const TYPE_LABEL = { invoices: 'Invoices', offers: 'Offers', customers: 'Customers', expenses: 'Expenses' };
  const FIELD_LABEL = { customer_name: 'Customer name', vat_id: 'VAT ID', tax_number: 'Tax number', customer_no: 'Customer number', line_description: 'Line: description', line_qty: 'Line: quantity', line_price: 'Line: unit price', due_date: 'Due / valid until', paid_date: 'Paid on', vat_rate: 'VAT rate', external_id: 'External ID' };
  const ACTION = { create: ['ok', 'new'], skip: ['muted', 'skipped'], overwrite: ['ok', 'replaced'], error: ['bad', 'error'] };

  const imp = () => (S.imp = S.imp || { type: 'invoices', file: null, pdf: null, mapping: null, report: null, headers: [] });
  const label = f => FIELD_LABEL[f] || f.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());

  const DEFAULT_OPTS = { on_duplicate: 'skip', decimal: 'auto', date_order: 'dmy', accept_unverified: false, merge_similar: false };
  const opts = () => imp().opts || DEFAULT_OPTS;

  function buildForm(withMapping) {
    const st = imp();
    const fd = new FormData();
    if (!st.file) throw new Error('Choose a file first');
    fd.append('file', st.file);
    fd.append('type', st.type);
    const o = opts();
    Object.entries(o).forEach(([k, v]) => { if (k === 'accept_unverified' || k === 'merge_similar') { if (v) fd.append(k, 'true'); } else fd.append(k, v); });
    if (withMapping && st.mapping) {
      const columns = Object.fromEntries(Object.entries(st.mapping).filter(([, h]) => h));
      fd.append('mapping', JSON.stringify({ columns }));
    } else if (st.mapText) fd.append('mapping', st.mapText);
    if (st.pdf) fd.append('pdfs', st.pdf);
    return fd;
  }

  function mappingHTML(st) {
    const headers = st.headers || [];
    const rows = FIELDS[st.type].map(f => {
      const cur = (st.mapping || {})[f] || '';
      return `<tr><td>${esc(label(f))}</td><td><select data-change="impMap" data-f="${esc(f)}"><option value="">— not in file —</option>${headers.map(h => `<option value="${esc(h)}" ${h === cur ? 'selected' : ''}>${esc(h)}</option>`).join('')}</select></td></tr>`;
    });
    return `<details><summary>Columns (${Object.values(st.mapping || {}).filter(Boolean).length} detected)</summary>
      <p class="small muted">Change a column if it was detected wrongly, then check the file again.</p>
      <table>${rows.join('')}</table></details>`;
  }

  function resultHTML(r) {
    const c = r.counts;
    const rows = r.rows.map(row => {
      const [cls, text] = ACTION[row.action] || ['muted', row.action];
      const cust = row.customer ? `${row.customer.action === 'merge' ? 'merge into' : 'new customer'} ${esc(row.customer.name || '')}${row.customer.score != null && row.customer.action === 'merge' ? ` (${Math.round(row.customer.score * 100)} %)` : ''}` : '';
      return `<tr><td>${esc(row.row)}</td><td><span class="${cls}">${esc(text)}</span></td><td>${esc(row.number || '')}</td><td class="small">${cust}</td><td class="small">${row.messages.map(esc).join('<br>')}</td></tr>`;
    });
    const canImport = c.error === 0 && c.create + c.overwrite > 0 && !imp().stale;
    return `<div class="card" data-import-report>
      <h2>Check result</h2>
      <p>${esc(r.rows_total)} rows: <b>${c.create}</b> to import, <b>${c.skip}</b> skipped, <b class="${c.error ? 'bad' : ''}">${c.error}</b> with errors.
        Customers: ${c.customers_created} new, ${c.customers_merged} matched.${r.pdf && r.pdf.matched ? ` Original PDFs matched: ${r.pdf.matched}.` : ''}
        ${r.pdf && r.pdf.unmatched && r.pdf.unmatched.length ? `<span class="muted small">Unmatched PDFs: ${r.pdf.unmatched.map(esc).join(', ')}</span>` : ''}</p>
      ${r.unmapped_headers && r.unmapped_headers.length ? `<p class="small muted">Columns not used: ${r.unmapped_headers.map(esc).join(', ')}</p>` : ''}
      ${mappingHTML(imp())}
      <div style="overflow:auto"><table><tr><th>Row</th><th>Result</th><th>Number</th><th>Customer</th><th>Messages</th></tr>${rows.join('')}</table></div>
      <div class="actions">${c.error ? '<span class="bad small">Fix the error rows (or the columns) and check again. Nothing is imported while errors remain.</span>' : ''}
        <button data-a="impCheck">Check again</button>
        <button class="primary" data-a="impRun" ${canImport ? '' : 'disabled'}>Import ${c.create + c.overwrite} document(s)</button></div>
      ${imp().stale ? '<p class="small bad">You changed the settings or columns: check the file again before importing.</p>' : ''}
    </div>`;
  }

  PAGES.import = async () => {
    if (!isBoss()) return denyMember();
    const st = imp();
    const batches = await api('GET', 'import/batches');
    const types = Object.keys(FIELDS).map(t => `<option value="${t}" ${t === st.type ? 'selected' : ''}>${esc(TYPE_LABEL[t])}</option>`).join('');
    return `<div class="head"><div><h1>Import</h1><div class="muted">Bring existing offers, invoices, customers or expenses from another tool. Check first; nothing is written until you import.</div></div><a href="#/settings" class="small">← Settings</a></div>
      <div class="card"><h2>1 · File</h2>
        <div class="row"><div class="field"><label for="i_type">What do you import?</label><select id="i_type" data-change="impType">${types}</select></div>
          <div class="field"><label for="i_file">File (CSV, XLSX or permitA38 JSON, max 50 MB)</label><input id="i_file" type="file" accept=".csv,.txt,.xlsx,.xlsm,.json" data-change="impFile">${st.file ? `<div class="small muted">${esc(st.file.name)}</div>` : ''}</div></div>
        <div class="row"><div class="field"><label for="i_dup">If the number already exists</label><select id="i_dup" data-change="impOpt"><option value="skip" ${opts().on_duplicate === 'skip' ? 'selected' : ''}>Skip (default)</option><option value="overwrite" ${opts().on_duplicate === 'overwrite' ? 'selected' : ''}>Replace an earlier import</option></select></div>
          <div class="field"><label for="i_decimal">Decimal separator</label><select id="i_decimal" data-change="impOpt"><option value="auto" ${opts().decimal === 'auto' ? 'selected' : ''}>Detect (1.234,56 or 1,234.56)</option><option value="comma" ${opts().decimal === 'comma' ? 'selected' : ''}>Comma (1234,56)</option><option value="point" ${opts().decimal === 'point' ? 'selected' : ''}>Point (1234.56)</option></select></div>
          <div class="field"><label for="i_dmy">Slash dates</label><select id="i_dmy" data-change="impOpt"><option value="dmy" ${opts().date_order === 'dmy' ? 'selected' : ''}>03/04/2026 = 3 April (German)</option><option value="mdy" ${opts().date_order === 'mdy' ? 'selected' : ''}>03/04/2026 = 4 March (US)</option></select></div></div>
        <div class="row"><div class="field"><label><input type="checkbox" id="i_merge" data-change="impOpt" ${opts().merge_similar ? 'checked' : ''}> Merge customers with similar names (≥ 90 %)</label></div>
          <div class="field"><label><input type="checkbox" id="i_accept" data-change="impOpt" ${opts().accept_unverified ? 'checked' : ''}> Import rows whose totals do not add up (marked as unverified)</label></div></div>
        <div class="row"><div class="field"><label for="i_pdf">Original PDFs (zip, optional)</label><input id="i_pdf" type="file" accept=".zip" data-change="impPdf">${st.pdf ? `<div class="small muted">${esc(st.pdf.name)}</div>` : ''}</div>
          <div class="field"><label for="i_map">Mapping file (JSON, optional)</label><input id="i_map" type="file" accept=".json" data-change="impMapFile"></div></div>
        <div class="actions"><button class="primary" data-a="impCheck">Check file</button></div>
      </div>
      <div id="i_result">${st.report ? resultHTML(st.report) : ''}</div>
      <div class="card"><h2>Earlier imports</h2>${batches.length ? `<table>${batches.map(b => `<tr><td>${esc(b.filename || '')}</td><td class="small">${esc(TYPE_LABEL[b.kind] || b.kind)} · ${esc(b.counts ? b.counts.create : '')} created</td><td class="small">${esc(b.status === 'undone' ? 'undone' : 'imported')}</td><td class="right">${b.status === 'imported' ? `<button class="sm" data-a="impUndo" data-id="${esc(b.id)}">Undo</button>` : ''}</td></tr>`).join('')}</table>` : '<p class="muted">No imports yet.</p>'}
        <p class="small muted">Undo removes the records of an import, if none of them changed since.</p></div>`;
  };

  const reset = () => { const st = imp(); st.report = null; st.mapping = null; st.mapText = null; st.stale = false; };
  A.impType = el => { imp().type = el.value; reset(); render(); };
  A.impFile = el => { reset(); imp().file = el.files[0] || null; render(); };
  A.impPdf = el => { imp().pdf = el.files[0] || null; imp().stale = !!imp().report; render(); };
  A.impMapFile = async el => {
    const f = el.files[0]; if (!f) return;
    const text = await f.text();
    try { JSON.parse(text); } catch { toast('The mapping file is not valid JSON'); return; }  // reject before it is sent
    imp().mapText = text;
    imp().mapping = null;
    imp().stale = !!imp().report;
    toast('Mapping file read. Check the file again to use it.');
    render();
  };
  // any change after a check makes that check stale: the import is refused until the file is checked again
  A.impMap = el => { const st = imp(); st.mapping = { ...(st.mapping || {}), [el.dataset.f]: el.value }; st.stale = true; render(); };
  A.impOpt = () => {
    const v = id => document.getElementById(id);
    imp().opts = { on_duplicate: v('i_dup').value, decimal: v('i_decimal').value, date_order: v('i_dmy').value, accept_unverified: v('i_accept').checked, merge_similar: v('i_merge').checked };
    imp().stale = !!imp().report;
    render();
  };

  A.impCheck = async () => {
    const st = imp();
    const r = await api('POST', 'import/preview', buildForm(!!st.mapping));
    st.report = r;
    st.headers = r.headers || [];
    st.mapping = { ...r.mapping };
    st.stale = false;
    render();
  };

  A.impRun = async () => {
    const st = imp();
    if (st.stale || !st.report) throw new Error('Check the file again before importing');
    const r = await api('POST', 'import/run', buildForm(true));
    reset();
    let msg = `Imported ${r.counts.create + r.counts.overwrite} document(s).`;
    if (r.next_number && r.next_number !== r.next_number_before) msg += ` Next number is now ${r.next_number}.`;
    toast(msg);
    render();
  };

  A.impUndo = async el => {
    if (!confirm('Remove all records of this import? This cannot be undone.')) return;
    const r = await api('POST', `import/batches/${el.dataset.id}/undo`);
    toast(`Removed ${r.removed} record(s).`);
    await loadAll();
    render();
  };
})();
