/* permitA38 – My settings (#/me) and the personal tax profile (#/me/tax).
   Contract: docs/specs/tax-burden-and-settings.md ("New grouping", "UI", "Models"). Relies on helpers from app.js. */

const TAX_HEALTH = { gkv: 'statutory health insurance', pkv: 'private health insurance', none: 'no health insurance modelled' };

async function mySettingsProfileCard() {
  let u = S.me || {};
  try { u = { ...u, ...(await api.get('auth/me')) }; } catch (e) { /* keep the bootstrap copy */ }
  const num = v => (v === null || v === undefined || v === '' ? '–' : esc(String(v)));
  return `<div class="card" id="my-profile"><h2>Profile</h2>
    <table><tr><td class="muted">Name</td><td data-f="name">${esc(u.name || '')}</td></tr>
    <tr><td class="muted">Username</td><td data-f="username">${esc(u.username || '')}</td></tr>
    <tr><td class="muted">Role</td><td data-f="role">${esc(ROLE_LABEL[u.role] || u.role || '')}</td></tr>
    <tr><td class="muted">Weekly hours</td><td data-f="weekly_hours">${num(u.weekly_hours)}</td></tr>
    <tr><td class="muted">Hourly rate €</td><td data-f="default_rate">${num(u.default_rate)}</td></tr></table>
    <p class="small muted">Weekly hours and hourly rate are changed on the Team page by the boss.</p>
    <div class="actions"><button data-a="changePassword">Change password</button> <button data-a="twoFactor">Two-factor authentication</button></div></div>`;
}

async function mySettingsTaxCard() {
  let body;
  try {
    const t = await api.get('settings/personal-tax');
    body = `${t.stored ? '' : '<div class="banner info" role="status">Not saved yet – the estimate uses the defaults.</div>'}
      <p>Health insurance: <b data-f="tax_health">${esc(TAX_HEALTH[t.health?.mode] || t.health?.mode || '–')}</b><br>Children: <b data-f="tax_children">${esc(String(t.children?.count ?? 0))}</b></p>`;
  } catch (e) { body = `<div class="banner" role="alert">${esc(e.message)}</div>`; }
  return `<div class="card" id="my-tax"><div class="row" style="align-items:center"><h2 style="margin:0">Personal tax profile</h2><div class="right auto"><a href="#/me/tax" class="sm">Open tax profile</a></div></div>
    <p class="small muted">Used for the income tax estimate on Money &amp; Taxes. Only the boss can see it.</p>${body}</div>`;
}

/* ---- Work limits (boss only), GET/PUT settings/work-limits; bounds mirror WorkLimitsIn. Infos and warnings only. ---- */
const WL_DEFAULTS = { info_day: 10, warn_day: 12, max_day: 16, info_week: 60, info_average: 48 };
const WL_FIELDS = [
  ['info_day', 'Info above (h/day)', 24, 'A day with more planned hours than this gets an info in the planner.'],
  ['warn_day', 'Warning above (h/day)', 24, 'A day with more planned hours than this gets a warning.'],
  ['max_day', 'Maximum (h/day)', 24, 'The most hours one person can be planned on a single day; more is flagged as too long.'],
  ['info_week', 'Long week above (h/week)', 168, 'A week with more planned hours than this gets an info.'],
  ['info_average', 'Average above (h/week, over 24 weeks)', 168, 'An average per week over 24 weeks above this gets an info.'],
];

