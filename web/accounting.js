/* permitA38 – accounting page: journal and reports, manual entries, period locks, fiscal years, DATEV export.
   Contract: docs/specs/accounting.md 4, 9.8, 11, 13, 14, 17, 18; datev-export.md "API and UI".
   Tabs: Journal, Ledger, Trial balance, EÜR (EÜR years) or P&L + Balance sheet (accrual years), Open items.
   Money comes from the API as decimal strings and is only shown through Money (web/money.js), never through floats.
   Everything that comes from the server goes through esc(). Vanilla JS, no build step. */

const ACC = {
  year: null, years: [], accounts: [], accountsYear: null,
  f: { from: '', to: '', account: '', tax_key: '', source: '' }, boundsYear: null, offset: 0, ledger: '',
};
const ACC_PAGE = 50;
const ACC_BASIS = { eur: 'EÜR (cash basis)', accrual: 'Accrual basis' };

const accQuery = o => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== '' && v !== null && v !== undefined) q.set(k, v);
  const s = q.toString();
  return s ? '?' + s : '';
};
/** Signed amount (debit positive, credit negative) -> [debit cell text, credit cell text]. */
const accDC = amt => (Money.isNeg(amt) ? ['', eur(Money.abs(amt))] : [eur(amt), '']);
const accTabs = basis => [['journal', 'Journal'], ['ledger', 'Ledger'], ['trial-balance', 'Trial balance'],
  ...(basis === 'eur' ? [['eur', 'EÜR']] : [['pnl', 'Profit & loss'], ['balance-sheet', 'Balance sheet']]),
  ['open-items', 'Open items']];
/** Second tab row: the bookkeeping actions. Inline styles keep the first row (the reports) a clean `.tabs`. */
const ACC_TABS_B = [['entry', 'Manual entry'], ['periods', 'Periods'], ['years', 'Years'], ['datev', 'DATEV export']];
/** The forms (entry, periods, DATEV) are write actions: without accounting write only the read-only Years list stays. */
const accTabsB = () => (can('accounting', 'write') ? ACC_TABS_B : ACC_TABS_B.filter(([k]) => k === 'years'));
const accAccountLabel = n => { const a = ACC.accounts.find(x => x.account === n); return a ? `${n} ${a.name}` : n; };
/** Expense amount (income-signed in the reports) shown as a positive figure; a refund keeps its minus. */
const accNeg = a => (Money.isZero(a) ? a : Money.isNeg(a) ? Money.abs(a) : '-' + Money.abs(a));
/** Readable names of the EÜR lines; the chart's own line key is the fallback (the accounts API returns the key only). */
const ACC_LINE_LABELS = {
  revenue_s19: 'Revenue 19 %', revenue_s7: 'Revenue 7 %', revenue_sx: 'Revenue (other rate)', revenue_s0: 'Revenue 0 %',
  revenue_ku: 'Revenue small business', revenue_eu_svc: 'Revenue EU services', expense_software: 'Software',
  expense_other: 'Other expenses', meals_deductible: 'Meals (deductible)', meals_nondeductible: 'Meals (not deductible)',
  travel_costs: 'Travel costs', bad_debts: 'Bad debts', cash_discount_granted: 'Cash discounts granted',
  trade_tax_expense: 'Trade tax', corporate_tax_expense: 'Corporate tax', tax_refunds: 'Tax refunds',
};
const accLineLabel = k => ACC_LINE_LABELS[k] || ACC_LINE_LABELS[String(k).replace(/^Line /, '')] || k;
const accEmpty = msg => `<div class="empty">${esc(msg)}</div>`;
const accRow = (label, amount, cls = '') => `<tr class="${cls}"><td>${esc(label)}</td><td class="num">${eur(amount)}</td></tr>`;

async function accLoadYears() {
  ACC.years = (await api('GET', 'accounting/years')).sort((a, b) => b.year - a.year);
  const wanted = Number(lsGet('permita38.accounting.year', '')) || ACC.year;
  ACC.year = ACC.years.some(y => y.year === wanted) ? wanted : (ACC.years[0] || {}).year ?? null;
  if (ACC.year !== null && ACC.boundsYear !== ACC.year) {   // a new year starts with its own date range
    ACC.f = { from: `${ACC.year}-01-01`, to: `${ACC.year}-12-31`, account: '', tax_key: '', source: '' }; ACC.boundsYear = ACC.year; ACC.offset = 0; ACC.ledger = '';
  }
}
async function accLoadAccounts() {
  if (ACC.accountsYear === ACC.year) return;
  ACC.accounts = await api('GET', 'accounting/accounts' + accQuery({ year: ACC.year }));
  ACC.accountsYear = ACC.year;
}
const accYearBar = (tab, basis) => `<div class="head"><div><h1>Accounting</h1><div class="muted">Journal and reports. All figures come from the journal.</div></div>
  <div class="field" style="margin:0"><label for="acc_year">Fiscal year</label><select id="acc_year" data-change="accYear">${ACC.years.map(y => `<option value="${y.year}" ${y.year === ACC.year ? 'selected' : ''}>${y.year}</option>`).join('')}</select></div></div>
  <div class="small muted" id="acc-basis" style="margin-bottom:8px">${esc(ACC_BASIS[basis] || basis || '')}</div>
  <div class="tabs" role="tablist">${accTabs(basis).map(([k, l]) => `<a href="#/accounting/${k}" role="tab" aria-selected="${k === tab}" data-testid="acc-tab-${k}" class="${k === tab ? 'on' : ''}">${esc(l)}</a>`).join('')}</div>
  <div role="tablist" data-testid="acc-tabs-b" style="display:flex;gap:4px;margin:-8px 0 16px;flex-wrap:wrap">${accTabsB().map(([k, l]) => `<a href="#/accounting/${k}" role="tab" aria-selected="${k === tab}" data-testid="acc-tab-${k}" style="padding:4px 12px;border-radius:14px;border:1px solid var(--line);${k === tab ? 'color:var(--accent);border-color:var(--accent);font-weight:600' : 'color:var(--muted)'}">${esc(l)}</a>`).join('')}</div>`;

/* ---------- journal ---------- */
function accEntryHTML(e) {
  const pills = [];
  if (e.reverses_id) pills.push('<span class="pill blue" title="Reverses another entry">Reversal</span>');
  if (e.reversed_by) pills.push('<span class="pill" title="A reversal entry exists">Reversed</span>');
  if (e.warnings?.length) pills.push(`<span class="pill warn" title="${esc(e.warnings.join('; '))}">VAT drift</span>`);
  if (e.origin === 'manual') pills.push('<span class="pill">Manual</span>');
  const hash = e.hash ? `<code title="hash ${esc(e.hash)}, previous ${esc(e.prev_hash || 'none')}">#${esc(String(e.hash).slice(0, 8))}</code>` : '<span class="muted">no hash</span>';
  const lines = (e.lines || []).map(l => {
    const [d, c] = accDC(l.amount);
    const tax = l.tax_key && l.tax_key !== 'NONE' ? `${esc(l.tax_key)}${l.tax_rate ? ' ' + esc(l.tax_rate) + ' %' : ''}${l.tax_base !== null && l.tax_base !== undefined ? ` <span class="small muted">base ${eur(l.tax_base)}</span>` : ''}` : '';
    return `<tr><td>${esc(l.role || '')}</td><td>${esc(accAccountLabel(l.account))}</td><td class="num">${d}</td><td class="num">${c}</td><td>${tax}</td><td class="nowrap">${esc(fmtDate(l.vat_date))}</td><td class="nowrap">${esc(fmtDate(l.income_date))}</td></tr>`;
  }).join('');
  return `<details class="acc-entry card" data-testid="acc-entry" data-entry="${esc(e.id)}"><summary><b>#${esc(e.entry_no)}</b> <span class="nowrap">${esc(fmtDate(e.booking_date))}</span>
    <span class="acc-text">${esc(e.text || '')}</span> ${e.source_number ? `<span class="small muted">${esc(e.source_number)}</span>` : ''} ${pills.join(' ')} <span class="small muted right">${hash}</span></summary>
    <div class="table-wrap"><table><caption class="sr-only">Lines of entry ${esc(e.entry_no)}</caption><thead><tr><th>Role</th><th>Account</th><th class="num">Debit</th><th class="num">Credit</th><th>Tax key</th><th>VAT date</th><th>Income date</th></tr></thead><tbody>${lines}</tbody></table></div>
    <div class="small muted">Document date ${esc(fmtDate(e.document_date))}${e.source_coll ? ` · source ${esc(e.source_coll)}` : ''}</div>
    ${e.origin === 'manual' && !e.reverses_id && !e.reversed_by && can('accounting', 'write') ? `<div style="margin-top:8px"><button type="button" class="sm" data-testid="acc-reverse" data-a="accReverse" data-id="${esc(e.id)}" data-no="${esc(e.entry_no)}">Reverse</button></div>` : ''}</details>`;
}
async function accJournal() {
  await accLoadAccounts();
  const f = ACC.f;
  const list = await api('GET', 'accounting/journal' + accQuery({ from: f.from, to: f.to, account: f.account, tax_key: f.tax_key, source: f.source, offset: ACC.offset, limit: ACC_PAGE + 1 }));
  const more = list.length > ACC_PAGE, rows = list.slice(0, ACC_PAGE);
  const accOpts = `<option value="">All accounts</option>${ACC.accounts.map(a => `<option value="${esc(a.account)}" ${a.account === f.account ? 'selected' : ''}>${esc(a.account + ' ' + a.name)}</option>`).join('')}`;
  const src = ['invoices', 'expenses', 'trips', 'manual'];
  return `<div class="card"><div class="row" style="align-items:flex-end;flex-wrap:wrap;gap:10px">
    <div class="field"><label for="acc_from">From</label><input id="acc_from" type="date" value="${esc(f.from)}"></div>
    <div class="field"><label for="acc_to">To</label><input id="acc_to" type="date" value="${esc(f.to)}"></div>
    <div class="field"><label for="acc_account">Account</label><select id="acc_account">${accOpts}</select></div>
    <div class="field"><label for="acc_taxkey">Tax key</label><input id="acc_taxkey" maxlength="20" value="${esc(f.tax_key)}" placeholder="e.g. S19"></div>
    <div class="field"><label for="acc_source">Source</label><input id="acc_source" maxlength="40" list="acc_sources" value="${esc(f.source)}"><datalist id="acc_sources">${src.map(s => `<option value="${s}">`).join('')}</datalist></div>
    <button class="primary" data-a="accApply">Apply</button> <button data-a="accReset">Reset</button></div></div>
    <div id="acc-journal">${rows.length ? rows.map(e => accEntryHTML(e)).join('') : '<div class="card">' + accEmpty('No entries for this selection.') + '</div>'}</div>
    <div class="row" style="justify-content:center;gap:12px"><button data-a="accPage" data-dir="-1" ${ACC.offset ? '' : 'disabled'}>← Previous</button>
    <span class="small muted" id="acc-paging">${rows.length ? `Entries ${ACC.offset + 1}–${ACC.offset + rows.length}` : ''}</span>
    <button data-a="accPage" data-dir="1" ${more ? '' : 'disabled'}>Next →</button></div>`;
}

