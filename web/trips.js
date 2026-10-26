/* permitA38 – business trips: list, the new-trip dialog and the trip page as steps (plan, itinerary, expenses, review),
   with planned items, mileage, checklist, finalize, reopen and statement. The browser holds no tax rule: every figure
   comes from the API (docs/contracts/trips.md). Expected costs are planning figures only. Vanilla JS, no build step.
   Everything user-supplied goes through esc(). */

const TRIP_COUNTRY_CODES = ('AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ '
  + 'CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY '
  + 'HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ '
  + 'NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ '
  + 'TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW').split(' ');
const TRIP_DAY_LIMIT = 90;
const TRIP_STEPS = [['plan', 'Plan'], ['itinerary', 'Itinerary'], ['expenses', 'Expenses'], ['review', 'Review']];
const TRIP_MEALS = [['breakfast', 'Breakfast'], ['lunch', 'Lunch'], ['dinner', 'Dinner']];
const TRIP_MEAL_TEXT = { breakfast: 'breakfast', lunch: 'lunch', dinner: 'dinner' };
const TRIP_CHECK_CATEGORY = { accommodation: 'Accommodation', transportation: 'Transportation', material: 'Material' };
const TRIP_CC = [['project', 'Charge to the customer or project above'], ['general', 'General: not charged to a customer or project'], ['', 'Not decided yet']];
const TRIP_MODES = [['', '—'], ['train', 'Train'], ['flight', 'Flight'], ['car', 'Car'], ['taxi', 'Taxi'], ['bus', 'Bus'], ['other', 'Other']];
const TRIP_ITEM_TYPES = [['hotel', 'Hotel'], ['train', 'Train'], ['flight', 'Flight'], ['car', 'Car (private)'], ['taxi', 'Taxi / other transport'], ['other', 'Other']];
const TRIP_ITEM_LABEL = { hotel: 'Hotel', train: 'Train', flight: 'Flight', taxi: 'Taxi / other', other: 'Other' };
const TRIP_ITEM_CATEGORY = { hotel: 'Accommodation', train: 'Transportation', flight: 'Transportation', taxi: 'Transportation', other: 'Other' };
let TRIP_PAGE = null;          // the open trip page: {trip (last stored Trip), step, ro, legs (working itinerary), rate}
let TRIP_PREVIEW_TIMER = null, TRIP_PREVIEW_SEQ = 0;
let TRIP_REGION = null, TRIP_COUNTRY_OPTS = null;
let TRIP_QUEUE = Promise.resolve();   // the trip writes run one after the other

