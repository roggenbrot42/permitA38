/* permitA38 – VAT page: ZM tab (recapitulative statement for EU services, docs/specs/zm.md) and the Today banners for
   VAT returns and ZM reports that are due or overdue (docs/specs/ustva.md "Deadlines"). The browser holds no tax rule:
   every figure, state and due date comes from the API. The UStVA ("Returns") tab arrives with a later work package.
   Money is a decimal string, shown through Money. Vanilla JS, no build step. Everything user-supplied goes through esc(). */

const ZM_STATE = { open: ['Open', 'blue'], 'due soon': ['Due soon', 'warn'], overdue: ['Overdue', 'bad'], final: ['Final', 'good'], filed: ['Filed', 'good'], not_required: ['Not required', ''], error: ['Error', 'bad'], draft: ['Draft', 'blue'] };
const zmPill = s => { const [l, c] = ZM_STATE[s] || [s || '', '']; return `<span class="pill ${c}">${esc(l)}</span>`; };
const VAT_BANNER_MAX = 3;   // more Today banners than this collapse into one summary banner
/** A final report that is not filed yet is overdue once its due date has passed (the server state stays "final"). */
const unfiledOverdue = it => it.state === 'final' && !!it.due_date && it.due_date < todayISO();
/** The state pill(s) of a list item; a final, unfiled report shows "final · not filed" and, past its due date, "overdue". */
function vatStatePill(it) {
  if (it.state !== 'final') return zmPill(it.state);
  return `<span class="pill ${unfiledOverdue(it) ? 'bad' : 'warn'}">Final · not filed</span>${unfiledOverdue(it) ? ' <span class="pill bad">Overdue</span>' : ''}`;
}

/** An API error as a banner; a 422 export or compute error lists its rows. */
function vatError(e) {
  const rows = Array.isArray(e.data?.rows) ? `<ul>${e.data.rows.map(r => `<li>${esc(r.vat_id ? r.vat_id + ': ' : '')}${esc(r.reason)}</li>`).join('')}</ul>` : '';
  return `<div class="banner bad" role="alert">${esc(e.message)}${rows}</div>`;
}

function vatTabs(tab) {
  return `<div class="tabs"><a href="#/vat/returns" class="${tab === 'returns' ? 'on' : ''}">Returns</a><a href="#/vat/zm" class="${tab === 'zm' ? 'on' : ''}">ZM</a></div>`;
}

PAGES.vat = async (tab = 'zm', year, quarter) => {
  const head = `<div class="head"><div><h1>VAT</h1><div class="muted">Recapitulative statement (ZM) for services to businesses in other EU states. Figures to enter or upload in the BZSt online portal; nothing is filed from here.</div></div></div>${vatTabs(tab)}`;
  if (tab !== 'zm') return head + '<div class="card"><div class="empty">VAT returns (UStVA): coming.</div></div>';
  const y = /^\d{4}$/.test(year || '') ? +year : new Date().getFullYear();
  if (quarter && /^[1-4]$/.test(quarter)) return head + await zmDetail(y, +quarter);
  return head + await zmList(y);
};