/* ---------- ledger ---------- */
async function accLedger() {
  await accLoadAccounts();
  const opts = `<option value="">Pick an account…</option>${ACC.accounts.map(a => `<option value="${esc(a.account)}" ${a.account === ACC.ledger ? 'selected' : ''}>${esc(a.account + ' ' + a.name)}</option>`).join('')}`;
  const pick = `<div class="card"><div class="field" style="margin:0"><label for="acc_ledger">Account</label><select id="acc_ledger" data-change="accLedgerAccount">${opts}</select></div></div>`;
  if (!ACC.ledger) return pick + `<div class="card">${accEmpty('Pick an account to see its postings with the running balance.')}</div>`;
  const l = await api('GET', 'accounting/reports/ledger' + accQuery({ year: ACC.year, account: ACC.ledger }));
  const body = l.rows.map(r => `<tr><td class="nowrap">${esc(fmtDate(r.booking_date))}</td><td>#${esc(r.entry_no)}</td><td>${esc(r.role)}</td><td class="num">${!Money.isZero(r.debit) ? eur(r.debit) : ''}</td><td class="num">${!Money.isZero(r.credit) ? eur(r.credit) : ''}</td><td class="num">${eur(r.balance)}</td></tr>`).join('');
  return pick + `<div class="card"><h2>${esc(l.account + ' ' + l.name)}</h2><div class="table-wrap"><table data-testid="acc-ledger"><caption class="sr-only">Ledger ${esc(l.account)}</caption><thead><tr><th>Date</th><th>Entry</th><th>Role</th><th class="num">Debit</th><th class="num">Credit</th><th class="num">Balance</th></tr></thead><tbody>
    <tr><td colspan="5">Opening balance</td><td class="num">${eur(l.opening)}</td></tr>${body}<tr><td colspan="5"><b>Closing balance</b></td><td class="num"><b>${eur(l.closing)}</b></td></tr></tbody></table></div></div>`;
}

/* ---------- reports ---------- */
async function accTrial() {
  const t = await api('GET', 'accounting/reports/trial-balance' + accQuery({ year: ACC.year }));
  const body = t.rows.map(r => `<tr><td>${esc(r.account)}</td><td>${esc(r.name)}</td><td class="num">${eur(r.debit)}</td><td class="num">${eur(r.credit)}</td><td class="num">${eur(r.balance)}</td></tr>`).join('');
  return `<div class="card"><h2>Trial balance ${esc(fmtDate(t.date_from))} – ${esc(fmtDate(t.date_to))}</h2>${t.rows.length ? `<div class="table-wrap"><table data-testid="acc-trial"><caption class="sr-only">Trial balance</caption><thead><tr><th>Account</th><th>Name</th><th class="num">Debit</th><th class="num">Credit</th><th class="num">Balance</th></tr></thead><tbody>${body}
    <tr><td colspan="2"><b>Total</b></td><td class="num"><b>${eur(t.debit_total)}</b></td><td class="num"><b>${eur(t.credit_total)}</b></td><td class="num"><b>${eur(t.balance_total)}</b></td></tr></tbody></table></div>` : accEmpty('Nothing is booked in this year yet.')}
    <p class="small muted" id="acc-trial-note">${esc(t.note || '')}</p></div>`;
}
async function accEur() {
  const e = await api('GET', 'accounting/reports/eur' + accQuery({ year: ACC.year }));
  const sub = (label, amt) => (Money.isZero(amt) ? '' : accRow(label, amt));
  const income = e.lines.filter(l => !Money.isNeg(l.amount)), expense = e.lines.filter(l => Money.isNeg(l.amount));
  return `<div class="card"><h2>EÜR ${esc(e.year)}</h2><div class="table-wrap"><table data-testid="acc-eur"><caption class="sr-only">EÜR ${esc(e.year)}: income, expenses and profit (gross form)</caption><tbody>
    <tr><th colspan="2">Income</th></tr>
    ${income.map(l => accRow(accLineLabel(l.line), l.amount)).join('')}${accRow('VAT received', e.vat_received)}${sub('VAT refunded by the tax office', e.vat_refunded)}
    <tr><th colspan="2">Expenses</th></tr>
    ${expense.map(l => accRow(accLineLabel(l.line), accNeg(l.amount))).join('')}${accRow('Input VAT paid', e.input_vat_paid)}${accRow('VAT paid to the tax office', e.vat_paid)}
    <tr><th colspan="2">Totals</th></tr>${accRow('Total income', e.total_income)}${accRow('Total expenses', e.total_expense)}${accRow('Profit', e.profit, 'acc-total')}
    ${e.non_deductible?.length ? `<tr><th colspan="2">Not deductible or not taxable, added back (outside the profit)</th></tr>${e.non_deductible.map(l => accRow(accLineLabel(l.line), Money.abs(l.amount))).join('')}` : ''}
    </tbody></table></div><p class="small muted">Reference figures; permitA38 does not file the Anlage EÜR.</p></div>`;
}
async function accPnl() {
  const p = await api('GET', 'accounting/reports/pnl' + accQuery({ year: ACC.year }));
  const rows = (list, f = x => x) => list.map(r => `<tr><td>${esc(r.account)}</td><td>${esc(r.name)}</td><td class="num">${eur(f(r.amount))}</td></tr>`).join('');
  return `<div class="card"><h2>Profit &amp; loss ${esc(p.year)}</h2><div class="table-wrap"><table data-testid="acc-pnl"><caption class="sr-only">Profit and loss ${esc(p.year)}</caption><tbody>
    <tr><th colspan="3">Income</th></tr>${rows(p.income)}<tr><td colspan="2"><b>Total income</b></td><td class="num"><b>${eur(p.total_income)}</b></td></tr>
    <tr><th colspan="3">Expenses</th></tr>${rows(p.expenses, accNeg)}<tr><td colspan="2"><b>Total expenses</b></td><td class="num"><b>${eur(p.total_expense)}</b></td></tr>
    <tr class="acc-total"><td colspan="2"><b>Profit</b></td><td class="num"><b>${eur(p.profit)}</b></td></tr></tbody></table></div></div>`;
}
async function accBalance() {
  const b = await api('GET', 'accounting/reports/balance-sheet' + accQuery({ year: ACC.year }));
  const side = (title, rows, total) => `<div class="card"><h2>${title}</h2><div class="table-wrap"><table><caption class="sr-only">${title}</caption><tbody>${rows.map(r => `<tr><td>${esc(r.account)}</td><td>${esc(r.name)}</td><td class="num">${eur(r.balance)}</td></tr>`).join('')}
    <tr><td colspan="2"><b>Total</b></td><td class="num"><b>${eur(total)}</b></td></tr></tbody></table></div></div>`;
  return `<div class="small muted" style="margin-bottom:8px">As of ${esc(fmtDate(b.as_of))}</div><div class="grid g2" data-testid="acc-balance">${side('Debit side (assets)', b.debit_side, b.debit_total)}${side('Credit side (liabilities)', b.credit_side, b.credit_total)}</div>
    <div class="card"><table><tbody>${accRow(`Result of ${b.year}`, b.result)}
    ${Money.isZero(b.earlier_years_result) ? '' : accRow('Result of earlier years not yet closed', b.earlier_years_result)}
    ${Money.isZero(b.difference) ? '' : accRow('Difference (the balance sheet does not balance)', b.difference, 'acc-diff')}</tbody></table></div>`;
}
async function accOpenItems() {
  const o = await api('GET', 'accounting/reports/open-items' + accQuery({ year: ACC.year }));
  let sups = [];
  if (can('suppliers') && o.items.some(i => i.kind === 'payable')) { try { sups = await api('GET', 'suppliers'); } catch { sups = []; } }
  const who = i => (i.kind === 'payable' ? (sups.find(x => x.id === i.counterparty_id) || {}).name : nameOf('customers', i.counterparty_id)) || '';
  const tbl = (title, kind, total) => {
    const items = o.items.filter(i => i.kind === kind);
    return `<div class="card"><h2>${title}</h2>${items.length ? `<div class="table-wrap"><table data-testid="acc-open-${kind}"><caption class="sr-only">${title}</caption><thead><tr><th>Document</th><th>Counterparty</th><th>Since</th><th class="num">Age (days)</th><th class="num">Open</th></tr></thead><tbody>
      ${items.map(i => `<tr><td>${esc(i.source_number || i.open_item_id)}</td><td>${esc(who(i))}</td><td class="nowrap">${esc(fmtDate(i.since))}</td><td class="num">${esc(i.age_days)}</td><td class="num">${eur(i.open_amount)}</td></tr>`).join('')}
      <tr><td colspan="4"><b>Total</b></td><td class="num"><b>${eur(total)}</b></td></tr></tbody></table></div>` : accEmpty('Nothing open.')}</div>`;
  };
  return `<div class="small muted" style="margin-bottom:8px">As of ${esc(fmtDate(o.as_of))}</div>${tbl('Receivables', 'receivable', o.receivables_total)}${tbl('Payables', 'payable', o.payables_total)}`;
}

