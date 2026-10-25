/* permitA38 – Plan & vacation: interactive what-if timeline (boss). Vanilla JS, no build step, no inline handlers.
   The server computes every number (POST /api/plan/simulate); this file draws the timeline, keeps a DRAFT of
   changes and sends it. Nothing is written until "Apply", and "Discard" drops the draft. */
(() => {
  const DW = 12;                                  // px per day on the timeline
  const LABEL_W = 200;
  const P = {
    weeks: +(localStorage.plnWeeks || 26),
    user: localStorage.plnUser ?? null,            // null = not chosen yet (default: me); '' = whole team
    project: localStorage.plnProject || '',        // '' = all projects, 'unassigned', or a project id
    alloc: {}, allocs: [], dayWeek: null, day: null,   // draft allocation changes ("r:<id>" / "n:<token>"), stored allocations, day detail
    abs: {},                                       // draft absences: key -> change
    com: {},                                       // draft commitments: "contract:<id>" | "offer:<id>" -> change
    sim: null, base: null, ent: null, hol: {}, scenarios: [], compare: null, sugg: null, sel: null,
    timer: null, busy: false,
  };
  const KINDS = { vacation: 'Vacation', holiday: 'Personal day off', sick: 'Sick', other: 'Other' };
  const pct = x => x == null ? '–' : Math.round(x) + ' %';
  const h1 = x => num(x, 1) + ' h';
  const userId = () => (P.user === null ? (S.me && S.me.id) || '' : P.user);
  const monday0 = () => mondayOf(todayISO());
  const dayIdx = s => Math.round((parseD(s) - parseD(monday0())) / 864e5);
  const dayAt = i => addDays(monday0(), i);
  const horizon = () => P.weeks * 7;
  const dirty = () => Object.keys(P.abs).length + Object.keys(P.com).length + Object.keys(P.alloc).length;
  const projName = id => (id ? (S.d.projects.find(p => p.id === id) || {}).name || '(deleted project)' : 'Unassigned');

  /* ---------- draft ---------- */
  function realAbs(id) { return S.d.absences.find(a => a.id === id); }
  function realOffer(id) { return S.d.offers.find(o => o.id === id); }
  function realContract(id) { return S.d.contracts.find(c => c.id === id); }
  function comOrig(key) {
    const [kind, id] = key.split(':');
    if (kind === 'contract') { const c = realContract(id); return c && { kind, id, start: c.start, end: c.end, hours_per_week: c.hours_per_week ?? null, total_hours: c.total_hours ?? null }; }
    const o = realOffer(id); const p = o && o.plan;
    return p && { kind, id, start: p.start, end: p.end, hours_per_week: p.hours_per_week ?? null, total_hours: p.total_hours ?? null };
  }
  function editCom(key, patch) {
    const base = P.com[key] || comOrig(key);
    if (!base) return;
    P.com[key] = { ...base, ...patch };
  }
  function editAbs(key, patch) {
    const orig = key.startsWith('r:') ? realAbs(key.slice(2)) : null;
    const cur = P.abs[key] || (orig ? { id: orig.id, user_id: orig.user_id, kind: orig.kind || 'vacation', start: orig.start, end: orig.end || orig.start } : null);
    if (!cur) return;
    P.abs[key] = { ...cur, ...patch };
  }
  function realAlloc(id) { return P.allocs.find(a => a.id === id); }
  function editAlloc(key, patch) {
    const orig = key.startsWith('r:') ? realAlloc(key.slice(2)) : null;
    const cur = P.alloc[key] || (orig ? { id: orig.id, user_id: orig.user_id, project_id: orig.project_id, start: orig.start, end: orig.end, hours_per_week: orig.hours_per_week } : null);
    if (!cur) return;
    P.alloc[key] = { ...cur, ...patch };
  }
  function payload() {
    const absences = Object.entries(P.abs).map(([k, a]) => {
      const out = { user_id: a.user_id, kind: a.kind, start: a.start, end: a.end };
      if (k.startsWith('r:')) out.id = k.slice(2);
      if (a.delete) return { id: out.id, delete: true };
      return out;
    });
    const commitments = Object.values(P.com).map(c => ({ kind: c.kind, id: c.id, start: c.start, end: c.end, hours_per_week: c.hours_per_week ?? null, total_hours: c.total_hours ?? null }));
    const allocations = Object.entries(P.alloc).map(([k, a]) => {
      if (a.delete) return { id: a.id, delete: true };
      const out = { user_id: a.user_id, project_id: a.project_id, start: a.start, end: a.end, hours_per_week: a.hours_per_week };
      if (k.startsWith('r:')) out.id = k.slice(2);
      return out;
    });
    return { absences, commitments, allocations };
  }
  function schedule() { clearTimeout(P.timer); P.timer = setTimeout(simulate, 120); }
  async function simulate() {
    try {
      const body = { changes: payload(), weeks: P.weeks, first: monday0(), user_id: userId() || null, project_id: P.project || null };
      P.sim = await api('POST', 'plan/simulate', body);
      P.base = await api('POST', 'plan/simulate', { changes: { absences: [], commitments: [], allocations: [] }, weeks: P.weeks, first: monday0(), user_id: userId() || null, project_id: P.project || null });
    } catch (e) { toast(e.message, true); }
    paint();
  }
  async function loadEntitlement() {
    P.ent = null;
    if (userId()) { try { P.ent = await api('GET', `plan/vacation?user_id=${userId()}&year=${new Date().getFullYear()}`); } catch { P.ent = null; } }
  }
  async function loadAllocs() {   // an older server (no allocations route) or a missing right: the allocation rows stay empty
    try { P.allocs = await api('GET', 'allocations?status=active'); } catch { P.allocs = []; }
  }
  async function loadHolidays() {
    const y = new Date().getFullYear();
    const [a, b] = await Promise.all([api('GET', 'holidays?year=' + y), api('GET', 'holidays?year=' + (y + 1))]);
    P.hol = { ...a, ...b };
  }

  /* ---------- drawing ---------- */
  const statusClass = s => (s === 'over' ? 'over' : s === 'tight' ? 'tight' : 'ok');
  function weekRows() { return (P.sim && P.sim.weeks) || []; }
  function explain(w) {
    const parts = [`Capacity ${h1(w.capacity)}`, `committed ${h1(w.confirmed)} (contracts)`, `open offers ${h1(w.binding_offers)} (worst case)`];
    if (w.vacation_hours) parts.push(`time off ${h1(w.vacation_hours)}`);
    if (w.holiday_hours) parts.push(`public holidays ${h1(w.holiday_hours)}`);
    if (w.gap > 0) parts.push(`not covered by allocations ${h1(w.gap)}`);
    else if (w.status === 'over') parts.push(`over by ${h1(-w.free_worst_case)}`);
    return parts.join('; ') + '. Basis: ' + w.basis + '.';
  }
  function barStyle(from, to) {
    const s = dayIdx(from), e = dayIdx(to);
    return `left:${Math.max(s, 0) * DW}px;width:${Math.max(e - Math.max(s, 0) + 1, 1) * DW - 2}px`;
  }
  function cells(cls = '') {
    let html = '';
    for (let i = 0; i < horizon(); i++) {
      const d = dayAt(i); const wd = parseD(d).getDay();
      const weekend = wd === 0 || wd === 6 ? ' wk' : '';
      const hol = P.hol[d] ? ' hol' : '';
      html += `<span class="pln-cell${weekend}${hol}${cls}" data-day="${d}" title="${esc(P.hol[d] || '')}"></span>`;
    }
    return html;
  }
  function laneTime(label, bars) {
    return `<div class="pln-row"><div class="pln-label">${label}</div><div class="pln-lane" data-lane="time" style="width:${horizon() * DW}px">${cells()}${bars}</div></div>`;
  }
  function absenceBars() {
    const uid = userId();
    let out = '';
    const drawn = new Set();
    for (const a of S.d.absences) {
      if (uid && a.user_id !== uid) continue;
      const key = 'r:' + a.id;
      if (P.abs[key] && P.abs[key].delete) continue;
      const cur = P.abs[key] || a;
      drawn.add(key);
      out += bar(cur.start, cur.end || cur.start, `abs ${cur.kind || 'vacation'}${P.abs[key] ? ' draft' : ''}`, `${KINDS[cur.kind] || cur.kind}: ${fmtDate(cur.start)}${cur.end && cur.end !== cur.start ? ' – ' + fmtDate(cur.end) : ''}`, { kind: 'abs', key, id: a.id });
    }
    for (const [key, a] of Object.entries(P.abs)) {
      if (drawn.has(key) || key.startsWith('r:')) continue;
      out += bar(a.start, a.end, 'abs draft', `New ${KINDS[a.kind] || a.kind}: ${fmtDate(a.start)} – ${fmtDate(a.end)}`, { kind: 'abs', key, id: key });
    }
    return out;
  }
  function bar(start, end, cls, label, data) {
    const attrs = Object.entries(data || {}).map(([k, v]) => `data-${k}="${esc(v)}"`).join(' ');
    return `<div class="pln-bar ${cls}" style="${barStyle(start, end)}" tabindex="0" role="group" aria-label="${esc(label)}" title="${esc(label)}" ${attrs}><i class="h l" data-mode="start" aria-hidden="true"></i><span class="pln-bar-t">${esc(label)}</span><i class="h r" data-mode="end" aria-hidden="true"></i></div>`;
  }
  function comBars() {
    const uid = userId();
    const items = [...S.d.contracts.filter(c => c.status !== 'done').map(c => ({ kind: 'contract', id: c.id, title: c.title || 'Contract', project_id: c.project_id || null, user_ids: c.user_ids || [], sub: 'contract' })),
      ...S.d.offers.filter(o => o.status === 'sent' && o.plan && !S.d.contracts.some(c => c.offer_id === o.id)).map(o => ({ kind: 'offer', id: o.id, title: 'Offer ' + (o.number || '(draft)'), project_id: o.project_id || null, user_ids: (o.plan && o.plan.user_ids) || [], sub: 'offer' }))];
    let html = '', group;
    items.sort((a, b) => projName(a.project_id).toLowerCase() < projName(b.project_id).toLowerCase() ? -1 : 1);
    for (const it of items) {
      const key = `${it.kind}:${it.id}`;
      if (it.project_id !== group) { group = it.project_id; html += `<div class="pln-row pln-group"><div class="pln-label"><b>${esc(projName(group))}</b></div></div>`; }
      const c = P.com[key] || comOrig(key);
      if (!c) continue;
      const mine = !uid || it.user_ids.length === 0 || it.user_ids.includes(uid);
      const sel = P.sel === key ? ' sel' : '';
      const otherProject = P.project && (P.project === 'unassigned' ? !!it.project_id : it.project_id !== P.project);
      const dim = mine && !otherProject ? '' : ' dim';
      const hours = c.hours_per_week ? `${c.hours_per_week} h/week` : `${c.total_hours || 0} h total`;
      const row = `<div class="pln-row${dim}"><div class="pln-label"><button type="button" class="pln-pick${sel}" data-a="plnSelect" data-key="${esc(key)}">${esc(it.title)}</button><div class="small muted">${esc(hours)}</div></div>
        <div class="pln-lane" data-lane="com" style="width:${horizon() * DW}px">${cells()}${bar(c.start, c.end, `${it.sub}${P.com[key] ? ' draft' : ''}${sel}`, `${it.title}: ${fmtDate(c.start)} – ${fmtDate(c.end)}, ${hours}`, { kind: 'com', key, id: key })}</div></div>`;
      html += row;
    }
    return html || '<div class="empty small">No contracts or open offers in this period.</div>';
  }
  function allocRows() {
    const uid = userId();
    const match = a => (!uid || a.user_id === uid) && (!P.project || a.project_id === P.project);
    const rows = [];
    const row = (key, a, draft) => {
      const label = `${projName(a.project_id)}: ${num(a.hours_per_week, 2)} h/week, ${fmtDate(a.start)} – ${fmtDate(a.end)}`;
      const who = (S.d.users.find(u => u.id === a.user_id) || {}).name || '';
      const dim = P.project && a.project_id !== P.project ? ' dim' : '';
      rows.push(`<div class="pln-row${dim}"><div class="pln-label"><span>${esc(projName(a.project_id))}</span><div class="small muted">${uid ? '' : esc(who) + ' · '}<input type="number" class="pln-hrs" min="0.25" max="80" step="0.25" value="${esc(a.hours_per_week)}" data-change="plnAllocHours" data-key="${esc(key)}" aria-label="Hours per week of ${esc(label)}"> h/week</div></div>
        <div class="pln-lane" data-lane="alloc" style="width:${horizon() * DW}px">${cells()}${bar(a.start, a.end, `alloc${draft ? ' draft' : ''}`, label, { kind: 'alloc', key, id: a.id || key })}</div></div>`);
    };
    for (const a of P.allocs) {
      if (a.kind && a.kind !== 'employee') continue;
      const key = 'r:' + a.id, d = P.alloc[key];
      if (d && d.delete) continue;
      const cur = d || a;
      if (match(cur)) row(key, cur, !!d);
    }
    for (const [key, a] of Object.entries(P.alloc)) if (key.startsWith('n:') && match(a)) row(key, a, true);
    return `<div class="pln-row pln-group"><div class="pln-label"><b>Allocations</b></div></div>${rows.join('') || '<div class="empty small">No allocations in this selection. Add a draft below, or use "Allocate my capacity" on the forecast tab.</div>'}`;
  }
  function weekRowsHtml() {
    const W = weekRows(); if (!W.length) return '';
    const cap = `<div class="pln-row"><div class="pln-label">Capacity</div><div class="pln-lane pln-weeks" style="width:${horizon() * DW}px">${W.map((w, i) => `<span class="pln-week" style="left:${i * 7 * DW}px;width:${7 * DW - 2}px" title="Week of ${esc(fmtDate(w.week))}">${num(w.capacity, 0)} h</span>`).join('')}</div></div>`;
    const util = `<div class="pln-row"><div class="pln-label">Utilisation (worst case)</div><div class="pln-lane pln-weeks" style="width:${horizon() * DW}px">${W.map((w, i) => `<button type="button" class="pln-week util ${statusClass(w.status)}${w.delta > 0 ? ' worse' : w.delta < 0 ? ' better' : ''}" style="left:${i * 7 * DW}px;width:${7 * DW - 2}px" data-a="plnWhy" data-i="${i}" aria-label="Week of ${esc(fmtDate(w.week))}: ${pct(w.utilisation)}, ${esc(w.status)}">${pct(w.utilisation)}</button>`).join('')}</div></div>`;
    const busy = W.some(w => w.busy_hours) ? `<div class="pln-row"><div class="pln-label">Calendar busy</div><div class="pln-lane pln-weeks" style="width:${horizon() * DW}px">${W.map((w, i) => `<span class="pln-week" style="left:${i * 7 * DW}px;width:${7 * DW - 2}px">${w.busy_hours ? num(w.busy_hours, 1) + ' h' : ''}</span>`).join('')}</div></div>` : '';
    return cap + util + busy + projectStack(W);
  }
  // One small column per project and week: outline = allocated, fill = planned, red part = not covered (server numbers, scaled to fit).
  function projectStack(W) {
    if (!W.some(w => w.projects && w.projects.length)) return '';
    const mx = Math.max(1, ...W.flatMap(w => (w.projects || []).map(p => Math.max(p.capacity, p.confirmed + p.binding_offers))));
    const H = 28;
    const cols = (w, i) => {
      const list = (w.projects || []).filter(p => p.capacity || p.confirmed || p.binding_offers || p.gap);
      const cw = Math.max(3, Math.floor((7 * DW - 4) / Math.max(list.length, 1)) - 1);
      return `<span class="pln-stack" style="left:${i * 7 * DW}px;width:${7 * DW - 2}px">${list.map(p => {
        const planned = p.confirmed + p.binding_offers;
        const d = p.delta ? `; draft ${p.delta > 0 ? '+' : ''}${num(p.delta, 1)} h gap` : '';
        const tip = `${p.name}: allocated ${h1(p.capacity)}, planned ${h1(planned)}${p.gap > 0 ? `, not covered ${h1(p.gap)}` : ''}${d}`;
        return `<i class="pln-pbar${p.delta > 0 ? ' worse' : p.delta < 0 ? ' better' : ''}" style="width:${cw}px" title="${esc(tip)}"><b class="cap" style="height:${p.capacity / mx * H}px"></b><b class="plan" style="height:${planned / mx * H}px"></b>${p.gap > 0 ? `<b class="gap" style="height:${Math.min(p.gap, planned) / mx * H}px;bottom:${(planned - Math.min(p.gap, planned)) / mx * H}px"></b>` : ''}</i>`;
      }).join('')}</span>`;
    };
    return `<div class="pln-row"><div class="pln-label">Per project<div class="small muted">allocated / planned / gap</div></div><div class="pln-lane pln-weeks pln-stacks" style="width:${horizon() * DW}px">${W.map(cols).join('')}</div></div>`;
  }
  function headerRow() {
    const W = weekRows();
    return `<div class="pln-row pln-head"><div class="pln-label">Week of</div><div class="pln-lane" style="width:${horizon() * DW}px">${W.map((w, i) => `<span class="pln-wk" style="left:${i * 7 * DW}px">${esc(fmtDate(w.week).slice(0, 5))}</span>`).join('')}</div></div>`;
  }
  function entBox() {
    const e = P.ent;
    if (!userId()) return '<div class="small muted">Pick one person to see the vacation entitlement.</div>';
    if (!e) return '<div class="small muted">No vacation entitlement entered yet.</div>';
    const cls = e.over ? 'bad' : '';
    return `<div class="pln-ent ${cls}"><b>Vacation ${e.year}:</b> entitlement ${e.entitlement == null ? 'not set' : num(e.entitlement, 1) + ' days'}, used ${num(e.used, 1)}, planned ${num(e.planned, 1)}, remaining ${e.remaining == null ? '–' : num(e.remaining, 1)} working days${e.over ? ' <span class="pill bad">beyond entitlement</span>' : ''}</div>`;
  }
  function compareBox() {
    if (!P.compare || !P.sim) return '';
    const t = (x) => x ? x.totals : {};
    const row = (label, f) => `<tr><th>${label}</th><td class="num">${f(t(P.base))}</td><td class="num">${f(t(P.sim))}</td><td class="num">${f(t(P.compare))}</td></tr>`;
    return `<div class="card"><h2>Compare scenarios</h2><table><tr><th></th><th class="num">Current base</th><th class="num">Draft</th><th class="num">${esc(P.compare.name)}</th></tr>
      ${row('Free hours (worst case)', x => num(x.free_worst_case, 1) + ' h')}
      ${row('Over-capacity weeks', x => String(x.over_weeks))}
      ${row('Revenue (basis rule)', x => eur(x.revenue))}
      ${row('Contracted revenue', x => eur(x.contracted_revenue))}</table></div>`;
  }
  function suggBox() {
    const s = P.sugg;
    if (!s) return '';
    if (s.kind === 'vacation') return `<div class="small"><b>Best windows for ${s.days} working days:</b><ol>${s.options.map((o, i) => `<li>${fmtDate(o.start)} – ${fmtDate(o.end)} · overload ${num(o.overload_hours, 1)} h · off days gained ${o.bridge_days} <button type="button" class="ghost sm" data-a="plnUseOption" data-i="${i}">Use</button></li>`).join('') || '<li>No window fits.</li>'}</ol></div>`;
    if (s.kind === 'start') return `<div class="small">${s.result.feasible ? `Earliest feasible start: <b>${fmtDate(s.result.start)}</b> (${num(s.result.free_hours, 1)} h free until the deadline).` : `Not feasible before the deadline: at most ${num(s.result.max_hours_from_earliest || 0, 1)} h are free.`}</div>`;
    if (s.kind === 'move') return `<div class="small">${s.result.fits ? 'It already fits.' : s.result.suggestions.map((x, i) => `<div>${esc(x.message)} ${x.type === 'move' ? `<button type="button" class="ghost sm" data-a="plnUseMove" data-i="${i}">Apply</button>` : ''}</div>`).join('')}</div>`;
    return '';
  }
  function scenarioOptions() {
    return P.scenarios.map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
  }
  function paint() {
    const el = document.getElementById('pln');
    if (!el) return;
    const act = document.activeElement, focused = act && act.closest && act.closest('.pln-bar');
    const focusKey = focused && el.contains(focused) ? focused.dataset.key : null;   // a redraw must not drop keyboard focus
    const wasDiscard = !!(act && act.closest && act.closest('.pln-draftlist') && el.contains(act));
    el.innerHTML = view();
    bindPlanner();
    if (wasDiscard) (el.querySelector('.pln-draftlist button') || el.querySelector('.pln-bar'))?.focus();   // the clicked button is gone
    if (P.sel) document.querySelector(`#pln .pln-bar[data-key="${CSS.escape(P.sel)}"]`)?.classList.add('sel');
    if (focusKey) { const again = document.querySelector(`.pln-bar[data-key="${CSS.escape(focusKey)}"]`); if (again) again.focus(); }
  }
  // One line per draft change, each with its own "Discard", so single items can be dropped without losing the rest.
  function draftList() {
    const items = [];
    for (const [key, a] of Object.entries(P.abs)) {
      const what = a.delete ? `Remove time off ${fmtDate(a.start || (realAbs(a.id) || {}).start)}` : key.startsWith('n:') ? `New ${KINDS[a.kind] || a.kind}: ${fmtDate(a.start)} – ${fmtDate(a.end)}` : `Move ${KINDS[a.kind] || a.kind}: ${fmtDate(a.start)} – ${fmtDate(a.end)}`;
      items.push({ store: 'abs', key, what });
    }
    for (const [key, c] of Object.entries(P.com)) {
      items.push({ store: 'com', key, what: `${key.startsWith('offer') ? 'Offer' : 'Contract'}: ${fmtDate(c.start)} – ${fmtDate(c.end)}` });
    }
    for (const [key, a] of Object.entries(P.alloc)) {
      items.push({ store: 'alloc', key, what: a.delete ? `Remove allocation to ${projName((realAlloc(a.id) || {}).project_id)}` : `${key.startsWith('n:') ? 'New allocation' : 'Allocation'} to ${projName(a.project_id)}: ${num(a.hours_per_week, 2)} h/week, ${fmtDate(a.start)} – ${fmtDate(a.end)}` });
    }
    if (!items.length) return '';
    return `${deltaBox()}<ul class="pln-draftlist">${items.map(i => `<li><span>${esc(i.what)}</span> <button type="button" class="ghost sm" data-a="plnDiscardOne" data-store="${i.store}" data-key="${esc(i.key)}">Discard</button></li>`).join('')}</ul>`;
  }
  function deltaBox() {
    const d = P.sim && P.sim.delta;
    if (!d || !d.projects || !d.projects.length) return '';
    return `<div class="pln-delta small" role="status"><b>Effect of the draft on the gap:</b> ${d.projects.map(p => `<span class="${p.delta > 0 ? 'worse' : p.delta < 0 ? 'better' : ''}">${esc(p.name)} ${esc(h1(p.base_gap))} → ${esc(h1(p.gap))} (${p.delta > 0 ? '+' : ''}${esc(num(p.delta, 1))} h)</span>`).join('; ')}</div>`;
  }
  function dayBox() {
    if (!P.day || !P.dayWeek) return '';
    const names = P.day.scope_names || {};
    const pool = (b, k) => b && b[k] && (b[k].cap || b[k].assigned) ? `${k} ${num(b[k].assigned, 2)}/${num(b[k].cap, 2)}${b[k].gap > 0 ? ` (gap ${num(b[k].gap, 2)})` : ''}` : '';
    return `<div class="card pln-day"><h2>Days of the week of ${esc(fmtDate(P.dayWeek))}</h2><table><tr><th>Day</th><th class="num">Capacity</th><th>Per project (planned / capacity)</th></tr>${P.day.days.map(d => `<tr><td class="nowrap">${esc(dayName(d.date))} ${esc(fmtDate(d.date).slice(0, 5))}</td><td class="num">${num(d.cap, 2)}</td><td class="small">${Object.entries(d.scopes || {}).map(([k, sc]) => `<span class="nowrap">${esc(names[k] || k)}: ${esc([pool(sc, 'internal'), pool(sc, 'external')].filter(Boolean).join(', '))}</span>`).join(' · ')}</td></tr>`).join('')}</table></div>`;
  }
  function view() {
    const W = weekRows();
    const tot = (P.sim && P.sim.totals) || {};
    const users = S.d.users.filter(u => u.active !== false);
    const n = dirty();
    return `<div class="pln-toolbar">
      <div class="auto"><label for="plnWho">Who</label><select id="plnWho" data-change="plnWho"><option value="">Whole team</option>${users.map(u => `<option value="${esc(u.id)}" ${u.id === userId() ? 'selected' : ''}>${esc(u.name)}</option>`).join('')}</select></div>
      <div class="auto"><label for="plnProj">Project</label><select id="plnProj" data-change="plnProject"><option value="">All projects</option>${S.d.projects.filter(p => p.status !== 'archived' || p.id === P.project).map(p => `<option value="${esc(p.id)}" ${p.id === P.project ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}<option value="unassigned" ${P.project === 'unassigned' ? 'selected' : ''}>Unassigned</option></select></div>
      <div class="auto"><label for="plnHor">Horizon</label><select id="plnHor" data-change="plnHorizon">${[13, 26, 52].map(x => `<option value="${x}" ${x === P.weeks ? 'selected' : ''}>${x} weeks</option>`).join('')}</select></div>
      <div class="pln-kpis small"><span>Free (worst case): <b>${num(tot.free_worst_case || 0, 0)} h</b></span><span>Over-capacity weeks: <b>${tot.over_weeks || 0}</b></span><span>Not covered: <b>${num(tot.gap || 0, 0)} h</b></span><span>Revenue: <b>${eur(tot.revenue || 0)}</b></span></div>
    </div>
    ${entBox()}
    <div class="pln-draftbar ${n ? 'on' : ''}" role="region" aria-label="Draft changes">
      <span>${n ? `${n} draft change${n === 1 ? '' : 's'} – nothing is saved yet.` : 'No draft changes. Drag on the time-off row to add vacation; drag bars to move or resize them.'}</span>
      <span class="right auto"><button type="button" class="ghost" data-a="plnDiscard" ${n ? '' : 'disabled'}>Discard</button> <button type="button" class="primary" data-a="plnApply" ${n ? '' : 'disabled'}>Apply</button></span>
    </div>
    ${draftList()}
    <div class="pln-help small muted">Keyboard: click or Tab to a bar, then ←/→ moves it one day (PageUp/PageDown: one week); Shift+←/→ changes its end, Ctrl+←/→ its start; Esc discards this item's change; Delete or Backspace removes a time-off entry. Touch: drag bars and the time-off row.</div>
    <div class="pln-wrap" id="plnWrap" aria-label="Timeline">
      <div class="pln-grid">
        ${headerRow()}
        <div class="pln-row"><div class="pln-label">Public holidays</div><div class="pln-lane" style="width:${horizon() * DW}px">${Array.from({ length: horizon() }, (_, i) => { const d = dayAt(i); return P.hol[d] ? `<span class="pln-mark" style="left:${i * DW}px" title="${esc(P.hol[d])}"></span>` : ''; }).join('')}</div></div>
        ${weekRowsHtml()}
        ${laneTime('Time off', absenceBars())}
        ${allocRows()}
        ${comBars()}
      </div>
    </div>
    ${dayBox()}
    <div class="pln-drag" id="plnDrag" hidden></div>
    <div class="grid g2 pln-panels">
      <div class="card"><h2>Vacation</h2>
        <form class="pln-form" data-submit="plnAddVac"><div class="row"><div class="auto"><label for="plnFrom">From</label><input id="plnFrom" type="date" value="${todayISO()}"></div><div class="auto"><label for="plnTo">To (inclusive)</label><input id="plnTo" type="date" value="${addDays(todayISO(), 6)}"></div><div class="auto"><button type="button" class="primary" data-a="plnAddVac">Add vacation</button></div></div></form>
        <form class="pln-form" data-submit="plnSuggest"><h3>Find a time</h3><div class="row"><div class="auto"><label for="plnDays">Working days</label><input id="plnDays" type="number" min="1" max="60" value="5"></div><div class="auto"><label for="plnWFrom">Window from</label><input id="plnWFrom" type="date" value="${todayISO()}"></div><div class="auto"><label for="plnWTo">Window to</label><input id="plnWTo" type="date" value="${addDays(todayISO(), 90)}"></div><div class="auto"><button type="button" data-a="plnSuggest">Suggest</button></div></div></form>
        <form class="pln-form" data-submit="plnStart"><h3>Earliest start for an offer</h3><div class="row"><div class="auto"><label for="plnHrs">Hours</label><input id="plnHrs" type="number" min="0" step="0.5" value="40"></div><div class="auto"><label for="plnDead">Deadline</label><input id="plnDead" type="date" value="${addDays(todayISO(), 120)}"></div><div class="auto"><button type="button" data-a="plnStart">Find start</button></div></div></form>
        <div id="plnSugg">${suggBox()}</div>
        <div class="small muted" style="margin-top:10px">Select an offer bar, then:</div>
        <button type="button" data-a="plnMustMove" ${P.sel && P.sel.startsWith('offer:') ? '' : 'disabled'}>What must move?</button>
      </div>
      <div class="card"><h2>Allocation draft</h2>
        <div class="small muted">Try giving a person's hours to another project. Nothing is saved until Apply.</div>
        <form class="pln-form" data-submit="plnAddAlloc"><div class="row"><div class="auto"><label for="plnAProj">Project</label><select id="plnAProj">${S.d.projects.filter(p => !['done', 'archived'].includes(p.status)).map(p => `<option value="${esc(p.id)}" ${p.id === P.project ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></div><div class="auto"><label for="plnAHrs">Hours per week</label><input id="plnAHrs" type="number" min="0.25" max="80" step="0.25" value="10"></div></div>
          <div class="row"><div class="auto"><label for="plnAFrom">From</label><input id="plnAFrom" type="date" value="${monday0()}"></div><div class="auto"><label for="plnATo">To</label><input id="plnATo" type="date" value="${addDays(monday0(), 27)}"></div><div class="auto"><button type="button" data-a="plnAddAlloc">Add allocation</button></div></div></form>
      </div>
      <div class="card"><h2>Scenarios</h2>
        <div class="row"><div class="auto"><label for="plnScnName">Name</label><input id="plnScnName" type="text" maxlength="200" placeholder="e.g. Skiing in March"></div><div class="auto"><button type="button" data-a="plnSaveScenario" ${n ? '' : 'disabled'}>Save draft as scenario</button></div></div>
        <div class="row"><div class="auto"><label for="plnLoad">Load</label><select id="plnLoad" data-change="plnLoadScenario"><option value="">–</option>${scenarioOptions()}</select></div><div class="auto"><label for="plnCmp">Compare with</label><select id="plnCmp" data-change="plnCompare"><option value="">–</option>${scenarioOptions()}</select></div></div>
        <div class="small muted">Loading a scenario replaces the draft. Saved scenarios are yours; they never change data until applied.</div>
        ${compareBox()}
      </div>
    </div>
    <div class="card"><h2>Week by week</h2><table><tr><th>Week of</th><th class="num">Capacity</th><th class="num">Contracts</th><th class="num">Offers</th><th class="num">Free</th><th class="num">Util.</th><th class="num">Gap</th><th>Basis</th><th class="num">Revenue</th><th>Notes</th></tr>
      ${W.map(w => `<tr title="${esc(explain(w))}"><td class="nowrap">${esc(fmtDate(w.week))}</td><td class="num">${num(w.capacity, 1)}</td><td class="num">${num(w.confirmed, 1)}</td><td class="num muted">${w.binding_offers ? num(w.binding_offers, 1) : ''}</td><td class="num ${w.free_worst_case < 0 ? 'free-bad' : 'free-good'}">${num(w.free_worst_case, 1)}</td><td class="num ${statusClass(w.status)}">${pct(w.utilisation)}</td><td class="num">${w.gap > 0 ? `<span class="pp-gap">${num(w.gap, 1)}</span>` : ''}${w.delta ? ` <span class="small ${w.delta > 0 ? 'worse' : 'better'}">${w.delta > 0 ? '+' : ''}${num(w.delta, 1)}</span>` : ''}</td><td><span class="pill basis-${w.basis}">${esc(BASIS_LABEL[w.basis] || w.basis)}</span></td><td class="num">${eur(w.revenue)}</td><td class="small muted">${w.events.map(esc).join(', ')}</td></tr>`).join('')}</table></div>`;
  }
  const BASIS_LABEL = { contract: 'contracted', 'offer-binding': 'open offers', intended: 'assumed' };

  /* ---------- pointer and keyboard ---------- */
  let drag = null;
  function dayFromPoint(x, y) {
    const el = document.elementFromPoint(x, y);
    const cell = el && el.closest && el.closest('[data-day]');
    return cell ? cell.dataset.day : null;
  }
  function applyBarDelta(kind, key, id, mode, days) {
    if (!days && mode !== 'create') return;
    if (kind === 'abs') {
      const cur = P.abs[key] || (key.startsWith('r:') ? (() => { const a = realAbs(id); return a && { id, user_id: a.user_id, kind: a.kind || 'vacation', start: a.start, end: a.end || a.start }; })() : null);
      if (!cur) return;
      let { start, end } = cur;
      if (mode === 'start') start = addDays(start, days);
      else if (mode === 'end') end = addDays(end, days);
      else { start = addDays(start, days); end = addDays(end, days); }
      if (end < start) return;
      editAbs(key, { start, end });
    } else if (kind === 'alloc') {
      const cur = P.alloc[key] || (key.startsWith('r:') ? (() => { const a = realAlloc(id); return a && { id, user_id: a.user_id, project_id: a.project_id, start: a.start, end: a.end, hours_per_week: a.hours_per_week }; })() : null);
      if (!cur) return;
      let { start, end } = cur;
      if (mode === 'start') start = addDays(start, days);
      else if (mode === 'end') end = addDays(end, days);
      else { start = addDays(start, days); end = addDays(end, days); }
      if (end < start) return;
      editAlloc(key, { start, end });
    } else {
      const c = P.com[key] || comOrig(key);
      if (!c) return;
      let { start, end } = c;
      if (mode === 'start') start = addDays(start, days);
      else if (mode === 'end') end = addDays(end, days);
      else { start = addDays(start, days); end = addDays(end, days); }
      if (end < start) return;
      editCom(key, { start, end });
    }
    schedule();
  }
  function selectBar(bar) {   // a clicked bar is selected and focused, so the keyboard works right after the click
    P.sel = bar.dataset.key;
    document.querySelectorAll('#pln .pln-bar.sel').forEach(b => b.classList.remove('sel'));
    bar.classList.add('sel');
    bar.focus({ preventScroll: true });
  }
  function onDown(e) {
    const bar = e.target.closest('.pln-bar');
    const lane = e.target.closest('[data-lane="time"]');
    if (bar && e.button === 0) {
      const mode = (e.target.dataset && e.target.dataset.mode) || 'move';
      drag = { kind: bar.dataset.kind, key: bar.dataset.key, id: bar.dataset.id, mode, x0: e.clientX, el: bar, delta: 0,
        left: parseFloat(bar.style.left) || 0, width: parseFloat(bar.style.width) || DW };
      selectBar(bar);
      bar.setPointerCapture && bar.setPointerCapture(e.pointerId);
    } else if (lane && e.target.closest('[data-day]') && e.button === 0 && !bar) {
      const d = e.target.closest('[data-day]').dataset.day;
      drag = { kind: 'create', mode: 'create', startDay: d, curDay: d, el: null, lane };
      showRange(d, d, e);
    } else { return; }
    e.preventDefault();   // no text selection / native drag; focus is set by selectBar()
    document.getElementById('plnWrap')?.classList.add('dragging');
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: true });
  }
  // Live feedback while dragging: a ghost bar in the lane (create) and a label that follows the pointer.
  function hint(text, e) {
    const box = document.getElementById('plnDrag');
    if (!box) return;
    box.hidden = false;
    box.textContent = text;
    if (e) { box.style.left = Math.min(e.clientX + 14, window.innerWidth - 240) + 'px'; box.style.top = (e.clientY + 18) + 'px'; }
  }
  function showRange(a, b, e) {
    const [s, t] = a <= b ? [a, b] : [b, a];
    if (drag && drag.lane) {
      let g = drag.lane.querySelector('.pln-ghost');
      if (!g) { g = document.createElement('div'); g.className = 'pln-ghost'; g.setAttribute('aria-hidden', 'true'); drag.lane.appendChild(g); }
      g.style.cssText = barStyle(s, t);
    }
    hint(`Vacation ${fmtDate(s)} – ${fmtDate(t)}: release to add`, e);
  }
  function plural(n, w) { return `${n} ${w}${Math.abs(n) === 1 ? '' : 's'}`; }
  function onMove(e) {
    if (!drag) return;
    const d = dayFromPoint(e.clientX, e.clientY);
    if (drag.mode === 'create') { if (d) { drag.curDay = d; showRange(drag.startDay, d, e); } return; }
    if (drag.el) {   // the bar itself is under the pointer, so measure distance instead of hit-testing a day cell
      let delta = Math.round((e.clientX - drag.x0) / DW);
      if (drag.mode === 'start') delta = Math.min(delta, Math.round((drag.width + 2) / DW) - 1);   // never past the end
      if (drag.mode === 'end') delta = Math.max(delta, 1 - Math.round((drag.width + 2) / DW));     // never before the start
      drag.delta = delta;
      const st = drag.el.style;
      if (drag.mode === 'move') st.transform = `translateX(${delta * DW}px)`;
      else if (drag.mode === 'start') { st.left = drag.left + delta * DW + 'px'; st.width = drag.width - delta * DW + 'px'; }
      else { st.width = drag.width + delta * DW + 'px'; }
      hint(`${{ move: 'Move', start: 'Start', end: 'End' }[drag.mode]} ${delta > 0 ? '+' : ''}${plural(delta, 'day')}: release to apply`, e);
    }
  }
  function onUp(e) {
    window.removeEventListener('pointermove', onMove);
    document.getElementById('plnWrap')?.classList.remove('dragging');
    const cur = drag; drag = null;
    if (!cur) return;
    const box = document.getElementById('plnDrag'); if (box) box.hidden = true;
    cur.lane?.querySelector('.pln-ghost')?.remove();
    if (cur.mode === 'create') {
      const d = dayFromPoint(e.clientX, e.clientY) || cur.curDay;
      if (!d) return;
      const [s, t] = cur.startDay <= d ? [cur.startDay, d] : [d, cur.startDay];
      const k = 'n:' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      P.abs[k] = { user_id: userId(), kind: 'vacation', start: s, end: t };
      schedule();
      return;
    }
    if (cur.el) { cur.el.style.transform = ''; cur.el.style.left = cur.left + 'px'; cur.el.style.width = cur.width + 'px'; }   // the redraw shows the result
    applyBarDelta(cur.kind, cur.key, cur.id, cur.mode, Math.round((e.clientX - cur.x0) / DW));
  }
  function onKey(e) {
    const bar = e.target.closest && e.target.closest('.pln-bar');
    if (!bar) return;
    if (e.key === 'Delete' || e.key === 'Backspace') {  // remove a time-off draft or mark a real one for deletion
      if (bar.dataset.kind !== 'abs' && bar.dataset.kind !== 'alloc') return;
      e.preventDefault();
      const st = bar.dataset.kind === 'alloc' ? P.alloc : P.abs;
      if (bar.dataset.key.startsWith('n:')) delete st[bar.dataset.key];
      else st[bar.dataset.key] = { ...(st[bar.dataset.key] || { id: bar.dataset.id, user_id: userId(), kind: 'vacation' }), delete: true };
      schedule();
      return;
    }
    if (e.key === 'Escape') {   // drop just this item's draft change
      const store = { abs: P.abs, alloc: P.alloc }[bar.dataset.kind] || P.com;
      if (!store[bar.dataset.key]) return;
      e.preventDefault();
      delete store[bar.dataset.key];
      schedule();
      return;
    }
    if (e.key === 'PageUp' || e.key === 'PageDown') {   // a week at a time (Shift: the end)
      e.preventDefault();
      applyBarDelta(bar.dataset.kind, bar.dataset.key, bar.dataset.id, e.shiftKey ? 'end' : e.ctrlKey || e.metaKey ? 'start' : 'move', e.key === 'PageDown' ? 7 : -7);
      return;
    }
    if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    e.preventDefault();
    const step = e.key === 'ArrowRight' ? 1 : -1;
    applyBarDelta(bar.dataset.kind, bar.dataset.key, bar.dataset.id, e.shiftKey ? 'end' : e.ctrlKey || e.metaKey ? 'start' : 'move', step);
  }

  function bindPlanner() {
    const wrap = document.getElementById('plnWrap');
    if (!wrap) return;
    wrap.addEventListener('pointerdown', onDown);
    wrap.addEventListener('keydown', onKey);
  }

  /* ---------- actions ---------- */
  A.plnSelect = el => { P.sel = el.dataset.key; paint(); };
  A.plnWhy = guard(async el => {
    const w = weekRows()[+el.dataset.i];
    toast(explain(w));
    if (!w) return;
    const uid = userId();
    P.day = await api('GET', `plan/day-table?from=${w.week}&to=${addDays(w.week, 6)}${uid ? '&user_id=' + uid : ''}${P.project ? '&project_id=' + encodeURIComponent(P.project) : ''}`);
    P.dayWeek = w.week;
    paint();
  });
  A.plnDiscardOne = el => { delete ({ abs: P.abs, alloc: P.alloc }[el.dataset.store] || P.com)[el.dataset.key]; schedule(); };
  A.plnDiscard = () => { P.abs = {}; P.com = {}; P.alloc = {}; P.sugg = null; schedule(); };
  A.plnProject = el => { P.project = el.value; localStorage.plnProject = P.project; P.day = null; P.dayWeek = null; schedule(); };
  A.plnAllocHours = el => {
    const v = +el.value;
    if (!(v >= 0.25 && v <= 80)) { toast('Hours per week must be between 0.25 and 80', true); return paint(); }
    editAlloc(el.dataset.key, { hours_per_week: v });
    schedule();
  };
  A.plnAddAlloc = () => {
    const uid = userId(); if (!uid) return toast('Pick one person first', true);
    const project_id = document.getElementById('plnAProj').value, start = document.getElementById('plnAFrom').value, end = document.getElementById('plnATo').value, hours = +document.getElementById('plnAHrs').value;
    if (!project_id || !start || !end) return toast('Choose a project and both dates', true);
    if (end < start) return toast('The end is before the start', true);
    if (!(hours >= 0.25 && hours <= 80)) return toast('Hours per week must be between 0.25 and 80', true);
    P.alloc['n:' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)] = { user_id: uid, project_id, start, end, hours_per_week: hours };
    schedule();
  };
  A.plnAddVac = () => {
    const s = document.getElementById('plnFrom').value, t = document.getElementById('plnTo').value;
    if (!s || !t) return toast('Choose both dates', true);
    if (t < s) return toast('The end is before the start', true);
    P.abs['n:' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)] = { user_id: userId(), kind: 'vacation', start: s, end: t };
    schedule();
  };
  A.plnApply = guard(async () => {
    const body = payload();
    const count = body.absences.length + body.commitments.length + body.allocations.length;
    if (!window.confirm(`Write ${count} change(s) to your real data? This cannot be undone with one click.`)) return;
    const r = await api('POST', 'plan/apply', { changes: body, confirm: true });
    toast(`Applied ${r.applied} change(s).`);
    P.abs = {}; P.com = {}; P.alloc = {}; P.sugg = null;
    await loadAll(['contracts', 'absences', 'offers']);
    await loadAllocs();
    if (r.allocation_warnings && r.allocation_warnings.length) showWarnings(r.allocation_warnings);
    await loadEntitlement();
    await simulate();
  });
  A.plnSuggest = guard(async () => {
    const uid = userId(); if (!uid) return toast('Pick one person first', true);
    const body = { user_id: uid, days: +document.getElementById('plnDays').value, window_from: document.getElementById('plnWFrom').value, window_to: document.getElementById('plnWTo').value };
    const res = await api('POST', 'plan/suggest-vacation', body);
    P.sugg = { kind: 'vacation', days: body.days, options: res.options };
    paint();
  });
  A.plnUseOption = el => {
    const o = P.sugg && P.sugg.options[+el.dataset.i];
    if (!o) return;
    P.abs['n:' + Date.now().toString(36)] = { user_id: userId(), kind: 'vacation', start: o.start, end: o.end };
    schedule();
  };
  A.plnStart = guard(async () => {
    const body = { hours: +document.getElementById('plnHrs').value, deadline: document.getElementById('plnDead').value, earliest: todayISO(), user_ids: userId() ? [userId()] : [] };
    const res = await api('POST', 'plan/suggest-start', body);
    P.sugg = { kind: 'start', result: res };
    paint();
  });
  A.plnMustMove = guard(async () => {
    const o = realOffer((P.sel || '').split(':')[1]);
    if (!o) return toast('Select an offer first', true);
    const c = P.com[P.sel] || comOrig(P.sel);
    const plan = { ...(o.plan || {}), start: c.start, end: c.end, hours_per_week: c.hours_per_week ?? undefined, total_hours: c.total_hours ?? undefined };
    const res = await api('POST', 'plan/what-must-move', { plan });
    P.sugg = { kind: 'move', result: res, key: P.sel };
    paint();
  });
  A.plnUseMove = el => {
    const s = P.sugg && P.sugg.result.suggestions[+el.dataset.i];
    if (!s || s.type !== 'move') return;
    const key = `${s.kind}:${s.id}`;
    const c = P.com[key] || comOrig(key);
    if (!c) return;
    editCom(key, { start: addDays(c.start, s.days), end: addDays(c.end, s.days) });
    schedule();
  };
  A.plnSaveScenario = guard(async () => {
    const name = (document.getElementById('plnScnName').value || '').trim();
    if (!name) return toast('Give the scenario a name', true);
    await api('POST', 'plan/scenarios', { name, changes: payload() });
    toast('Scenario saved');
    await loadScenarios();
    paint();
  });
  async function loadScenarios() { P.scenarios = await api('GET', 'plan/scenarios'); }
  A.plnLoadScenario = el => {
    const s = P.scenarios.find(x => x.id === el.value);
    if (!s) return;
    P.abs = {}; P.com = {}; P.alloc = {};
    for (const a of s.changes.allocations || []) {
      if (a.id) P.alloc['r:' + a.id] = a.delete ? { id: a.id, delete: true } : { id: a.id, user_id: a.user_id, project_id: a.project_id, start: a.start, end: a.end, hours_per_week: a.hours_per_week };
      else P.alloc['n:' + Math.random().toString(36).slice(2, 8)] = { user_id: a.user_id, project_id: a.project_id, start: a.start, end: a.end, hours_per_week: a.hours_per_week };
    }
    for (const a of s.changes.absences || []) {
      if (a.id) { if (a.delete) P.abs['r:' + a.id] = { id: a.id, delete: true, user_id: a.user_id }; else P.abs['r:' + a.id] = { id: a.id, user_id: a.user_id, kind: a.kind || 'vacation', start: a.start, end: a.end }; }
      else P.abs['n:' + Math.random().toString(36).slice(2, 8)] = { user_id: a.user_id, kind: a.kind || 'vacation', start: a.start, end: a.end };
    }
    for (const c of s.changes.commitments || []) P.com[`${c.kind}:${c.id}`] = { ...c };
    schedule();
  };
  A.plnCompare = guard(async el => {
    const s = P.scenarios.find(x => x.id === el.value);
    if (!s) { P.compare = null; return paint(); }
    const r = await api('POST', 'plan/simulate', { changes: s.changes, weeks: P.weeks, first: monday0(), user_id: userId() || null, project_id: P.project || null });
    P.compare = { name: s.name, totals: r.totals };
    paint();
  });
  A.plnDeleteScenario = guard(async el => {
    if (!el.dataset.id || !window.confirm('Delete this scenario?')) return;
    await api('DELETE', 'plan/scenarios/' + el.dataset.id);
    await loadScenarios(); paint();
  });
  A.plnEditVacation = guard(async el => {
    const u = S.d.users.find(x => x.id === el.dataset.id);
    if (!u) return;
    const days = prompt('Vacation days per year', u.vacation_days_per_year ?? '');
    if (days === null) return;
    const carry = prompt('Carried over from last year (days)', u.vacation_carry_over ?? '0');
    if (carry === null) return;
    const body = { username: u.username, name: u.name, role: u.role, weekly_hours: u.weekly_hours, workdays: u.workdays, default_rate: u.default_rate, cost_rate: u.cost_rate, color: u.color, tracking_start: u.tracking_start, active: u.active !== false, vacation_days_per_year: days === '' ? null : +days, vacation_carry_over: carry === '' ? null : +carry };
    await api.put('users/' + u.id, body);
    toast('Saved');
    await loadAll(['users']);
    await loadEntitlement();
    paint();
  });
  A.plnHoursSet = el => {
    const key = el.dataset.key; const c = P.com[key] || comOrig(key); if (!c) return;
    const v = Math.max(0, +el.value || 0);
    if (c.hours_per_week != null) editCom(key, { hours_per_week: v }); else editCom(key, { total_hours: v });
    schedule();
  };
  // data-change handlers used by the toolbar (the app's delegated change listener calls A[name])
  A.plnWho = el => { P.user = el.value; localStorage.plnUser = P.user; P.sel = null; loadEntitlement().then(schedule); };
  A.plnHorizon = el => { P.weeks = +el.value; localStorage.plnWeeks = P.weeks; loadHolidays().then(schedule); };
  A.plnHours = el => A.plnHoursSet(el);
  // form submits (Enter in a field) run the matching button action
  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || !e.target.closest) return;
    const form = e.target.closest('form[data-submit]');
    if (!form) return;
    e.preventDefault();
    const fn = A[form.dataset.submit]; if (fn) fn(form);
  });

  /* ---------- page entry ---------- */
  PAGES.planVacation = async () => {
    await loadAll(['contracts', 'absences', 'customers', 'users', 'offers', 'projects']);
    await Promise.all([loadHolidays(), loadScenarios(), loadEntitlement(), loadAllocs()]);
    await simulate();
    render.after = () => { bindPlanner(); };
    return `<div class="pln-page"><div id="pln">${view()}</div></div>`;
  };
  /* ---------- non-blocking planning warnings (banner) and the project period box ---------- */
  // The warnings stay in `pending` until dismissed (or the user moves on), because saving usually re-renders the page.
  let pending = null;
  function paintWarnings() {
    const main = document.querySelector('main');
    let box = document.getElementById('plnWarn');
    if (!pending) { if (box) box.remove(); return; }
    if (!main || (box && box.dataset.at === String(pending.at))) return;
    if (!box) { box = document.createElement('div'); box.id = 'plnWarn'; box.className = 'banner plnwarn'; main.prepend(box); }
    box.dataset.at = String(pending.at);
    const list = pending.list;
    const rows = list.map(w => `<li data-code="${esc(w.code)}"><b>${esc(w.severity)}</b>: ${esc(w.message)}</li>`).join('');
    box.innerHTML = `<div><b>${list.length} warning${list.length === 1 ? '' : 's'}</b> (saved – you can still continue)</div><ul class="plnwarn-list">${rows}</ul><button type="button" class="ghost sm" data-a="plnDismiss">Dismiss</button>`;
  }
  function showWarnings(list) { pending = { list, at: Date.now() }; paintWarnings(); }
  A.plnDismiss = () => { pending = null; paintWarnings(); };
  api.onWarnings = showWarnings;
  new MutationObserver(() => { if (pending) paintWarnings(); }).observe(document.body, { childList: true, subtree: true });
  window.addEventListener('hashchange', () => { if (pending && Date.now() - pending.at > 1500) A.plnDismiss(); });

  async function projectPlanBox(id) {
    let t;
    try { t = await api('GET', `projects/${id}/planning`); } catch { return ''; }
    const p = t.project;
    const period = p.start && p.end ? `${fmtDate(p.start)} – ${fmtDate(p.end)}` : 'not set';
    const hint = p.start && p.end ? '' : ' <span class="pill warn">set the dates</span>';
    const ext = (t.extensions || []).slice().reverse().map(e => `<li>${e.kind === 'offer' ? 'Accepted offer' : 'Manual extension'}: end ${e.from ? esc(fmtDate(e.from)) : '–'} → ${esc(fmtDate(e.to))}${e.reason ? ' – ' + esc(e.reason) : ''}</li>`).join('');
    const warn = (t.warnings || []).map(w => `<li>${esc(w.message)}</li>`).join('');
    return `<div class="card pln-proj"><h2>Project period</h2>
      <div>Period: <b>${esc(period)}</b>${hint}</div>
      ${warn ? `<ul class="small">${warn}</ul>` : ''}
      <details><summary class="small">Extension history (${(t.extensions || []).length})</summary><ul class="small">${ext || '<li>None yet.</li>'}</ul></details>
      ${p.start && p.end ? `<form class="pln-form" data-submit="projExtend"><div class="row"><div class="auto"><label for="projNewEnd">Extend end to</label><input id="projNewEnd" type="date" min="${esc(p.end)}" value="${esc(p.end)}"></div><div class="auto"><label for="projReason">Reason</label><input id="projReason" type="text" maxlength="2000"></div><div class="auto"><button type="button" data-a="projExtend" data-id="${esc(p.id)}">Extend</button></div></div></form>` : ''}
    </div>`;
  }
  A.projExtend = guard(async el => {
    const end = document.getElementById('projNewEnd').value;
    const reason = document.getElementById('projReason').value.trim();
    const r = await api('POST', `projects/${el.dataset.id}/extend`, { new_end: end, reason: reason || null });
    if (!r.planning_warnings || !r.planning_warnings.length) toast('Project extended');
    await loadAll(['projects']);
    render();
  });
  const projectPage = PAGES.project;
  PAGES.project = async id => (await projectPage(id)) + (await projectPlanBox(id));

  window.PLANNER = { paint, simulate, state: P };
})();
