/* permitA38 – Team timeline: who is away and who has contracts, drawn at the viewer's visibility level (read-only).
   The server removes what the level does not allow (GET /api/team/timeline); this file draws what it receives and
   computes the clash days itself from the absences. Vanilla JS, no build step. Reuses the planner's grid classes. */
const TL_DAY_W = { week: 24, month: 8 };                  // px per day: week zoom (numbers visible) and month zoom (compact)
const TL_SPANS = [4, 8, 13, 26];                          // window length in weeks (the API allows at most 366 days)
const TL_LEVEL_TEXT = {
  away: 'away only: who is away and when, without reasons',
  'time-off': 'time off with kind and vacation labels',
  full: 'full details: sick kind, contracts and weekly hours',
};
const TL_KIND = { vacation: 'vacation', holiday: 'personal day off', sick: 'sick', other: 'other' };
const TL_SETTING_LEVELS = [
  ['off', 'Off: no Team timeline (the page answers "switched off")'],
  ['away', 'Away: who is away and when, no reasons'],
  ['time-off', 'Time off: plus kind and vacation labels'],
  ['full', 'Full: plus sick kind, contracts and weekly hours'],
];
const TL_COLOR = /^#[0-9a-fA-F]{6}$/;
const tlInt = (v, lo, hi, d) => { const x = Math.trunc(Number(v)); return Number.isFinite(x) && x >= lo && x <= hi ? x : d; };
function tlPref(key, dflt) { try { return localStorage.getItem(key) ?? dflt; } catch { return dflt; } }
function tlSetPref(key, v) { try { localStorage.setItem(key, String(v)); } catch { /* storage unavailable: the view still works */ } }
const TL = {
  start: null,                                            // first day of the window (a Monday by default)
  span: TL_SPANS.includes(+tlPref('tlSpan', 8)) ? +tlPref('tlSpan', 8) : 8,
  zoom: tlPref('tlZoom', 'week') === 'month' ? 'month' : 'week',
  n: tlInt(tlPref('tlN', 2), 1, 999, 2),                  // clash threshold: a per-viewer number, never sent to the server
  bars: [],                                               // bars of the last paint, addressed by data-i
  win: null,                                              // { start, days } of the last paint
};

/* ---------- pure helpers (no DOM) ---------- */
function tlDays(from, to) { const out = []; for (let d = from; d <= to; d = addDays(d, 1)) out.push(d); return out; }
function tlIdx(day) { return Math.round((parseD(day) - parseD(TL.win.start)) / 864e5); }
function tlIsoWeek(s) {   // ISO calendar week number (Monday-based, week 1 contains the first Thursday)
  const d = parseD(s); d.setDate(d.getDate() - ((d.getDay() + 6) % 7) + 3);
  const jan4 = new Date(d.getFullYear(), 0, 4); jan4.setDate(jan4.getDate() - ((jan4.getDay() + 6) % 7));
  return 1 + Math.round((d - jan4) / 604800000);
}
function tlRange(s, e) {   // "9 to 13 November", "18 November", "30 October to 2 November"
  const last = e || s, a = parseD(s), b = parseD(last);
  const md = x => x.toLocaleDateString('en-GB', { day: 'numeric', month: 'long' });
  if (last === s) return md(a);
  if (a.getMonth() === b.getMonth()) return `${a.getDate()} to ${md(b)}`;
  return `${md(a)} to ${md(b)}`;
}
const tlDayLabel = s => parseD(s).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
const tlAbsLabel = a => (a.kind ? TL_KIND[a.kind] || a.kind : 'away');
/* people away per day (distinct people with an absence entry covering the day) */
function tlCounts(data) {
  const counts = {};
  for (const d of tlDays(data.start, data.end)) {
    const away = new Set((data.people || []).filter(p => (p.absences || []).some(a => a.start <= d && d <= (a.end || a.start))).map(p => p.id));
    counts[d] = away.size;
  }
  return counts;
}
/* a clash day: a Mon-Fri day that is not a public holiday and has more than N people away (strict) */
function tlClashDays(data, counts, n) {
  const hol = data.holidays || {};
  return Object.keys(counts).filter(d => { const wd = parseD(d).getDay(); return wd >= 1 && wd <= 5 && !hol[d] && counts[d] > n; });
}

