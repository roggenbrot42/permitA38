/* permitA38 – numbering page: pattern builder with live preview, start number, warnings, switch wizard, history.
   Talks only to /api/numbering (see docs/numbering.md). */
(() => {
  const TOKENS = ['{year}', '{yy}', '{month}', '{n:04d}'];
  const RESETS = [['yearly', 'Every year (restarts on 1 January)'], ['monthly', 'Every month'], ['never', 'Never (one continuous sequence)']];
  const LABEL = { invoice: 'Invoices', offer: 'Offers' };
  const TYPES = ['invoice', 'offer'];
  const WARN_ORDER = ['backwards', 'gap', 'duplicate', 'pattern_change', 'reset_change', 'sorting'];

  const val = id => (document.getElementById(id) || {}).value || '';
  const intOrNull = s => (String(s).trim() === '' ? null : Number(s));

  /* ---------- wizard: guess pattern and next number from the last number used in the old tool ---------- */
  function guessFromLast(last) {
    const text = String(last || '').trim();
    const m = text.match(/^(.*?)(\d+)(\D*)$/);
    if (!m || m[2].length > 9) return null;
    let prefix = m[1], digits = m[2], suffix = m[3];
    const year = prefix.match(/\d{4}/);
    if (year) prefix = prefix.replace(year[0], '{year}');
    const width = digits.length > 1 ? digits.length : 0;
    const pattern = prefix + (width ? `{n:0${width}d}` : '{n}') + suffix;
    return { pattern, next: Number(digits) + 1 };
  }

  function fieldsFor(type, st) {
    const id = t => `n_${t}_${type}`;
    return { pattern: id('pattern'), reset: id('reset'), next: id('next'), reason: id('reason'), prev: id('prev'), st };
  }

  function card(type, st) {
    const f = fieldsFor(type, st);
    const chips = TOKENS.map(t => `<button type="button" class="sm" data-a="numToken" data-type="${type}" data-t="${esc(t)}">${esc(t)}</button>`).join(' ');
    return `<div class="card" data-numcard="${type}">
      <h2>${esc(LABEL[type])}</h2>
      <div class="field"><label for="${f.pattern}">Pattern</label>
        <input id="${f.pattern}" value="${esc(st.pattern)}" data-numlive="${type}" maxlength="100">
        <div class="row small" style="margin-top:6px">${chips}</div>
        <div class="muted small">Literal text is kept as typed. Example: RE-{year}-{n:04d} gives RE-2026-0043.</div></div>
      <div class="row">
        <div class="field"><label for="${f.reset}">Sequence restarts</label>
          <select id="${f.reset}" data-numlive="${type}">${RESETS.map(([v, l]) => `<option value="${v}" ${v === st.reset ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
        <div class="field"><label for="${f.next}">Next number (start)</label>
          <input id="${f.next}" type="number" min="1" step="1" placeholder="${esc(st.highest_in_period + 1)}" data-numlive="${type}">
          <div class="muted small">Leave empty to keep the current counter. Currently highest in this period: ${esc(st.highest_in_period)}.</div></div>
      </div>
      <div id="${f.prev}" class="small" style="margin:8px 0"></div>
      <div class="field"><label for="${f.reason}">Reason (for the history)</label><input id="${f.reason}" maxlength="500" placeholder="e.g. continue after the old tool"></div>
      <div class="actions"><button class="primary" data-a="numSave" data-type="${type}">Save ${esc(LABEL[type].toLowerCase())} numbering</button></div>
    </div>`;
  }

  function renderPreview(type, r) {
    const box = document.getElementById(`n_prev_${type}`);
    if (!box) return;
    if (r.detail) { box.innerHTML = `<span class="bad">${esc(typeof r.detail === 'string' ? r.detail : 'Check the pattern')}</span>`; return; }
    const warns = [...(r.warnings || [])].sort((a, b) => WARN_ORDER.indexOf(a.code) - WARN_ORDER.indexOf(b.code));
    box.innerHTML = `<div>Your next ${esc(LABEL[type].toLowerCase().replace(/s$/, ''))} number: <b>${esc(r.preview[0])}</b>
      <span class="muted">then ${r.preview.slice(1).map(esc).join(', ')}</span></div>
      ${warns.map(w => `<div class="warn small" data-warn="${esc(w.code)}">⚠ ${esc(w.message)}</div>`).join('')}`;
  }

  async function check(type) {
    const body = { type, pattern: val(`n_pattern_${type}`), reset: val(`n_reset_${type}`) };
    const next = intOrNull(val(`n_next_${type}`));
    if (next !== null) body.next = next;
    try {
      renderPreview(type, await api('POST', 'numbering/check', body));
    } catch (e) {
      renderPreview(type, { detail: e.message });
    }
  }

  A.numToken = el => {
    const input = document.getElementById(`n_pattern_${el.dataset.type}`);
    const pos = input.selectionStart ?? input.value.length;
    input.value = input.value.slice(0, pos) + el.dataset.t + input.value.slice(pos);
    input.focus();
    check(el.dataset.type);
  };

  A.numSave = async el => {
    const type = el.dataset.type;
    const body = { type, pattern: val(`n_pattern_${type}`), reset: val(`n_reset_${type}`), reason: val(`n_reason_${type}`) };
    const next = intOrNull(val(`n_next_${type}`));
    if (next !== null) body.next = next;
    const dry = await api('POST', 'numbering/check', { type, pattern: body.pattern, reset: body.reset, next });
    const warns = dry.warnings || [];
    if (warns.length && !confirm('Please check:\n\n' + warns.map(w => '• ' + w.message).join('\n') + '\n\nSave anyway?')) return;
    if (warns.length) body.confirm = true;
    await api('PUT', 'numbering', body);
    toast('Numbering saved');
    render();
  };

  /* ---------- switch wizard ---------- */
  A.numWizard = () => {
    const type = val('n_wizard_type') || 'invoice';
    const guess = guessFromLast(val('n_wizard_last'));
    const box = document.getElementById('n_wizard_out');
    if (!guess) { box.innerHTML = '<span class="bad">Enter the last number your old tool used, for example RE-2026-1042.</span>'; return; }
    box.innerHTML = `<div>Pattern guess: <code>${esc(guess.pattern)}</code>, next number <b>${esc(guess.next)}</b>.</div>
      <div class="muted small">Check the preview below, then apply it to the ${esc(LABEL[type].toLowerCase())} numbering.</div>
      <div class="actions"><button class="primary" data-a="numWizardApply" data-type="${type}" data-p="${esc(guess.pattern)}" data-n="${guess.next}">Use this for ${esc(LABEL[type].toLowerCase())}</button></div>`;
  };
  A.numWizardApply = async el => {
    const type = el.dataset.type;
    const pattern = el.dataset.p;
    // fill the fields of the page, then show the same preview and warnings as a manual change
    document.getElementById(`n_pattern_${type}`).value = pattern;
    document.getElementById(`n_next_${type}`).value = el.dataset.n;
    await check(type);
    toast('Filled in. Check the preview and save.');
  };

  /* ---------- page ---------- */
  function historyTable(log) {
    if (!log.length) return '<p class="muted">No changes yet.</p>';
    const rows = [...log].reverse().map(r => {
      const b = r.before || {}, a = r.after || {};
      return `<tr><td class="small">${esc(r.created ? new Date(r.created * 1000).toLocaleDateString('de-DE') : '')}</td>
        <td>${esc(LABEL[r.doc_type === 'offers' ? 'offer' : 'invoice'])}</td>
        <td class="small">${esc(b.pattern)} → <b>${esc(a.pattern)}</b>, next ${esc(b.next)} → <b>${esc(a.next)}</b>${a.reset !== b.reset ? `, restarts ${esc(a.reset)}` : ''}</td>
        <td class="small">${(r.warnings || []).map(esc).join(', ')}</td><td class="small">${esc(r.reason || '')}</td></tr>`;
    });
    return `<table><tr><th>Date</th><th>Type</th><th>Change</th><th>Warnings</th><th>Reason</th></tr>${rows.join('')}</table>`;
  }

  PAGES.numbering = async () => {
    if (!isBoss()) return denyMember();
    const [inv, off, log] = await Promise.all([
      api('GET', 'numbering/status?type=invoice'),
      api('GET', 'numbering/status?type=offer'),
      api('GET', 'numbering/log'),
    ]);
    const st = { invoice: inv, offer: off };
    render.after = () => { TYPES.forEach(t => check(t)); };
    return `<div class="head"><div><h1>Numbering</h1><div class="muted">Document numbers are unique and sequential. Change them here, with a preview.</div></div><a href="#/settings" class="small">← Settings</a></div>
      <div class="grid g2">${TYPES.map(t => card(t, st[t])).join('')}</div>
      <div class="card"><h2>Switching from another tool?</h2>
        <p class="small muted">Enter the last number your old tool used. permitA38 suggests the pattern and the next number.</p>
        <div class="row"><div class="field"><label for="n_wizard_last">Last number used</label><input id="n_wizard_last" placeholder="RE-2026-1042"></div>
          <div class="field"><label for="n_wizard_type">For</label><select id="n_wizard_type"><option value="invoice">Invoices</option><option value="offer">Offers</option></select></div></div>
        <div class="actions"><button data-a="numWizard">Suggest</button></div>
        <div id="n_wizard_out" style="margin-top:8px"></div></div>
      <div class="card"><h2>History of changes</h2>${historyTable(log)}</div>`;
  };

  let debounce = null;
  document.addEventListener('input', e => {
    const el = e.target.closest && e.target.closest('[data-numlive]');
    if (!el) return;
    clearTimeout(debounce);
    debounce = setTimeout(() => check(el.dataset.numlive), 250);
  });
  window.numberingCheck = check;
})();
