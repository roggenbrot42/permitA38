/* permitA38 – full data export and import (Settings > Data export and import), boss only.
   Talks only to /api/data/* (docs/contracts/data-export.md). The archive is never kept anywhere but in memory while
   it is downloaded or uploaded; nothing of it goes to localStorage. Vanilla JS, no build step. */
(() => {
  const LARGE_BYTES = 500 * 1000 * 1000;
  const MiB = 1024 * 1024;
  const ACTION_LABEL = { export: 'Export', export_master_data: 'Export (customers and suppliers)', import_preview: 'Import check', import: 'Import' };
  const NEVER = ['The secret key of the instance', 'Two-factor secrets and recovery codes', 'API tokens', 'The web addresses of calendar links', 'Cached calendar events'];
  const dx = { info: null, transfers: [], file: null, preview: null, result: null, busy: '', confirmed: false, exportError: '', importError: '' };

  const size = b => (b >= 1e9 ? (b / 1e9).toFixed(1) + ' GB' : b >= 1e6 ? (b / 1e6).toFixed(1) + ' MB' : b >= 1e3 ? Math.round(b / 1e3) + ' kB' : (b || 0) + ' B');
  const when = t => (t ? new Date(t * 1000).toLocaleString('de-DE') : '');
  const spin = on => (on ? '<span class="dx-spin" aria-hidden="true"></span> ' : '');
  /** Message for a failed request, by status (contract: 401/403/409/413/422/429). */
  function failText(e) {
    const m = e.message || 'Request failed';
    if (e.status === 429) return 'Too many requests. Wait a minute and try again.';
    if (e.status === 403) return m && m !== 'Forbidden' ? m : 'Only the boss may export or import data, and not with an API token.';
    return m;
  }

  /* ---------- page ---------- */
  PAGES.dataexport = async () => {
    if (!isBoss()) return '<div class="card" role="alert">Data export and import is not allowed for your role.</div>';
    Object.assign(dx, { file: null, preview: null, result: null, busy: '', confirmed: false, exportError: '', importError: '' });
    const [info, transfers] = await Promise.all([api.get('data/export/info'), api.get('data/transfers')]);
    dx.info = info; dx.transfers = transfers;
    return `<div class="head"><div><h1>Data export and import</h1><div class="muted">Take a copy of all your data, or fill a new, empty instance from such a copy.</div></div><a href="#/settings" class="small">← Settings</a></div>
      <div id="dx_export">${exportCardHTML()}</div>
      <div class="banner info" role="note">This is a copy for moving and for your own safety. It is not a legally compliant archive (GoBD). Keep the original invoices and receipts as the law requires. Store the file encrypted. It contains personal data.</div>
      <div id="dx_import">${importCardHTML()}</div>
      <div id="dx_transfers">${transfersHTML()}</div>`;
  };

  const countsHTML = (c, caption) => {
    const rows = Object.entries(c || {});
    return rows.length ? `<table class="dx-counts"><caption class="sr-only">${esc(caption)}</caption><tr><th scope="col">Records</th><th scope="col" class="num">Count</th></tr>${rows.map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, ' '))}</td><td class="num">${esc(v)}</td></tr>`).join('')}</table>` : '';
  };

  function exportCardHTML() {
    const i = dx.info, busy = !!dx.busy, dis = busy ? 'disabled' : '';
    const att = i.attachments || {};
    return `<div class="card"><h2>Export</h2>
      <p class="small">Your instance now holds ${Object.entries(i.counts || {}).filter(([, v]) => v).map(([k, v]) => `${esc(v)} ${esc(k.replace(/_/g, ' '))}`).join(', ') || 'no records'}; ${esc(att.files ?? 0)} attachment file(s), ${esc(size(att.bytes))}. The archive will be about ${esc(size(i.estimated_bytes))} at most.</p>
      ${countsHTML(i.counts, 'Records that an export contains')}
      ${i.estimated_bytes > LARGE_BYTES ? '<div class="banner" role="alert">This archive is large; the browser keeps it in memory. Use <code>permita38 export</code> on the server instead.</div>' : ''}
      <div class="field"><label><input type="checkbox" id="dx_att" checked ${dis}> Include attachments (invoice PDFs, receipts)</label></div>
      <div class="field"><label><input type="checkbox" id="dx_acc" ${dis}> Include user accounts and password hashes</label>
        <div class="muted small">The archive then allows logging in as these users. Store it as carefully as the database. Two-factor secrets and API tokens are never included.</div></div>
      <details class="small"><summary>What is never exported</summary><ul>${NEVER.map(n => `<li>${esc(n)}</li>`).join('')}</ul></details>
      <div class="actions"><button type="button" data-a="dxExportMaster" ${dis}>${spin(dx.busy === 'master')}Download customers and suppliers only</button>
        <button type="button" class="primary" data-a="dxExportFull" ${dis}>${spin(dx.busy === 'full')}Download full archive</button></div>
      <div id="dx_progress" class="small muted" role="status">${esc(dx.progress || '')}</div>
      ${dx.exportError ? `<div class="banner bad" role="alert">${esc(dx.exportError)}</div>` : ''}</div>`;
  }

  function transfersHTML() {
    const t = dx.transfers || [];
    return `<div class="card"><h2>Recent transfers</h2>${t.length ? `<table><caption class="sr-only">Recent exports and imports</caption><tr><th scope="col">Time</th><th scope="col">User</th><th scope="col">Action</th><th scope="col">Result</th><th scope="col">Records</th></tr>${t.map(r => `<tr><td class="nowrap">${esc(when(r.at))}</td><td>${esc(nameOf('users', r.user_id) || r.user_id || '')}</td><td>${esc(ACTION_LABEL[r.action] || r.action)}</td><td><span class="pill ${r.ok ? 'good' : 'bad'}">${r.ok ? 'OK' : 'Failed'}</span></td><td class="small">${esc(Object.values(r.counts || {}).reduce((a, b) => a + b, 0))}</td></tr>`).join('')}</table>` : '<p class="muted">No transfers yet.</p>'}
      <p class="small muted">Old archives are never stored; download a new one when you need it.</p></div>`;
  }

  /* ---------- export ---------- */
  function blobName(r) {
    const m = /filename="?(permita38-export-[0-9-]+\.zip)"?/.exec(r.headers.get('Content-Disposition') || '');
    return m ? m[1] : `permita38-export-${todayISO().replace(/-/g, '')}.zip`;
  }
  const setProgress = text => { dx.progress = text; const el = $('#dx_progress'); if (el) el.textContent = text; };
  function refreshExport() { const el = $('#dx_export'); if (!el) return; const keep = [$('#dx_att')?.checked, $('#dx_acc')?.checked]; el.innerHTML = exportCardHTML(); if (keep[0] !== undefined) { $('#dx_att').checked = keep[0]; $('#dx_acc').checked = keep[1]; } }

  async function download(master) {
    if (dx.busy) return;
    const body = master ? { scope: 'master_data' } : { scope: 'full', include_accounts: !!$('#dx_acc')?.checked, include_attachments: !!$('#dx_att')?.checked };
    if (body.include_accounts && !confirm('The archive will contain the password hashes of all users and allows logging in as them. Store it as carefully as the database. Continue?')) return;
    Object.assign(dx, { busy: master ? 'master' : 'full', exportError: '', progress: 'Building the archive on the server …' });
    refreshExport();
    try {
      const headers = { 'Content-Type': 'application/json' }, t = api.token.get();
      if (t) headers.Authorization = 'Bearer ' + t;
      let r;
      try { r = await fetch(api.url('data/export'), { method: 'POST', headers, body: JSON.stringify(body), credentials: 'include' }); }
      catch { throw new Error('Cannot reach the permitA38 server. Is it running?'); }
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        const err = new Error(typeof d.detail === 'string' ? d.detail : r.statusText || 'Export failed');
        err.status = r.status;
        if (r.status === 401) { api.token.set(null); if (typeof api.onUnauthorized === 'function') api.onUnauthorized(); }
        throw err;
      }
      const total = Number(r.headers.get('Content-Length')) || 0, chunks = [];
      let got = 0;
      if (r.body && r.body.getReader) {
        const rd = r.body.getReader();
        for (;;) {
          const { done, value } = await rd.read();
          if (done) break;
          chunks.push(value); got += value.length;
          setProgress(total ? `Downloading … ${Math.floor(got / total * 100)} % (${size(got)} of ${size(total)})` : `Downloading … ${size(got)}`);
        }
      } else chunks.push(new Uint8Array(await r.arrayBuffer()));
      const url = URL.createObjectURL(new Blob(chunks, { type: 'application/zip' }));
      const a = document.createElement('a');
      a.href = url; a.download = blobName(r);
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      dx.progress = `Saved ${a.download} (${size(got)}).`;
      toast('Archive downloaded');
      dx.transfers = await api.get('data/transfers').catch(() => dx.transfers);
      const tl = $('#dx_transfers'); if (tl) tl.innerHTML = transfersHTML();
    } catch (e) {
      dx.exportError = failText(e); dx.progress = '';
    } finally {
      dx.busy = '';
      refreshExport();
    }
  }
  A.dxExportFull = () => download(false);
  A.dxExportMaster = () => download(true);

  /* ---------- import ---------- */
  const itemsHTML = (items, kind) => {
    if (!items || !items.length) return '';
    const li = items.map(w => `<li>${esc(w.message)}${w.file ? ` <span class="muted small">(${esc(w.file)})</span>` : ''}</li>`).join('');
    const head = kind === 'error' ? `Errors (${items.length})` : `Warnings (${items.length})`;
    if (kind === 'error') return `<div class="banner bad" role="alert"><b>${head}</b><ul>${li}</ul></div>`;
    return items.length > 5 ? `<details class="dx-warn" role="status"><summary>${head}</summary><ul>${li}</ul></details>` : `<div class="dx-warn" role="status"><b>${head}</b><ul>${li}</ul></div>`;
  };
  const usersHTML = u => {
    if (!u) return '';
    const parts = [];
    if (u.replaces_setup_user) parts.push(`The account <b>${esc(u.replaces_setup_user)}</b> of this instance is replaced by the one from the archive.`);
    if (u.kept_setup_user) parts.push(`The account <b>${esc(u.kept_setup_user)}</b> of this instance is kept.`);
    if ((u.without_password || []).length) parts.push(`Without a password (set one with <code>permita38 set-password</code>): ${u.without_password.map(esc).join(', ')}.`);
    return parts.length ? `<h3>Users</h3><p class="small">${parts.join(' ')}</p>` : '';
  };
  const numberingHTML = n => {
    if (!n) return '';
    const rows = Object.entries(n).map(([k, v]) => `<li>${k === 'invoices' ? 'Invoices' : k === 'offers' ? 'Offers' : esc(k)}: highest imported ${esc(v.highest_imported || 'none')}; next number will be <b>${esc(v.next || '')}</b></li>`).join('');
    return `<h3>Numbering</h3><ul class="small">${rows}</ul><p class="small">Check the next numbers on the <a href="#/numbering">numbering page</a>.</p>`;
  };
  function previewHTML(p) {
    const m = p.manifest, c = p.compatibility || {};
    const info = m ? `<p class="small">Created ${esc(m.created || '')} by permitA38 ${esc(m.app_version || '')}; scope: ${esc(m.scope === 'full' ? 'full archive' : 'customers and suppliers only')}${m.include_accounts ? '; includes user accounts' : ''}${m.include_attachments === false ? '; without attachments' : ''}.</p>` : '';
    const compat = c.status ? `<p class="small">Compatibility: <span class="pill ${c.status === 'same' ? 'good' : c.status === 'older' ? 'warn' : 'bad'}">${esc({ same: 'Same version', older: 'Older version', newer: 'Newer version', unknown: 'Unknown version' }[c.status] || c.status)}</span> ${esc(c.message || '')}</p>` : '';
    const att = p.attachments || {};
    return `<div class="${p.ok ? '' : 'dx-bad'}"><h3>${p.ok ? 'The archive can be imported' : 'The archive cannot be imported'}</h3>
      ${info}${compat}${countsHTML(p.counts, 'Records in the archive')}
      ${att.files ? `<p class="small">${esc(att.files)} attachment file(s), ${esc(size(att.bytes))}.</p>` : ''}
      ${numberingHTML(p.numbering)}${usersHTML(p.users)}
      ${itemsHTML(p.errors, 'error')}${itemsHTML(p.warnings, 'warning')}
      ${p.truncated ? '<p class="small muted">The list is shortened.</p>' : ''}</div>`;
  }
  const canRun = () => !!(dx.preview && dx.preview.ok && dx.preview.manifest?.scope === 'full' && dx.info?.import?.possible && !dx.busy);

  function importCardHTML() {
    const imp = dx.info.import || {}, off = !imp.possible, busy = !!dx.busy;
    if (dx.result) return resultHTML(dx.result);
    return `<div class="card"><h2>Import</h2>
      ${off ? `<div class="banner" role="alert">${esc(imp.reason || 'This instance cannot be imported into.')} Import works only on an empty instance; to merge data into an existing instance use <a href="#/import">Import documents</a>.</div>` : ''}
      <div class="field"><label for="dx_file">Archive (.zip made by permitA38)</label><input id="dx_file" type="file" accept=".zip,application/zip" data-change="dxFile" ${off || busy ? 'disabled' : ''}>${dx.file ? `<div class="small muted">Selected: ${esc(dx.file.name)} (${esc(size(dx.file.size))})</div>` : ''}</div>
      <div class="actions"><button type="button" data-a="dxPreview" ${off || busy || !dx.file ? 'disabled' : ''}>${spin(dx.busy === 'preview')}Check file</button></div>
      ${dx.importError ? `<div class="banner bad" role="alert">${esc(dx.importError)}</div>` : ''}
      <div id="dx_preview">${dx.preview ? previewHTML(dx.preview) : ''}</div>
      ${dx.preview && !off ? `<div class="field"><label><input type="checkbox" id="dx_ok" data-change="dxConfirmBox" ${dx.confirmed ? 'checked' : ''} ${canRun() ? '' : 'disabled'}> I understand that this instance will be filled with the data of this archive</label></div>
        <div class="actions"><button type="button" class="primary" data-a="dxRun" ${canRun() && dx.confirmed ? '' : 'disabled'}>${spin(dx.busy === 'run')}Import now</button></div>` : ''}</div>`;
  }
  function resultHTML(r) {
    return `<div class="card" role="status"><h2>Import finished</h2>
      <p>The archive was imported.</p>${countsHTML(r.counts, 'Imported records')}
      ${numberingHTML(r.numbering)}${usersHTML(r.users)}${itemsHTML(r.warnings, 'warning')}
      ${r.relogin ? '<div class="banner info">Your account was replaced by the one from the archive. Log in again.</div><div class="actions"><button type="button" class="primary" data-a="dxRelogin">Log in again</button></div>' : ''}</div>`;
  }
  function refreshImport() { const el = $('#dx_import'); if (el) el.innerHTML = importCardHTML(); }

  A.dxFile = el => {
    dx.file = el.files[0] || null; dx.preview = null; dx.confirmed = false; dx.importError = '';
    refreshImport();
  };
  A.dxConfirmBox = el => { dx.confirmed = el.checked; const b = $('[data-a=dxRun]'); if (b) b.disabled = !(canRun() && dx.confirmed); };

  function formOf(extra) {
    const fd = new FormData();
    fd.append('file', dx.file);
    Object.entries(extra || {}).forEach(([k, v]) => fd.append(k, v));
    return fd;
  }
  function failImport(e) {
    if (e.status === 422 && Array.isArray(e.data?.errors)) { dx.preview = { ok: false, manifest: dx.preview?.manifest || null, compatibility: dx.preview?.compatibility, counts: {}, attachments: {}, warnings: [], errors: e.data.errors }; dx.importError = e.message; }
    else dx.importError = failText(e);
  }
  A.dxPreview = async () => {
    if (!dx.file || dx.busy) return;
    if (dx.file.size > 512 * MiB) { dx.importError = 'The file is larger than 512 MiB.'; refreshImport(); return; }
    Object.assign(dx, { busy: 'preview', importError: '', preview: null, confirmed: false });
    refreshImport();
    try { dx.preview = await api.post('data/import/preview', formOf()); }
    catch (e) { failImport(e); }
    dx.busy = '';
    refreshImport();
  };
  A.dxRun = () => {
    if (!canRun() || !dx.confirmed) return;
    modal(`<h2>Import now?</h2><p>This instance will be filled with the data of the archive. The import is all or nothing: if anything fails, nothing is written. It cannot be undone.</p>
      <div class="actions"><button type="button" id="dx-cancel">Cancel</button><button type="button" class="primary" id="dx-go">Import now</button></div>`);
    $('#dx-cancel').onclick = closeModal;
    $('#dx-go').onclick = guard(async () => {
      closeModal();
      Object.assign(dx, { busy: 'run', importError: '' });
      refreshImport();
      try {
        const r = await api.post('data/import/run', formOf({ confirm: 'true' }));
        dx.result = r; dx.file = null; dx.preview = null;
        toast('Import finished');
      } catch (e) {
        failImport(e);
        if (e.status === 409 && dx.info.import) dx.info.import = { possible: false, reason: e.message };
      }
      dx.busy = '';
      refreshImport();
    });
  };
  A.dxRelogin = async () => { dx.result = null; await A.logout(); };
})();
