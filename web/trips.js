/* permitA38 – business trips: list, trip dialog (live breakdown from POST /trips/preview), reminder checklist,
   finalize, reopen, statement. The browser holds no tax rule: every figure comes from the API
   (docs/contracts/trips.md). Vanilla JS, no build step. Everything user-supplied goes through esc(). */

const TRIP_COUNTRY_CODES = ('AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ '
  + 'CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY '
  + 'HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ '
  + 'NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ '
  + 'TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW').split(' ');
const TRIP_DAY_LIMIT = 90;
const TRIP_MEALS = [['breakfast', 'Breakfast'], ['lunch', 'Lunch'], ['dinner', 'Dinner']];
const TRIP_MEAL_TEXT = { breakfast: 'breakfast', lunch: 'lunch', dinner: 'dinner' };
const TRIP_CHECK_CATEGORY = { accommodation: 'Accommodation', transportation: 'Transportation', material: 'Material' };
const TRIP_CC = [['project', 'Charge to the customer or project above'], ['general', 'General: not charged to a customer or project'], ['', 'Not decided yet']];
let TRIP_OPEN = null;                 // the open trip dialog: {id, trip (last stored Trip or null), ro (read-only)}
let TRIP_PREVIEW_TIMER = null, TRIP_PREVIEW_SEQ = 0;
let TRIP_REGION = null;

const countryName = c => { try { TRIP_REGION ||= new Intl.DisplayNames(['en'], { type: 'region' }); return TRIP_REGION.of(c) || c; } catch { return c; } };
const countryLabel = c => `${countryName(c)} (${c})`;
/** The code from a country entry: "Germany (DE)" or "DE"; '' when the text is not a country from the list. */
const parseCountry = s => { const t = String(s ?? '').trim(); const m = t.match(/\(([A-Za-z]{2})\)$/) || t.match(/^([A-Za-z]{2})$/); return m ? m[1].toUpperCase() : ''; };
/** Dates of a trip, inclusive, or null when end is not after start or the trip lasts more than 90 days. */
function tripDates(start, end) {
  if (!start || !end || end <= start) return null;
  const out = [];
  for (let d = start.slice(0, 10); d <= end.slice(0, 10); d = addDays(d, 1)) { out.push(d); if (out.length > TRIP_DAY_LIMIT) return null; }
  return out;
}
const dayLabel = d => `${dayName(d)} ${parseD(d).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' })}`;
const tripCountryOptions = () => TRIP_COUNTRY_CODES.map(c => countryLabel(c)).sort((a, b) => a.localeCompare(b)).map(l => `<option value="${esc(l)}"></option>`).join('');
/** Trips for the bill dialog's trip select (newest first, as the server sends them). */
const tripOptions = keep => {
  const opts = (S.d.trips || []).map(t => [t.id, `${fmtDate(t.start.slice(0, 10))} ${t.title || t.purpose}`]);
  if (keep && !opts.some(o => o[0] === keep)) opts.push([keep, 'Other trip']);   // never drop a link the list does not know: saving would unlink the bill
  return opts;
};
const destinationOf = t => {
  const names = (t.places || []).map(p => (p.city ? `${p.city} (${countryName(p.country)})` : countryName(p.country)));
  if (!names.length) names.push(countryName('DE'));
  return [...new Set(names)].join(' → ');
};

/* ---------- list page ---------- */
PAGES.trips = async () => {
  await loadAll(['trips', 'customers', 'projects', 'expenses']);
  const list = S.d.trips || [];
  return `<div class="head"><div><h1>Business trips</h1><div class="muted">Allowance, mileage and the bills of each trip, with a checklist of what to collect.</div></div>${can('trips', 'write') ? '<button class="primary" data-a="newTrip">+ New trip</button>' : ''}</div>
  <div class="card">${list.length ? `<table class="trip-list"><caption class="sr-only">Business trips</caption><tr><th scope="col">Period</th><th scope="col">Destination</th><th scope="col">Purpose</th><th scope="col" class="num">Allowance</th><th scope="col" class="num">Mileage</th><th scope="col" class="num">Bills</th><th scope="col">Status</th></tr>${list.map(tripRowHTML).join('')}</table>` : '<div class="empty">No business trips yet.</div>'}</div>`;
};
function tripRowHTML(t) {
  const period = `${fmtDate(t.start.slice(0, 10))} – ${fmtDate(t.end.slice(0, 10))}`;
  const final = t.status === 'final';
  return `<tr><td class="nowrap"><button type="button" class="linkbtn" data-a="editTrip" data-id="${esc(t.id)}">${esc(period)}</button></td>
    <td>${esc(destinationOf(t))}</td>
    <td>${esc(t.purpose || '')}${t.title ? `<div class="small muted">${esc(t.title)}</div>` : ''}</td>
    <td class="num">${eur(t.allowance_total)}</td>
    <td class="num">${num(t.mileage_km, 1)} km<div class="small muted">${eur(t.mileage_total)}</div></td>
    <td class="num">${t.bills?.count ?? 0}<div class="small muted">${eur(t.bills?.net)}</div></td>
    <td><span class="pill ${final ? 'good' : ''}">${final ? 'Final' : 'Draft'}</span></td></tr>`;
}

