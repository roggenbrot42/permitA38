/* permitA38 – VAT page: ZM tab (recapitulative statement for EU services, docs/specs/zm.md) and the Today banners for
   VAT returns and ZM reports that are due or overdue (docs/specs/ustva.md "Deadlines"). The browser holds no tax rule:
   every figure, state and due date comes from the API (the box labels below are display text only). Tabs: Returns (UStVA,
   docs/specs/ustva.md "UI sketch") and ZM.
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

PAGES.vat = async (tab = 'returns', a, b, c) => {
  const head = `<div class="head"><div><h1>VAT</h1><div class="muted">${tab === 'zm' ? 'Recapitulative statement (ZM) for services to businesses in other EU states. Figures to enter or upload in the BZSt online portal; nothing is filed from here.' : 'VAT advance returns (UStVA). Figures to enter in Mein ELSTER; nothing is filed from here.'}</div></div></div>${vatTabs(tab)}`;
  const y = /^\d{4}$/.test(a || '') ? +a : new Date().getFullYear();
  if (tab === 'zm') return head + (b && /^[1-4]$/.test(b) ? await zmDetail(y, +b) : await zmList(y));
  if (['month', 'quarter'].includes(b) && /^\d{1,2}$/.test(c || '')) return head + await vatDetail(y, b, +c);
  return head + await vatList(y);
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

/** Download an export with the session cookie. A 422 lists the offending rows in the box `boxSel`; the page stays. */
async function vatDownload(el, boxSel, fallbackName) {
  const box = $(boxSel); if (box) box.innerHTML = '';
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
  const name = /filename="?([^";]+)"?/.exec(r.headers.get('Content-Disposition') || '')?.[1] || fallbackName;
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement('a');
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
A.zmExport = el => vatDownload(el, '#zm-export-err', 'zm-export');
A.vatExport = el => vatDownload(el, '#vat-export-err', 'ustva-export');

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

/* ---------- Returns tab (UStVA) ---------- */

/** Display labels of the boxes in form order. The figures come from the API; the labels are text only. */
const VAT_BOXES = [['81', 'Taxable sales at 19 % (base)'], ['86', 'Taxable sales at 7 % (base)'], ['35', 'Taxable sales at other rates (base)'], ['36', 'Tax on sales at other rates'],
  ['21', 'Non-taxable services to EU businesses (§ 18b)'], ['46', '§ 13b services from EU suppliers (base)'], ['47', 'Tax on § 13b services from EU suppliers'], ['84', 'Other § 13b services (base)'], ['85', 'Tax on other § 13b services'],
  ['66', 'Input VAT from invoices of other businesses'], ['67', 'Input VAT from § 13b services'], ['39', 'Credit of the special prepayment'], ['83', 'Remaining prepayment (+) / refund (−)'], ['10', 'Amended return (enter 1)']];
const VAT_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const vatPeriodName = (kind, no) => kind === 'month' ? VAT_MONTHS[no - 1] : 'Q' + no;
const vatLink = (y, kind, no) => `#/vat/returns/${y}/${kind}/${no}`;
const vatYearNav = y => `<div class="row tight" style="align-items:center;margin-bottom:10px"><a href="#/vat/returns/${y - 1}" aria-label="Previous year">‹</a><b>${y}</b><a href="#/vat/returns/${y + 1}" aria-label="Next year">›</a></div>`;
/** The adviser banner. The API does not expose the rules status, so this is static: the rules of a release stay unconfirmed until a tax adviser has checked them. */
const VAT_ADVISER = '<div class="banner" id="vat-adviser" role="status">The VAT rules and boxes are not confirmed by a tax adviser yet. Check every figure before you enter it in Mein ELSTER.</div>';

