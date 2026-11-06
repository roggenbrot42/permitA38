/* permitA38 – Assignments dialog (boss). Entry points: the project page and the team page call
   openAllocations({projectId?, userId?}); the planner overview's "Assign" buttons call it with a pre-fill
   {projectId, userId, start, end, total_hours} and the new-row form opens filled in. Rows of people and of
   subcontractors; the server computes everything (planned hours, planned net, warnings); this file lists, edits and
   draws. Over-assignment is a warning that never blocks a save. Vanilla JS, no build step. */
(() => {
  const Al = { cancel: null, projectId: null, userId: null, list: [], edit: undefined, prefill: null, draft: null, error: '', notes: [], changed: false, busy: false };
  const h2 = x => num(x, 2);
  const users = () => (S.d.users || []).filter(u => u.active !== false);
  const openProjects = () => (S.d.projects || []).filter(p => !['done', 'archived'].includes(p.status));
  const body = () => document.getElementById('al-body');
  const userName = id => ((S.d.users || []).find(u => u.id === id) || {}).name || '';
  const projectName = id => ((S.d.projects || []).find(p => p.id === id) || {}).name || '';

  async function load() {
    const q = [];
    if (Al.projectId) q.push('project_id=' + encodeURIComponent(Al.projectId));
    if (Al.userId) q.push('user_id=' + encodeURIComponent(Al.userId));
    Al.list = await api('GET', 'allocations' + (q.length ? '?' + q.join('&') : ''));
  }

  function title() {
    if (Al.projectId) return `Assignments of ${esc(projectName(Al.projectId))}`;
    if (Al.userId) return `Assignments of ${esc(userName(Al.userId))}`;
    return 'Assignments';
  }

  const supplierName = id => ((window.SUP && window.SUP.byId(id)) || {}).name || '';
  const capacitySuppliers = () => ((window.SUP && window.SUP.list) || []).filter(s => s.kind === 'person-capacity' && s.active !== false);

  function row(a) {
    const external = a.kind === 'external';
    const cancelled = a.status === 'cancelled';
    const writable = external ? can('procurement', 'write') : can('planning', 'write');
    const readOnly = cancelled || !writable;
    const who = external ? (a.supplier_name || supplierName(a.supplier_id) || 'Subcontractor') : (a.user_name || userName(a.user_id));
    const proj = a.project_name || projectName(a.project_id);
    const hours = a.hours_per_week != null ? esc(h2(a.hours_per_week)) + ' h/week' : a.total_hours != null ? esc(h2(a.total_hours)) + ' h in total' : '';
    return `<tr class="${readOnly ? '' : 'click'}" ${readOnly ? '' : `data-a="alEdit" data-id="${esc(a.id)}"`}>
      <td><b>${esc(who)}</b>${external ? ' <span class="pill">subcontractor</span>' : ''}${a.incoming_offer_id ? `<div class="small muted al-offer">from offer ${esc(a.offer_reference || '')}</div>` : ''}</td><td>${esc(proj)}</td>
      <td class="nowrap">${esc(fmtDate(a.start))} – ${esc(fmtDate(a.end))}</td>
      <td class="num">${hours}</td><td class="num">${a.planned_hours == null ? '' : esc(h2(a.planned_hours)) + ' h'}${external && a.planned_net != null ? `<div class="small muted">${esc(eur(a.planned_net))} planned${a.open_net != null ? `, ${esc(eur(a.open_net))} open` : ''}</div>` : ''}</td>
      <td>${cancelled ? `<span class="pill">cancelled${a.cancelled_from ? ' from ' + esc(fmtDate(a.cancelled_from)) : ''}</span> ` : ''}${external && !cancelled && writable ? `<button type="button" class="ghost sm" data-a="alCancelAlloc" data-id="${esc(a.id)}" aria-label="Cancel assignment of ${esc(who)} to ${esc(proj)}">Cancel…</button> ` : ''}${readOnly ? '<span class="small muted">read-only</span>' : `<button type="button" class="ghost sm" data-a="alEdit" data-id="${esc(a.id)}" aria-label="Edit assignment of ${esc(who)} to ${esc(proj)}">Edit</button>${a.incoming_offer_id ? '' : ` <button type="button" class="ghost sm danger" data-a="alRowDelete" data-id="${esc(a.id)}" aria-label="Delete assignment of ${esc(who)} to ${esc(proj)}">Delete</button>`}`}</td></tr>`;
  }

  function ownerOptions(sel, a) {   // value "u:<id>" / "s:<id>"; an owner who is no longer active stays selectable for their own row
    const us = users().map(u => ({ id: u.id, name: u.name }));
    if (a.user_id && !us.some(u => u.id === a.user_id)) us.push({ id: a.user_id, name: (userName(a.user_id) || 'Unknown person') + ' (inactive)' });
    const ss = capacitySuppliers().map(x => ({ id: x.id, name: x.name }));
    if (a.supplier_id && !ss.some(x => x.id === a.supplier_id)) ss.push({ id: a.supplier_id, name: (a.supplier_name || supplierName(a.supplier_id) || 'Subcontractor') + ' (inactive)' });
    const opt = (k, x) => `<option value="${k}:${esc(x.id)}" ${sel === k + ':' + x.id ? 'selected' : ''}>${esc(x.name)}</option>`;
    return `<optgroup label="People">${us.map(x => opt('u', x)).join('')}</optgroup>${ss.length ? `<optgroup label="Subcontractors">${ss.map(x => opt('s', x)).join('')}</optgroup>` : ''}`;
  }
  function form() {
    const e = Al.edit;
    if (e === undefined) return '';
    const a = Al.draft || e || Al.prefill || {};   // a draft keeps the typed values after a failed check or save
    const uid = a.user_id || Al.userId || (users()[0] || {}).id;
    const sel = a.supplier_id ? 's:' + a.supplier_id : 'u:' + uid;
    const pid = a.project_id || Al.projectId || (openProjects()[0] || {}).id;
    const monday = mondayOf(todayISO());
    const projects = openProjects().slice();
    if (pid && !projects.some(p => p.id === pid)) projects.push((S.d.projects || []).find(p => p.id === pid) || { id: pid, name: '(deleted project)' });
    const total = a.total_hours != null;
    const isSup = sel[0] === 's';
    const rate = a.rate != null ? a.rate : (isSup ? ((window.SUP && window.SUP.byId(a.supplier_id)) || {}).rate : '');
    return `<form class="card al-form" novalidate><h3>${a.id ? 'Edit assignment' : 'New assignment'}</h3>
      ${a.incoming_offer_id ? `<div class="small muted al-offer">From offer ${esc(a.offer_reference || '')}: it can be edited or cancelled, not deleted.</div>` : ''}
      <div class="row"><div class="auto"><label for="al_owner">Person or subcontractor</label><select id="al_owner">${ownerOptions(sel, a)}</select></div>
      <div class="auto"><label for="al_project">Project</label><select id="al_project">${projects.map(p => `<option value="${esc(p.id)}" ${p.id === pid ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></div></div>
      <div class="row"><div class="auto"><label for="al_start">From</label><input id="al_start" type="date" value="${esc(a.start || monday)}"></div>
      <div class="auto"><label for="al_end">To</label><input id="al_end" type="date" value="${esc(a.end || addDays(monday, 27))}"></div>
      <div class="auto"><label for="al_mode">Hours</label><select id="al_mode"><option value="week" ${total ? '' : 'selected'}>per week</option><option value="total" ${total ? 'selected' : ''}>in total</option></select></div>
      <div class="auto"><label for="al_hours">Amount of hours</label><input id="al_hours" type="number" min="0.25" max="${total ? 100000 : 80}" step="0.25" value="${esc(total ? a.total_hours : (a.hours_per_week ?? 10))}"></div>
      <div class="auto" id="al_ratebox" ${isSup ? '' : 'hidden'}><label for="al_rate">Rate € per hour (net)</label><input id="al_rate" type="number" min="0" max="10000" step="0.01" value="${esc(rate ?? '')}"></div></div>
      <div class="actions">${a.id && !a.incoming_offer_id ? '<button type="button" class="danger" data-a="alDelete" style="margin-right:auto">Delete</button>' : ''}<button type="button" data-a="alCancelEdit">Cancel</button><button type="button" class="primary" data-a="alSave" ${Al.busy ? 'disabled' : ''}>Save</button></div></form>`;
  }

  function cancelForm() {
    const a = Al.cancel;
    if (!a) return '';
    const day = todayISO() > a.start ? todayISO() : a.start;
    return `<form class="card al-form al-cancel" novalidate><h3>Cancel the assignment of ${esc(a.supplier_name || 'the subcontractor')}</h3>
      <div class="small muted">Hours from this day on are released; earlier days and linked expenses stay.</div>
      <div class="row"><div class="auto"><label for="al_cfrom">Effective from</label><input id="al_cfrom" type="date" value="${esc(Al.cancelFrom || day)}"></div><div><label for="al_creason">Reason (optional)</label><input id="al_creason" type="text" maxlength="200" value="${esc(Al.cancelReason || '')}"></div></div>
      <div class="actions"><button type="button" data-a="alCancelAbort">Keep assignment</button><button type="button" class="primary" data-a="alCancelDo" ${Al.busy ? 'disabled' : ''}>Cancel assignment</button></div></form>`;
  }

  function paint() {
    const el = body();
    if (!el) return;
    const notes = Al.notes.map(w => `<li data-code="${esc(w.code)}">${esc(w.message)}</li>`).join('');
    el.innerHTML = `${Al.error ? `<div class="banner bad" role="alert">${esc(Al.error)}</div>` : ''}
      ${notes ? `<div class="banner al-warn" role="status"><b>Saved. Please check:</b><ul class="small">${notes}</ul></div>` : ''}
      ${cancelForm()}${form()}
      ${Al.list.length ? `<table><tr><th>Who</th><th>Project</th><th>Period</th><th class="num">Hours</th><th class="num">Planned</th><th></th></tr>${Al.list.map(row).join('')}</table>`
        : `<div class="empty">No assignments yet. ${Al.projectId && Al.edit === undefined ? '<button type="button" class="primary sm" data-a="alNew">Add assignment</button> ' : ''}An assignment gives the hours of a person or subcontractor to a project; the planner counts only these hours for that project.</div>`}`;
    if (Al.focus) { Al.focus = false; focusFirst(); }
  }

  function focusFirst() {
    const el = body();
    const target = el && (el.querySelector('[role=alert]') || el.querySelector('form.al-form select, form.al-form input'));
    if (target) target.focus();
  }
  async function refresh() { await load(); paint(); }

  function open(opts = {}) {
    Al.open = true;
    const pre = !!(opts.start || opts.end || opts.total_hours != null);   // the overview's "Assign" buttons
    Object.assign(Al, { cancel: null, projectId: opts.projectId || null, userId: pre ? null : (opts.userId || null), list: [], edit: pre ? null : undefined, error: '', notes: [], changed: false, busy: false, draft: null,
      prefill: pre ? { user_id: opts.userId || undefined, project_id: opts.projectId || undefined, start: opts.start, end: opts.end, total_hours: opts.total_hours } : null });
    modal(`<div class="row" style="align-items:center"><h2 style="margin:0">${title()}</h2><div class="right auto"><button type="button" class="primary" data-a="alNew">+ Assignment</button></div></div><div id="al-body"><div class="empty">Loading…</div></div><div class="actions"><button type="button" id="al-close" data-a="alClose">Close</button></div>`, true);
    closeModal.onClose = () => { Al.open = false; if (Al.changed) { Al.changed = false; render(); } };   // every way of closing re-renders the page
    if (window.SUP && !window.SUP.list.length && can('suppliers')) window.SUP.load().then(paint).catch(() => {});
    refresh().catch(e => { Al.error = e.message; paint(); });
  }
  window.openAllocations = open;

  function read() {
    const v = id => document.getElementById(id).value;
    const k = v('al_owner').slice(0, 1), oid = v('al_owner').slice(2);
    const hrs = Number(v('al_hours')), total = v('al_mode') === 'total';
    const b = { project_id: v('al_project'), start: v('al_start'), end: v('al_end') };
    b[k === 's' ? 'supplier_id' : 'user_id'] = oid;
    b[total ? 'total_hours' : 'hours_per_week'] = hrs;
    if (k === 's') b.rate = Number(v('al_rate'));
    return { b, kind: k, hrs, total, rate: v('al_rate') };
  }

  A.allocations = el => open({ projectId: el.dataset.project || null, userId: el.dataset.user || null });
  A.alClose = () => { closeModal(); };   // the close hook below re-renders when something changed
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && Al.open && document.getElementById('al-body')) { e.preventDefault(); closeModal(); }
  });
  A.alCancelAlloc = el => { Al.cancel = Al.list.find(a => a.id === el.dataset.id) || null; Al.cancelFrom = ''; Al.cancelReason = ''; Al.edit = undefined; Al.error = ''; Al.notes = []; Al.focus = true; paint(); };
  A.alCancelAbort = () => { Al.cancel = null; paint(); };
  A.alCancelDo = async () => {
    const a = Al.cancel;
    if (!a || Al.busy) return;
    Al.cancelFrom = document.getElementById('al_cfrom').value; Al.cancelReason = document.getElementById('al_creason').value.trim();
    if (!Al.cancelFrom) { Al.error = 'Choose the effective date.'; return paint(); }
    Al.busy = true; Al.error = '';
    try {
      await api.post(`allocations/${encodeURIComponent(a.id)}/cancel`, { effective_from: Al.cancelFrom, reason: Al.cancelReason || null });
      Al.cancel = null; Al.changed = true; toast('Assignment cancelled');
      await load();
    } catch (e) { Al.error = e.message; }
    Al.busy = false; Al.focus = true; paint();
  };
  A.alNew = () => { Al.edit = null; Al.prefill = null; Al.draft = null; Al.cancel = null; Al.error = ''; Al.notes = []; Al.focus = true; paint(); };
  A.alEdit = el => { Al.draft = null; Al.edit = Al.list.find(a => a.id === el.dataset.id) || null; Al.error = ''; Al.notes = []; Al.focus = true; paint(); };
  A.alCancelEdit = () => { Al.edit = undefined; Al.prefill = null; Al.draft = null; Al.error = ''; paint(); };
  A.alSave = async () => {
    if (Al.busy) return;
    const r0 = read(), b = r0.b, id = Al.edit && Al.edit.id;
    Al.draft = { ...(Al.edit || {}), ...b, rate: r0.kind === 's' ? r0.rate : undefined, user_id: b.user_id, supplier_id: b.supplier_id };
    if (r0.total) { delete Al.draft.hours_per_week; } else { delete Al.draft.total_hours; }
    Al.error = '';
    if (!(b.user_id || b.supplier_id) || !b.project_id || !b.start || !b.end) { Al.error = 'Choose a person or subcontractor, a project and both dates.'; return paint(); }
    if (b.end < b.start) { Al.error = 'The end date is before the start date.'; return paint(); }
    if (r0.total ? !(r0.hrs > 0 && r0.hrs <= 100000) : !(r0.hrs >= 0.25 && r0.hrs <= 80)) { Al.error = r0.total ? 'Total hours must be above 0.' : 'Hours per week must be between 0.25 and 80.'; return paint(); }
    if (r0.kind === 's' && (r0.rate === '' || !(b.rate >= 0 && b.rate <= 10000))) { Al.error = 'Enter the rate of the subcontractor.'; return paint(); }
    Al.busy = true;
    try {
      const r = await (id ? api.put('allocations/' + id, b) : api.post('allocations', b));
      Al.notes = r.planning_warnings || [];
      if (typeof A.plnDismiss === 'function') A.plnDismiss();   // the dialog shows these warnings itself: no duplicate banner behind it
      Al.edit = undefined; Al.changed = true;
      Al.prefill = null; Al.draft = null; toast('Assignment saved');
      await load();
    } catch (e) { Al.error = e.message; }
    Al.busy = false;
    Al.focus = true;
    paint();
  };
  A.alDelete = async () => {
    const id = Al.edit && Al.edit.id;
    if (!id || !window.confirm('Delete this assignment?')) return;
    try { await api.del('allocations/' + id); Al.edit = undefined; Al.changed = true; Al.notes = []; Al.error = ''; await load(); }
    catch (e) { Al.error = e.message; }
    paint();
  };
  A.alRowDelete = async el => {
    const id = el.dataset.id;
    if (!id || !window.confirm('Delete this assignment?')) return;
    try { await api.del('allocations/' + id); Al.changed = true; Al.notes = []; Al.error = ''; await load(); }
    catch (e) { Al.error = e.message; }
    paint();
  };
  document.addEventListener('change', e => {
    if (e.target.id === 'al_owner') {
      const sup = e.target.value[0] === 's', box = document.getElementById('al_ratebox');
      if (box) box.hidden = !sup;
      const r = document.getElementById('al_rate'), rec = sup && window.SUP && window.SUP.byId(e.target.value.slice(2));
      if (r && rec && !r.value && rec.rate != null) r.value = rec.rate;
    } else if (e.target.id === 'al_mode') {
      const i = document.getElementById('al_hours');
      if (i) i.max = e.target.value === 'total' ? 100000 : 80;
    }
  });
  // Enter in a form field saves (same convention as planner.js)
  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || !e.target.closest) return;
    const f = e.target.matches('input, select') && e.target.closest('form.al-form');
    if (f) { e.preventDefault(); guard(f.classList.contains('al-cancel') ? A.alCancelDo : A.alSave)(); }
  });
})();
