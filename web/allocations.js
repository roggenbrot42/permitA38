/* permitA38 – Allocations dialog (planner per project, boss). Entry points: the project page and the team page call
   openAllocations({projectId?, userId?}); the forecast banner calls A.allocBootstrap. The server computes everything
   (planned hours, warnings, the bootstrap proposal); this file lists, edits and draws. Over-allocation is a warning
   that never blocks a save. Vanilla JS, no build step. */
(() => {
  const SKIP = { already_allocated: 'already allocated', period_too_long: 'period longer than 1830 days' };
  const Al = { cancel: null, projectId: null, userId: null, list: [], edit: undefined, error: '', notes: [], preview: null, created: null, changed: false, busy: false };
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
    if (Al.projectId) return `Allocations of ${esc(projectName(Al.projectId))}`;
    if (Al.userId) return `Allocations of ${esc(userName(Al.userId))}`;
    return 'Allocations';
  }

  function row(a) {
    const external = a.kind === 'external';
    const readOnly = external || (a.status && a.status !== 'active');   // external and cancelled rows are not edited here
    const who = external ? (a.supplier_name || 'Subcontractor') : (a.user_name || userName(a.user_id));
    const cancelled = a.status === 'cancelled';
    return `<tr class="${readOnly ? '' : 'click'}" ${readOnly ? '' : `data-a="alEdit" data-id="${esc(a.id)}"`}>
      <td><b>${esc(who)}</b>${external ? ' <span class="pill">subcontractor</span>' : ''}</td><td>${esc(a.project_name || projectName(a.project_id))}</td>
      <td class="nowrap">${esc(fmtDate(a.start))} – ${esc(fmtDate(a.end))}</td>
      <td class="num">${a.hours_per_week == null ? '' : esc(h2(a.hours_per_week)) + ' h/week'}</td><td class="num">${a.planned_hours == null ? '' : esc(h2(a.planned_hours)) + ' h'}${external && a.planned_net != null ? `<div class="small muted">${esc(eur(a.planned_net))} planned, ${esc(eur(a.open_net))} open</div>` : ''}</td>
      <td>${cancelled ? `<span class="pill">cancelled${a.cancelled_from ? ' from ' + esc(fmtDate(a.cancelled_from)) : ''}</span> ` : ''}${external && !cancelled && can('procurement', 'write') ? `<button type="button" class="ghost sm" data-a="alCancelAlloc" data-id="${esc(a.id)}" aria-label="Cancel allocation of ${esc(who)} to ${esc(a.project_name || projectName(a.project_id))}">Cancel…</button> ` : ''}${readOnly ? '<span class="small muted">read-only</span>' : `<button type="button" class="ghost sm" data-a="alEdit" data-id="${esc(a.id)}" aria-label="Edit allocation of ${esc(who)} to ${esc(a.project_name || projectName(a.project_id))}">Edit</button>`}</td></tr>`;
  }

  function userOptions(uid) {   // a person who is no longer active stays selectable for their own row, so saving never reassigns it
    const list = users().map(u => ({ id: u.id, name: u.name }));
    if (uid && !list.some(u => u.id === uid)) list.push({ id: uid, name: (userName(uid) || 'Unknown person') + ' (inactive)' });
    return list.map(u => `<option value="${esc(u.id)}" ${u.id === uid ? 'selected' : ''}>${esc(u.name)}</option>`).join('');
  }
  function form() {
    const e = Al.edit;
    if (e === undefined) return '';
    const a = e || {};
    const uid = a.user_id || Al.userId || (users()[0] || {}).id;
    const pid = a.project_id || Al.projectId || (openProjects()[0] || {}).id;
    const monday = mondayOf(todayISO());
    const projects = openProjects().slice();
    if (pid && !projects.some(p => p.id === pid)) projects.push((S.d.projects || []).find(p => p.id === pid) || { id: pid, name: '(deleted project)' });
    return `<form class="card al-form" novalidate><h3>${a.id ? 'Edit allocation' : 'New allocation'}</h3>
      <div class="row"><div class="auto"><label for="al_user">Person</label><select id="al_user">${userOptions(uid)}</select></div>
      <div class="auto"><label for="al_project">Project</label><select id="al_project">${projects.map(p => `<option value="${esc(p.id)}" ${p.id === pid ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></div>
      <div class="auto"><label for="al_hours">Hours per week</label><input id="al_hours" type="number" min="0.25" max="80" step="0.25" value="${esc(a.hours_per_week ?? 10)}"></div></div>
      <div class="row"><div class="auto"><label for="al_start">From</label><input id="al_start" type="date" value="${esc(a.start || monday)}"></div>
      <div class="auto"><label for="al_end">To</label><input id="al_end" type="date" value="${esc(a.end || addDays(monday, 27))}"></div></div>
      <div class="actions">${a.id ? '<button type="button" class="danger" data-a="alDelete" style="margin-right:auto">Delete</button>' : ''}<button type="button" data-a="alCancelEdit">Cancel</button><button type="button" class="primary" data-a="alSave" ${Al.busy ? 'disabled' : ''}>Save</button></div></form>`;
  }

  function cancelForm() {
    const a = Al.cancel;
    if (!a) return '';
    const day = todayISO() > a.start ? todayISO() : a.start;
    return `<form class="card al-form al-cancel" novalidate><h3>Cancel the allocation of ${esc(a.supplier_name || 'the subcontractor')}</h3>
      <div class="small muted">Hours from this day on are released; earlier days and linked expenses stay.</div>
      <div class="row"><div class="auto"><label for="al_cfrom">Effective from</label><input id="al_cfrom" type="date" value="${esc(Al.cancelFrom || day)}"></div><div><label for="al_creason">Reason (optional)</label><input id="al_creason" type="text" maxlength="200" value="${esc(Al.cancelReason || '')}"></div></div>
      <div class="actions"><button type="button" data-a="alCancelAbort">Keep allocation</button><button type="button" class="primary" data-a="alCancelDo" ${Al.busy ? 'disabled' : ''}>Cancel allocation</button></div></form>`;
  }
  function previewBox() {
    const p = Al.preview;
    if (!p) return '';
    const skipped = (p.skipped || []).map(s => `<li>${esc(userName(s.user_id))} – ${esc(projectName(s.project_id))}: ${esc(SKIP[s.reason] || s.reason)}</li>`).join('');
    const warn = (p.warnings || []).map(w => `<li>${esc(w.message)}</li>`).join('');
    if (p.created) {
      return `<div class="card al-preview"><h3>Capacity allocated</h3><div role="status">${(p.allocations || []).length ? `Created ${(p.allocations || []).length} allocation${(p.allocations || []).length === 1 ? '' : 's'}.` : 'Nothing to create: everything is already allocated.'}</div></div>`;
    }
    return `<div class="card al-preview"><h3>Allocate my capacity</h3>
      <div class="small muted">One allocation per person and contract, with the highest weekly demand of the contract's period. Nothing is written until you confirm.</div>
      ${(p.allocations || []).length ? `<table><tr><th>Person</th><th>Project</th><th>Period</th><th class="num">Hours/week</th><th>From contract</th></tr>${p.allocations.map(a => `<tr><td>${esc(a.user_name)}</td><td>${esc(a.project_name)}</td><td class="nowrap">${esc(fmtDate(a.start))} – ${esc(fmtDate(a.end))}</td><td class="num">${esc(h2(a.hours_per_week))}</td><td class="small muted">${esc(a.source_contract_title || '')}</td></tr>`).join('')}</table>` : '<div class="empty small">Nothing to propose: no open contract without an allocation.</div>'}
      ${skipped ? `<div class="small muted">Skipped:</div><ul class="small">${skipped}</ul>` : ''}
      ${warn ? `<div class="banner"><ul class="small">${warn}</ul></div>` : ''}
      <div class="actions"><button type="button" data-a="alPreviewClose">Cancel</button><button type="button" class="primary" data-a="alConfirm" ${(p.allocations || []).length && !Al.busy ? '' : 'disabled'}>Create ${(p.allocations || []).length} allocation${(p.allocations || []).length === 1 ? '' : 's'}</button></div></div>`;
  }

  function paint() {
    const el = body();
    if (!el) return;
    const notes = Al.notes.map(w => `<li data-code="${esc(w.code)}">${esc(w.message)}</li>`).join('');
    el.innerHTML = `${Al.error ? `<div class="banner bad" role="alert">${esc(Al.error)}</div>` : ''}
      ${notes ? `<div class="banner al-warn" role="status"><b>Saved. Please check:</b><ul class="small">${notes}</ul></div>` : ''}
      ${cancelForm()}${previewBox()}${form()}
      ${Al.list.length ? `<table><tr><th>Person</th><th>Project</th><th>Period</th><th class="num">Hours/week</th><th class="num">Planned</th><th></th></tr>${Al.list.map(row).join('')}</table>`
        : `<div class="empty">No allocations yet. An allocation gives a person's hours per week to a project; the planner counts only these hours for that project.</div>`}`;
    if (Al.focus) { Al.focus = false; focusFirst(); }
  }

  function focusFirst() {
    const el = body();
    const target = el && (el.querySelector('[role=alert]') || el.querySelector('form.al-form select, form.al-form input') || el.querySelector('.al-preview button.primary:not([disabled])'));
    if (target) target.focus();
  }
  async function refresh() { await load(); paint(); }

  function open(opts = {}) {
    Al.open = true;
    Object.assign(Al, { cancel: null, projectId: opts.projectId || null, userId: opts.userId || null, list: [], edit: undefined, error: '', notes: [], preview: null, changed: false, busy: false });
    modal(`<div class="row" style="align-items:center"><h2 style="margin:0">${title()}</h2><div class="right auto"><button type="button" data-a="alBootstrap">Allocate my capacity</button> <button type="button" class="primary" data-a="alNew">+ Allocation</button></div></div><div id="al-body"><div class="empty">Loading…</div></div><div class="actions"><button type="button" id="al-close" data-a="alClose">Close</button></div>`, true);
    closeModal.onClose = () => { Al.open = false; if (Al.changed) { Al.changed = false; render(); } };   // every way of closing re-renders the page
    refresh().catch(e => { Al.error = e.message; paint(); });
  }
  window.openAllocations = open;

  function read() {
    const v = id => document.getElementById(id).value;
    return { user_id: v('al_user'), project_id: v('al_project'), start: v('al_start'), end: v('al_end'), hours_per_week: Number(v('al_hours')) };
  }

  A.allocations = el => open({ projectId: el.dataset.project || null, userId: el.dataset.user || null });
  A.allocBootstrap = async () => { open({}); await A.alBootstrap(); };   // the forecast banner
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
      Al.cancel = null; Al.changed = true; toast('Allocation cancelled');
      await load();
    } catch (e) { Al.error = e.message; }
    Al.busy = false; Al.focus = true; paint();
  };
  A.alNew = () => { Al.edit = null; Al.cancel = null; Al.error = ''; Al.notes = []; Al.focus = true; paint(); };
  A.alEdit = el => { Al.edit = Al.list.find(a => a.id === el.dataset.id) || null; Al.error = ''; Al.notes = []; Al.focus = true; paint(); };
  A.alCancelEdit = () => { Al.edit = undefined; Al.error = ''; paint(); };
  A.alSave = async () => {
    if (Al.busy) return;
    const b = read(), id = Al.edit && Al.edit.id;
    Al.error = '';
    if (!b.user_id || !b.project_id || !b.start || !b.end) { Al.error = 'Choose a person, a project and both dates.'; return paint(); }
    if (b.end < b.start) { Al.error = 'The end date is before the start date.'; return paint(); }
    if (!(b.hours_per_week >= 0.25 && b.hours_per_week <= 80)) { Al.error = 'Hours per week must be between 0.25 and 80.'; return paint(); }
    Al.busy = true;
    try {
      const r = await (id ? api.put('allocations/' + id, b) : api.post('allocations', b));
      Al.notes = r.planning_warnings || [];
      if (typeof A.plnDismiss === 'function') A.plnDismiss();   // the dialog shows these warnings itself: no duplicate banner behind it
      Al.edit = undefined; Al.changed = true;
      toast('Allocation saved');
      await load();
    } catch (e) { Al.error = e.message; }
    Al.busy = false;
    Al.focus = true;
    paint();
  };
  A.alDelete = async () => {
    const id = Al.edit && Al.edit.id;
    if (!id || !window.confirm('Delete this allocation?')) return;
    try { await api.del('allocations/' + id); Al.edit = undefined; Al.changed = true; Al.notes = []; Al.error = ''; await load(); }
    catch (e) { Al.error = e.message; }
    paint();
  };
  A.alBootstrap = async () => {
    Al.error = ''; Al.notes = []; Al.edit = undefined;
    try { Al.preview = await api.post('allocations/bootstrap', { confirm: false }); } catch (e) { Al.error = e.message; }
    paint();
  };
  A.alPreviewClose = () => { Al.preview = null; paint(); };
  A.alConfirm = async () => {
    if (Al.busy) return;
    Al.busy = true; Al.error = '';
    try {
      Al.preview = await api.post('allocations/bootstrap', { confirm: true });
      Al.changed = true;
      Al.notes = Al.preview.warnings || [];
      await load();
    } catch (e) { Al.error = e.message; }
    Al.busy = false;
    paint();
  };
  // Enter in a form field saves (same convention as planner.js)
  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || !e.target.closest) return;
    const f = e.target.matches('input, select') && e.target.closest('form.al-form');
    if (f) { e.preventDefault(); guard(f.classList.contains('al-cancel') ? A.alCancelDo : A.alSave)(); }
  });
})();