async function vatList(y) {
  let list;
  try { list = await api('GET', 'vat-returns?year=' + y); } catch (e) { return vatYearNav(y) + vatError(e); }
  const rows = list.map(p => {
    const link = vatLink(y, p.kind, p.no);
    return `<tr data-href="${link}" class="click"><td><b>${esc(vatPeriodName(p.kind, p.no))}</b></td><td class="small">${esc(fmtDate(p.from_date))} – ${esc(fmtDate(p.to_date))}</td>
      <td>${esc(fmtDate(p.due_date))}</td><td>${vatStatePill(p)}</td><td class="num">${p.result === null || p.result === undefined ? '' : esc(eur(p.result))}</td><td class="num">${p.lines ?? ''}</td><td class="right"><a href="${link}">Open</a></td></tr>`;
  }).join('');
  const table = list.length ? `<div class="card"><table id="vat-list"><thead><tr><th>Period</th><th>Period dates</th><th>Due</th><th>State</th><th class="num">Kz 83</th><th class="num">Lines</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="card"><div class="empty">No VAT periods for this year.</div></div>';
  return `${vatYearNav(y)}${VAT_ADVISER}${table}${await vatSpecialPanel(y)}`;
}

/** The "Special prepayment" panel (Sondervorauszahlung, monthly filers with Dauerfrist only). */
async function vatSpecialPanel(y) {
  let sp;
  try { sp = await api('GET', 'vat/special-prepayment?year=' + y); } catch (e) { return ''; }
  if (!sp.dauerfrist) return '';
  const pay = sp.payment;
  const basis = `(Kz 83 of ${y - 1}: ${esc(eur(sp.previous_kz83))} + Kz 39: ${esc(eur(sp.previous_kz39))}) × 12 / ${sp.months ?? '?'} months ÷ 11`;
  const payment = pay ? `<div id="sp-payment">Booked payment: <b>${esc(eur(pay.amount))}</b> on ${esc(fmtDate(pay.paid_on))}${pay.reversed ? ' <span class="pill bad">Reversed</span>' : ' <span class="pill good">Paid</span>'}</div>` : '<div id="sp-payment" class="muted">No payment booked.</div>';
  const edit = can('vat', 'write') ? `<button data-a="vatSpecial" data-year="${y}" data-proposal="${esc(sp.proposal)}" data-amount="${esc(sp.amount ?? '')}" data-paid-on="${esc(pay?.paid_on || '')}">${sp.amount === null || sp.amount === undefined ? 'Enter amount / record payment' : 'Change amount / record payment'}</button>` : '';
  return `<div class="card" id="vat-special"><h3>Special prepayment ${y}</h3>
    <div>Proposal: <b id="sp-proposal">${esc(eur(sp.proposal))}</b> <span class="small muted">${basis}</span></div>
    <div>Your amount: <b id="sp-amount">${sp.amount === null || sp.amount === undefined ? '–' : esc(eur(sp.amount))}</b> · due ${esc(fmtDate(sp.due_date))}</div>
    ${payment}<div class="row tight" style="margin-top:8px">${edit}</div>
    <p class="small muted">Credited as Kz 39 in the December return.</p></div>`;
}

A.vatSpecial = el => {
  const y = +el.dataset.year;
  openForm({
    title: `Special prepayment ${y}`, saveLabel: 'Save', vals: { amount: el.dataset.amount || el.dataset.proposal, paid_on: el.dataset.paidOn },
    fields: [{ k: 'amount', l: 'Amount (EUR)', m: true, h: `Proposal: ${eur(el.dataset.proposal)}` }, { k: 'paid_on', l: 'Payment date (leave empty to only save the amount)', t: 'date' }],
    save: async v => {
      if (v.amount === null || v.amount === '') throw new Error('Enter the amount');
      await api('PUT', 'vat/special-prepayment', { year: y, amount: v.amount, paid_on: v.paid_on || null });
      toast('Saved'); await render();
    },
  });
};

async function vatDetail(y, kind, no) {
  const back = `<p><a href="#/vat/returns/${y}">← VAT ${y}</a></p>`;
  let item, ret, err = '';
  try { item = (await api('GET', 'vat-returns?year=' + y)).find(x => x.kind === kind && x.no === no); } catch (e) { return back + vatError(e); }
  if (!item) return back + '<div class="card"><div class="empty">This period does not exist for the year.</div></div>';
  try { ret = item.id ? await api('GET', 'vat-returns/' + item.id) : await api('GET', `vat-returns/preview?year=${y}&kind=${kind}&no=${no}`); } catch (e) { err = vatError(e); }
  const name = `${vatPeriodName(kind, no)} ${y}`;
  const title = `<h2>${esc(name)} ${vatStatePill(item)}${ret?.amends_id ? ' <span class="pill blue">Amended return · Kz 10</span>' : ''}</h2>`;
  if (!ret) return back + `<div class="card">${title}${err}</div>`;
  const rid = ret.id, writable = can('vat', 'write'), final = ret.status === 'final', filed = !!ret.filed_on;
  const meta = [`Due ${esc(fmtDate(ret.due_date || item.due_date))}`, ret.basis ? esc(ret.basis) : '', ret.dauerfrist ? 'Dauerfrist' : '', ret.rules_version ? `rules ${esc(ret.rules_version)}` : '',
    filed ? `filed ${esc(fmtDate(ret.filed_on))}${ret.reference ? ', reference ' + esc(ret.reference) : ''}` : ''].filter(Boolean).join(' · ');
  const boxes = { ...(ret.boxes || {}) };
  if (!boxes['83'] && ret.result !== null && ret.result !== undefined) boxes['83'] = { amount: ret.result };
  const drill = kz => rid && !['83', '10', '39'].includes(kz) ? `<button class="ghost sm" data-a="vatBox" data-id="${esc(rid)}" data-kz="${kz}">Lines</button>` : '';
  const trs = VAT_BOXES.filter(([kz]) => boxes[kz]).map(([kz, label]) => {
    const b = boxes[kz];
    const base = b.base !== null && b.base !== undefined ? esc(eur(b.base)) : '';
    const tax = b.tax ?? b.amount;
    const booked = b.booked_tax !== null && b.booked_tax !== undefined && b.booked_tax !== b.tax ? `booked ${esc(eur(b.booked_tax))}` : '';
    const flag = b.flag !== null && b.flag !== undefined ? esc(String(b.flag)) : '';
    return `<tr data-kz="${kz}"><td><b>${kz}</b></td><td>${esc(label)}</td><td class="num">${base}</td><td class="num">${flag || (tax !== null && tax !== undefined ? esc(eur(tax)) : '')}</td><td class="small muted">${booked}</td><td class="right">${drill(kz)}</td></tr>`;
  }).join('');
  const table = trs ? `<table id="vat-boxes"><thead><tr><th>Kz</th><th>Box</th><th class="num">Base (form)</th><th class="num">Tax / amount (form)</th><th>Booked</th><th></th></tr></thead><tbody>${trs}</tbody></table>
    <div class="small" id="vat-rounding">Rounding difference (booked − form), stays on the VAT accounts until the annual return: <b>${esc(eur(ret.rounding_difference ?? 0))}</b></div>` : '<div class="empty">No figures in this period.</div>';
  const drift = ret.drift?.entries ? `<div class="banner" id="vat-drift" role="alert">⚠ ${ret.drift.entries} entr${ret.drift.entries === 1 ? 'y' : 'ies'} written after finalising change${ret.drift.entries === 1 ? 's' : ''} the tax by ${esc(eur(ret.drift.tax))}. File an amended return.
    <ul>${(ret.drift.lines || []).map(l => `<li>${esc(l.document || '')} · ${esc(l.tax_key || '')} · ${esc(fmtDate(l.vat_date))} · base ${l.base === null || l.base === undefined ? '' : esc(eur(l.base))}${l.tax === null || l.tax === undefined ? '' : ' · tax ' + esc(eur(l.tax))}</li>`).join('')}</ul></div>` : '';
  const warn = (ret.warnings || []).length ? `<div class="banner">${ret.warnings.map(w => esc(w)).join('<br>')}</div>` : '';
  const btn = (a, label, cls = '') => `<button class="${cls}" data-a="${a}" data-year="${y}" data-kind="${kind}" data-no="${no}" data-id="${esc(rid || '')}">${label}</button>`;
  const actions = [];
  if (writable) {
    if (!rid) actions.push(btn('vatCreate', 'Create draft', 'primary'));
    else if (!final) actions.push(btn('vatFinalize', 'Finalize', 'primary'), btn('vatDelete', 'Delete draft', 'danger'));
    else if (!filed) actions.push(btn('vatFiled', 'Mark filed', 'primary'), btn('vatReopen', 'Reopen'));
    else if (!ret.replaced_by) actions.push(btn('vatAmend', 'Create amended return', ret.drift?.entries ? 'primary' : ''));
  }
  const dl = (id, path, label) => `<a href="${esc(api.url(`vat-returns/${rid}/${path}`))}" id="${id}" data-a="vatExport">${label}</a>`;
  const exp = rid ? [dl('vat-pdf', 'export.pdf', 'PDF'), dl('vat-csv', 'export.csv', 'CSV'), dl('vat-csv-lines', 'export.csv?lines=true', 'Lines CSV'), dl('vat-json', 'export.json', 'JSON')].join(' · ') : '';
  return `${back}${VAT_ADVISER}<div class="card">${title}<div class="muted small">${meta}</div>${err}${warn}${drift}<div id="vat-export-err"></div>${table}
    ${ret.note ? `<p class="small">Note: ${esc(ret.note)}</p>` : ''}<div class="row tight" style="margin-top:12px;align-items:center">${actions.join('')}<span class="right small">${exp}</span></div>
    <p class="small muted">Not a filing. Enter these figures in Mein ELSTER.${rid ? '' : ' A preview: create a draft to see the source lines of each box.'}</p></div>
    <div id="vat-box-lines"></div>${final ? vatPayments(ret, writable) : ''}`;
}

const VAT_PAY_STATE = { none: ['No payment due', ''], open: ['Open', 'warn'], paid: ['Paid', 'good'], overpaid: ['Overpaid', 'bad'] };
/** Payments and refunds of Kz 83 of a final return, with the paid/open state. */
function vatPayments(ret, writable) {
  const [label, cls] = VAT_PAY_STATE[ret.payment_state] || [ret.payment_state || '', ''];
  const rows = (ret.payments || []).map(x => `<tr><td>${esc(fmtDate(x.date))}</td><td>${x.kind === 'refund' ? 'Refund' : 'Payment'}</td><td class="num">${esc(eur(x.amount))}</td><td>${x.reversed ? '<span class="pill bad">Reversed</span>' : ''}</td></tr>`).join('');
  const add = writable ? `<button data-a="vatPay" data-id="${esc(ret.id)}" data-open="${esc(ret.open ?? '0.00')}" data-result="${esc(ret.result ?? '0.00')}">Record payment or refund</button>` : '';
  return `<div class="card" id="vat-payments"><h3>Payment <span class="pill ${cls}" id="vat-pay-state">${esc(label)}</span></h3>
    <div class="small">Kz 83 ${esc(eur(ret.result ?? 0))} · paid ${esc(eur(ret.paid ?? 0))} · open <b id="vat-open">${esc(eur(ret.open ?? 0))}</b></div>
    ${rows ? `<table id="vat-pay-table"><thead><tr><th>Date</th><th>Kind</th><th class="num">Amount</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : ''}<div class="row tight" style="margin-top:8px">${add}</div></div>`;
}

A.vatBox = async el => {
  const box = $('#vat-box-lines');
  const d = await api('GET', `vat-returns/${el.dataset.id}/boxes/${el.dataset.kz}`);
  const rows = (d.lines || []).map(l => `<tr><td>${esc(l.document || '')}</td><td>${esc(String(l.entry_no ?? ''))}</td><td>${esc(fmtDate(l.booking_date))}</td><td>${esc(fmtDate(l.vat_date))}</td><td>${esc(l.tax_key || '')}</td><td class="num">${l.base === null || l.base === undefined ? '' : esc(eur(l.base))}</td><td class="num">${l.tax === null || l.tax === undefined ? '' : esc(eur(l.tax))}</td></tr>`).join('');
  box.innerHTML = `<div class="card" id="vat-box-card"><h3>Source lines of Kz ${esc(d.kz)}</h3>${rows ? `<table id="vat-box-table"><thead><tr><th>Document</th><th>Entry</th><th>Booking date</th><th>VAT date</th><th>Key</th><th class="num">Base</th><th class="num">Tax</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">No lines.</div>'}</div>`;
};

A.vatCreate = async el => { await api('POST', 'vat-returns', { year: +el.dataset.year, kind: el.dataset.kind, no: +el.dataset.no }); toast('Draft created'); await render(); };
A.vatAmend = async el => { await api('POST', 'vat-returns', { year: +el.dataset.year, kind: el.dataset.kind, no: +el.dataset.no, amends_id: el.dataset.id }); toast('Amended return created'); await render(); };
/** Finalize; a 409 with `months` means earlier months are not locked yet: ask, then retry with confirm_gap. */
A.vatFinalize = async el => {
  const path = `vat-returns/${el.dataset.id}/finalize`;
  try { await api('POST', path); }
  catch (e) {
    const months = e.status === 409 && Array.isArray(e.data?.months) ? e.data.months : null;
    if (!months) throw e;
    if (!confirm(`These earlier months are not locked yet and will be locked too: ${months.join(', ')}. Finalize anyway?`)) return;
    await api('POST', path + '?confirm_gap=true');
  }
  toast('Finalized'); await render();
};
A.vatReopen = async el => { await api('POST', `vat-returns/${el.dataset.id}/reopen`); toast('Reopened'); await render(); };
A.vatDelete = async el => { if (!confirm('Delete this draft?')) return; await api('DELETE', 'vat-returns/' + el.dataset.id); toast('Draft deleted'); location.hash = '#/vat/returns/' + el.dataset.year; await render(); };
A.vatFiled = el => {
  const id = el.dataset.id;
  openForm({
    title: 'Mark VAT return as filed', saveLabel: 'Mark filed', vals: { filed_on: todayISO(), reference: '', note: '' },
    fields: [{ k: 'filed_on', l: 'Filed on', t: 'date' }, { k: 'reference', l: 'Reference (ELSTER transfer ticket)', t: 'text' }, { k: 'note', l: 'Note', t: 'area', rows: 2 }],
    save: async v => {
      if (!v.filed_on) throw new Error('Enter the filing date');
      await api('POST', `vat-returns/${id}/filed`, { filed_on: v.filed_on, reference: v.reference || null, note: v.note || null });
      toast('Marked as filed'); await render();
    },
  });
};
A.vatPay = el => {
  const id = el.dataset.id, open = el.dataset.open, refund = Money.isNeg(open) || (Money.isZero(open) && Money.isNeg(el.dataset.result));
  openForm({
    title: 'Record payment or refund', saveLabel: 'Record', vals: { date: todayISO(), amount: Money.abs(open), kind: refund ? 'refund' : 'payment' },
    fields: [{ k: 'kind', l: 'Kind', t: 'select', o: [['payment', 'Payment to the tax office'], ['refund', 'Refund from the tax office']] }, { k: 'date', l: 'Date', t: 'date' }, { k: 'amount', l: 'Amount (EUR, positive)', m: true }],
    save: async v => {
      if (!v.date) throw new Error('Enter the date');
      if (v.amount === null || v.amount === '') throw new Error('Enter the amount');
      await api('POST', `vat-returns/${id}/payment`, { date: v.date, amount: v.amount, kind: v.kind });
      toast('Payment recorded'); await render();
    },
  });
};