const countryName = c => { try { TRIP_REGION ||= new Intl.DisplayNames(['en'], { type: 'region' }); return TRIP_REGION.of(c) || c; } catch { return c; } };
const countryLabel = c => `${countryName(c)} (${c})`;
/** The code from a country entry: "Germany (DE)" or "DE"; '' when the text is not a country from the list. */
const parseCountry = s => { const t = String(s ?? '').trim(); const m = t.match(/\(([A-Za-z]{2})\)$/) || t.match(/^([A-Za-z]{2})$/); return m ? m[1].toUpperCase() : ''; };
/** Country options of a select: [code, label] sorted by name. */
const tripCountryOpts = () => TRIP_COUNTRY_OPTS ||= TRIP_COUNTRY_CODES.map(c => [c, countryLabel(c)]).sort((a, b) => a[1].localeCompare(b[1]));
/** Dates of a trip, inclusive, or null when end is not after start or the trip lasts more than 90 days. */
function tripDates(start, end) {
  if (!start || !end || end <= start) return null;
  const out = [];
  for (let d = start.slice(0, 10); d <= end.slice(0, 10); d = addDays(d, 1)) { out.push(d); if (out.length > TRIP_DAY_LIMIT) return null; }
  return out;
}
const dayLabel = d => `${dayName(d)} ${parseD(d).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' })}`;
const fmtDT = s => s ? `${fmtDate(s.slice(0, 10))} ${s.slice(11, 16)}` : '';
const selOpts = (opts, cur) => opts.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(cur ?? '') ? 'selected' : ''}>${esc(l)}</option>`).join('');
/** Trips for the bill dialog's trip select (newest first, as the server sends them). */
const tripOptions = keep => {
  const opts = (S.d.trips || []).map(t => [t.id, `${fmtDate(t.start.slice(0, 10))} ${t.title || t.purpose}`]);
  if (keep && !opts.some(o => o[0] === keep)) opts.push([keep, 'Other trip']);   // never drop a link the list does not know: saving would unlink the bill
  return opts;
};
/** Planned items of a trip for the bill dialog's item select. */
function tripItemOptions(tripId) {
  const t = (S.d.trips || []).find(x => x.id === tripId);
  return (t?.items || []).map(i => [i.id, `${fmtDate(i.date)} ${TRIP_ITEM_LABEL[i.category] || i.category}${i.text ? ': ' + i.text : ''}`]);
}
const destinationOf = t => {
  const names = (t.places || []).map(p => (p.city ? `${p.city} (${countryName(p.country)})` : countryName(p.country)));
  if (!names.length) names.push(countryName('DE'));
  return [...new Set(names)].join(' → ');
};
/* exact cents (BigInt) of a money value, and the euro amount of cents */
const tripCents = x => { const p = Money.parse(x ?? 0); return Money.roundDiv(p.n * 100n, 10n ** BigInt(p.s)); };
const eurC = c => eur(Money.toNumber(c));
const billCents = b => tripCents(b.net || 0) + tripCents(b.vat || 0);
/** Private car: km x rate, exact cents, half up. The rate comes from the server (preview). */
const kmCost = (km, rate) => { const a = Money.parse(km), b = Money.parse(rate); return Money.toNumber(Money.roundDiv(a.n * b.n * 100n, 10n ** BigInt(a.s + b.s))); };
/** A trip write waits for the writes already started: a Back, Next or inline edit is queued, never dropped. */
const tripWrite = fn => { const run = TRIP_QUEUE.then(fn); TRIP_QUEUE = run.catch(() => {}); return run; };
/** A button that starts a write: a second click while its own write is queued or running is ignored. */
const once = fn => { let pending = false; return (...a) => { if (pending) return; pending = true; return tripWrite(() => fn(...a)).finally(() => { pending = false; }); }; };

/* ---------- list page ---------- */
PAGES.trips = async (id, step) => {
  if (!id) {
    await loadAll(['trips', 'customers', 'projects', 'expenses']);
    return tripListHTML();
  }
  await loadAll(['customers', 'projects', 'expenses', 'trips']);
  const trip = await api('GET', `trips/${encodeURIComponent(id)}`);
  const cur = TRIP_STEPS.some(s => s[0] === step) ? step : 'expenses';   // a stored trip opens on its expenses
  TRIP_PAGE = { trip, step: cur, ro: !can('trips', 'write') || trip.status === 'final', legs: null, rate: null };
  // the private car rate comes from the server: only needed for the mileage rows of the expenses table
  if (cur === 'expenses' && trip.mileage?.length && can('trips', 'write')) {
    try { TRIP_PAGE.rate = (await api('POST', 'trips/preview', tripIn(trip))).mileage_rate ?? null; } catch { TRIP_PAGE.rate = null; }
  }
  render.after = tripAfter;
  return tripPageHTML();
};
function tripListHTML() {
  const list = S.d.trips || [];
  return `<div class="head"><div><h1>Business trips</h1><div class="muted">Allowance, mileage and the bills of each trip, with a checklist of what to collect.</div></div>${can('trips', 'write') ? '<button class="primary" data-a="newTrip">+ New trip</button>' : ''}</div>
  <div class="card">${list.length ? `<table class="trip-list"><caption class="sr-only">Business trips</caption><tr><th scope="col">Period</th><th scope="col">Destination</th><th scope="col">Purpose</th><th scope="col" class="num">Allowance</th><th scope="col" class="num">Mileage</th><th scope="col" class="num">Bills</th><th scope="col">Status</th></tr>${list.map(tripRowHTML).join('')}</table>` : '<div class="empty">No business trips yet.</div>'}</div>`;
}
function tripRowHTML(t) {
  const period = `${fmtDate(t.start.slice(0, 10))} – ${fmtDate(t.end.slice(0, 10))}`;
  const final = t.status === 'final';
  return `<tr><td class="nowrap"><a href="#/trips/${esc(t.id)}">${esc(period)}</a></td>
    <td>${esc(destinationOf(t))}</td>
    <td>${esc(t.purpose || '')}${t.title ? `<div class="small muted">${esc(t.title)}</div>` : ''}</td>
    <td class="num">${eur(t.allowance_total)}</td>
    <td class="num">${num(t.mileage_km, 1)} km<div class="small muted">${eur(t.mileage_total)}</div></td>
    <td class="num">${t.bills?.count ?? 0}<div class="small muted">${eur(t.bills?.net)}</div></td>
    <td><span class="pill ${final ? 'good' : ''}">${final ? 'Final' : 'Draft'}</span></td></tr>`;
}

/* ---------- new trip dialog ---------- */
A.newTrip = () => {
  const today = todayISO();
  const fields = [
    { k: 'purpose', l: 'Reason for the trip (printed on the statement)' },
    { k: 'title', l: 'Title (optional)' },
    { k: 'customer_id', l: 'Customer', t: 'select', o: [['', '— no customer —'], ...(S.d.customers || []).map(c => [c.id, c.name])], row: 1 },
    { k: 'project_id', l: 'Project', t: 'select', o: tripProjectOptions(''), row: 1 },
    { k: 'start', l: 'Start', t: 'datetime-local', row: 2 }, { k: 'end', l: 'End', t: 'datetime-local', row: 2 },
    { k: 'origin', l: 'Start place', row: 3 }, { k: 'destination', l: 'Destination', row: 3 },
    { k: 'destination_country', l: 'Country of the destination', t: 'select', o: tripCountryOpts() },
  ];
  openForm({
    title: 'New business trip', saveLabel: 'Create trip', fields, wide: true,
    vals: { purpose: '', title: '', customer_id: '', project_id: '', start: `${today}T08:00`, end: `${addDays(today, 1)}T18:00`, origin: S.settings.city || '', destination: '', destination_country: 'DE' },
    save: async v => {
      const purpose = String(v.purpose || '').trim();
      if (!purpose) throw new Error('Enter the reason for the trip');
      if (!v.start || !v.end) throw new Error('Enter the start and the end of the trip');
      if (!tripDates(v.start, v.end)) throw new Error('The trip must end after it starts and last at most 90 days');
      const origin = String(v.origin || '').trim(), destination = String(v.destination || '').trim();
      if (!origin) throw new Error('Enter the start place');
      if (!destination) throw new Error('Enter the destination');
      const body = {
        purpose, title: String(v.title || '').trim(), customer_id: v.customer_id || null, project_id: v.project_id || null,
        start: v.start, end: v.end, origin, destination, destination_country: v.destination_country || 'DE',
        places: [{ from_date: v.start.slice(0, 10), country: v.destination_country || 'DE', city: destination }],
        meals_provided: [], mileage: [], itinerary: [], items: [], cost_centre: null, not_needed: [], status: 'draft',
      };
      const t = await api('POST', 'trips', body);
      await loadAll(['trips']);
      toast('Trip created');
      location.hash = `#/trips/${encodeURIComponent(t.id)}/itinerary`;
    },
  });
  const cust = $('#f_customer_id'), proj = $('#f_project_id');
  cust?.addEventListener('change', () => { proj.innerHTML = selOpts(tripProjectOptions(cust.value), ''); });
};
/** Projects of the chosen customer (all projects when no customer is chosen), archived ones left out. */
function tripProjectOptions(customerId) {
  const list = (S.d.projects || []).filter(p => p.status !== 'archived' && (!customerId || p.customer_id === customerId));
  return [['', '— no project —'], ...list.map(p => [p.id, p.name + (p.personal ? ' (personal)' : '')])];
}

/* ---------- body of the trip (PUT replaces the whole trip) ---------- */
/** Fields the server only sends back: never part of a PUT. Every other stored field (also unknown ones) is kept. */
const TRIP_RESPONSE_ONLY = ['id', 'created', 'updated', 'user_id', 'days', 'bills', 'checklist', 'allowance_total', 'mileage_km',
  'mileage_total', 'rules_version', 'items_expected_total', 'items_actual_total'];
/** The stored trip as a TripIn: the stored fields first (unknown ones survive), then the known fields overwrite them. */
function tripIn(t) {
  const kept = Object.fromEntries(Object.entries(t).filter(([k]) => !TRIP_RESPONSE_ONLY.includes(k)));
  return {
    ...kept,
    title: t.title || '', purpose: t.purpose || '', customer_id: t.customer_id || null, project_id: t.project_id || null,
    start: t.start, end: t.end, origin: t.origin || '', destination: t.destination || '', destination_country: t.destination_country || 'DE',
    places: (t.places || []).map(p => ({ ...p })), meals_provided: (t.meals_provided || []).map(m => ({ ...m })),
    mileage: (t.mileage || []).map(m => ({ ...m })), itinerary: (t.itinerary || []).map(l => ({ ...l })),
    items: (t.items || []).map(i => ({ ...i })), cost_centre: t.cost_centre ?? null, not_needed: t.not_needed || [], status: 'draft',
  };
}
/** One PUT with the stored trip and the given fields changed; the answer becomes the stored trip. */
async function tripPutPartial(partial, force = false) {
  const t = await api('PUT', `trips/${encodeURIComponent(TRIP_PAGE.trip.id)}${force ? '?force=true' : ''}`, { ...tripIn(TRIP_PAGE.trip), ...partial });
  TRIP_PAGE.trip = t;
  await loadAll(['trips']);
  return t;
}