/* ---------- day and mileage tables ---------- */
/** One row per day. A day without its own destination keeps the place of the day before. */
function fillDays(dates, old) {
  const by = Object.fromEntries(old.map(r => [r.date, r]));
  let last = null;
  return dates.map(date => {
    const r = by[date] ? { ...by[date] } : { date, country: last?.country ?? countryLabel('DE'), city: last?.city ?? '', breakfast: false, lunch: false, dinner: false };
    last = r;
    return r;
  });
}
/** Rows of a stored trip: the destination in force on each day (the last place starting on or before it). */
function tripRowsOf(t) {
  const dates = tripDates(t.start, t.end) || [];
  const places = (t.places || []).slice().sort((a, b) => (a.from_date < b.from_date ? -1 : 1));
  const meals = Object.fromEntries((t.meals_provided || []).map(m => [m.date, m]));
  return dates.map(date => {
    const p = places.filter(x => x.from_date <= date).pop() || places[0] || { country: 'DE' };
    const m = meals[date] || {};
    return { date, country: countryLabel(p.country), city: p.city || '', breakfast: !!m.breakfast, lunch: !!m.lunch, dinner: !!m.dinner };
  });
}
function tripRowsFromDOM() {
  return [...document.querySelectorAll('#t_days tr[data-day]')].map(tr => ({
    date: tr.dataset.day,
    country: tr.querySelector('[data-f=country]').value,
    city: tr.querySelector('[data-f=city]').value,
    breakfast: tr.querySelector('[data-f=breakfast]').checked,
    lunch: tr.querySelector('[data-f=lunch]').checked,
    dinner: tr.querySelector('[data-f=dinner]').checked,
  }));
}
function dayRowHTML(r, ro) {
  const dis = ro ? 'disabled' : '', on = fmtDate(r.date);
  return `<tr data-day="${esc(r.date)}"><th scope="row" class="nowrap">${esc(dayLabel(r.date))}</th>
    <td><input data-f="country" list="t_countries" value="${esc(r.country)}" autocomplete="off" aria-label="Country on ${esc(on)}" ${dis}></td>
    <td><input data-f="city" value="${esc(r.city)}" maxlength="200" aria-label="City on ${esc(on)}" ${dis}></td>
    ${TRIP_MEALS.map(([k, l]) => `<td class="center"><input type="checkbox" data-f="${k}" ${r[k] ? 'checked' : ''} aria-label="${esc(l)} provided on ${esc(on)}" ${dis}></td>`).join('')}</tr>`;
}
function daysHTML(rows, ro) {
  if (!rows.length) return '<p class="hint" role="status">Set the start and the end (the end after the start, at most 90 days) to list the days.</p>';
  return `<div class="table-wrap"><table class="trip-table lines"><caption class="sr-only">Days, destinations and meals provided</caption>
    <thead><tr><th scope="col">Day</th><th scope="col">Country</th><th scope="col">City</th><th scope="col">Breakfast provided</th><th scope="col">Lunch provided</th><th scope="col">Dinner provided</th></tr></thead>
    <tbody>${rows.map(r => dayRowHTML(r, ro)).join('')}</tbody></table></div>`;
}
function rebuildDays() {
  const rows = fillDays(tripDates($('#t_start').value, $('#t_end').value) || [], tripRowsFromDOM());
  $('#t_days').innerHTML = daysHTML(rows, TRIP_OPEN.ro);
}
function readKmRows() {
  return [...document.querySelectorAll('#t_km tr[data-km]')].map(tr => ({
    date: tr.querySelector('[data-f=date]').value, from: tr.querySelector('[data-f=from]').value, to: tr.querySelector('[data-f=to]').value,
    km: tr.querySelector('[data-f=km]').value, purpose: tr.querySelector('[data-f=purpose]').value,
  }));
}
function kmRowHTML(r, i, ro) {
  const dis = ro ? 'disabled' : '', n = i + 1;
  return `<tr data-km="${i}"><td><input type="date" data-f="date" value="${esc(r.date)}" aria-label="Mileage ${n}: date" ${dis}></td>
    <td><input data-f="from" value="${esc(r.from)}" maxlength="200" aria-label="Mileage ${n}: from" ${dis}></td>
    <td><input data-f="to" value="${esc(r.to)}" maxlength="200" aria-label="Mileage ${n}: to" ${dis}></td>
    <td><input type="number" data-f="km" step="0.1" min="0.1" max="5000" inputmode="decimal" value="${esc(r.km)}" aria-label="Mileage ${n}: km" ${dis}></td>
    <td><input data-f="purpose" value="${esc(r.purpose)}" maxlength="200" aria-label="Mileage ${n}: purpose" ${dis}></td>
    <td>${ro ? '' : `<button type="button" class="ghost sm" data-kmdel="${i}" aria-label="Remove mileage ${n}">✕</button>`}</td></tr>`;
}
function setKmRows(rows) { $('#t_km').innerHTML = rows.map((r, i) => kmRowHTML(r, i, TRIP_OPEN.ro)).join(''); }
function addKmRow() {
  const rows = readKmRows();
  rows.push({ date: $('#t_start').value.slice(0, 10), from: '', to: '', km: '', purpose: '' });
  setKmRows(rows);
  $('#t_km tr:last-child [data-f=from]')?.focus();
  scheduleTripPreview();
}
function removeKmRow(i) {
  const rows = readKmRows();
  rows.splice(i, 1);
  setKmRows(rows);
  $('#t_km_add')?.focus();
  scheduleTripPreview();
}