/* ---------- drawing ---------- */
function tlCell(day, dayW, data, extra = '', attrs = '', txt = '') {
  const wd = parseD(day).getDay(), hol = data.holidays || {};
  const cls = ['tl-cell', wd === 0 || wd === 6 ? 'wk' : '', hol[day] ? 'hol' : '', extra].filter(Boolean).join(' ');
  return `<span class="${cls}" style="width:${dayW}px" ${attrs}>${txt}</span>`;
}
function tlBar(item, dayW, top, cls) {
  const w = Math.max(item.e - item.s + 1, 1) * dayW - 2;
  return `<button type="button" class="tl-bar ${cls}" style="left:${item.s * dayW}px;width:${w}px;top:${top}px" data-a="tlSelect" data-i="${item.i}" aria-label="${esc(item.aria)}"><span class="tl-bar-t">${esc(item.text)}</span></button>`;
}
function tlAdd(item) { item.i = TL.bars.length; TL.bars.push(item); return item; }
function tlPaint(data, n) {
  const days = tlDays(data.start, data.end), N = days.length, dayW = TL_DAY_W[TL.zoom], W = N * dayW;
  const counts = tlCounts(data), clash = tlClashDays(data, counts, n);
  const viewerId = S.me && S.me.id;
  TL.win = { start: data.start, days: N };
  TL.bars = [];
  // header: calendar weeks (grouped by ISO week), day numbers, people away (clash cells carry data-clash)
  let weekHead = '';
  for (let k = 0; k < N;) {
    const wk = tlIsoWeek(days[k]); let j = k;
    while (j < N && tlIsoWeek(days[j]) === wk) j++;
    weekHead += `<span class="tl-wk" style="width:${(j - k) * dayW}px">KW ${wk}</span>`;
    k = j;
  }
  const dayHead = days.map(d => {
    const show = TL.zoom === 'week' || parseD(d).getDay() === 1;
    return `<span class="tl-dn" style="width:${dayW}px">${show ? parseD(d).getDate() : ''}</span>`;
  }).join('');
  const awayRow = days.map(d => {
    const isClash = clash.includes(d);
    const txt = TL.zoom === 'week' && counts[d] ? counts[d] : '';
    return tlCell(d, dayW, data, isClash ? 'tl-clash' : '', `data-day="${d}"${isClash ? ' data-clash="1"' : ''}`, txt);
  }).join('');
  const baseCells = days.map(d => tlCell(d, dayW, data, clash.includes(d) ? 'tl-clashcol' : '', `data-day="${d}"`)).join('');
  let rows = `<div class="pln-row tl-head"><div class="pln-label">Calendar week</div><div class="pln-lane tl-lane" style="width:${W}px;height:22px">${weekHead}</div></div>
    <div class="pln-row tl-head"><div class="pln-label">Day</div><div class="pln-lane tl-lane" style="width:${W}px;height:20px">${dayHead}</div></div>
    <div class="pln-row tl-head"><div class="pln-label">People away</div><div class="pln-lane tl-lane" style="width:${W}px;height:24px">${awayRow}</div></div>`;
  data.people.forEach((p, pi) => {
    const own = p.id === viewerId;
    const contracts = p.contracts || [];
    const hasCon = contracts.length > 0;
    const h = hasCon ? 68 : 34;
    const hoursText = p.weekly_hours != null ? `${p.weekly_hours} hours per week`
      : data.level === 'full' || own ? 'hours not set' : 'hours hidden at your level';
    const chip = TL_COLOR.test(p.color || '') ? p.color : 'var(--muted)';
    const label = `<div class="pln-label"><span class="tl-chip" style="background:${esc(chip)}" aria-hidden="true"></span><b>${esc(p.name)}</b>${own ? ' <span class="pill blue">you</span>' : ''}<div class="small muted">${esc(hoursText)}</div></div>`;
    const absItems = [];
    for (const a of p.absences || []) {
      const s = Math.max(0, tlIdx(a.start)), e = Math.min(N - 1, tlIdx(a.end || a.start));
      if (e < 0 || s > N - 1) continue;
      const text = tlAbsLabel(a) + (a.label ? ': ' + a.label : '');
      const range = tlRange(a.start, a.end || a.start);
      const lines = [`Dates: ${range}`];
      if (a.kind) lines.push(`Type: ${tlAbsLabel(a)}`);
      if (a.label) lines.push(`Note: ${a.label}`);
      if (!a.kind) lines.push(data.level === 'away' ? 'Reason not shown at your level (away only).' : 'Sick days are shown as away at your level.');
      let link = null;
      if (own) link = can('planning', 'write') ? ['#/plan/timeoff', 'Edit in Plan, time off'] : can('absences', 'write') ? ['#/timeoff', 'Edit your time off'] : null;
      absItems.push(tlAdd({ lane: pi * 2, s, e, text, aria: `${p.name}, ${text}, ${range}`, cls: `tl-${a.kind || 'away'}`, title: `${p.name}${own ? ' (you)' : ''}`, lines, link }));
    }
    const conItems = contracts.map(c => {
      const s = Math.max(0, tlIdx(c.start)), e = c.end ? Math.min(N - 1, tlIdx(c.end)) : N - 1;
      const hours = c.hours_per_week != null ? `${c.hours_per_week} hours per week` : 'hours not set';
      const dates = c.end ? tlRange(c.start, c.end) : `from ${tlRange(c.start, c.start)}, open-ended`;
      const title = c.title || 'Contract';
      return tlAdd({ lane: pi * 2 + 1, s, e, text: title, aria: `${title}, ${c.customer || 'no customer'}, ${hours}, ${dates}`, cls: 'contract', title, lines: [`Customer: ${c.customer || 'none'}`, hours, `Dates: ${dates}`], link: null });
    }).filter(x => x.e >= 0 && x.s <= N - 1);
    const absBars = absItems.map(it => tlBar(it, dayW, 4, it.cls)).join('');
    const conBars = conItems.map(it => tlBar(it, dayW, 38, 'contract')).join('');
    rows += `<div class="pln-row">${label}<div class="pln-lane tl-lane" data-person="${esc(p.id)}" style="width:${W}px;height:${h}px">${baseCells}${absBars}${conBars}</div></div>`;
  });
  const today = todayISO();
  const todayLine = today >= data.start && today <= data.end ? `<div class="tl-today" style="left:${tlIdx(today) * dayW + dayW / 2}px" aria-hidden="true"></div>` : '';
  return { html: `<div class="pln-wrap tl-wrap" id="tlWrap" tabindex="0" role="region" aria-label="Team timeline. Tab moves between bars, arrow keys move between bars or scroll.">
      <div class="pln-grid tl-grid">${rows}${todayLine}</div></div>`, counts, clash };
}