/* ---------- the trip page ---------- */
function tripPageHTML() {
  const { trip: t, step, ro } = TRIP_PAGE;
  const idx = TRIP_STEPS.findIndex(s => s[0] === step);
  const final = t.status === 'final';
  const banner = final && ro ? `<div class="banner info" role="status">${isBoss() ? 'This trip is final. Reopen it to change it.' : 'This trip is final. Only the boss can reopen it.'}</div>` : '';
  const body = { plan: planHTML, itinerary: itineraryHTML, expenses: expensesHTML, review: reviewHTML }[step](t, ro);
  return `<div class="head"><div><h1>${esc(t.title || t.purpose)}</h1><div class="muted"><a href="#/trips">← Business trips</a></div></div><span class="pill ${final ? 'good' : ''}">${final ? 'Final' : 'Draft'}</span></div>
    ${banner}
    ${step === 'plan' ? '' : tripSummaryHTML(t)}
    <nav aria-label="Trip steps"><ol class="stepper">${TRIP_STEPS.map(([k, l], i) => `<li><a href="#/trips/${esc(t.id)}/${k}"${k === step ? ' aria-current="step"' : ''}><span class="stepper-no">${i + 1}</span> ${esc(l)}</a></li>`).join('')}</ol></nav>
    <p id="t_error" class="banner bad" role="alert" tabindex="-1" hidden></p>
    <section class="card trip-step" id="t_step" aria-label="${esc(TRIP_STEPS[idx][1])}">${body}</section>
    <div class="trip-nav">${idx > 0 ? `<button type="button" data-a="tripMove" data-step="${TRIP_STEPS[idx - 1][0]}">← Back</button>` : '<span></span>'}${idx < TRIP_STEPS.length - 1 ? `<button type="button" class="primary" data-a="tripMove" data-step="${TRIP_STEPS[idx + 1][0]}">Next →</button>` : '<span></span>'}</div>`;
}
function tripSummaryHTML(t) {
  const cust = (S.d.customers || []).find(c => c.id === t.customer_id);
  const proj = (S.d.projects || []).find(p => p.id === t.project_id);
  const who = [cust?.name, proj?.name].filter(Boolean).join(' / ');
  return `<p class="trip-summary">${esc(t.purpose)}${who ? ' · ' + esc(who) : ''} · ${fmtDate(t.start.slice(0, 10))} – ${fmtDate(t.end.slice(0, 10))} · <a href="#/trips/${esc(t.id)}/plan">Edit the plan</a></p>`;
}
function tripAfter() {
  const p = TRIP_PAGE, st = $('#t_step');
  if (!p || !st) return;
  if (p.step === 'plan' && !p.ro) $('#t_customer')?.addEventListener('change', () => {
    const cur = $('#t_project').value;
    const opts = tripProjectOptions($('#t_customer').value);
    $('#t_project').innerHTML = selOpts(opts, opts.some(o => o[0] === cur) ? cur : '');
  });
  if (p.step === 'review') {
    if (p.ro) return renderBreakdown(p.trip);
    st.addEventListener('input', scheduleTripPreview);
    st.addEventListener('change', scheduleTripPreview);
    runTripPreview();
  }
}
function tripError(msg) {
  const el = $('#t_error');
  if (el) { el.textContent = msg; el.hidden = false; el.focus?.(); }
  else toast(msg, true);
}
/** Back and Next: save the current step (one PUT) and move; a refused save stays on the step with the message. */
A.tripMove = once(async el => {
  const to = el.dataset.step;
  if (!TRIP_PAGE.ro) {
    try { await tripSaveStep(); }
    catch (e) { return tripError(e.message); }
  }
  location.hash = `#/trips/${encodeURIComponent(TRIP_PAGE.trip.id)}/${to}`;
});
async function tripSaveStep() {
  const values = tripStepValues(TRIP_PAGE.step);
  // meals of days the shorter period no longer has are dropped by tripPlanValues: the user is told how many
  const dropped = TRIP_PAGE.step === 'plan' && values.meals_provided ? (TRIP_PAGE.trip.meals_provided || []).length - values.meals_provided.length : 0;
  await tripPutPartial(values);
  toast(dropped ? `Saved. ${dropped} ${dropped === 1 ? 'meal entry' : 'meal entries'} outside the new period ${dropped === 1 ? 'was' : 'were'} removed.` : 'Saved');
}
/** The fields of the current step as a partial TripIn. Throws a plain message for what the user must complete first. */
function tripStepValues(step) {
  const t = TRIP_PAGE.trip;
  if (step === 'plan') return tripPlanValues(t);
  if (step === 'itinerary') {
    const legs = tripLegsFromDOM();
    if (legs.some(l => !l.from || !l.to)) throw new Error('Every row needs a place');
    if (legs.some(l => !l.depart)) throw new Error('Enter the time of every row');
    // same rule as the server: a departure lies in [start, end] (datetime-local strings compare in time order)
    const late = legs.find(l => l.depart < t.start || l.depart > t.end);
    if (late) throw new Error(`The departure ${fmtDT(late.depart)} is outside the trip (${fmtDT(t.start)} to ${fmtDT(t.end)})`);
    // the rows keep their order: a row may not depart before the row above it
    for (let i = 1; i < legs.length; i++) {
      if (legs[i].depart < legs[i - 1].depart) throw new Error(`The times must not go backwards: ${fmtDT(legs[i].depart)} is before the row above it (${fmtDT(legs[i - 1].depart)})`);
    }
    TRIP_PAGE.legs = legs;
    return { itinerary: legs, places: placesFromLegs(legs, t.start) };
  }
  if (step === 'review') return { ...tripDaysValues(), not_needed: checkedNotNeeded() };
  return {};   // expenses: items and mileage are saved when they change
}
function tripPlanValues(t) {
  const val = id => document.getElementById(id)?.value ?? '';
  const purpose = val('t_purpose').trim();
  if (!purpose) throw new Error('Enter the reason for the trip');
  const start = val('t_start'), end = val('t_end');
  if (!start || !end) throw new Error('Enter the start and the end of the trip');
  if (!tripDates(start, end)) throw new Error('The trip must end after it starts and last at most 90 days');
  const origin = val('t_origin').trim(), destination = val('t_destination').trim();
  if (!origin) throw new Error('Enter the start place');
  if (!destination) throw new Error('Enter the destination');
  const values = {
    title: val('t_title').trim(), purpose, customer_id: val('t_customer') || null, project_id: val('t_project') || null,
    start, end, origin, destination, destination_country: val('t_dest_country') || 'DE',
    cost_centre: document.querySelector('input[name="t_cc"]:checked')?.value || null,
  };
  const dest_c = values.destination_country;
  const stored = t.itinerary || [];
  // a place not of the period any more: the rows must lie inside the new period (the server refuses them otherwise)
  const late = stored.find(l => l.depart < start || l.depart > end);
  if (late) throw new Error(`The itinerary has a row departing ${fmtDT(late.depart)}, outside the new period: correct the times on the Itinerary step first`);
  // mileage rows outside the period: removed only when the user asks for it
  const sd = start.slice(0, 10), ed = end.slice(0, 10);
  const outsideKm = (t.mileage || []).filter(m => m.date < sd || m.date > ed);
  if (outsideKm.length) {
    const n = outsideKm.length;
    if (!confirm(`${n} mileage ${n === 1 ? 'row lies' : 'rows lie'} outside the new period. Remove ${n === 1 ? 'it' : 'them'}?`)) {
      throw new Error('Mileage outside the new period: remove it on the Expenses step, or change the period back');
    }
    values.mileage = (t.mileage || []).filter(m => m.date >= sd && m.date <= ed);
  }
  // meals of days outside the period are dropped (the save toast names how many)
  const meals = t.meals_provided || [];
  const keptMeals = meals.filter(m => m.date >= sd && m.date <= ed);
  if (keptMeals.length !== meals.length) values.meals_provided = keptMeals;
  // the places of the allowance follow the plan (origin, destination, its country) and the period
  const changed = t.origin !== origin || t.destination !== destination || (t.destination_country || 'DE') !== dest_c;
  if (!changed && start === t.start && end === t.end) return values;   // nothing of the place or the period moved: places (and day corrections) stay
  // stored legs: the user decides whether their places follow the plan; without legs the two default legs are used
  const useNew = stored.length > 0 && changed && confirm('The start place, the destination or its country changed. Use the new places in the itinerary rows too?');
  if (useNew) values.itinerary = stored.map(l => {
    const x = { ...l, from: mapPlace(l.from, t, origin, destination), to: mapPlace(l.to, t, origin, destination) };
    if (l.to === t.destination) x.country = dest_c;
    return x;
  });
  const legs = values.itinerary || stored;
  const planLegs = legs.length ? legs : defaultLegs({ start, end, origin, destination, destination_country: dest_c });
  values.places = placesFromLegs(planLegs, start);
  return values;
}
const mapPlace = (p, t, origin, destination) => p === t.origin ? origin : p === t.destination ? destination : p;