/* ---------- form -> request body ---------- */
/** Reads the dialog into a TripIn body. Throws a plain message for what the user has to complete first. */
function tripBody(status) {
  const val = id => $('#' + id)?.value ?? '';
  const purpose = val('t_purpose').trim();
  if (!purpose) throw new Error('Enter the purpose of the trip');
  const start = val('t_start'), end = val('t_end');
  if (!start || !end) throw new Error('Enter the start and the end of the trip');
  if (!tripDates(start, end)) throw new Error('The trip must end after it starts and last at most 90 days');
  let cur = null;
  const days = tripRowsFromDOM().map(r => {
    const text = r.country.trim();
    const country = parseCountry(text) || (text ? '' : (cur ? cur.country : 'DE'));
    if (!country) throw new Error(`Pick a country from the list on ${dayLabel(r.date)}`);
    const city = r.city.trim() || (!text && cur ? cur.city : '');
    cur = { date: r.date, country, city, breakfast: r.breakfast, lunch: r.lunch, dinner: r.dinner };
    return cur;
  });
  // only the places where the destination changes are sent (the first day always starts one)
  const places = [];
  let key = '';
  for (const d of days) {
    const k = d.country + '|' + d.city;
    if (k === key) continue;
    key = k;
    places.push(d.city ? { from_date: d.date, country: d.country, city: d.city } : { from_date: d.date, country: d.country });
  }
  const meals_provided = days.filter(d => d.breakfast || d.lunch || d.dinner)
    .map(d => ({ date: d.date, breakfast: !!d.breakfast, lunch: !!d.lunch, dinner: !!d.dinner }));
  const mileage = readKmRows().filter(r => [r.date, r.from, r.to, r.km, r.purpose].some(x => String(x).trim() !== '')).map((r, i) => {
    if (!r.date) throw new Error(`Mileage row ${i + 1}: enter the date`);
    const km = toNum(r.km);
    if (Number.isNaN(km)) throw new Error(`Mileage row ${i + 1}: enter the km`);
    const row = { date: r.date, from: r.from.trim(), to: r.to.trim(), km };
    if (r.purpose.trim()) row.purpose = r.purpose.trim();
    return row;
  });
  const cc = document.querySelector('input[name="t_cc"]:checked');
  const not_needed = [...document.querySelectorAll('input[data-not]:checked')].map(i => i.dataset.not);
  return {
    title: val('t_title').trim(), purpose, customer_id: val('t_customer') || null, project_id: val('t_project') || null,
    start, end, places, meals_provided, mileage, cost_centre: cc && cc.value ? cc.value : null, not_needed, status,
  };
}