/* ---------- manual entry (9.8) ---------- */
const ACC_TAX_KEYS = ['', 'S19', 'S7', 'SX', 'KU', 'EU_SVC', 'VST', 'RC_EU_SVC', 'RC_3C_SVC', 'NONE'];
const ACC_KIND_OF = { S19: 'tax', S7: 'tax', SX: 'tax', KU: 'base_only', EU_SVC: 'base_only', VST: 'input', RC_EU_SVC: 'tax', RC_3C_SVC: 'tax' };
const accBlankLine = () => ({ account: '', d: '', c: '', tk: '', kind: '', rate: '', base: '', vdate: '', tpl: '' });
ACC.draft = null;   // {tpl, amount, date, text, lines: [...]}, kept while the form is on screen
const accDraftNew = () => ({ tpl: '', amount: '', date: new Date().toISOString().slice(0, 10), text: '', lines: [accBlankLine(), accBlankLine()] });

async function accEntryTab() {
  await accLoadAccounts();
  if (!ACC.tpls) ACC.tpls = await api('GET', 'accounting/entry-templates');
  if (!ACC.draft) ACC.draft = accDraftNew();
  const d = ACC.draft;
  return `<div class="card" id="acc-entry-form"><h2>Manual entry</h2>
    <div class="muted small" style="margin-bottom:8px">Free, balanced entries (debit = credit). To correct one, reverse it in the journal and post a new one.</div>
    <div class="row" style="flex-wrap:wrap;gap:10px;align-items:flex-end">
      <div class="field"><label for="acc_tpl">Template</label><select id="acc_tpl" data-change="accTemplate"><option value="">Free entry</option>${ACC.tpls.map(t => `<option value="${esc(t.key)}" ${t.key === d.tpl ? 'selected' : ''}>${esc(t.label)}</option>`).join('')}</select></div>
      <div class="field" id="acc_amount_wrap" ${d.tpl ? '' : 'hidden'}><label for="acc_amount">Amount</label><input id="acc_amount" inputmode="decimal" autocomplete="off" value="${esc(d.amount)}" placeholder="0,00"></div>
      <div class="field"><label for="acc_edate">Date</label><input id="acc_edate" type="date" value="${esc(d.date)}"></div>
      <div class="field" style="flex:1;min-width:220px"><label for="acc_etext">Text</label><input id="acc_etext" maxlength="200" value="${esc(d.text)}"></div></div>
    <div class="table-wrap"><table data-testid="acc-entry-lines"><thead><tr><th>Account</th><th class="num">Debit</th><th class="num">Credit</th><th>Tax key</th><th></th></tr></thead><tbody id="acc-lines">${accLinesHTML()}</tbody></table></div>
    <div class="row" style="gap:10px;align-items:center;margin-top:8px"><button type="button" data-a="accAddLine">Add line</button>
      <span id="acc-balance" data-testid="acc-balance-state" class="small" role="status"></span></div>
    <div id="acc-entry-err" class="small" role="alert" data-testid="acc-entry-err" style="color:var(--danger,#b00020);margin-top:8px"></div>
    <div class="actions" style="margin-top:8px"><button type="button" class="primary" data-testid="acc-entry-submit" data-a="accEntrySubmit">Post entry</button></div></div>`;
}
function accLinesHTML() {
  const d = ACC.draft, opts = sel => `<option value="">Pick an account…</option>${ACC.accounts.map(a => `<option value="${esc(a.account)}" ${a.account === sel ? 'selected' : ''}>${esc(a.account + ' ' + a.name + (a.role ? ' (' + a.role + ')' : ''))}</option>`).join('')}`;
  return d.lines.map((l, i) => {
    const taxed = l.tk && l.tk !== 'NONE';
    const x = taxed ? `<div class="row" style="flex-wrap:wrap;gap:6px;margin-top:4px">
      <select id="acc_l${i}_kind" aria-label="Tax kind line ${i + 1}">${['tax', 'input', 'base_only'].map(k => `<option value="${k}" ${k === l.kind ? 'selected' : ''}>${k}</option>`).join('')}</select>
      <input id="acc_l${i}_rate" aria-label="Tax rate line ${i + 1}" placeholder="Rate %" size="6" value="${esc(l.rate)}">
      <input id="acc_l${i}_base" aria-label="Tax base line ${i + 1}" inputmode="decimal" placeholder="Tax base" size="10" value="${esc(l.base)}">
      <input id="acc_l${i}_vdate" type="date" aria-label="VAT date line ${i + 1}" value="${esc(l.vdate)}"></div>` : '';
    return `<tr data-line="${i}"><td><select id="acc_l${i}_acct" aria-label="Account line ${i + 1}">${opts(l.account)}</select>${x}</td>
      <td class="num"><input id="acc_l${i}_d" aria-label="Debit line ${i + 1}" inputmode="decimal" autocomplete="off" size="10" value="${esc(l.d)}"></td>
      <td class="num"><input id="acc_l${i}_c" aria-label="Credit line ${i + 1}" inputmode="decimal" autocomplete="off" size="10" value="${esc(l.c)}"></td>
      <td><select id="acc_l${i}_tk" data-change="accTaxKey" aria-label="Tax key line ${i + 1}">${ACC_TAX_KEYS.map(k => `<option value="${k}" ${k === l.tk ? 'selected' : ''}>${k || '–'}</option>`).join('')}</select></td>
      <td>${d.lines.length > 2 ? `<button type="button" class="sm" data-a="accDelLine" data-i="${i}" aria-label="Remove line ${i + 1}">×</button>` : ''}</td></tr>`;
  }).join('');
}
/** Read the form back into ACC.draft (the DOM is the source of truth while the user types). */
function accCollect() {
  const d = ACC.draft, v = id => ($('#' + id) ? $('#' + id).value : '');
  d.tpl = v('acc_tpl'); d.amount = v('acc_amount'); d.date = v('acc_edate'); d.text = v('acc_etext');
  d.lines = d.lines.map((l, i) => ({ ...l, account: v(`acc_l${i}_acct`), d: v(`acc_l${i}_d`), c: v(`acc_l${i}_c`), tk: v(`acc_l${i}_tk`),
    kind: $(`#acc_l${i}_kind`) ? v(`acc_l${i}_kind`) : l.kind, rate: $(`#acc_l${i}_rate`) ? v(`acc_l${i}_rate`) : l.rate,
    base: $(`#acc_l${i}_base`) ? v(`acc_l${i}_base`) : l.base, vdate: $(`#acc_l${i}_vdate`) ? v(`acc_l${i}_vdate`) : l.vdate }));
}
const accRedrawLines = () => { $('#acc-lines').innerHTML = accLinesHTML(); accBalanceState(); };
/** One debit/credit pair: {ok, value: signed canonical text (debit positive) or null for a blank line} or {ok:false, error}. */
function accSigned(l, i) {
  const d = Money.parseInput(l.d), c = Money.parseInput(l.c);
  if (!d.ok || !c.ok) return { ok: false, error: `Line ${i + 1}: ${(d.ok ? c : d).error}` };
  if (d.value !== null && c.value !== null) return { ok: false, error: `Line ${i + 1}: enter either a debit or a credit, not both` };
  const v = d.value ?? c.value;
  if (v === null) return { ok: true, value: null };
  if (Money.isNeg(v)) return { ok: false, error: `Line ${i + 1}: amounts are positive; use the other column for the opposite side` };
  return { ok: true, value: d.value !== null ? v : (Money.isZero(v) ? '0' : '-' + v) };
}
function accBalanceState() {
  const el = $('#acc-balance'); if (!el || !ACC.draft) return;
  const ds = [], cs = []; let bad = false;
  ACC.draft.lines.forEach(l => { const a = Money.parseInput(l.d), b = Money.parseInput(l.c); if (!a.ok || !b.ok) { bad = true; return; } if (a.value !== null) ds.push(a.value); if (b.value !== null) cs.push(b.value); });
  if (bad) { el.textContent = 'Check the amounts: one decimal separator, no thousands separators.'; el.dataset.state = 'bad'; return; }
  const dt = Money.sum(ds), ct = Money.sum(cs), diff = Money.sum([dt, Money.isZero(ct) ? '0' : '-' + Money.abs(ct)]);
  const ok = Money.isZero(diff) && ds.length + cs.length > 0;
  el.dataset.state = ok ? 'balanced' : 'unbalanced';
  el.textContent = `Debit ${eur(dt)} · Credit ${eur(ct)} · ${ok ? 'balanced' : 'difference ' + eur(diff)}`;
}
/** The request body from the draft, or {error}. */
function accBuildEntry() {
  const d = ACC.draft; if (!d.text.trim()) return { error: 'Enter a text for the entry.' }; if (!d.date) return { error: 'Enter a date.' };
  const lines = [];
  for (let i = 0; i < d.lines.length; i++) {
    const l = d.lines[i], s = accSigned(l, i);
    if (!s.ok) return { error: s.error };
    if (s.value === null) { if (l.account) return { error: `Line ${i + 1}: enter an amount.` }; continue; }
    if (!l.account) return { error: `Line ${i + 1}: pick an account.` };
    const o = { account: l.account, amount: s.value };
    if (l.tk) {
      o.tax_key = l.tk;
      if (l.tk !== 'NONE') {
        o.tax_kind = l.kind || ACC_KIND_OF[l.tk];
        if (!l.vdate) return { error: `Line ${i + 1}: a tax key needs a VAT date.` }; o.vat_date = l.vdate;
        if (l.rate.trim()) o.tax_rate = l.rate.trim();
        if (o.tax_kind !== 'input') { const b = Money.parseInput(l.base); if (!b.ok || b.value === null) return { error: `Line ${i + 1}: ${b.ok ? 'a tax or base-only line needs the tax base' : b.error}` }; o.tax_base = b.value; }
      }
    }
    if (Money.isZero(s.value) && o.tax_kind !== 'base_only') return { error: `Line ${i + 1}: a zero amount is only allowed on a base-only tax line.` };
    lines.push(o);
  }
  if (lines.length < 2) return { error: 'An entry needs at least two lines.' };
  const sum = Money.sum(lines.map(x => x.amount));
  if (!Money.isZero(sum)) return { error: `The entry is not balanced: debit and credit differ by ${eur(Money.abs(sum))}.` };
  return { body: { date: d.date, text: d.text.trim(), lines } };
}
A.accTemplate = el => {
  accCollect(); const d = ACC.draft, t = ACC.tpls.find(x => x.key === el.value);
  if (!t) d.lines = [accBlankLine(), accBlankLine()];
  else d.lines = t.lines.map(tl => ({ ...accBlankLine(), tpl: tl.side, account: (ACC.accounts.find(a => (a.roles || []).includes(tl.role)) || {}).account || '' }));
  $('#acc_amount_wrap').hidden = !t; accRedrawLines(); accApplyAmount();
};
/** A template is one debit and one credit line: the typed amount goes on both sides. */
function accApplyAmount() {
  const d = ACC.draft; if (!d.tpl) return;
  const a = Money.parseInput($('#acc_amount').value), txt = a.ok && a.value !== null ? a.value : '';
  d.lines.forEach((l, i) => {
    const dn = $(`#acc_l${i}_d`), cr = $(`#acc_l${i}_c`);
    if (l.tpl === 'debit') { dn.value = txt; cr.value = ''; } else if (l.tpl === 'credit') { cr.value = txt; dn.value = ''; }
  });
  accCollect(); accBalanceState();
}
A.accTaxKey = el => {
  accCollect(); const i = Number(el.id.match(/acc_l(\d+)_tk/)[1]), l = ACC.draft.lines[i];
  l.kind = ACC_KIND_OF[l.tk] || ''; accRedrawLines();
};
A.accAddLine = () => { accCollect(); ACC.draft.lines.push(accBlankLine()); accRedrawLines(); };
A.accDelLine = el => { accCollect(); ACC.draft.lines.splice(Number(el.dataset.i), 1); accRedrawLines(); };
A.accEntrySubmit = async el => {
  accCollect(); const err = $('#acc-entry-err'); err.textContent = '';
  const r = accBuildEntry(); if (r.error) { err.textContent = r.error; return; }
  el.disabled = true;
  try { await api('POST', 'accounting/entries', r.body); }
  catch (e) { err.textContent = e.message; el.disabled = false; return; }
  ACC.draft = null; toast('Entry posted'); render();
};
document.addEventListener('input', e => {
  if (!ACC.draft || !e.target.closest || !e.target.closest('#acc-entry-form')) return;
  if (e.target.id === 'acc_amount') { accApplyAmount(); return; }
  if (/^acc_l\d+_[dc]$/.test(e.target.id)) { accCollect(); accBalanceState(); }
});