/* ---------- details panel (text, never only a tooltip) ---------- */
function tlShowDetail(i) {
  const b = TL.bars[i], box = document.getElementById('tlDetail');
  if (!b || !box) return;
  document.querySelectorAll('#tlWrap .tl-bar.sel').forEach(x => x.classList.remove('sel'));
  document.querySelector(`#tlWrap .tl-bar[data-i="${i}"]`)?.classList.add('sel');
  box.innerHTML = `<b>${esc(b.title)}</b><div>${b.lines.map(l => esc(l)).join('<br>')}</div>${b.link ? `<div><a href="${esc(b.link[0])}">${esc(b.link[1])}</a></div>` : ''}`;
}
A.tlSelect = el => tlShowDetail(+el.dataset.i);
document.addEventListener('focusin', e => { const bar = e.target.closest && e.target.closest('#tlWrap .tl-bar'); if (bar) tlShowDetail(+bar.dataset.i); });

/* ---------- keyboard: Left/Right move between bars of one lane, Up/Down between lanes (nearest start day);
   where there is no bar to go to, the arrow keys scroll the timeline. No Alt shortcuts (Firefox uses Alt+arrows). */
document.addEventListener('keydown', e => {
  const el = e.target.closest && e.target.closest('#tlWrap .tl-bar');
  const wrap = document.getElementById('tlWrap');
  if (!el || !wrap) return;
  const b = TL.bars[+el.dataset.i];
  if (!b || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
  e.preventDefault();
  let target = null;
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    const lane = TL.bars.filter(x => x.lane === b.lane).sort((x, y) => x.s - y.s || x.i - y.i);
    const at = lane.indexOf(b) + (e.key === 'ArrowRight' ? 1 : -1);
    target = lane[at] || null;
    if (!target) return wrap.scrollBy({ left: (e.key === 'ArrowRight' ? 1 : -1) * 7 * TL_DAY_W[TL.zoom] });
  } else {
    const dir = e.key === 'ArrowDown' ? 1 : -1;
    const lanes = [...new Set(TL.bars.map(x => x.lane))].filter(l => (dir > 0 ? l > b.lane : l < b.lane));
    if (!lanes.length) return wrap.scrollBy({ top: dir * 48 });
    const near = dir > 0 ? Math.min(...lanes) : Math.max(...lanes);
    target = TL.bars.filter(x => x.lane === near).sort((x, y) => Math.abs(x.s - b.s) - Math.abs(y.s - b.s))[0];
  }
  document.querySelector(`#tlWrap .tl-bar[data-i="${target.i}"]`)?.focus();
});