async function zmList(y) {
  const nav = `<div class="row tight" style="align-items:center;margin-bottom:10px"><a href="#/vat/zm/${y - 1}" aria-label="Previous year">‹</a><b>${y}</b><a href="#/vat/zm/${y + 1}" aria-label="Next year">›</a></div>`;
  let list;
  try { list = await api('GET', 'zm?year=' + y); } catch (e) { return nav + vatError(e); }
  const rows = list.map(q => {
    const detail = `#/vat/zm/${y}/${q.quarter}`;
    return `<tr data-href="${detail}" class="click"><td><b>Q${q.quarter}</b></td><td class="small">${esc(fmtDate(q.from_date))} – ${esc(fmtDate(q.to_date))}</td>
      <td>${esc(fmtDate(q.due_date))}</td><td>${vatStatePill(q)}${q.drift ? ' <span class="pill warn" title="Entries after finalising change this report">Must be corrected</span>' : ''}${q.error ? `<div class="small" style="color:var(--bad)">${esc(q.error)}</div>` : ''}</td>
      <td class="num">${q.total === null || q.total === undefined ? '' : esc(eur(q.total))}</td><td class="num">${q.rows ?? ''}</td><td class="right"><a href="${detail}">Open</a></td></tr>`;
  }).join('');
  return `${nav}<div class="card"><table><thead><tr><th>Quarter</th><th>Period</th><th>Due</th><th>State</th><th class="num">Total</th><th class="num">Customers</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function zmDetail(y, q) {
  const back = `<p><a href="#/vat/zm/${y}">← ZM ${y}</a></p>`;
  let item;
  try { item = (await api('GET', 'zm?year=' + y)).find(x => x.quarter === q); } catch (e) { return back + vatError(e); }
  let rep, err = '';
  try { rep = item?.id ? await api('GET', 'zm/' + item.id) : await api('GET', `zm/preview?year=${y}&quarter=${q}`); } catch (e) { err = vatError(e); }
  const state = item?.state || 'open';
  const title = `<h2>ZM Q${q} ${y} ${item ? vatStatePill(item) : zmPill(state)}${rep?.amends_id ? ' <span class="pill blue">Correction</span>' : ''}</h2>`;
  if (!rep) return back + `<div class="card">${title}${err}</div>`;
  const rid = rep.id, writable = can('vat', 'write'), filed = !!rep.filed_on;
  const lines = rep.lines || [];
  const total = Money.sum(lines.map(l => l.amount));
  const meta = [`Due ${esc(fmtDate(rep.due_date || item?.due_date))}`, rep.rules_version ? `rules ${esc(rep.rules_version)}` : '', filed ? `filed ${esc(fmtDate(rep.filed_on))}${rep.reference ? ', reference ' + esc(rep.reference) : ''}` : ''].filter(Boolean).join(' · ');
  const drift = rep.drift?.entries ? `<div class="banner" role="alert">⚠ The ZM of Q${q} ${y} must be corrected: ${rep.drift.entries} entr${rep.drift.entries === 1 ? 'y' : 'ies'} written after finalising belong${rep.drift.entries === 1 ? 's' : ''} to this quarter.
    <ul>${(rep.drift.lines || []).map(l => `<li>${esc(l.document || '')} · ${esc(l.vat_id)} · ${esc(fmtDate(l.vat_date))} · base ${esc(eur(l.base))}</li>`).join('')}</ul></div>` : '';
  const notRequired = rep.required === false && !rid ? '<div class="banner info">No ZM is required for this quarter (no EU services, or a small business).</div>' : '';
  const btn = (a, label, cls = '') => `<button class="${cls}" data-a="${a}" data-year="${y}" data-quarter="${q}" data-id="${esc(rid || '')}">${label}</button>`;
  const actions = [];
  if (writable) {
    if (!rid) { if (rep.required !== false) actions.push(btn('zmCreate', 'Create draft', 'primary')); }
    else if (rep.status !== 'final') actions.push(btn('zmFinalize', 'Finalize', 'primary'), btn('zmDelete', 'Delete draft', 'danger'));
    else if (!filed) actions.push(btn('zmFiled', 'Mark filed', 'primary'), btn('zmReopen', 'Reopen'));
    else if (!rep.replaced_by) actions.push(btn('zmCorrect', 'Create correction', rep.drift?.entries ? 'primary' : ''));
  }
  const xl = (id, path, label) => `<a href="${esc(api.url(`zm/${rid}/${path}`))}" id="${id}" data-a="zmExport">${label}</a>`;
  const exp = rid ? `${xl('zm-csv', 'export.csv', 'BZSt CSV')} · ${xl('zm-csv-readable', 'export.csv?readable=1', 'Readable CSV')} · ${xl('zm-pdf', 'export.pdf', 'PDF')}` : '';
  const body = lines.length ? `<table><thead><tr><th>Customer VAT id</th><th>Country</th><th class="num">Amount (whole EUR)</th><th>Kind</th></tr></thead><tbody>${lines.map(l => `<tr><td>${esc(l.vat_id)}</td><td>${esc(l.country)}</td><td class="num">${esc(eur(l.amount))}</td><td>${esc(l.kind)}</td></tr>`).join('')}</tbody>
    <tfoot><tr><td colspan="2"><b>Sum</b></td><td class="num" id="zm-sum"><b>${esc(eur(total))}</b></td><td></td></tr></tfoot></table>` : '<div class="empty">No EU services in this quarter.</div>';
  return `${back}<div class="card">${title}<div class="muted small">${meta}</div>${err}${notRequired}${drift}<div id="zm-export-err"></div>${body}
    ${rep.note ? `<p class="small">Note: ${esc(rep.note)}</p>` : ''}<div class="row tight" style="margin-top:12px;align-items:center">${actions.join('')}<span class="right small">${exp}</span></div>
    <p class="small muted">Not a filing. Enter or upload these figures in the BZSt online portal.</p></div>`;
}

A.zmCreate = async el => { await api('POST', 'zm', { year: +el.dataset.year, quarter: +el.dataset.quarter }); toast('Draft created'); await render(); };
A.zmCorrect = async el => { await api('POST', 'zm', { year: +el.dataset.year, quarter: +el.dataset.quarter, amends_id: el.dataset.id }); toast('Correction created'); await render(); };
A.zmFinalize = async el => { await api('POST', `zm/${el.dataset.id}/finalize`); toast('Finalized'); await render(); };
A.zmReopen = async el => { await api('POST', `zm/${el.dataset.id}/reopen`); toast('Reopened'); await render(); };
A.zmDelete = async el => { if (!confirm('Delete this draft?')) return; await api('DELETE', 'zm/' + el.dataset.id); toast('Draft deleted'); location.hash = '#/vat/zm/' + el.dataset.year; await render(); };
A.zmFiled = el => {
  const id = el.dataset.id;
  openForm({
    title: 'Mark ZM as filed', saveLabel: 'Mark filed', vals: { filed_on: todayISO(), reference: '', note: '' },
    fields: [{ k: 'filed_on', l: 'Filed on', t: 'date' }, { k: 'reference', l: 'Reference (portal transfer ticket)', t: 'text' }, { k: 'note', l: 'Note', t: 'area', rows: 2 }],
    save: async v => {
      if (!v.filed_on) throw new Error('Enter the filing date');
      await api('POST', `zm/${id}/filed`, { filed_on: v.filed_on, reference: v.reference || null, note: v.note || null });
      toast('Marked as filed'); await render();
    },
  });
};

/** Download an export with the session cookie. A 422 lists the offending rows on the page; the page stays. */
A.zmExport = async el => {
  const box = $('#zm-export-err'); if (box) box.innerHTML = '';
  const t = api.token.get();
  let r;
  try { r = await fetch(el.getAttribute('href'), { credentials: 'include', headers: t ? { Authorization: 'Bearer ' + t } : {} }); }
  catch { throw new Error('Cannot reach the permitA38 server. Is it running?'); }
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    const e = new Error(typeof data.detail === 'string' ? data.detail : (r.statusText || 'Export failed'));
    e.data = data;
    if (r.status === 422 && box) { box.innerHTML = vatError(e); return; }
    throw e;
  }
  const name = /filename="?([^";]+)"?/.exec(r.headers.get('Content-Disposition') || '')?.[1] || 'zm-export';
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement('a');
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

/** Today page banners for VAT returns and ZM reports: the server state "due soon" / "overdue", a final report that is
    not filed after its due date, and ZM drift. More than VAT_BANNER_MAX collapse into one summary. '' without the vat right. */
async function vatTodayBanners() {
  if (!can('vat')) return '';
  const now = todayISO(), y = parseD(now).getFullYear(), out = [];
  const get = async p => { try { return await api('GET', p); } catch { return []; } };   // e.g. accounting not initialised for that year
  for (const year of [y - 1, y]) {
    const [rets, zms] = await Promise.all([get('vat-returns?year=' + year), get('zm?year=' + year)]);
    const kindOf = it => unfiledOverdue(it) ? 'unfiled' : ['due soon', 'overdue'].includes(it.state) ? it.state : null;
    for (const r of rets) { const k = kindOf(r); if (k) out.push([r.due_date, `VAT return ${r.kind === 'month' ? 'month ' + r.no : 'Q' + r.no} ${year}`, k, '#/vat/returns']); }
    for (const z of zms) {
      const k = kindOf(z);
      if (k && (z.rows || k === 'unfiled')) out.push([z.due_date, `ZM Q${z.quarter} ${year}`, k, `#/vat/zm/${year}/${z.quarter}`]);
      if (z.drift) out.push([z.due_date, `The ZM of Q${z.quarter} ${year} must be corrected`, 'drift', `#/vat/zm/${year}/${z.quarter}`]);
    }
  }
  out.sort((a, b) => a[0].localeCompare(b[0]));
  if (out.length > VAT_BANNER_MAX) {
    const bad = out.some(o => o[2] !== 'due soon');
    return `<div class="banner ${bad ? 'bad' : ''}" role="status">⚠ <a href="#/vat">${out.length} VAT/ZM periods need attention</a>.</div>`;
  }
  return out.map(([due, what, state, href]) => state === 'drift'
    ? `<div class="banner" role="status">⚠ <a href="${href}">${esc(what)}</a> (late entries).</div>`
    : state === 'unfiled'
      ? `<div class="banner bad" role="status">⚠ <a href="${href}">${esc(what)}</a> finalised but not filed — due ${esc(fmtDate(due))}.</div>`
      : `<div class="banner ${state === 'overdue' ? 'bad' : ''}" role="status">${state === 'overdue' ? '⚠ Overdue' : 'Due soon'}: <a href="${href}">${esc(what)}</a>, due ${esc(fmtDate(due))}.</div>`).join('');
}

/** Fill the banner slot of the Today page after it has rendered; a failure or a page change leaves nothing behind. */
async function vatFillBanners() {
  try {
    const html = await vatTodayBanners(), el = $('#vat-banners');
    if (el) el.innerHTML = html;
  } catch { /* the banners are a hint, never a reason to fail */ }
}