/* ---------- itinerary (step 2): three rows for the whole trip, stays added as needed ---------- */
/** The legs of a plain trip: start -> destination at the start, destination -> start place at the end. */
function defaultLegs(t) {
  return [
    { depart: t.start, from: t.origin || '', to: t.destination || '', country: t.destination_country || 'DE' },
    { depart: t.end, from: t.destination || '', to: t.origin || '', country: 'DE' },
  ];
}
function itineraryHTML(t, ro) {
  if (!TRIP_PAGE.legs) TRIP_PAGE.legs = (t.itinerary || []).length >= 2 ? t.itinerary.map(l => ({ ...l })) : defaultLegs(t);
  const legs = TRIP_PAGE.legs, n = legs.length, dis = ro ? 'disabled' : '';
  const modeSel = (cur, label) => `<select data-f="mode" aria-label="${esc(label)}" ${dis}>${TRIP_MODES.map(([v, l]) => `<option value="${v}" ${v === (cur || '') ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
  const countrySel = (cur, label) => `<select data-f="country" aria-label="${esc(label)}" ${dis}>${tripCountryOpts().map(([c, l]) => `<option value="${c}" ${c === (cur || 'DE') ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
  const rows = [`<tr data-row="dep"><th scope="row">Departure</th>
    <td><input data-f="place" value="${esc(legs[0].from || '')}" maxlength="200" aria-label="Departure: place" ${dis}></td>
    <td class="muted">—</td>
    <td><input type="datetime-local" data-f="time" value="${esc(legs[0].depart)}" aria-label="Departure: time" ${dis}></td>
    <td class="muted">—</td>
    <td>${modeSel(legs[0].mode, 'Departure: travel by')}</td><td></td></tr>`];
  for (let j = 1; j < n; j++) {
    rows.push(`<tr data-row="stay"><th scope="row">${n > 2 ? `Stay ${j}` : 'Stay'}</th>
      <td><input data-f="place" value="${esc(legs[j - 1].to || '')}" maxlength="200" aria-label="Stay ${j}: place" ${dis}></td>
      <td>${countrySel(legs[j - 1].country, `Stay ${j}: country`)}</td>
      <td class="nowrap small">${esc(fmtDT(legs[j - 1].depart))}</td>
      <td><input type="datetime-local" data-f="until" value="${esc(legs[j].depart)}" aria-label="Stay ${j}: until" ${dis}></td>
      <td>${modeSel(legs[j].mode, `Stay ${j}: travel on by`)}</td>
      <td>${ro || n <= 2 ? '' : `<button type="button" class="ghost sm" data-a="tripStayDel" data-i="${j}" aria-label="Remove stay ${j}">Remove</button>`}</td></tr>`);
  }
  rows.push(`<tr data-row="ret"><th scope="row">Return</th>
    <td><input data-f="place" value="${esc(legs[n - 1].to || '')}" maxlength="200" aria-label="Return: place" ${dis}></td>
    <td>${countrySel(legs[n - 1].country, 'Return: country')}</td>
    <td class="nowrap small">${esc(fmtDT(legs[n - 1].depart))}</td>
    <td class="muted">—</td><td class="muted">—</td><td></td></tr>`);
  return `<h2>Itinerary</h2>
    <p class="small muted">Where you start, where you stay and where you end up. Rows are per stay, not per day. Times are departure times; the stay starts when the leg before it leaves and the return shows the last departure.</p>
    <div class="table-wrap"><table class="trip-table lines trip-itin"><caption class="sr-only">Itinerary of the trip</caption>
      <thead><tr><th scope="col">Row</th><th scope="col">Place</th><th scope="col">Country</th><th scope="col">From</th><th scope="col">Until</th><th scope="col">Travel by</th><th scope="col"><span class="sr-only">Remove</span></th></tr></thead>
      <tbody>${rows.join('')}</tbody></table></div>
    ${ro ? '' : '<p><button type="button" class="ghost sm" data-a="tripStayAdd">+ Add stay</button></p>'}`;
}
/** Rows back into legs (the working copy keeps the fields this page does not show). */
function tripLegsFromDOM() {
  const base = TRIP_PAGE.legs || [];
  const rows = [...document.querySelectorAll('#t_step tr[data-row]')];
  const val = (r, f) => r.querySelector(`[data-f=${f}]`)?.value ?? '';
  const legs = [];
  for (let i = 0; i + 1 < rows.length; i++) {
    const a = rows[i], b = rows[i + 1];
    const leg = { ...(base[i] || {}), depart: val(a, a.dataset.row === 'dep' ? 'time' : 'until'), from: val(a, 'place').trim(), to: val(b, 'place').trim(), country: val(b, 'country') || 'DE' };
    const mode = val(a, 'mode');
    if (mode) leg.mode = mode; else delete leg.mode;
    legs.push(leg);
  }
  return legs;
}
/** Draws the itinerary again; the focus goes to the place of the stay at index focusStay (0 = the first stay). */
function rerenderItinerary(focusStay) {
  $('#t_step').innerHTML = itineraryHTML(TRIP_PAGE.trip, TRIP_PAGE.ro);
  const stays = document.querySelectorAll('#t_step tr[data-row="stay"] [data-f="place"]');
  if (focusStay != null && stays.length) stays[Math.min(focusStay, stays.length - 1)].focus();
}
A.tripStayAdd = () => {
  // a new stay at the place of the last stay: the last leg is split in two (the times are set in the row)
  const legs = tripLegsFromDOM(), n = legs.length;
  if (n < 2) return;
  const last = legs[n - 1], x = legs[n - 2].to;
  // the mode and the note stay with the leg that goes on (the original leg); the arriving leg gets neither
  const arrive = { ...last, to: x, country: legs[n - 2].country || 'DE' };
  delete arrive.mode;
  delete arrive.note;
  TRIP_PAGE.legs = [...legs.slice(0, n - 1), arrive, { ...last, from: x }];
  rerenderItinerary(n - 1);   // the new stay is the last one before Return
};
A.tripStayDel = el => {
  // the stay j is removed: the leg before it and the leg after it become one leg; a trip keeps at least two legs
  const j = +el.dataset.i, legs = tripLegsFromDOM();
  if (legs.length <= 2 || j < 1 || j >= legs.length) return;
  TRIP_PAGE.legs = [...legs.slice(0, j - 1), { ...legs[j - 1], to: legs[j].to, country: legs[j].country }, ...legs.slice(j + 1)];
  rerenderItinerary(j - 1);   // the stay that takes the place of the removed one
};
/** The places of the allowance from the legs: the place in force from the day it is reached (one entry per change). */
function placesFromLegs(legs, start) {
  const seq = [{ from_date: start.slice(0, 10), country: 'DE', city: legs[0].from || '' }];
  for (const l of legs) seq.push({ from_date: l.depart.slice(0, 10), country: l.country || 'DE', city: l.to || '' });
  const out = [];
  for (const p of seq) {
    if (out.length && out[out.length - 1].from_date === p.from_date) out.pop();   // the later place of a day wins
    const prev = out[out.length - 1];
    if (prev && prev.country === p.country && prev.city === p.city) continue;
    out.push(p);
  }
  return out.map(p => (p.city ? p : { from_date: p.from_date, country: p.country }));
}

/* ---------- plan (step 1) ---------- */
function planHTML(t, ro) {
  const dis = ro ? 'disabled' : '', cc = t.cost_centre ?? '', cust = t.customer_id || '';
  return `<h2>Plan</h2>
    <div class="row"><div class="field"><label for="t_purpose">Reason for the trip (printed on the statement)</label><input id="t_purpose" value="${esc(t.purpose || '')}" maxlength="200" required aria-required="true" ${dis}></div>
      <div class="field"><label for="t_title">Title (optional)</label><input id="t_title" value="${esc(t.title || '')}" maxlength="200" ${dis}></div></div>
    <div class="row"><div class="field"><label for="t_customer">Customer</label><select id="t_customer" ${dis}>${selOpts([['', '— no customer —'], ...(S.d.customers || []).map(c => [c.id, c.name])], cust)}</select></div>
      <div class="field"><label for="t_project">Project</label><select id="t_project" ${dis}>${selOpts(tripProjectOptions(cust), t.project_id || '')}</select></div></div>
    <div class="row"><div class="field"><label for="t_start">Start</label><input id="t_start" type="datetime-local" value="${esc(t.start)}" ${dis}></div>
      <div class="field"><label for="t_end">End</label><input id="t_end" type="datetime-local" value="${esc(t.end)}" ${dis}></div></div>
    <div class="row"><div class="field"><label for="t_origin">Start place</label><input id="t_origin" value="${esc(t.origin || '')}" maxlength="200" ${dis}></div>
      <div class="field"><label for="t_destination">Destination</label><input id="t_destination" value="${esc(t.destination || '')}" maxlength="200" ${dis}></div>
      <div class="field"><label for="t_dest_country">Country of the destination</label><select id="t_dest_country" ${dis}>${selOpts(tripCountryOpts(), t.destination_country || 'DE')}</select></div></div>
    <fieldset ${dis}><legend><b>Cost centre / project</b></legend>
      <p class="small muted">Decide what the trip is charged to. The checklist stays open until you choose.</p>
      ${TRIP_CC.map(([v, l]) => `<label class="radio"><input type="radio" name="t_cc" id="t_cc_${v || 'none'}" value="${esc(v)}" ${cc === v ? 'checked' : ''} ${dis}> ${esc(l)}</label>`).join('')}</fieldset>`;
}

/* ---------- expenses (step 3): planned items, bills, mileage ---------- */
function expensesHTML(t, ro) {
  const items = t.items || [];
  const bills = (S.d.expenses || []).filter(e => e.trip_id === t.id);
  const planned = new Set(items.map(i => i.id));
  const canBill = !ro && can('expenses', 'write');
  const rows = [];
  for (const i of items) rows.push({ date: i.date, html: itemRowHTML(i, bills.filter(b => b.trip_item_id === i.id), ro, canBill) });
  (t.mileage || []).forEach((m, k) => rows.push({ date: m.date, html: carRowHTML(m, k) }));
  for (const b of bills.filter(b => !b.trip_item_id || !planned.has(b.trip_item_id))) rows.push({ date: b.date, html: unplannedRowHTML(b, t, canBill) });
  rows.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  const addSel = ro ? '' : `<div class="trip-add"><label for="t_add_item">Add item</label>
      <select id="t_add_item" data-change="tripAddItem"><option value="">Choose a type…</option>${TRIP_ITEM_TYPES.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join('')}</select></div>`;
  return `<h2>Planned costs and bills</h2>
    <p class="small muted">Planned costs are planning figures only: they are not part of the statement, the allowance or VAT. The actual column is the gross of the bills booked against the row.</p>
    ${addSel}
    <div class="table-wrap"><table class="lines trip-items"><caption class="sr-only">Planned costs of the trip, with the bills booked against them</caption>
      <thead><tr><th scope="col">Date</th><th scope="col">Category</th><th scope="col">Description</th><th scope="col" class="num">Expected</th><th scope="col" class="num">Actual</th><th scope="col">Receipt</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>${rows.length ? rows.map(r => r.html).join('') : '<tr><td colspan="7" class="muted">No planned costs or bills yet.</td></tr>'}</tbody></table></div>
    <div class="trip-totals">${tripTotalsHTML(t)}</div>
    <h2>Mileage with a private car</h2>
    ${mileageTableHTML(t, ro)}`;
}
/** The footer: the server's totals (planning figures, exact cents here). The expected total has no mileage when car rows are listed. */
function tripTotalsHTML(t) {
  const expC = tripCents(t.items_expected_total ?? 0), actC = tripCents(t.items_actual_total ?? 0);
  const label = (t.mileage || []).length ? 'Expected total (without mileage)' : 'Expected total';
  return `<div><span>${esc(label)}</span><b>${eurC(expC)}</b></div><div><span>Actual total</span><b>${eurC(actC)}</b></div><div><span>Difference (actual − expected)</span><b>${eurC(actC - expC)}</b></div>`;
}
const itemDesc = i =>[i.from && i.to ? `${i.from} → ${i.to}` : '', i.text || ''].filter(Boolean).join(' · ');
function receiptHTML(bills) {
  return bills.length ? bills.map(b => `<span class="pill ${b.attachment ? 'good' : 'warn'}">${b.attachment ? 'attached' : 'missing'}</span>`).join(' ') : '<span class="muted">—</span>';
}
/** A planned item whose dates lie outside the trip period: a hint only, nothing is blocked. */
function itemOutsidePeriod(i, t) {
  const first = i.date, last = i.date_to || i.date;
  return first < t.start.slice(0, 10) || last > t.end.slice(0, 10);
}
function itemRowHTML(i, bills, ro, canBill) {
  const label = TRIP_ITEM_LABEL[i.category] || i.category, desc = itemDesc(i);
  const outside = itemOutsidePeriod(i, TRIP_PAGE.trip) ? ' <span class="pill warn">Outside the trip period</span>' : '';
  const actual = bills.length ? eurC(bills.reduce((a, b) => a + billCents(b), 0n)) : '';
  const expVal = i.expected ?? '';
  const exp = ro ? `<span>${eur(i.expected || 0)}</span>` : `<input type="number" step="0.01" min="0" inputmode="decimal" value="${esc(expVal)}" data-item-exp="${esc(i.id)}" data-change="tripExpected" aria-label="Expected for ${esc(label)} on ${esc(fmtDate(i.date))}">`;
  const acts = ro ? '' : [
    canBill ? `<button type="button" class="ghost sm" data-a="editExpense" data-trip="${esc(TRIP_PAGE.trip.id)}" data-project="${esc(TRIP_PAGE.trip.project_id || '')}" data-category="${esc(TRIP_ITEM_CATEGORY[i.category] || 'Other')}" data-item="${esc(i.id)}" aria-label="Add bill for ${esc(label)}">Add bill</button>` : '',
    `<button type="button" class="ghost sm" data-a="tripItemEdit" data-id="${esc(i.id)}" aria-label="Edit ${esc(label)} ${esc(fmtDate(i.date))}">Edit</button>`,
    bills.length ? '' : `<button type="button" class="ghost sm" data-a="tripItemDel" data-id="${esc(i.id)}" aria-label="Remove ${esc(label)} ${esc(fmtDate(i.date))}">Remove</button>`,
  ].join(' ');
  return `<tr data-item="${esc(i.id)}"><td class="nowrap">${esc(i.date_to ? `${fmtDate(i.date)} – ${fmtDate(i.date_to)}` : fmtDate(i.date))}</td>
    <td>${esc(label)}</td><td>${esc(desc)}${outside}</td><td class="num">${exp}</td><td class="num">${esc(actual)}</td><td>${receiptHTML(bills)}</td><td>${acts}</td></tr>`;
}
function carRowHTML(m, k) {
  const cost = TRIP_PAGE.rate != null ? eur(kmCost(m.km, TRIP_PAGE.rate)) : '—';
  return `<tr data-mileage="${k}"><td class="nowrap">${esc(fmtDate(m.date))}</td><td>Car (private)</td>
    <td>${esc(`${m.from || ''} → ${m.to || ''}, ${num(m.km, 1)} km`)}${m.purpose ? ` · ${esc(m.purpose)}` : ''}</td>
    <td class="num">${esc(cost)}</td><td class="num"></td><td><span class="muted">—</span></td><td></td></tr>`;
}
function unplannedRowHTML(b, t, canBill) {
  const acts = canBill ? `<button type="button" class="ghost sm" data-a="editExpense" data-id="${esc(b.id)}" data-trip="${esc(t.id)}" aria-label="Edit bill ${esc(b.vendor || '')}">Edit bill</button>` : '';
  return `<tr data-bill="${esc(b.id)}"><td class="nowrap">${esc(fmtDate(b.date))}</td><td>${esc(b.category || '')}</td>
    <td>${esc(b.vendor || '')}<div class="small muted">not planned</div></td><td class="num"><span class="muted">—</span></td>
    <td class="num">${eurC(billCents(b))}</td><td>${receiptHTML([b])}</td><td>${acts}</td></tr>`;
}
function mileageTableHTML(t, ro) {
  const list = t.mileage || [];
  const rows = list.map((m, k) => `<tr data-mileage="${k}"><td class="nowrap">${esc(fmtDate(m.date))}</td><td>${esc(m.from || '')}</td><td>${esc(m.to || '')}</td>
      <td class="num">${num(m.km, 1)}</td><td class="num">${TRIP_PAGE.rate != null ? eur(kmCost(m.km, TRIP_PAGE.rate)) : '—'}</td><td>${esc(m.purpose || '')}</td>
      <td>${ro ? '' : `<button type="button" class="ghost sm" data-a="tripMileageEdit" data-k="${k}" aria-label="Edit mileage ${k + 1}">Edit</button>`}</td></tr>`).join('');
  return `<div class="table-wrap"><table class="lines trip-table"><caption class="sr-only">Mileage with a private car</caption>
      <thead><tr><th scope="col">Date</th><th scope="col">From</th><th scope="col">To</th><th scope="col" class="num">km</th><th scope="col" class="num">Cost</th><th scope="col">Purpose</th><th scope="col"><span class="sr-only">Edit</span></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="7" class="muted">No mileage yet.</td></tr>'}</tbody></table></div>
    ${ro ? '' : '<p><button type="button" class="ghost sm" data-a="tripMileageAdd">+ Add mileage</button></p>'}`;
}
A.tripAddItem = el => {
  const type = el.value;
  el.value = '';
  if (!type) return;
  if (type === 'car') return tripMileageDialog(null);
  tripItemDialog(type, null);
};
A.tripItemEdit = el => {
  const item = (TRIP_PAGE.trip.items || []).find(i => i.id === el.dataset.id);
  if (item) tripItemDialog(item.category, item);
};
A.tripItemDel = once(async el => {
  if (!confirm('Remove this planned item?')) return;
  await tripPutPartial({ items: (TRIP_PAGE.trip.items || []).filter(i => i.id !== el.dataset.id) });
  toast('Item removed');
  render();
});
A.tripExpected = once(async el => {
  const id = el.dataset.itemExp, raw = el.value.trim();
  if (raw !== '' && (Number(raw) < 0 || Number.isNaN(Number(raw)) || Money.parse(raw).s > 2)) return tripError('Expected: enter an amount of at least 0 with at most two decimals');
  const items = (TRIP_PAGE.trip.items || []).map(i => {
    if (i.id !== id) return i;
    const { expected, ...rest } = i;
    return raw === '' ? rest : { ...rest, expected: Number(raw) };
  });
  const t = await tripPutPartial({ items });
  // a local update: the footer from the server's answer, no page render (the focus and the other rows stay)
  const box = document.querySelector('#t_step .trip-totals');
  if (box) box.innerHTML = tripTotalsHTML(t);
  toast('Saved');
});
/** One planned item of a type; the fields depend on the type (docs/specs/business-trips-wizard.md, section 4). */
function tripItemDialog(type, item) {
  const t = TRIP_PAGE.trip;
  const v = item
    ? { date: item.date || '', date_to: item.date_to || '', text: item.text || '', from: item.from || '', to: item.to || '', expected: item.expected ?? '' }
    : { date: t.start.slice(0, 10), date_to: t.end.slice(0, 10), text: type === 'hotel' ? (t.destination || '') : '', from: t.origin || '', to: t.destination || '', expected: '' };
  const route = [{ k: 'from', l: 'From', row: 1 }, { k: 'to', l: 'To', row: 1 }, { k: 'date', l: 'Date', t: 'date', row: 2 }];
  const fields = {
    hotel: [{ k: 'text', l: 'Place (hotel, city)' }, { k: 'date', l: 'Check-in', t: 'date', row: 1 }, { k: 'date_to', l: 'Check-out', t: 'date', row: 1 }],
    train: [...route, { k: 'text', l: 'Carrier or note', row: 2 }],
    flight: [...route, { k: 'text', l: 'Carrier or note', row: 2 }],
    taxi: [{ k: 'from', l: 'From', row: 1 }, { k: 'to', l: 'To', row: 1 }, { k: 'date', l: 'Date', t: 'date' }],
    other: [{ k: 'text', l: 'Description' }, { k: 'date', l: 'Date', t: 'date' }],
  }[type] || [];
  fields.push({ k: 'expected', l: 'Expected € (planning figure)', t: 'number', h: 'Not part of the statement, the allowance or VAT.' });
  openForm({
    title: `${item ? 'Edit' : 'Add'} ${TRIP_ITEM_LABEL[type] || type}`, fields, vals: v,
    del: item ? async () => { await tripPutPartial({ items: (TRIP_PAGE.trip.items || []).filter(i => i.id !== item.id) }); render(); } : undefined,
    save: async o => {
      const it = tripItemFrom(type, o, item);
      const items = item ? (TRIP_PAGE.trip.items || []).map(i => (i.id === item.id ? it : i)) : [...(TRIP_PAGE.trip.items || []), it];
      await tripPutPartial({ items });
      render();
    },
  });
}
function tripItemFrom(type, o, item) {
  const date = String(o.date || '');
  if (!date) throw new Error('Enter the date');
  // the stored fields this dialog does not show stay (also unknown ones); the fields it shows are set or removed below
  const it = { ...(item || {}), category: type, date, text: String(o.text ?? '').trim() };
  delete it.date_to; delete it.from; delete it.to; delete it.expected;
  if (type === 'hotel') {
    const to = String(o.date_to || '');
    if (to) { if (to < date) throw new Error('Check-out is before check-in'); it.date_to = to; }
  }
  if (type === 'train' || type === 'flight' || type === 'taxi') {
    it.from = String(o.from || '').trim(); it.to = String(o.to || '').trim();
    if (!it.text) it.text = [it.from, it.to].filter(Boolean).join(' – ');
  }
  const exp = String(o.expected ?? '').trim();
  if (exp !== '') {
    if (Number(exp) < 0 || Number.isNaN(Number(exp)) || Money.parse(exp).s > 2) throw new Error('Expected: enter an amount of at least 0 with at most two decimals');
    it.expected = Number(exp);
  }
  return it;
}
A.tripMileageAdd = () => tripMileageDialog(null);
A.tripMileageEdit = el => tripMileageDialog(+el.dataset.k);
/** The private car: a mileage row (existing fields and rules), not an item. */
function tripMileageDialog(k) {
  const t = TRIP_PAGE.trip, ex = k == null ? null : (t.mileage || [])[k];
  const v = ex ? { date: ex.date, from: ex.from || '', to: ex.to || '', km: ex.km ?? '', purpose: ex.purpose || '' }
    : { date: t.start.slice(0, 10), from: t.origin || '', to: t.destination || '', km: '', purpose: '' };
  openForm({
    title: ex ? 'Edit mileage' : 'Add mileage (private car)', vals: v,
    fields: [{ k: 'date', l: 'Date', t: 'date', row: 1 }, { k: 'km', l: 'km', t: 'number', step: '0.1', row: 1 },
      { k: 'from', l: 'From', row: 2 }, { k: 'to', l: 'To', row: 2 }, { k: 'purpose', l: 'Purpose (optional)' }],
    del: ex ? async () => { await tripPutPartial({ mileage: (t.mileage || []).filter((_, i) => i !== k) }); render(); } : undefined,
    save: async o => {
      const row = tripMileageFrom(o);
      const mileage = ex ? (t.mileage || []).map((m, i) => (i === k ? row : m)) : [...(t.mileage || []), row];
      await tripPutPartial({ mileage });
      render();
    },
  });
}
function tripMileageFrom(o) {
  const date = String(o.date || '');
  if (!date) throw new Error('Enter the date');
  const km = String(o.km ?? '').trim(), kmN = Number(km);
  if (km === '' || Number.isNaN(kmN) || kmN < 0.1 || kmN > 5000 || Money.parse(km).s > 1) throw new Error('km: between 0.1 and 5000, at most one decimal');
  const row = { date, from: String(o.from || '').trim(), to: String(o.to || '').trim(), km: kmN };
  if (String(o.purpose || '').trim()) row.purpose = String(o.purpose).trim();
  return row;
}

/* ---------- review (step 4): days, live breakdown, checklist, finalize ---------- */
/** Days table rows (one per day of the trip, from the stored places and meals). */
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
/** Places and meals from the days table: the places are sent where the destination changes. Empty when there is no table. */
function tripDaysValues() {
  const rows = tripRowsFromDOM();
  if (!rows.length) return {};
  let cur = null;
  const days = rows.map(r => {
    const text = r.country.trim();
    const country = parseCountry(text) || (text ? '' : (cur ? cur.country : 'DE'));
    if (!country) throw new Error(`Pick a country from the list on ${dayLabel(r.date)}`);
    const city = r.city.trim() || (!text && cur ? cur.city : '');
    cur = { date: r.date, country, city, breakfast: r.breakfast, lunch: r.lunch, dinner: r.dinner };
    return cur;
  });
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
  return { places, meals_provided };
}
function checkedNotNeeded() {
  return [...document.querySelectorAll('#t_step input[data-not]:checked')].map(i => i.dataset.not);
}
function dayRowHTML(r, ro) {
  const dis = ro ? 'disabled' : '', on = fmtDate(r.date);
  return `<tr data-day="${esc(r.date)}"><th scope="row" class="nowrap">${esc(dayLabel(r.date))}</th>
    <td><input data-f="country" list="t_countries" value="${esc(r.country)}" autocomplete="off" aria-label="Country on ${esc(on)}" ${dis}></td>
    <td><input data-f="city" value="${esc(r.city)}" maxlength="200" aria-label="City on ${esc(on)}" ${dis}></td>
    ${TRIP_MEALS.map(([k, l]) => `<td class="center"><input type="checkbox" data-f="${k}" ${r[k] ? 'checked' : ''} aria-label="${esc(l)} provided on ${esc(on)}" ${dis}></td>`).join('')}</tr>`;
}
function daysHTML(rows, ro) {
  if (!rows.length) return '<p class="hint" role="status">The trip has no days: set the start and the end on the Plan step (the end after the start, at most 90 days).</p>';
  return `<div class="table-wrap"><table class="trip-table lines"><caption class="sr-only">Days, destinations and meals provided</caption>
    <thead><tr><th scope="col">Day</th><th scope="col">Country</th><th scope="col">City</th><th scope="col">Breakfast provided</th><th scope="col">Lunch provided</th><th scope="col">Dinner provided</th></tr></thead>
    <tbody>${rows.map(r => dayRowHTML(r, ro)).join('')}</tbody></table></div>
    <datalist id="t_countries">${tripCountryOpts().map(([c, l]) => `<option value="${esc(l)}"></option>`).join('')}</datalist>`;
}
function reviewHTML(t, ro) {
  const b = t.bills || { count: 0, net: 0, vat: 0 };
  return `<h2>Days and destinations</h2>
    <p class="small muted">One line per day. A day without its own entry keeps the place of the day before. Tick each meal the hotel or the host provided.</p>
    <div id="t_days">${daysHTML(tripRowsOf(t), ro)}</div>
    <h2>Breakdown</h2>
    <section id="t_breakdown" aria-live="polite" aria-label="Breakdown of the figures"></section>
    <h2>Reminder checklist</h2>
    <div id="t_checklist">${checklistHTML(t, ro)}</div>
    <p>${b.count} ${b.count === 1 ? 'bill' : 'bills'} linked: net ${esc(eur(b.net))}, VAT ${esc(eur(b.vat))}</p>
    <div class="trip-final">${reviewActionsHTML(t, ro)}</div>`;
}
function reviewActionsHTML(t, ro) {
  const final = t.status === 'final';
  const left = [];
  if (!ro && !final) left.push(`<button type="button" class="danger" data-a="tripDelete" data-id="${esc(t.id)}">Delete draft</button>`);
  left.push(`<a class="btn" href="${esc(api.url(`trips/${encodeURIComponent(t.id)}/statement.pdf`))}" target="_blank" rel="noopener">Statement (PDF)</a>`);
  const right = [];
  if (final && isBoss()) right.push(`<button type="button" data-a="tripReopen" data-id="${esc(t.id)}">Reopen trip</button>`);
  if (!ro) right.push('<button type="button" class="primary" data-a="tripFinalize">Finalize trip</button>');
  return `<div class="trip-left">${left.join(' ')}</div>${right.join(' ')}`;
}
/** The detail line of a checklist item, from the server's count and sum. */
function countDetail(it) {
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
  const skip = it.id === 'allowance' ? '' : `<label class="check-skip"><input type="checkbox" data-not="${esc(it.id)}" id="t_not_${esc(it.id)}" data-change="tripNotNeeded" ${it.state === 'not_needed' ? 'checked' : ''} ${ro ? 'disabled' : ''}> Not needed</label>`;
  return `<li class="check-item"><div><b>${esc(it.label)}</b> <span class="pill ${tone}">${esc(label)}</span></div>
    <div class="small muted">${esc(it.hint || '')}</div>
    ${countDetail(it) ? `<div>${esc(countDetail(it))}</div>` : ''}
    ${add || skip ? `<div class="check-actions">${add}${skip}</div>` : ''}</li>`;
}
function ccLineHTML(trip, it) {
  const added = it?.state === 'added';
  return `<li class="check-item"><div><b>Cost centre / project</b> <span class="pill ${added ? 'good' : 'warn'}">${added ? 'Added' : 'Open'}</span></div>
    <div class="small muted">Decided on the Plan step. <a href="#/trips/${esc(trip.id)}/plan">Decide the cost centre</a></div></li>`;
}
function checklistHTML(trip, ro) {
  const items = trip?.checklist || [];
  if (!items.length) return '<p class="small muted">The reminder lines appear once the trip is saved.</p>';
  return `<ol class="trip-check">${items.map(i => (i.id === 'cost_centre' ? ccLineHTML(trip, i) : checkItemHTML(i, trip, ro))).join('')}</ol>`;
}
/** Not-needed decisions are stored at once; the checklist then shows the server's states. */
A.tripNotNeeded = once(async () => {
  const t = await tripPutPartial({ not_needed: checkedNotNeeded() });
  $('#t_checklist').innerHTML = checklistHTML(t, false);
});

/* ---------- live breakdown (POST /trips/preview: the server computes every figure) ---------- */
function scheduleTripPreview() { clearTimeout(TRIP_PREVIEW_TIMER); TRIP_PREVIEW_TIMER = setTimeout(runTripPreview, 300); }
async function runTripPreview() {
  clearTimeout(TRIP_PREVIEW_TIMER);
  const box = $('#t_breakdown');
  if (!box || !TRIP_PAGE) return;
  const seq = ++TRIP_PREVIEW_SEQ;
  let body;
  try { body = { ...tripIn(TRIP_PAGE.trip), ...tripDaysValues() }; }
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

/* ---------- finalize, reopen, delete ---------- */
A.tripFinalize = once(async () => {
  const body = { ...tripIn(TRIP_PAGE.trip), ...tripStepValues('review'), status: 'final' };
  const open = (TRIP_PAGE.trip.checklist || []).filter(i => i.state === 'open').map(i => i.label);
  let force = false;
  if (open.length) {   // ask before sending: one request, with force when the boss confirms
    if (!confirm(`Checklist has open items: ${open.join(', ')}\n\nFinalize anyway? The open lines stay open in the statement.`)) return;
    force = true;
  }
  const put = f => api('PUT', `trips/${encodeURIComponent(TRIP_PAGE.trip.id)}${f ? '?force=true' : ''}`, body);
  let t;
  try { t = await put(force); }
  catch (e) {   // the stored checklist was stale: the server still decides
    if (force || e.status !== 409 || !/Checklist has open items/.test(e.message)) throw e;
    if (!confirm(`${e.message}\n\nFinalize anyway? The open lines stay open in the statement.`)) return;
    t = await put(true);
  }
  TRIP_PAGE.trip = t;
  await loadAll(['trips']);
  toast('Trip finalized');
  render();
});
A.tripReopen = once(async el => {
  const t = await api('POST', `trips/${encodeURIComponent(el.dataset.id)}/reopen`, {});
  TRIP_PAGE.trip = t;
  await loadAll(['trips']);
  toast('Trip reopened');
  render();
});
A.tripDelete = once(async el => {
  if (!confirm('Delete this draft trip? Its bills stay and lose the trip link.')) return;
  await api('DELETE', `trips/${encodeURIComponent(el.dataset.id)}`);
  toast('Trip deleted');
  await loadAll(['trips', 'expenses']);
  location.hash = '#/trips';
});

/* A bill started from a trip returns to the trip when its dialog closes, so the totals and the checklist show the new state. */
const editExpenseBase = A.editExpense;
A.editExpense = el => {
  editExpenseBase(el);
  if (!el?.dataset?.trip) return;
  const prev = closeModal.onClose;
  closeModal.onClose = () => { if (prev) prev(); setTimeout(() => { if (route()[0] === 'trips') guard(render)(); }, 0); };
};