function workLimitsHTML(w, err) {
  const v = k => (w && w[k] !== undefined && w[k] !== null ? w[k] : WL_DEFAULTS[k]);
  return `<div class="card" id="work-limits"><h2>Work limits</h2>
    <p class="small muted">Limits for your own planning. They only produce infos and warnings, nothing is refused.</p>
    <div id="wl-error">${err ? `<div class="banner" role="alert">${esc(err)}</div>` : ''}</div>
    <div class="grid g2">${WL_FIELDS.map(([k, label, max, hint]) => `<div class="field"><label for="wl_${k}">${esc(label)}</label><input type="number" id="wl_${k}" data-wl="${k}" step="0.5" min="1" max="${max}" value="${esc(String(v(k)))}" placeholder="${esc(String(WL_DEFAULTS[k]))}"><div class="small muted">${esc(hint)} Default ${WL_DEFAULTS[k]}.</div></div>`).join('')}</div>
    <div class="actions"><button class="primary" data-a="wlSave" id="wl-save">Save</button> <button data-a="wlReset" id="wl-reset">Reset to defaults</button></div></div>`;
}

async function workLimitsCard() {
  try { return workLimitsHTML(await api.get('settings/work-limits')); }
  catch (e) { return `<div class="card" id="work-limits"><h2>Work limits</h2><div class="banner" role="alert">${esc(e.message)}</div></div>`; }
}

async function wlSend(body) {
  try {
    const res = await api('PUT', 'settings/work-limits', body);
    $('#work-limits').outerHTML = workLimitsHTML(res);
    toast('Work limits saved');
  } catch (e) {
    const box = $('#wl-error');
    if (box) box.innerHTML = `<div class="banner" role="alert">${esc(e.message)}</div>`;
    toast(e.message, true);
  }
}
A.wlSave = async () => {
  const body = {};
  for (const [k, label, max] of WL_FIELDS) {
    const raw = $('#wl_' + k).value.trim();
    if (raw === '') { body[k] = WL_DEFAULTS[k]; continue; }
    const n = Number(raw);
    if (!Number.isFinite(n) || n < 1 || n > max) { toast(`${label}: enter a number from 1 to ${max}.`, true); return; }
    body[k] = n;
  }
  await wlSend(body);
};
A.wlReset = async () => { await wlSend({ ...WL_DEFAULTS }); };

async function mySettingsPage() {
  const cal = can('planning', 'write') && scopeOf('planning') === 'all' ? await calendarsCard() : '';
  return `<div class="head"><div><h1>My settings</h1><div class="muted">Your profile, sign-in and personal connections.</div></div></div>
  ${await mySettingsProfileCard()}
  ${isBoss() ? await mySettingsTaxCard() : ''}
  ${isBoss() ? await workLimitsCard() : ''}
  ${cal}
  ${await tokensCard()}`;
}

/* ---- Personal tax profile (#/me/tax), WP-A5. Bounds mirror PersonalTaxIn in core/models.py. ---- */
const TP_MONTHLY_MAX = 100000, TP_YEAR_MAX = 1000000000;
const TP_NOTE = 'Estimate only – not tax advice. Not modelled: capital gains, foreign income, Verlustvortrag, church tax of the spouse, Pflege for several health insurers, and all individual special cases. Confirm with your Steuerberater.';