/* ---------- reverse (journal) ---------- */
A.accReverse = el => {
  modal(`<h2>Reverse entry #${esc(el.dataset.no)}</h2><p>A reversal entry is booked with the opposite lines. The original entry stays in the journal. Reversing is final; a mistake is corrected by a new entry.</p>
    <div class="field"><label for="acc_rdate">Date of the reversal (optional)</label><input id="acc_rdate" type="date"></div>
    <div class="field"><label for="acc_rtext">Text (optional)</label><input id="acc_rtext" maxlength="200"></div>
    <div id="acc-rev-err" class="small" role="alert" style="color:var(--danger,#b00020)"></div>
    <div class="actions"><button type="button" data-a="closeModal">Cancel</button><button type="button" class="primary" data-testid="acc-reverse-confirm" data-a="accReverseGo" data-id="${esc(el.dataset.id)}">Reverse entry</button></div>`);
};
A.accReverseGo = async el => {
  const body = {}, d = $('#acc_rdate').value, t = $('#acc_rtext').value.trim();
  if (d) body.date = d;
  if (t) body.text = t;
  try { await api('POST', `accounting/entries/${el.dataset.id}/reverse`, body); }
  catch (e) { $('#acc-rev-err').textContent = e.message; return; }
  closeModal(); toast('Entry reversed'); render();
};

/* ---------- periods (11) ---------- */
const accMonthKey = (y, m) => `${y}-${String(m).padStart(2, '0')}`;
/** First lockable month: the accounting start from the settings, else January of the earliest fiscal year (as the server). */
const accStart = () => {
  const st = S.settings?.['accounting.start'] || S.settings?.accounting?.start;
  if (/^\d{4}-\d{2}/.test(st || '')) return [Number(st.slice(0, 4)), Number(st.slice(5, 7))];
  return [Math.min(...ACC.years.map(y => y.year)), 1];
};
const accNextMonth = (y, m) => (m === 12 ? [y + 1, 1] : [y, m + 1]);
async function accPeriods() {
  const locks = (await api('GET', 'accounting/locks')).sort((a, b) => a.year - b.year || a.month - b.month);
  ACC.locks = locks;
  const reason = r => (r === 'manual' ? 'Locked manually' : r === 'year_close' ? 'Year closing' : String(r || '').startsWith('vat_return') ? 'VAT return ' + String(r).split(':').slice(1).join(':') : String(r || ''));
  const last = locks[locks.length - 1], next = last ? accNextMonth(last.year, last.month) : accStart();
  return `<div class="card"><h2>Locked months</h2><div class="muted small">A locked month takes no more bookings (GoBD). Months are locked in order and cannot be unlocked.</div>
    ${locks.length ? `<div class="table-wrap"><table data-testid="acc-locks"><caption class="sr-only">Locked months</caption><thead><tr><th>Month</th><th>Reason</th><th>Locked on</th></tr></thead><tbody>${locks.map(l => `<tr><td>${esc(accMonthKey(l.year, l.month))}</td><td>${esc(reason(l.reason))}</td><td class="nowrap">${l.locked_at ? esc(new Date(l.locked_at * 1000).toLocaleDateString('de-DE')) : ''}</td></tr>`).join('')}</tbody></table></div>` : accEmpty('No month is locked yet.')}</div>
    <div class="card"><h2>Lock up to a month</h2><div class="row" style="align-items:flex-end;gap:10px;flex-wrap:wrap">
      <div class="field"><label for="acc_lock_month">Lock all months up to and including</label><input id="acc_lock_month" type="month" value="${esc(accMonthKey(...next))}"></div>
      <button type="button" class="primary" data-testid="acc-lock-go" data-a="accLockAsk">Lock up to this month…</button></div></div>`;
}
A.accLockAsk = async () => {
  const v = $('#acc_lock_month').value;
  if (!/^\d{4}-\d{2}$/.test(v)) { toast('Pick a month', true); return; }
  const y = Number(v.slice(0, 4)), m = Number(v.slice(5));
  let months;
  try { months = await api('GET', `accounting/locks/preview?year=${y}&month=${m}`); }  // the months the server will lock
  catch (e) { toast(e.message, true); return; }
  const todo = months.map(x => accMonthKey(x.year, x.month));
  if (!todo.length) { toast('That month is already locked.', true); return; }
  const gap = todo.length > 1 ? `<p data-testid="acc-lock-gap"><b>${todo.length} months</b> will be locked, because months are locked in order: ${esc(todo.join(', '))}.</p>` : '';
  modal(`<h2>Lock up to ${esc(v)}</h2>${gap}<p>Locking is final: there is no unlock. No booking can be made in a locked month afterwards.</p>
    <div id="acc-lock-err" class="small" role="alert" style="color:var(--danger,#b00020)"></div>
    <div class="actions"><button type="button" data-a="closeModal">Cancel</button><button type="button" class="primary" data-testid="acc-lock-confirm" data-a="accLockGo" data-year="${y}" data-month="${m}">Lock</button></div>`);
};
A.accLockGo = async el => {
  let r;
  try { r = await api('POST', 'accounting/locks', { year: Number(el.dataset.year), month: Number(el.dataset.month) }); }
  catch (e) { $('#acc-lock-err').textContent = e.message; return; }
  closeModal(); toast(`${r.length} month${r.length === 1 ? '' : 's'} locked`); render();
};