/* ---------- live breakdown ---------- */
function scheduleTripPreview() { clearTimeout(TRIP_PREVIEW_TIMER); TRIP_PREVIEW_TIMER = setTimeout(runTripPreview, 300); }
async function runTripPreview() {
  clearTimeout(TRIP_PREVIEW_TIMER);
  const box = $('#t_breakdown');
  if (!box || !TRIP_OPEN || TRIP_OPEN.ro) return;
  const seq = ++TRIP_PREVIEW_SEQ;
  let body;
  try { body = tripBody('draft'); }
  catch (e) { box.innerHTML = `<p class="hint" role="status">${esc(e.message)}. The figures appear once it is complete.</p>`; return; }
  try {
    const res = await api('POST', 'trips/preview', body);
    if (seq === TRIP_PREVIEW_SEQ && $('#t_breakdown') === box) renderBreakdown(res);
  } catch (e) {
    if (seq === TRIP_PREVIEW_SEQ && $('#t_breakdown') === box) box.innerHTML = `<p class="banner" role="alert">${esc(e.message)}</p>`;
  }
}
/** One sentence per day, built from the day fields: base, each meal reduction and the result. */
function explainDay(d) {
  const place = `${countryName(d.country)}${d.city ? ', ' + d.city : ''}`;
  const reductions = (d.reductions || []).map(r => ` − ${TRIP_MEAL_TEXT[r.meal] || r.meal} provided ${eur(r.amount)}`).join('');
  return `<li><b>${esc(dayLabel(d.date))}</b>: ${esc(num(d.absence_hours, 2))} h in ${esc(place)}: ${esc(eur(d.base) + reductions)} = <b>${esc(eur(d.amount))}</b>${d.note ? `<div class="small">Note: ${esc(d.note)}</div>` : ''}</li>`;
}
function renderBreakdown(res) {
  const box = $('#t_breakdown');
  if (!box) return;
  const days = res.days || [];
  box.innerHTML = `${days.length ? `<ol class="trip-explain">${days.map(explainDay).join('')}</ol>` : '<p class="muted">No days yet.</p>'}
    <p>Daily allowance total: <b>${esc(eur(res.allowance_total))}</b></p>
    <p>Mileage: ${esc(num(res.mileage_km, 1))} km${res.mileage_rate != null ? ` × ${esc(eur(res.mileage_rate))} per km` : ''} = <b>${esc(eur(res.mileage_total))}</b></p>
    <p class="small muted">Rules version ${esc(res.rules_version || '')}. The server computes these figures; a save stores the same ones.</p>`;
}