function tpNum(id, label, v, o = {}) {
  const val = v === null || v === undefined ? '' : v;
  return `<div class="field"${o.wrap || ''}><label for="tp_${id}">${label}</label><input type="number" id="tp_${id}" data-tp="${id}" step="${o.step || '0.01'}" min="${o.min ?? 0}" max="${o.max ?? TP_YEAR_MAX}" value="${esc(String(val))}"${o.ph ? ` placeholder="${esc(o.ph)}"` : ''}>${o.hint ? `<div class="small muted">${o.hint}</div>` : ''}</div>`;
}
function tpCheck(id, label, v) { return `<div class="field"><label><input type="checkbox" id="tp_${id}" data-tp="${id}"${v ? ' checked' : ''}> ${label}</label></div>`; }
function tpSel(id, label, v, opts, o = {}) {
  return `<div class="field"><label for="tp_${id}">${label}</label><select id="tp_${id}" data-tp="${id}"${o.change ? ` data-change="${o.change}"` : ''}>${opts.map(([k, l]) => `<option value="${k}"${String(v ?? '') === String(k) ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>${o.hint ? `<div class="small muted">${o.hint}</div>` : ''}</div>`;
}

function taxFormHTML(t, warnings) {
  const c = t.children || {}, h = t.health || {}, p = t.pension || {}, u = t.unemployment || {}, e = t.employment || null;
  const hide = ok => (ok ? '' : ' hidden');
  const year = new Date().getFullYear();
  return `<div id="tax-profile">
  ${t.stored ? '' : '<div class="banner info" id="tp-not-saved" role="status">Not saved yet – the estimate uses the defaults shown here.</div>'}
  <div id="tp-warnings">${(warnings || []).map(w => `<div class="banner" role="status">${esc(w)}</div>`).join('')}</div>
  <div class="grid g2">
  <div class="card" id="tp-assessment"><h2>Assessment</h2>
    ${tpSel('assessment', 'Assessment', t.assessment || 'single', [['single', 'Single (Einzelveranlagung)'], ['joint', 'Joint (Zusammenveranlagung)']])}
    ${tpNum('church_tax_percent', 'Church tax %', t.church_tax_percent ?? 0, { max: 15, step: '0.1', hint: '0 = none; 8 in Bayern and Baden-Württemberg, 9 elsewhere' })}
    ${tpNum('other_income_year', 'Other income per year € (after its own costs)', t.other_income_year ?? 0)}
    ${tpNum('other_deductions_year', 'Other deductions per year € (Sonderausgaben, besides insurance)', t.other_deductions_year ?? 0)}
    ${tpNum('other_insurance_year', 'Other insurance per year € (liability, disability, term life)', t.other_insurance_year ?? 0)}</div>
  <div class="card" id="tp-children"><h2>Children</h2>
    ${tpNum('children_count', 'Children with Kindergeld', c.count ?? 0, { max: 15, step: '1' })}
    ${tpCheck('children_full_allowance', 'Single assessment: the full child allowances were transferred to me', c.full_allowance)}
    ${tpCheck('children_pv_parent', 'I have or had a child (no childless surcharge in care insurance)', c.pv_parent)}
    ${tpNum('children_pv_under_25', 'Children under 25 (care insurance reduction)', c.pv_children_under_25 ?? 0, { max: 15, step: '1' })}</div>
  <div class="card" id="tp-health"><h2>Health and care insurance</h2>
    ${tpSel('health_mode', 'Health insurance', h.mode || 'gkv', [['gkv', 'Statutory (GKV)'], ['pkv', 'Private (PKV)'], ['none', 'None modelled']], { change: 'tpMode' })}
    <div data-tp-mode="gkv"${hide((h.mode || 'gkv') === 'gkv')}>
      ${tpCheck('health_krankengeld', 'Including Krankengeld (general rate 14.6 %)', h.krankengeld ?? true)}
      ${tpNum('health_zusatzbeitrag_percent', 'Zusatzbeitrag %', h.zusatzbeitrag_percent, { max: 10, step: '0.01', ph: 'average', hint: 'Empty = the average Zusatzbeitrag of the rate table.' })}</div>
    <div data-tp-mode="pkv"${hide(h.mode === 'pkv')}>
      ${tpNum('health_pkv_monthly', 'Premium per month € (total)', h.pkv_monthly ?? 0, { max: TP_MONTHLY_MAX })}
      ${tpNum('health_pkv_basis_monthly', 'Of which basic cover per month €', h.pkv_basis_monthly ?? 0, { max: TP_MONTHLY_MAX, hint: 'From your insurer\'s annual statement; must not exceed the premium.' })}
      ${tpNum('health_pkv_care_monthly', 'Care insurance per month €', h.pkv_care_monthly ?? 0, { max: TP_MONTHLY_MAX })}
      ${tpNum('health_pkv_employer_subsidy_monthly', 'Employer subsidy per month €', h.pkv_employer_subsidy_monthly ?? 0, { max: TP_MONTHLY_MAX })}</div></div>
  <div class="card" id="tp-pension"><h2>Pension</h2>
    ${tpSel('pension_mode', 'Pension insurance', p.mode || 'none', [['none', 'None'], ['statutory_income', 'Statutory, on income'], ['statutory_regular', 'Statutory, regular amount'], ['fixed', 'Fixed amount (Rürup, Versorgungswerk, voluntary)']], { change: 'tpMode' })}
    <div data-tp-mode="pension_fixed"${hide(p.mode === 'fixed')}>${tpNum('pension_fixed_monthly', 'Amount per month €', p.fixed_monthly ?? 0, { max: TP_MONTHLY_MAX })}</div>
    <h3 style="margin-top:12px">Unemployment insurance</h3>
    ${tpSel('unemployment_mode', 'Voluntary unemployment insurance', u.mode || 'none', [['none', 'None'], ['voluntary', 'Voluntary'], ['voluntary_half', 'Half rate (founding years)']])}</div>
  <div class="card" id="tp-employment"><h2>Employment</h2>
    ${tpCheck('has_employment', 'I also have employment income', !!e).replace('data-tp="has_employment"', 'data-tp="has_employment" data-change="tpMode"')}
    <div data-tp-mode="employment"${hide(!!e)}>
      ${tpNum('employment_gross_year', 'Gross wage per year € (before Entgeltumwandlung, without VWL and employer bAV)', e?.gross_year ?? 0)}
      ${tpCheck('employment_sv_liable', 'Subject to social insurance', e ? e.sv_liable : true)}
      ${tpSel('employment_steuerklasse', 'Steuerklasse', e?.steuerklasse ?? '', [['', '–'], ...[1, 2, 3, 4, 5, 6].map(n => [n, String(n)])], { hint: 'Only affects monthly withholding, not the annual tax.' })}
      ${tpNum('employment_bav_employee_year', 'bAV Entgeltumwandlung per year €', e?.bav_employee_year ?? 0, { max: 100000 })}
      ${tpNum('employment_bav_employer_year', 'bAV employer contribution per year €', e?.bav_employer_year ?? 0, { max: 100000 })}
      ${tpNum('employment_vwl_employer_year', 'VWL employer contribution per year €', e?.vwl_employer_year ?? 0, { max: 10000 })}
      ${tpNum('employment_werbungskosten_year', 'Werbungskosten per year €', e?.werbungskosten_year ?? 0, { hint: '0 = Pauschbetrag.' })}
      ${tpNum('employment_withheld_year', 'Tax withheld for the whole year € (Lohnsteuer + Soli + church tax)', e?.withheld_year, { ph: 'unknown' })}</div></div></div>
  <div class="actions" style="margin:14px 0"><button class="primary" data-a="tpSave" id="tp-save">Save tax profile</button></div>
  <p class="small muted" id="tp-footer">${TP_NOTE} Rates ${year} from the official tables; values marked as not confirmed are listed after saving.</p></div>`;
}

async function taxProfilePage() {
  const head = '<div class="head"><div><h1>Personal tax profile</h1><div class="muted">Used for the income tax estimate on Money &amp; Taxes.</div></div><a href="#/me" class="small">← My settings</a></div>';
  if (!isBoss()) return `${head}<div class="card"><p>Only the boss can see and change the personal tax profile.</p></div>`;
  let t;
  try { t = await api.get('settings/personal-tax'); } catch (e) { return `${head}<div class="banner" role="alert">${esc(e.message)}</div>`; }
  return head + taxFormHTML(t, []);
}

A.tpMode = () => {
  const val = id => $('#tp_' + id)?.value;
  const show = { gkv: val('health_mode') === 'gkv', pkv: val('health_mode') === 'pkv', pension_fixed: val('pension_mode') === 'fixed', employment: !!$('#tp_has_employment')?.checked };
  document.querySelectorAll('#tax-profile [data-tp-mode]').forEach(el => { el.hidden = !show[el.dataset.tpMode]; });
};

/* Read the form into a PersonalTaxIn body. Throws an Error with a readable message on invalid input. */
function tpCollect() {
  const el = id => $('#tp_' + id);
  const num = (id, label, max, int) => {
    const raw = el(id).value.trim();
    if (raw === '') return 0;
    const n = Number(raw);
    if (!Number.isFinite(n) || n < 0 || n > max || (int && !Number.isInteger(n))) throw new Error(`${label}: enter ${int ? 'a whole number' : 'a number'} from 0 to ${max}.`);
    return n;
  };
  const opt = (id, label, max) => (el(id).value.trim() === '' ? null : num(id, label, max));
  const mode = el('health_mode').value, pmode = el('pension_mode').value;
  const health = { mode };
  if (mode === 'gkv') { health.krankengeld = el('health_krankengeld').checked; health.zusatzbeitrag_percent = opt('health_zusatzbeitrag_percent', 'Zusatzbeitrag', 10); }
  if (mode === 'pkv') {
    health.pkv_monthly = num('health_pkv_monthly', 'PKV premium', TP_MONTHLY_MAX);
    health.pkv_basis_monthly = num('health_pkv_basis_monthly', 'PKV basic cover', TP_MONTHLY_MAX);
    health.pkv_care_monthly = num('health_pkv_care_monthly', 'PKV care insurance', TP_MONTHLY_MAX);
    health.pkv_employer_subsidy_monthly = num('health_pkv_employer_subsidy_monthly', 'Employer subsidy', TP_MONTHLY_MAX);
    if (health.pkv_basis_monthly > health.pkv_monthly) throw new Error('The basic-cover share must not exceed the premium.');
  }
  const pension = { mode: pmode };
  if (pmode === 'fixed') pension.fixed_monthly = num('pension_fixed_monthly', 'Pension amount', TP_MONTHLY_MAX);
  const body = {
    assessment: el('assessment').value,
    church_tax_percent: num('church_tax_percent', 'Church tax %', 15),
    other_income_year: num('other_income_year', 'Other income', TP_YEAR_MAX),
    other_deductions_year: num('other_deductions_year', 'Other deductions', TP_YEAR_MAX),
    other_insurance_year: num('other_insurance_year', 'Other insurance', TP_YEAR_MAX),
    children: { count: num('children_count', 'Children', 15, true), full_allowance: el('children_full_allowance').checked, pv_parent: el('children_pv_parent').checked, pv_children_under_25: num('children_pv_under_25', 'Children under 25', 15, true) },
    health, pension,
    unemployment: { mode: el('unemployment_mode').value },
    employment: null,
  };
  if (el('has_employment').checked) {
    const sk = el('employment_steuerklasse').value;
    body.employment = {
      gross_year: num('employment_gross_year', 'Gross wage', TP_YEAR_MAX),
      sv_liable: el('employment_sv_liable').checked,
      steuerklasse: sk === '' ? null : Number(sk),
      bav_employee_year: num('employment_bav_employee_year', 'bAV Entgeltumwandlung', 100000),
      bav_employer_year: num('employment_bav_employer_year', 'bAV employer', 100000),
      vwl_employer_year: num('employment_vwl_employer_year', 'VWL', 10000),
      werbungskosten_year: num('employment_werbungskosten_year', 'Werbungskosten', TP_YEAR_MAX),
      withheld_year: opt('employment_withheld_year', 'Tax withheld', TP_YEAR_MAX),
    };
  }
  return body;
}

A.tpSave = async () => {
  let body;
  try { body = tpCollect(); } catch (e) { toast(e.message, true); return; }
  const res = await api('PUT', 'settings/personal-tax', body);
  $('#tax-profile').outerHTML = taxFormHTML(res, res.warnings || []);
  toast('Tax profile saved');
};

PAGES.me = async sub => (sub === 'tax' ? taxProfilePage() : mySettingsPage());