/* ---------- fiscal years and closing (4, 13) ---------- */
const accYesNo = b => (b === null || b === undefined ? '' : b ? 'yes' : 'no');
async function accYears() {
  const boss = S.me?.role === 'boss';
  return `<div class="card"><h2>Fiscal years</h2><div class="table-wrap"><table data-testid="acc-years"><caption class="sr-only">Fiscal years and their bases</caption>
    <thead><tr><th>Year</th><th>Chart</th><th>Income basis</th><th>VAT basis</th><th>VAT returns</th><th>Small business</th><th>Status</th><th></th></tr></thead><tbody>
    ${ACC.years.map(y => `<tr data-year="${esc(y.year)}"><td>${esc(y.year)}</td><td>${esc(y.chart || '')}</td><td>${esc(ACC_BASIS[y.income_basis] || y.income_basis || '')}</td><td>${esc(y.vat_basis || '')}</td><td>${esc(y.vat_frequency || '')}</td><td>${accYesNo(y.small_business)}</td>
      <td>${y.closed_at ? (y.income_basis === 'eur' ? 'Ended' : 'Closed') : 'Open'}</td>
      <td>${boss && !y.closed_at ? `<button type="button" class="sm" data-testid="acc-close-${esc(y.year)}" data-a="accCloseAsk" data-year="${esc(y.year)}" data-basis="${esc(y.income_basis)}">${y.income_basis === 'eur' ? 'End year' : 'Close year'}</button>` : ''}</td></tr>`).join('')}</tbody></table></div>
    <p class="small muted">Closing a year is final. Only the boss can close or end a year.</p></div>`;
}
const ACC_PROV = { trade_tax: ['Provision for trade tax', 'acc_prov_trade', 'trade tax'], corporate_tax: ['Provision for corporate tax', 'acc_prov_corp', 'corporate tax'] };
/** Blockers and the planned entries of a close preview; the part that changes while provisions are typed. */
function accCloseDyn(pv) {
  const blockers = pv.blockers.length ? `<div class="small" role="alert" data-testid="acc-close-preview-blockers" style="color:var(--danger,#b00020)"><b>This year cannot be closed yet:</b><ul>${pv.blockers.map(b => `<li>${esc(b)}</li>`).join('')}</ul></div>` : '';
  const plan = pv.plan.length ? `<details data-testid="acc-close-plan"><summary>Entries the closing will post (${pv.plan.length})</summary>${pv.plan.map(e => `<p class="small"><b>${esc(e.text)}</b><br>${e.lines.map(l => `${esc(l.account)}: ${esc(eur(l.amount))}`).join(' · ')}</p>`).join('')}</details>` : '';
  return blockers + plan;
}
/** The provisions typed so far: {provisions: [{kind, amount}]} or {error}. A kind that is already booked has no input. */
function accCloseProvisions() {
  const provisions = [];
  for (const [kind, [, id, label]] of Object.entries(ACC_PROV)) {
    if (!$(`#${id}`)) continue;
    const p = Money.parseInput($(`#${id}`).value);
    if (!p.ok) return { error: `Provision for ${label}: ${p.error}` };
    if (p.value !== null && !Money.isZero(p.value)) {
      if (Money.isNeg(p.value)) return { error: `Provision for ${label} must be positive.` };
      provisions.push({ kind, amount: p.value });
    }
  }
  return { provisions };
}
A.accCloseAsk = async el => {
  const y = el.dataset.year, eurYear = el.dataset.basis === 'eur';
  let pv;
  try { pv = await api('GET', `accounting/years/${encodeURIComponent(y)}/close-preview`); }
  catch (e) { toast(e.message, true); return; }
  const field = p => {
    const [label, id] = ACC_PROV[p.kind], prepaid = `Prepayments expensed: <span data-testid="acc-prov-prepaid-${esc(p.kind)}">${esc(eur(p.prepayments_expensed))}</span>`;
    if (!Money.isZero(p.booked)) return `<div class="field" data-testid="acc-prov-${esc(p.kind)}"><b>${esc(label)}</b><div data-testid="acc-prov-booked-${esc(p.kind)}">Already booked for this year: ${esc(eur(p.booked))}</div><div class="small muted">${prepaid}</div></div>`;
    return `<div class="field" data-testid="acc-prov-${esc(p.kind)}"><label for="${id}">${esc(label)} (optional)</label><input id="${id}" inputmode="decimal" autocomplete="off" data-change="accCloseRefresh"><div class="small muted">${prepaid}</div></div>`;
  };
  const prov = pv.provisions.length ? `<p class="small muted" data-testid="acc-close-note">Enter the provision for the tax still to be paid for the year (the amount to book, net of the prepayments already expensed). Leave a field empty to post no provision.</p>
    <div class="row" style="gap:10px;flex-wrap:wrap">${pv.provisions.map(field).join('')}</div>` : '';
  modal(`<h2>${eurYear ? 'End' : 'Close'} year ${esc(y)}</h2>
    <p>${eurYear ? 'December is locked, the end of the year is sealed in the journal chain and the next year is opened. Open items stay open.' : 'The provisions are posted, the VAT accounts are transferred, the closing entry is made, December is locked and the next year is opened with its opening balances.'} This is final.</p>
    ${prov}<div id="acc-close-dyn">${accCloseDyn(pv)}</div>
    <div id="acc-close-err" class="small" role="alert" data-testid="acc-close-blockers" style="color:var(--danger,#b00020)"></div>
    <div class="actions"><button type="button" data-a="closeModal">Cancel</button><button type="button" class="primary" data-testid="acc-close-confirm" data-a="accCloseGo" data-year="${esc(y)}" data-basis="${esc(el.dataset.basis)}" ${pv.blockers.length ? 'disabled' : ''}>${eurYear ? 'End year' : 'Close year'}</button></div>`);
  ACC.closeYear = y; ACC.closeSeq = 0;
};
/** A provision was typed: ask the server again, so the planned entries and the blockers reflect it. */
A.accCloseRefresh = async () => {
  const err = $('#acc-close-err'), btn = $('[data-testid=acc-close-confirm]'); if (!err || !btn) return;
  err.textContent = '';
  const r = accCloseProvisions();
  if (r.error) { err.textContent = r.error; btn.disabled = true; return; }
  const q = {}; r.provisions.forEach(p => { q[p.kind] = p.amount; });
  const seq = ++ACC.closeSeq;
  let pv;
  try { pv = await api('GET', `accounting/years/${encodeURIComponent(ACC.closeYear)}/close-preview${accQuery(q)}`); }
  catch (e) { if (seq === ACC.closeSeq && $('#acc-close-err')) { $('#acc-close-err').textContent = e.message; btn.disabled = true; } return; }
  if (seq !== ACC.closeSeq || !$('#acc-close-dyn')) return;
  $('#acc-close-dyn').innerHTML = accCloseDyn(pv);
  btn.disabled = pv.blockers.length > 0;
};
A.accCloseGo = async el => {
  const err = $('#acc-close-err'); err.textContent = '';
  const r = el.dataset.basis === 'eur' ? { provisions: [] } : accCloseProvisions();
  if (r.error) { err.textContent = r.error; return; }
  el.disabled = true;
  try { await api('POST', `accounting/years/${el.dataset.year}/close`, { provisions: r.provisions }); }
  catch (e) { err.textContent = e.message; el.disabled = false; return; }
  closeModal(); toast(`Year ${el.dataset.year} ${el.dataset.basis === 'eur' ? 'ended' : 'closed'}`); render();
};

/* ---------- status: set-up state and adviser banner (accounting.md 16) ---------- */
/** GET accounting/status; null when it cannot be read (the pages then simply show their normal content). */
async function accStatus() { try { return await api('GET', 'accounting/status'); } catch { return null; } }
/** The "Set up accounting" panel shown instead of the tabs until accounting is initialised. The boss sets it up (W8), others ask the boss. */
function accSetupPanel(title) {
  const body = isBoss()
    ? '<p>Accounting has not been set up yet.</p><p><a class="button primary" data-testid="accounting-setup-link" href="#/accounting/setup">Set up accounting</a></p>'
    : '<p data-testid="accounting-setup-ask">Ask the boss to set up accounting.</p>';
  return `<div class="head"><div><h1>${esc(title)}</h1></div></div><div class="card" data-testid="accounting-setup"><h2>Set up accounting</h2>${body}</div>`;
}
const accAdviserDismissed = () => { try { return sessionStorage.getItem('permita38.adviser.dismissed') === '1'; } catch { return false; } };
/** The adviser banner while the rules in use are not confirmed by a tax adviser; dismissible per browser session. */
function accAdviserBanner(st, id = '') {
  if (!st?.initialised || !st.rules_unconfirmed || accAdviserDismissed()) return '';
  return `<div class="banner" data-testid="adviser-banner"${id ? ` id="${id}"` : ''} role="status">${esc(st.banner_text)} <button type="button" class="ghost sm" data-a="adviserDismiss" aria-label="Dismiss">Dismiss</button></div>`;
}
A.adviserDismiss = el => { try { sessionStorage.setItem('permita38.adviser.dismissed', '1'); } catch { /* storage unavailable */ } el.closest('.banner')?.remove(); };