/* ---------- checklist and bills ---------- */
function checkDetail(it) {
  if (it.id === 'allowance') return it.state === 'not_needed' ? 'No days over 8 hours: no allowance.' : `${it.count} ${it.count === 1 ? 'day' : 'days'} with allowance: ${eur(it.sum)}`;
  if (it.id === 'food') return '';
  if (it.state === 'open') return 'Nothing added yet.';
  if (it.state === 'not_needed') return 'Marked as not needed.';
  return `${it.count} ${it.count === 1 ? 'entry' : 'entries'}: ${eur(it.sum)}`;
}
function checkItemHTML(it, trip, ro) {
  const label = { added: 'Added', open: 'Open', not_needed: 'Not needed' }[it.state] || it.state;
  const tone = { added: 'good', open: 'warn' }[it.state] || '';
  const cat = TRIP_CHECK_CATEGORY[it.id];
  const add = !ro && can('expenses', 'write') && it.state === 'open' && cat
    ? `<button type="button" class="ghost sm" data-a="editExpense" data-trip="${esc(trip.id)}" data-category="${esc(cat)}" data-project="${esc(trip.project_id || '')}">Add bill</button>` : '';
  const skip = it.id === 'allowance' ? '' : `<label class="check-skip"><input type="checkbox" data-not="${esc(it.id)}" id="t_not_${esc(it.id)}" ${it.state === 'not_needed' ? 'checked' : ''} ${ro ? 'disabled' : ''}> Not needed</label>`;
  return `<li class="check-item"><div><b>${esc(it.label)}</b> <span class="pill ${tone}">${esc(label)}</span></div>
    <div class="small muted">${esc(it.hint || '')}</div>
    ${checkDetail(it) ? `<div>${esc(checkDetail(it))}</div>` : ''}
    ${add || skip ? `<div class="check-actions">${add}${skip}</div>` : ''}</li>`;
}
function ccHTML(trip, it, ro) {
  const cur = trip ? (trip.cost_centre ?? '') : '';
  const tone = it?.state === 'added' ? 'good' : 'warn';
  return `<li class="check-item"><fieldset ${ro ? 'disabled' : ''}><legend><b>Cost centre / project</b> <span class="pill ${tone}">${it?.state === 'added' ? 'Added' : 'Open'}</span></legend>
    <p class="small muted">Decide what the trip is charged to. The checklist stays open until you choose.</p>
    ${TRIP_CC.map(([v, l]) => `<label class="radio"><input type="radio" name="t_cc" id="t_cc_${v || 'none'}" value="${esc(v)}" ${cur === v ? 'checked' : ''}> ${esc(l)}</label>`).join('')}</fieldset></li>`;
}
function checklistHTML(trip, ro) {
  const items = trip?.checklist || [];
  const body = items.length
    ? items.map(i => (i.id === 'cost_centre' ? ccHTML(trip, i, ro) : checkItemHTML(i, trip, ro))).join('')
    : ccHTML(trip, null, ro);
  return `<ol class="trip-check">${body}</ol>${trip ? '' : '<p class="small muted">The other reminder lines appear after the first save.</p>'}`;
}
function billsHTML(trip, ro) {
  const bills = (S.d.expenses || []).filter(e => e.trip_id === trip.id).sort((a, b) => (a.date < b.date ? -1 : 1));
  const b = trip.bills || { count: 0, net: 0, vat: 0 };
  return `<h3>Bills of this trip</h3>
    <p>${b.count} ${b.count === 1 ? 'bill' : 'bills'}: net ${esc(eur(b.net))}, VAT ${esc(eur(b.vat))}</p>
    ${bills.length ? `<table class="lines"><caption class="sr-only">Bills of this trip</caption><tr><th scope="col">Date</th><th scope="col">Vendor</th><th scope="col">Category</th><th scope="col" class="num">Net</th><th scope="col" class="num">VAT</th></tr>${bills.map(e => `<tr><td class="nowrap">${esc(fmtDate(e.date))}</td><td>${esc(e.vendor || '')}</td><td>${esc(e.category || '')}</td><td class="num">${eur(e.net)}</td><td class="num">${eur(e.vat)}</td></tr>`).join('')}</table>` : '<p class="small muted">No bills linked yet.</p>'}
    ${ro || !can('expenses', 'write') ? '' : `<p><button type="button" class="ghost" data-a="editExpense" data-trip="${esc(trip.id)}" data-project="${esc(trip.project_id || '')}">Add bill to this trip</button></p>`}`;
}

