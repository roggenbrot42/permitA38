/* permitA38 – accounting page: journal and reports, read only. Contract: docs/specs/accounting.md 14, 17, 18.
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
const accYearBar = (tab, basis) => `<div class="head"><div><h1>Accounting</h1><div class="muted">Journal and reports, read only. All figures come from the journal.</div></div>
  <div class="field" style="margin:0"><label for="acc_year">Fiscal year</label><select id="acc_year" data-change="accYear">${ACC.years.map(y => `<option value="${y.year}" ${y.year === ACC.year ? 'selected' : ''}>${y.year}</option>`).join('')}</select></div></div>
  <div class="small muted" id="acc-basis" style="margin-bottom:8px">${esc(ACC_BASIS[basis] || basis || '')}</div>
  <div class="tabs" role="tablist">${accTabs(basis).map(([k, l]) => `<a href="#/accounting/${k}" role="tab" aria-selected="${k === tab}" data-testid="acc-tab-${k}" class="${k === tab ? 'on' : ''}">${esc(l)}</a>`).join('')}</div>`;

/* ---------- journal ---------- */
function accEntryHTML(e) {
  const pills = [];
  if (e.reverses_id) pills.push('<span class="pill blue" title="Reverses another entry">Reversal</span>');
  if (e.reversed) pills.push('<span class="pill">Reversed</span>');   // the API sends no such flag yet: the original stays unmarked, the reversal is marked
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
    <div class="small muted">Document date ${esc(fmtDate(e.document_date))}${e.source_coll ? ` · source ${esc(e.source_coll)}` : ''}</div></details>`;
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

const ACC_TABS = { journal: accJournal, ledger: accLedger, 'trial-balance': accTrial, eur: accEur, pnl: accPnl, 'balance-sheet': accBalance, 'open-items': accOpenItems };

PAGES.accounting = async (tab = 'journal') => {
  await accLoadYears();
  if (ACC.year === null) return `<div class="head"><div><h1>Accounting</h1></div></div><div class="card">${accEmpty('No fiscal year has been opened yet.')}</div>`;
  const basis = (ACC.years.find(y => y.year === ACC.year) || {}).income_basis;
  if (!accTabs(basis).some(([k]) => k === tab)) tab = 'journal';   // the EÜR tab of an accrual year (and vice versa) does not exist
  let body;
  try { body = await ACC_TABS[tab](); }
  catch (e) {
    if (e.status === 422) body = `<div class="card">${accEmpty('This report does not exist for the income basis of this year.')}</div>`;   // never shown as an error
    else if (e.status === 404) body = `<div class="card">${accEmpty('No fiscal year opened for this year.')}</div>`;
    else throw e;
  }
  return accYearBar(tab, basis) + body;
};

A.accYear = el => { ACC.year = Number(el.value); lsSet('permita38.accounting.year', String(ACC.year)); ACC.ledger = ''; render(); };
A.accApply = () => {
  ACC.f = { from: $('#acc_from').value, to: $('#acc_to').value, account: $('#acc_account').value, tax_key: $('#acc_taxkey').value.trim(), source: $('#acc_source').value.trim() };
  ACC.offset = 0; render();
};
A.accReset = () => { ACC.f = { from: `${ACC.year}-01-01`, to: `${ACC.year}-12-31`, account: '', tax_key: '', source: '' }; ACC.offset = 0; render(); };
A.accPage = el => { ACC.offset = Math.max(0, ACC.offset + Number(el.dataset.dir) * ACC_PAGE); render(); };
A.accLedgerAccount = el => { ACC.ledger = el.value; render(); };