/* ---------- DATEV export ---------- */
async function accDatev() {
  const y = ACC.year;
  return `<div class="card"><h2>Export for the tax adviser (DATEV)</h2>
    <div class="muted small">One fiscal year per export, as a zip with the booking file and a report. Unlocked months are exported too; their rows are not marked as locked (Festschreibung 0) and the report says so.</div>
    <div class="row" style="align-items:flex-end;gap:10px;flex-wrap:wrap;margin-top:8px">
      <div class="field"><label for="acc_dx_from">From</label><input id="acc_dx_from" type="date" value="${y}-01-01"></div>
      <div class="field"><label for="acc_dx_to">To</label><input id="acc_dx_to" type="date" value="${y}-12-31"></div>
      <button type="button" class="primary" data-testid="acc-datev-go" data-a="accDatevGo">Download export</button></div>
    <div id="acc-datev-msg" data-testid="acc-datev-msg" role="status" class="small" style="margin-top:8px"></div></div>`;
}
A.accDatevGo = async el => {
  const msg = $('#acc-datev-msg'), from = $('#acc_dx_from').value, to = $('#acc_dx_to').value;
  msg.style.color = ''; msg.textContent = '';
  if (!from || !to) { msg.textContent = 'Enter both dates.'; return; }
  if (from > to) { msg.textContent = 'From must not be after to.'; return; }
  if (from.slice(0, 4) !== to.slice(0, 4)) { msg.textContent = 'An export covers one fiscal year: choose dates in the same year.'; return; }
  el.disabled = true;
  try {
    const t = api.token.get(), r = await fetch(api.url('accounting/datev' + accQuery({ from, to })), { headers: t ? { Authorization: 'Bearer ' + t } : {}, credentials: 'include' });
    if (!r.ok) {
      const data = await r.json().catch(() => ({})), text = typeof data.detail === 'string' ? data.detail : (r.statusText || 'Export failed');
      msg.style.color = 'var(--danger,#b00020)';
      msg.innerHTML = `${esc(text)}${r.status === 422 && /settings/i.test(text) ? ' <a href="#/settings" data-testid="acc-datev-settings-link">Open the settings</a>' : ''}`;
      return;
    }
    const blob = await r.blob(), a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `datev-export_${from}_${to}.zip`; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    msg.textContent = `Downloaded ${a.download}. The zip contains the report with the figures of the export.`;
  } catch (e) { msg.style.color = 'var(--danger,#b00020)'; msg.textContent = e.message; }
  finally { el.disabled = false; }
};
A.closeModal = () => closeModal();


/* ---------- set-up wizard (accounting.md 16, 18; boss only) ----------
   Steps: 1 bases, 2 opening balances (accrual), 3 review list, 4 returns filed outside the app, 5 summary + POST.
   The state lives in WZ; every step reads its form back into WZ before moving on. Server text goes through esc(). */
const WZ_STEPS = ['Bases', 'Opening balances', 'Review list', 'Filed returns', 'Summary'];
const WZ_ROLES = [['bank', 'Bank'], ['cash', 'Cash'], ['payables', 'Payables'], ['payables_employee', 'Payables to staff'], ['vat_prepayments', 'VAT prepayments'], ['equity', 'Equity'], ['profit_carryforward', 'Profit carry-forward']];
const WZ_TREAT = { standard: 'Standard (input VAT deductible)', no_input_vat: 'No input VAT', reverse_charge_eu_service: 'Reverse charge, EU service', reverse_charge_third_country: 'Reverse charge, third country' };
const WZ_ACTION = { confirm: 'Confirm', treatment: 'Choose a tax treatment', date: 'Enter the payment date', credit_note: 'Create the credit note and refund' };
const WZ_CONFIRM = { vat_before_start: 'Confirm: declared outside the app', expense_prepaid: 'Confirm: the prepayment becomes an opening item', rc_stored_vat: 'Confirm: recompute the self-assessed VAT',
  legacy_cancelled_paid: 'Keep as paid', legacy_paid_no_date: 'Use the invoice date', legacy_paid_before_invoice: 'Keep the date' };
const wzActionLabel = (code, a) => (a === 'confirm' && WZ_CONFIRM[code]) || WZ_ACTION[a] || a;
const wzBlankBal = () => ({ role: '', account: '', d: '', c: '' });
const wzNew = () => ({ step: 1, p: { chart: 'skr03', income_basis: 'eur', vat_basis: 'soll', frequency: 'month', dauerfrist: false, small_business: false, start: `${new Date().getFullYear()}-01-01` },
  bal: [wzBlankBal(), wzBlankBal()], review: [], periods: [], reviewKey: '', res: {}, extr: {}, unresolved: [], error: '', result: null, gmbh: false });
let WZ = wzNew();
const wzKey = p => `${p.year}-${p.kind}-${p.no}`;
const wzPeriodLabel = p => (p.kind === 'quarter' ? `Q${p.no} ${p.year}` : `${p.year}-${String(p.no).padStart(2, '0')}`) + ` (${fmtDate(p.from)} – ${fmtDate(p.to)})`;
const wzVal = id => { const el = $('#' + id); return el ? el.value : ''; };

/** Read the visible step's form back into WZ. */
function wzCollect() {
  if (WZ.step === 1) {
    WZ.p = { chart: wzVal('wz_chart'), income_basis: wzVal('wz_basis'), vat_basis: wzVal('wz_vat'), frequency: wzVal('wz_freq'),
      dauerfrist: !!$('#wz_df')?.checked, small_business: !!$('#wz_small')?.checked, start: wzVal('wz_start') };
  } else if (WZ.step === 2 && WZ.p.income_basis === 'accrual') {
    WZ.bal = WZ.bal.map((_, i) => ({ role: wzVal(`wz_b${i}_role`), account: wzVal(`wz_b${i}_acct`).trim(), d: wzVal(`wz_b${i}_d`), c: wzVal(`wz_b${i}_c`) }));
  } else if (WZ.step === 3) {
    WZ.review.forEach((it, i) => {
      const action = wzVal(`wz_r${i}_act`);
      if (!action) { delete WZ.res[it.key]; return; }
      WZ.res[it.key] = { action, treatment: wzVal(`wz_r${i}_treat`) || (it.treatments || [])[0] || '', date: wzVal(`wz_r${i}_date`) };
    });
  } else if (WZ.step === 4) {
    WZ.periods.forEach((p, i) => { WZ.extr[wzKey(p)] = { mode: wzVal(`wz_x${i}_mode`), kz83: wzVal(`wz_x${i}_kz83`), kz39: wzVal(`wz_x${i}_kz39`), filed: wzVal(`wz_x${i}_filed`) }; });
  }
}
/** Opening balances: {rows: [{role|account, amount}], debit, credit, diff} or {error}. Debit positive, like a journal line. */
function wzBalances() {
  const rows = [], ds = [], cs = [];
  for (let i = 0; i < WZ.bal.length; i++) {
    const b = WZ.bal[i], s = accSigned(b, i);
    if (!s.ok) return { error: s.error };
    if (s.value === null) { if (b.role || b.account) return { error: `Line ${i + 1}: enter an amount.` }; continue; }
    if (!!b.role === !!b.account) return { error: `Line ${i + 1}: pick a role or type an account number, not both.` };
    if (Money.isZero(s.value)) return { error: `Line ${i + 1}: the amount must not be zero.` };
    rows.push({ [b.role ? 'role' : 'account']: b.role || b.account, amount: s.value });
    (Money.isNeg(s.value) ? cs : ds).push(Money.abs(s.value));
  }
  const debit = Money.sum(ds), credit = Money.sum(cs), diff = Money.sum([debit, Money.isZero(credit) ? '0' : '-' + credit]);
  return { rows, debit, credit, diff };
}
function wzBalanceText() {
  const r = wzBalances();
  if (r.error) return { state: 'bad', text: r.error };
  const ok = Money.isZero(r.diff);
  return { state: ok ? 'balanced' : 'unbalanced', text: `Debit ${eur(r.debit)} · Credit ${eur(r.credit)} · ${ok ? 'balanced' : 'difference ' + eur(Money.abs(r.diff))}` };
}
/** Validate the step just filled; returns an error text or ''. */
function wzValidate() {
  if (WZ.step === 1) {
    if (!/^\d{4}-\d{2}-01$/.test(WZ.p.start)) return 'The accounting start must be the first day of a month.';
  } else if (WZ.step === 2 && WZ.p.income_basis === 'accrual') {
    const r = wzBalances();
    if (r.error) return r.error;
    if (r.rows.length && !Money.isZero(r.diff)) return `The opening balances must balance: debit and credit differ by ${eur(Money.abs(r.diff))}.`;
  } else if (WZ.step === 4) {
    for (const p of WZ.periods) {
      const x = WZ.extr[wzKey(p)] || {};
      if (!x.mode) return `${wzPeriodLabel(p)}: answer "filed" or "none filed".`;
      if (x.mode === 'filed') {
        const k = Money.parseInput(x.kz83), k9 = Money.parseInput(x.kz39);
        if (!k.ok || k.value === null) return `${wzPeriodLabel(p)}: Kz 83 ${k.ok ? 'is required' : k.error}.`;
        if (!k9.ok) return `${wzPeriodLabel(p)}: Kz 39 ${k9.error}.`;
        if (k9.value !== null && Money.isNeg(k9.value)) return `${wzPeriodLabel(p)}: Kz 39 must not be negative.`;
        if (!x.filed) return `${wzPeriodLabel(p)}: enter the filing date.`;
      }
    }
  }
  return '';
}
const wzParamQuery = () => accQuery({ chart: WZ.p.chart, income_basis: WZ.p.income_basis, accounting_start: WZ.p.start, vat_basis: WZ.p.vat_basis, frequency: WZ.p.frequency, small_business: WZ.p.small_business ? 'true' : 'false' });
async function wzLoadReview() {
  const key = wzParamQuery();
  if (key === WZ.reviewKey) return;
  const r = await api('GET', 'accounting/init/review' + key);
  WZ.review = r.review || []; WZ.periods = r.external_returns || []; WZ.reviewKey = key;
  const keys = new Set(WZ.review.map(x => x.key)); for (const k of Object.keys(WZ.res)) if (!keys.has(k)) delete WZ.res[k];
  const pk = new Set(WZ.periods.map(wzKey)); for (const k of Object.keys(WZ.extr)) if (!pk.has(k)) delete WZ.extr[k];
}
function wzBody() {
  const p = WZ.p, body = { chart: p.chart, income_basis: p.income_basis, vat_basis: p.vat_basis, frequency: p.frequency, dauerfrist: p.dauerfrist, small_business: p.small_business, accounting_start: p.start,
    opening_balances: p.income_basis === 'accrual' ? (wzBalances().rows || []) : [], review_resolutions: [], external_returns: [] };
  for (const it of WZ.review) {
    const r = WZ.res[it.key]; if (!r) continue;
    const o = { key: it.key, action: r.action };
    if (r.action === 'treatment') o.treatment = r.treatment;
    if (r.action === 'date') o.date = r.date || null;
    body.review_resolutions.push(o);
  }
  for (const per of WZ.periods) {
    const x = WZ.extr[wzKey(per)] || {}, o = { year: per.year, kind: per.kind, no: per.no };
    if (x.mode === 'none') o.none_filed = true;
    else { o.kz83 = Money.parseInput(x.kz83).value; const k9 = Money.parseInput(x.kz39).value; o.kz39 = k9 === null ? '0' : k9; o.filed_on = x.filed; }
    body.external_returns.push(o);
  }
  return body;
}