/* ---------- page ---------- */
PAGES.timeline = async () => {
  if (!TL.start) TL.start = mondayOf(todayISO());
  const end = addDays(TL.start, TL.span * 7 - 1);
  const head = `<div class="head"><div><h1>Team timeline</h1><div class="muted">Who is away when, and who is booked on what.</div></div></div>`;
  let data;
  try { data = await api('GET', `team/timeline?start=${TL.start}&end=${end}`); }
  catch (e) {
    if (e.status === 403) return head + `<div class="card"><h2>Not available</h2><p>The team timeline is switched off for your role. The boss can change this under Settings, Roles.</p></div>`;
    if (e.status === 422) return head + `<div class="card"><h2>Cannot show this period</h2><p>${esc(e.message)}</p></div>`;
    throw e;
  }
  const n = TL.n;
  const { html, counts, clash } = tlPaint(data, n);
  const hol = data.holidays || {}, holKeys = Object.keys(hol).sort();
  const absCount = data.people.reduce((a, p) => a + (p.absences || []).length, 0);
  const spanOpts = TL_SPANS.map(w => `<option value="${w}" ${w === TL.span ? 'selected' : ''}>${w} weeks</option>`).join('');
  return head + `
  <div class="tl-toolbar">
    <div class="auto"><label for="tlFrom">First day</label><input id="tlFrom" type="date" value="${esc(TL.start)}" data-change="tlFrom"></div>
    <div class="auto"><button type="button" data-a="tlShift" data-n="-7">← Earlier</button> <button type="button" data-a="tlThisWeek">This week</button> <button type="button" data-a="tlShift" data-n="7">Later →</button></div>
    <div class="auto"><label for="tlSpan">Length</label><select id="tlSpan" data-change="tlSpan">${spanOpts}</select></div>
    <div class="auto"><label for="tlZoom">Zoom</label><select id="tlZoom" data-change="tlZoom"><option value="week" ${TL.zoom === 'week' ? 'selected' : ''}>Week (numbers shown)</option><option value="month" ${TL.zoom === 'month' ? 'selected' : ''}>Month (compact)</option></select></div>
    <div class="auto"><label for="tlN">Clash: more than N people away</label><input id="tlN" type="number" min="1" max="999" step="1" value="${n}" data-change="tlN"></div>
  </div>
  <div class="card">
    <div class="small muted">You see ${esc(TL_LEVEL_TEXT[data.level] || data.level)}. Window: ${esc(tlRange(data.start, data.end))}.</div>
    ${absCount === 0 ? '<p class="empty">Nobody has time off in this period.</p>' : ''}
    <h2>Clash days</h2>
    ${clash.length
      ? `<p class="small">More than ${n} people away. Weekends and public holidays never count.</p><ul class="tl-clashlist">${clash.map(d => `<li data-day="${d}"><b>${esc(tlDayLabel(d))}</b>: ${counts[d]} people away</li>`).join('')}</ul>`
      : `<p class="small">No clash days: no weekday has more than ${n} people away.</p>`}
    ${holKeys.length ? `<p class="small"><b>Public holidays:</b> ${holKeys.map(d => `${esc(tlDayLabel(d))} ${esc(hol[d])}`).join('; ')}</p>` : ''}
    ${data.level !== 'full' ? '<p class="small muted">Contract bars are shown at level Full only.</p>' : ''}
  </div>
  ${html}
  <div id="tlDetail" class="tl-detail" aria-live="polite">Click or Tab to a bar to see its details here.</div>
  <p class="small muted">Keyboard: Tab to a bar; arrow keys move between bars (Up and Down change the row), or scroll when there is no bar to go to.</p>`;
};
A.tlFrom = el => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(el.value) || isNaN(parseD(el.value))) return toast('Enter a date', true);
  TL.start = mondayOf(el.value); render();
};
A.tlShift = el => { TL.start = addDays(TL.start || mondayOf(todayISO()), +el.dataset.n); render(); };
A.tlThisWeek = () => { TL.start = mondayOf(todayISO()); render(); };
A.tlSpan = el => { TL.span = TL_SPANS.includes(+el.value) ? +el.value : 8; tlSetPref('tlSpan', TL.span); render(); };
A.tlZoom = el => { TL.zoom = el.value === 'month' ? 'month' : 'week'; tlSetPref('tlZoom', TL.zoom); render(); };
A.tlN = el => {
  const v = tlInt(el.value, 1, 999, NaN);
  if (!Number.isFinite(v)) { el.value = TL.n; return toast('The clash threshold is a whole number from 1 to 999', true); }
  TL.n = v; tlSetPref('tlN', v); render();
};

/* ---------- Settings, Roles: the boss's visibility levels for the team timeline ---------- */
function teamVisibilityCard() {
  if (!isBoss()) return '';
  const v = S.settings.team_timeline_visibility || {};
  return `<div class="card" id="tlVis"><h2>Roles: team timeline</h2>
    <p class="small muted">What each role sees on the Team timeline. The boss always sees everything. Details that a level does not allow are removed on the server, so they never reach the browser.</p>
    <div class="row">${fieldHTML({ k: 'tl_employee', l: 'Employee', t: 'select', o: TL_SETTING_LEVELS, row: 1 }, { tl_employee: v.employee || 'away' })}${fieldHTML({ k: 'tl_accountant', l: 'Accountant', t: 'select', o: TL_SETTING_LEVELS, row: 1 }, { tl_accountant: v.accountant || 'off' })}</div>
    <ul class="small muted">${TL_SETTING_LEVELS.map(([k, l]) => `<li><b>${esc(k)}</b>: ${esc(l)}</li>`).join('')}</ul></div>`;
}