/* ---------- dialog ---------- */
function renderTripDialog(trip) {
  const ro = !can('trips', 'write') || trip?.status === 'final';
  TRIP_OPEN = { id: trip?.id || null, trip, ro };
  const final = trip?.status === 'final';
  const v = trip || { title: '', purpose: '', customer_id: null, project_id: null, start: `${todayISO()}T08:00`, end: `${addDays(todayISO(), 1)}T18:00`, mileage: [] };
  const dis = ro ? 'disabled' : '';
  const sel = (opts, cur) => opts.map(([val, label]) => `<option value="${esc(val)}" ${String(val) === String(cur ?? '') ? 'selected' : ''}>${esc(label)}</option>`).join('');
  const customers = [['', '— no customer —'], ...(S.d.customers || []).map(c => [c.id, c.name])];
  const rows = trip ? tripRowsOf(trip) : fillDays(tripDates(v.start, v.end) || [], []);
  const kmRows = (v.mileage || []).map(m => ({ date: m.date, from: m.from || '', to: m.to || '', km: String(m.km ?? ''), purpose: m.purpose || '' }));
  const banner = ro ? `<div class="banner info" role="status">${final ? (isBoss() ? 'This trip is final. Reopen it to change it.' : 'This trip is final. Only the boss can reopen it.') : 'Read-only for your role.'}</div>` : '';
  const left = [];
  if (trip && !ro) left.push(`<button type="button" class="danger" data-a="tripDelete" data-id="${esc(trip.id)}">Delete</button>`);
  if (trip) left.push(`<a class="btn" href="${esc(api.url(`trips/${encodeURIComponent(trip.id)}/statement.pdf`))}" target="_blank" rel="noopener">Statement (PDF)</a>`);
  const right = [];
  if (trip && final && isBoss()) right.push(`<button type="button" data-a="tripReopen" data-id="${esc(trip.id)}">Reopen trip</button>`);
  if (!ro) right.push('<button type="button" data-a="tripFinalize">Finalize trip</button>');
  right.push('<button type="button" data-a="tripClose">Close</button>');
  if (!ro) right.push('<button type="button" class="primary" data-a="tripSave">Save</button>');
  modal(`<h2>${esc(trip ? (trip.title || trip.purpose) : 'New business trip')}</h2>
    ${trip ? `<p><span class="pill ${final ? 'good' : ''}">${final ? 'Final' : 'Draft'}</span></p>` : ''}${banner}
    <div id="t_form">
      <div class="row"><div class="field"><label for="t_title">Title</label><input id="t_title" value="${esc(v.title || '')}" maxlength="200" ${dis}></div>
        <div class="field"><label for="t_purpose">Purpose (printed on the statement)</label><input id="t_purpose" value="${esc(v.purpose || '')}" maxlength="200" required aria-required="true" ${dis}></div></div>
      <div class="row"><div class="field"><label for="t_customer">Customer</label><select id="t_customer" ${dis}>${sel(customers, v.customer_id)}</select></div>
        <div class="field"><label for="t_project">Project</label><select id="t_project" ${dis}>${sel(projectOptions(), v.project_id)}</select></div></div>
      <div class="row"><div class="field"><label for="t_start">Start</label><input id="t_start" type="datetime-local" value="${esc(v.start)}" ${dis}></div>
        <div class="field"><label for="t_end">End</label><input id="t_end" type="datetime-local" value="${esc(v.end)}" ${dis}></div></div>
      <datalist id="t_countries">${tripCountryOptions()}</datalist>
      <h3>Days and destinations</h3>
      <p class="small muted">One line per day. A day without its own entry keeps the place of the day before. Tick each meal the hotel or the host provided.</p>
      <div id="t_days">${daysHTML(rows, ro)}</div>
      <h3>Mileage with a private car</h3>
      <div class="table-wrap"><table class="trip-table lines"><caption class="sr-only">Mileage</caption>
        <thead><tr><th scope="col">Date</th><th scope="col">From</th><th scope="col">To</th><th scope="col">km</th><th scope="col">Purpose</th><th scope="col"><span class="sr-only">Remove</span></th></tr></thead>
        <tbody id="t_km">${kmRows.map((r, i) => kmRowHTML(r, i, ro)).join('')}</tbody></table></div>
      ${ro ? '' : '<p><button type="button" class="ghost sm" id="t_km_add">+ Add mileage</button></p>'}
      <h3>Breakdown</h3>
      <section id="t_breakdown" aria-live="polite" aria-label="Breakdown of the figures"></section>
      <h3>Reminder checklist</h3>
      <div id="t_checklist">${checklistHTML(trip, ro)}</div>
      ${trip ? billsHTML(trip, ro) : ''}
    </div>
    <div class="actions"><div class="trip-left">${left.join('')}</div>${right.join('')}</div>`, true);
  const m = $('#modal-root .modal');
  m.addEventListener('click', tripClick);
  m.addEventListener('change', tripChange);
  m.addEventListener('input', tripInput);
  m.addEventListener('keydown', e => { if (e.key === 'Escape') closeTrip(); });
  if (ro) { if (trip) renderBreakdown(trip); $('#modal-root [data-a=tripClose]')?.focus(); }   // keyboard focus stays in the dialog
  else runTripPreview();
}
function tripClick(e) {
  const b = e.target.closest('button');
  if (!b || !TRIP_OPEN) return;
  if (b.id === 't_km_add') return addKmRow();
  if (b.dataset.kmdel !== undefined) return removeKmRow(+b.dataset.kmdel);
}
function tripChange(e) {
  if (!TRIP_OPEN || TRIP_OPEN.ro) return;
  const t = e.target;
  if (t.id === 't_start' || t.id === 't_end') { rebuildDays(); return scheduleTripPreview(); }
  if (t.dataset.not !== undefined || t.name === 't_cc') return persistChecklist();
  scheduleTripPreview();
}
function tripInput(e) { if (TRIP_OPEN && !TRIP_OPEN.ro && e.target.closest('#t_form')) scheduleTripPreview(); }
/** Not-needed and cost-centre decisions are stored at once (a saved trip), so the checklist states are the server's. */
async function persistChecklist() {
  if (!TRIP_OPEN?.id || TRIP_OPEN.ro) return;   // a new trip keeps them until its first save
  const focus = document.activeElement?.id || '';
  let t = null;
  try { t = await tripWrite(tripBody('draft')); }
  catch (e) { toast(e.message, true); }
  if (t) { TRIP_OPEN.trip = t; toast('Saved'); await loadAll(['trips']); }
  $('#t_checklist').innerHTML = checklistHTML(TRIP_OPEN.trip, false);
  if (focus) document.getElementById(focus)?.focus();
  if (t) scheduleTripPreview();
}
/** POST for a new trip, PUT for a stored one; force skips only the open-checklist check. */
function tripWrite(body, force = false) {
  const q = force ? '?force=true' : '';
  return TRIP_OPEN?.id ? api('PUT', `trips/${TRIP_OPEN.id}${q}`, body) : api('POST', `trips${q}`, body);
}
async function tripAfterWrite(t) {
  await loadAll(['trips', 'expenses']);
  renderTripDialog(t);
  render();
}
function closeTrip() {
  clearTimeout(TRIP_PREVIEW_TIMER);
  TRIP_OPEN = null;
  closeModal();
  render();
}