const wzField = (id, label, control, hint = '') => `<div class="field"><label for="${id}">${label}</label>${control}${hint ? `<div class="small muted">${hint}</div>` : ''}</div>`;
const wzSel = (id, opts, cur, extra = '') => `<select id="${id}" data-change="wzChange" ${extra}>${opts.map(([v, l]) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>`;

function wzStep1() {
  const p = WZ.p;
  return `<div class="grid g2">
    ${wzField('wz_chart', 'Chart of accounts', wzSel('wz_chart', [['skr03', 'SKR03'], ['skr04', 'SKR04']], p.chart))}
    ${wzField('wz_basis', 'Income basis', wzSel('wz_basis', [['eur', 'EÜR (cash basis)'], ['accrual', 'Accrual basis (Bilanz)']], WZ.gmbh ? 'accrual' : p.income_basis, `data-rerender="1" ${WZ.gmbh ? 'disabled' : ''}`),
      WZ.gmbh ? 'A UG / GmbH must use the accrual basis.' : '')}
    ${wzField('wz_vat', 'VAT basis', wzSel('wz_vat', [['soll', 'Soll (when invoiced)'], ['ist', 'Ist (when paid)']], p.vat_basis),
      'Ist-Versteuerung needs the permission of your tax office (§ 20 UStG). Choose Soll unless you have it.')}
    ${wzField('wz_freq', 'VAT return frequency', wzSel('wz_freq', [['month', 'Monthly'], ['quarter', 'Quarterly'], ['none', 'None (exempt from advance returns)']], p.frequency))}
    ${wzField('wz_start', 'Accounting start', `<input id="wz_start" type="date" value="${esc(p.start)}" data-change="wzChange">`, 'The first day of a month. Months before it hold no journal and are never locked.')}
    <div class="field"><label><input id="wz_df" type="checkbox" ${p.dauerfrist ? 'checked' : ''} data-change="wzChange"> Dauerfristverlängerung (one month extra to file)</label>
      <label><input id="wz_small" type="checkbox" ${p.small_business ? 'checked' : ''} data-change="wzChange"> Small business (§ 19 UStG, no VAT charged)</label></div></div>`;
}
function wzStep2() {
  if (WZ.p.income_basis !== 'accrual') return '<p data-testid="wz-no-balances">Opening balances are only needed on the accrual basis. Under EÜR there is nothing to enter here.</p>';
  const b = wzBalanceText();
  return `<p class="muted">Type the balance-sheet figures on the start date. Assets go in the debit column, equity and liabilities in the credit column; open invoices come from your documents.</p>
    <div class="table-wrap"><table><caption class="sr-only">Opening balances</caption><thead><tr><th>Role</th><th>or account number</th><th class="num">Debit</th><th class="num">Credit</th><th></th></tr></thead><tbody>
    ${WZ.bal.map((r, i) => `<tr><td>${wzSel(`wz_b${i}_role`, [['', '–'], ...WZ_ROLES], r.role, `aria-label="Role of line ${i + 1}"`)}</td>
      <td><input id="wz_b${i}_acct" maxlength="20" value="${esc(r.account)}" aria-label="Account number of line ${i + 1}" style="width:110px"></td>
      <td><input id="wz_b${i}_d" inputmode="decimal" class="num" value="${esc(r.d)}" aria-label="Debit of line ${i + 1}" style="width:120px"></td>
      <td><input id="wz_b${i}_c" inputmode="decimal" class="num" value="${esc(r.c)}" aria-label="Credit of line ${i + 1}" style="width:120px"></td>
      <td>${WZ.bal.length > 1 ? `<button type="button" class="ghost sm" data-a="wzDelBal" data-i="${i}" aria-label="Remove line ${i + 1}">✕</button>` : ''}</td></tr>`).join('')}</tbody></table></div>
    <div class="row" style="gap:12px;align-items:center"><button type="button" class="sm" data-a="wzAddBal">+ Line</button><span id="wz-balance" data-testid="wz-balance" data-state="${b.state}" class="small" role="status">${esc(b.text)}</span></div>`;
}
function wzStep3() {
  if (!WZ.review.length) return '<p data-testid="wz-review-empty">No record needs a decision. Continue.</p>';
  const open = WZ.review.filter(it => !WZ.res[it.key]).length;
  return `<p class="muted">These records cannot be booked without your decision. Nothing is guessed: every row needs an answer before accounting can start.</p>
    <p class="small" id="wz-open" data-testid="wz-open">${open ? `${open} of ${WZ.review.length} rows still need an answer.` : 'Every row has an answer.'}</p>
    <div class="table-wrap"><table><caption class="sr-only">Records that need a decision</caption><thead><tr><th>Record</th><th>What is the problem</th><th>Your answer</th></tr></thead><tbody>
    ${WZ.review.map((it, i) => {
      const r = WZ.res[it.key] || {}, bad = WZ.unresolved.includes(it.key) && !r.action;
      const acts = it.resolutions.map(a => `<option value="${esc(a)}" ${r.action === a ? 'selected' : ''}>${esc(wzActionLabel(it.code, a))}</option>`).join('');
      const extra = r.action === 'treatment' ? `<select id="wz_r${i}_treat" data-change="wzChange" aria-label="Tax treatment">${(it.treatments || []).map(t => `<option value="${esc(t)}" ${r.treatment === t ? 'selected' : ''}>${esc(WZ_TREAT[t] || t)}</option>`).join('')}</select>`
        : r.action === 'date' ? `<input id="wz_r${i}_date" type="date" value="${esc(r.date || '')}" data-change="wzChange" aria-label="Payment date">` : '';
      return `<tr data-testid="wz-review-row" data-key="${esc(it.key)}" data-code="${esc(it.code)}" ${bad ? 'data-unresolved="1" style="outline:2px solid var(--danger,#b00020)"' : ''}>
        <td><b>${esc(it.number || it.id)}</b><div class="small muted">${esc(it.collection)}</div></td><td>${esc(it.message)}${bad ? '<div class="small" style="color:var(--danger,#b00020)">Needs an answer</div>' : ''}</td>
        <td><select id="wz_r${i}_act" data-change="wzChange" data-rerender="1" aria-label="Answer for ${esc(it.number || it.id)}"><option value="">– choose –</option>${acts}</select> ${extra}</td></tr>`;
    }).join('')}</tbody></table></div>`;
}
function wzStep4() {
  if (!WZ.periods.length) return '<p data-testid="wz-no-periods">No VAT period has ended since the start date, so there is nothing to enter.</p>';
  return `<p class="muted">For every ended period state what was filed outside permitA38 (Kz 83 is the VAT payable or refundable). The months are locked afterwards. Answer every period; "none filed" is an answer too.</p>
    <div class="table-wrap"><table><caption class="sr-only">Returns filed outside the app</caption><thead><tr><th>Period</th><th>Answer</th><th class="num">Kz 83</th><th class="num">Kz 39</th><th>Filed on</th></tr></thead><tbody>
    ${WZ.periods.map((p, i) => {
      const x = WZ.extr[wzKey(p)] || { mode: '', kz83: '', kz39: '', filed: '' }, f = x.mode === 'filed';
      return `<tr data-testid="wz-period" data-period="${esc(wzKey(p))}"><td class="nowrap">${esc(wzPeriodLabel(p))}</td>
        <td>${wzSel(`wz_x${i}_mode`, [['', '– answer –'], ['filed', 'Filed'], ['none', 'None filed']], x.mode, `data-rerender="1" aria-label="Answer for ${esc(wzPeriodLabel(p))}"`)}</td>
        <td>${f ? `<input id="wz_x${i}_kz83" inputmode="decimal" class="num" value="${esc(x.kz83)}" aria-label="Kz 83 of ${esc(wzPeriodLabel(p))}" style="width:110px">` : ''}</td>
        <td>${f ? `<input id="wz_x${i}_kz39" inputmode="decimal" class="num" value="${esc(x.kz39)}" aria-label="Kz 39 of ${esc(wzPeriodLabel(p))}" style="width:110px" placeholder="0">` : ''}</td>
        <td>${f ? `<input id="wz_x${i}_filed" type="date" value="${esc(x.filed)}" aria-label="Filing date of ${esc(wzPeriodLabel(p))}">` : ''}</td></tr>`;
    }).join('')}</tbody></table></div>`;
}
function wzStep5() {
  const p = WZ.p, open = WZ.review.filter(it => !WZ.res[it.key]).length, filed = WZ.periods.filter(x => (WZ.extr[wzKey(x)] || {}).mode === 'filed').length;
  const bal = p.income_basis === 'accrual' ? (wzBalances().rows || []).length : 0;
  return `<table data-testid="wz-summary"><tbody>
    <tr><td>Chart</td><td>${esc(p.chart.toUpperCase())}</td></tr><tr><td>Income basis</td><td>${esc(ACC_BASIS[p.income_basis])}</td></tr>
    <tr><td>VAT basis</td><td>${p.vat_basis === 'ist' ? 'Ist (when paid)' : 'Soll (when invoiced)'}</td></tr><tr><td>Frequency</td><td>${esc({ month: 'Monthly', quarter: 'Quarterly', none: 'None' }[p.frequency])}${p.dauerfrist ? ', with Dauerfrist' : ''}</td></tr>
    <tr><td>Small business</td><td>${p.small_business ? 'Yes' : 'No'}</td></tr><tr><td>Accounting start</td><td>${esc(fmtDate(p.start))}</td></tr>
    <tr><td>Opening balances</td><td>${bal} line(s)</td></tr><tr><td>Review rows answered</td><td>${WZ.review.length - open} of ${WZ.review.length}</td></tr>
    <tr><td>Returns filed outside the app</td><td>${filed} filed, ${WZ.periods.length - filed} none filed</td></tr></tbody></table>
    ${open ? `<div class="banner bad" role="alert" data-testid="wz-open-warning">${open} review row(s) have no answer yet; the server will refuse to start until they are answered.</div>` : ''}
    <p class="small muted">Setting up writes the opening entry, books every record from the start date on and locks the filed months. It can run only once.</p>`;
}
function wzUnresolvedBanner() {
  if (!WZ.unresolved.length) return '';
  return `<div class="banner bad" role="alert" data-testid="wz-unresolved">These rows still need an answer: <ul>${WZ.unresolved.map(k => `<li><code>${esc(k)}</code></li>`).join('')}</ul></div>`;
}
function wzDone() {
  const r = WZ.result, counts = Object.entries(r.counts || {}).map(([k, n]) => `<li>${esc(k.replace(/_/g, ' '))}: ${esc(n)}</li>`).join('');
  return `<div class="head"><div><h1>Accounting</h1></div></div><div class="card" data-testid="wz-done"><h2>Accounting is set up</h2>
    <p>Fiscal year ${esc(r.year)}, accounting start ${esc(fmtDate(r.accounting_start))}.</p>${counts ? `<ul>${counts}</ul>` : ''}
    <p><a class="button primary" href="#/vat" data-testid="wz-go-vat">Open the VAT page</a> <a class="button" href="#/accounting" data-testid="wz-go-accounting">Open the Accounting page</a></p></div>`;
}
async function accSetupWizard() {
  if (WZ.result) return wzDone();
  try { const s = await api('GET', 'settings'); WZ.gmbh = s.company_type === 'gmbh'; } catch { /* the server enforces the rule anyway */ }
  if (WZ.gmbh) WZ.p.income_basis = 'accrual';
  if (WZ.step >= 3) {
    try { await wzLoadReview(); } catch (e) { WZ.step = 1; WZ.error = e.message; }
  }
  const body = [wzStep1, wzStep2, wzStep3, wzStep4, wzStep5][WZ.step - 1]();
  const nav = `<ol class="wz-steps" data-testid="wz-steps" style="display:flex;gap:12px;flex-wrap:wrap;list-style:none;padding:0;margin:0 0 12px">${WZ_STEPS.map((t, i) => `<li data-step="${i + 1}" ${i + 1 === WZ.step ? 'aria-current="step" style="font-weight:700;color:var(--accent)"' : 'class="muted"'}>${i + 1}. ${esc(t)}</li>`).join('')}</ol>`;
  return `<div class="head"><div><h1>Accounting</h1><div class="muted">Set-up assistant</div></div></div>
    <div class="card" data-testid="wz" data-step="${WZ.step}">${nav}<h2>${esc(WZ_STEPS[WZ.step - 1])}</h2>${wzUnresolvedBanner()}${body}
    <div class="banner bad" id="wz-err" data-testid="wz-err" role="alert" ${WZ.error ? '' : 'hidden'}>${esc(WZ.error)}</div>
    <div class="row" style="gap:8px;margin-top:12px">${WZ.step > 1 ? '<button type="button" data-a="wzBack" data-testid="wz-back">Back</button>' : ''}
    ${WZ.step < 5 ? '<button type="button" class="primary" data-a="wzNext" data-testid="wz-next">Next</button>' : '<button type="button" class="primary" data-a="wzSubmit" data-testid="wz-submit">Set up accounting</button>'}</div></div>`;
}
A.wzChange = el => { wzCollect(); if (el.dataset.rerender) render(); };
A.wzNext = () => {
  wzCollect(); WZ.error = wzValidate();
  if (!WZ.error) WZ.step += 1;
  render();
};
A.wzBack = () => { wzCollect(); WZ.error = ''; WZ.step -= 1; render(); };
A.wzAddBal = () => { wzCollect(); WZ.bal.push(wzBlankBal()); render(); };
A.wzDelBal = el => { wzCollect(); WZ.bal.splice(Number(el.dataset.i), 1); render(); };
A.wzSubmit = async el => {
  WZ.error = ''; WZ.unresolved = [];
  const open = WZ.periods.find(p => !(WZ.extr[wzKey(p)] || {}).mode);
  if (open) { WZ.error = `${wzPeriodLabel(open)}: answer "filed" or "none filed".`; WZ.step = 4; render(); return; }
  el.disabled = true;
  try { WZ.result = await api('POST', 'accounting/init', wzBody()); }
  catch (e) {
    if (e.status === 409 && Array.isArray(e.data?.unresolved)) { WZ.unresolved = e.data.unresolved; WZ.error = e.message; WZ.step = 3; }
    else WZ.error = e.message;
    el.disabled = false;
  }
  render();
};
document.addEventListener('input', e => {
  if (WZ.step !== 2 || !e.target.id || !/^wz_b\d+_[dc]$/.test(e.target.id)) return;
  wzCollect(); const b = wzBalanceText(), el = $('#wz-balance');
  if (el) { el.dataset.state = b.state; el.textContent = b.text; }
});