/** A second click while a write runs is ignored (no double trip, no double finalize). */
let TRIP_BUSY = false;
const once = fn => async (...a) => { if (TRIP_BUSY) return; TRIP_BUSY = true; try { return await fn(...a); } finally { TRIP_BUSY = false; } };

/* A bill started from a trip returns to the trip when its dialog closes, so the checklist shows the new state. */
const editExpenseBase = A.editExpense;
A.editExpense = el => {
  editExpenseBase(el);
  const id = el?.dataset?.trip;
  if (!id) return;
  const prev = closeModal.onClose;
  closeModal.onClose = () => { if (prev) prev(); setTimeout(() => guard(async () => { await loadAll(['trips', 'expenses']); if (!$('#modal-root .modal')) renderTripDialog(await api('GET', `trips/${encodeURIComponent(id)}`)); })(), 0); };
};

A.newTrip = () => renderTripDialog(null);
A.editTrip = async el => renderTripDialog(await api('GET', `trips/${el.dataset.id}`));
A.tripClose = () => closeTrip();
A.tripSave = once(async () => {
  const t = await tripWrite(tripBody('draft'));
  toast('Trip saved');
  await tripAfterWrite(t);
});
A.tripFinalize = once(async () => {
  const body = tripBody('final');
  let t;
  try { t = await tripWrite(body); }
  catch (e) {
    if (e.status !== 409 || !/Checklist has open items/.test(e.message)) throw e;
    if (!confirm(`${e.message}\n\nFinalize anyway? The open lines stay open in the statement.`)) return;
    t = await tripWrite(body, true);
  }
  toast('Trip finalized');
  await tripAfterWrite(t);
});
A.tripReopen = once(async el => {
  const t = await api('POST', `trips/${el.dataset.id}/reopen`, {});
  toast('Trip reopened');
  await tripAfterWrite(t);
});
A.tripDelete = once(async el => {
  if (!confirm('Delete this draft trip? Its bills stay and lose the trip link.')) return;
  await api('DELETE', `trips/${el.dataset.id}`);
  toast('Trip deleted');
  await loadAll(['trips', 'expenses']);
  closeTrip();
});