const ACC_TABS = { entry: accEntryTab, periods: accPeriods, years: accYears, datev: accDatev, journal: accJournal, ledger: accLedger, 'trial-balance': accTrial, eur: accEur, pnl: accPnl, 'balance-sheet': accBalance, 'open-items': accOpenItems };

PAGES.accounting = async (tab = 'journal') => {
  const st = await accStatus();
  if (tab !== 'setup' && WZ.result) WZ = wzNew();   // the wizard is done: leaving its page resets it
  if (tab === 'setup' && isBoss() && (WZ.result || !st || !st.initialised)) return accSetupWizard();
  if (tab === 'setup' && st?.initialised) tab = 'journal';   // already set up: the normal accounting page
  if (st && !st.initialised) return accSetupPanel('Accounting');
  await accLoadYears();
  if (ACC.year === null) return accAdviserBanner(st) + `<div class="head"><div><h1>Accounting</h1></div></div><div class="card">${accEmpty('No fiscal year has been opened yet.')}</div>`;
  const basis = (ACC.years.find(y => y.year === ACC.year) || {}).income_basis;
  if (!accTabs(basis).some(([k]) => k === tab) && !accTabsB().some(([k]) => k === tab)) tab = 'journal';   // the EÜR tab of an accrual year (and vice versa) does not exist
  let body;
  try { body = await ACC_TABS[tab](); }
  catch (e) {
    if (e.status === 422) body = `<div class="card">${accEmpty('This report does not exist for the income basis of this year.')}</div>`;   // never shown as an error
    else if (e.status === 404) body = `<div class="card">${accEmpty('No fiscal year opened for this year.')}</div>`;
    else throw e;
  }
  return accAdviserBanner(st) + accYearBar(tab, basis) + body;
};

A.accYear = el => { ACC.year = Number(el.value); lsSet('permita38.accounting.year', String(ACC.year)); ACC.ledger = ''; render(); };
A.accApply = () => {
  ACC.f = { from: $('#acc_from').value, to: $('#acc_to').value, account: $('#acc_account').value, tax_key: $('#acc_taxkey').value.trim(), source: $('#acc_source').value.trim() };
  ACC.offset = 0; render();
};
A.accReset = () => { ACC.f = { from: `${ACC.year}-01-01`, to: `${ACC.year}-12-31`, account: '', tax_key: '', source: '' }; ACC.offset = 0; render(); };
A.accPage = el => { ACC.offset = Math.max(0, ACC.offset + Number(el.dataset.dir) * ACC_PAGE); render(); };
A.accLedgerAccount = el => { ACC.ledger = el.value; render(); };
