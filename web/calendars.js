/* permitA38 – Settings → Calendars. Connect a personal calendar (Proton, Nextcloud, Google, Outlook, Apple, Fastmail…)
   so its meetings reduce the capacity used for planning. Uses only the calendar API; no calendar data is kept in the browser.
   Relies on helpers from app.js (esc, toast, api, A, openForm, guard, fmtDate) which load first. */
(() => {
  const POLICY = [['none', 'Store no titles (recommended)'], ['redacted', 'Store "Busy" instead of titles'], ['full', 'Store titles and categories']];
  const EVERY = [[15, 'every 15 minutes'], [60, 'every hour'], [360, 'every 6 hours'], [1440, 'once a day'], [10080, 'once a week']];
  const STATUS = { ok: 'Synced', not_modified: 'Unchanged', error: 'Sync failed', needs_upload: 'Upload the file again' };
  const words = s => String(s || '').split(',').map(x => x.trim()).filter(Boolean);
  const epoch = s => (s ? new Date(Number(s) * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }) : 'never');
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const showErr = msg => { const e = document.getElementById('cal-err'); if (e) { e.textContent = msg || ''; e.hidden = !msg; } };

  function guide() {
    return `<details class="calendar-guide" open><summary><b>Connect Proton Calendar</b> (recommended: share link, read-only)</summary>
      <ol class="small">
        <li>Open Proton Calendar in your browser.</li>
        <li>Click the gear icon, then <b>Settings</b>, then <b>Calendars</b>.</li>
        <li>Choose the calendar you want to use and select <b>Share via link</b>.</li>
        <li>Create the link and <b>copy</b> it.</li>
        <li>Paste the link below and press <b>Connect link</b>. Or export the calendar as an .ics file and upload it instead.</li>
      </ol>
      <p class="muted small">Proton offers no public API for apps like this one, so the steps use its sharing and export features. Menu names can change when Proton updates its interface. Changes in Proton appear here at the next sync. permitA38 only reads the calendar.</p>
    </details>`;
  }

  function privacy() {
    return `<div class="banner calendar-privacy small"><b>Privacy.</b> A calendar link is a secret: anyone who has it can read the whole calendar.
      permitA38 keeps the link only in the server's database, never shows it again (only the host is displayed) and never writes it to logs.
      By default no meeting titles are stored; meetings of other people are personal data, so keep titles off unless you need them.</div>`;
  }

  function sourceRow(s) {
    const kind = s.kind === 'ics_url' ? `Link · ${esc(s.url_masked || '')}` : 'File upload';
    const status = STATUS[s.last_status] || esc(s.last_status || 'not synced yet');
    const err = s.last_error ? `<div class="small" style="color:var(--danger, #b00)">${esc(s.last_error)}</div>` : '';
    const WARN = { unsupported_rrule: 'A repeat rule is not supported; only the first meeting of that series is used.', unknown_timezone: 'A time zone was not recognised; your own time zone was assumed.', event_too_complex: 'A repeating meeting was too complex and was skipped.', out_of_range: 'A meeting with an impossible date was skipped.', bad_event: 'An unreadable entry was skipped.', bad_value: 'An unreadable date was skipped.' };
    const warn = (s.last_warnings || []).length ? `<div class="small muted">Note: ${s.last_warnings.map(w => esc(WARN[w] || w)).join(' ')}</div>` : '';
    const sync = s.kind === 'ics_url' ? `<button class="sm" data-a="calSync" data-id="${esc(s.id)}">Sync now</button>` : '';
    return `<tr><td><b>${esc(s.name)}</b><div class="muted small">${kind}</div>${err}${warn}${s.reupload_advised ? '<div class="small muted">This file was uploaded a while ago: upload it again to extend repeating meetings.</div>' : ''}</td>
      <td class="small">${status}<div class="muted">last: ${epoch(s.last_fetched)}</div><div class="muted">${s.event_count} events · ${esc(s.title_policy)} · ${s.kind === 'ics_url' ? EVERY.find(([m]) => m === s.refresh_minutes)?.[1] || 'every ' + s.refresh_minutes + ' min' : 'manual upload'}</div></td>
      <td class="right"><button class="sm" data-a="calPreview" data-id="${esc(s.id)}">Preview 14 days</button> ${sync}
      <button class="sm" data-a="calEdit" data-id="${esc(s.id)}">Options</button> <button class="sm danger" data-a="calDelete" data-id="${esc(s.id)}">Delete</button></td></tr>`;
  }

  function addForm() {
    return `<div class="card-inner"><h3>Add a calendar</h3>
      <div class="row"><div class="field"><label for="cal_name">Name</label><input id="cal_name" placeholder="e.g. Personal" maxlength="200"></div>
      <div class="field"><label for="cal_policy">Titles</label><select id="cal_policy">${POLICY.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join('')}</select></div></div>
      <div class="field"><label for="cal_link">ICS link (https, starts with https:// or webcal://)</label>
        <input id="cal_link" type="text" autocomplete="off" spellcheck="false" placeholder="https://…"></div>
      <div class="row"><div class="field"><label for="cal_refresh">Refresh</label><select id="cal_refresh">${EVERY.map(([v, l]) => `<option value="${v}" ${v === 360 ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
      <div class="field"><label><input type="checkbox" id="cal_allday"> Whole-day events block the whole working day</label></div></div>
      <div class="row"><div class="field"><label for="cal_inc">Only events containing (comma separated, optional)</label><input id="cal_inc" maxlength="2000"></div>
      <div class="field"><label for="cal_exc">Ignore events containing (comma separated, optional)</label><input id="cal_exc" maxlength="2000"></div></div>
      <div class="actions"><button class="primary" data-a="calConnect">Connect link</button></div>
      <div class="field" style="margin-top:12px"><label for="cal_file">…or upload an .ics file</label><input id="cal_file" type="file" accept=".ics,text/calendar"></div>
      <div class="actions"><button data-a="calUpload">Upload file</button></div></div>`;
  }

  async function calendarsCard() {
    let list = [], error = '', suggestions = [];
    try { list = await api.get('calendars'); suggestions = await api.get('calendars/suggestions'); } catch (e) { error = e.message; }
    const rows = list.map(sourceRow).join('');
    const sug = suggestions.length
      ? `<h3>Possible absences</h3><p class="muted small">These entries look like vacation or out-of-office time. Nothing is created automatically: enter them under Time off if you want them.</p>
         <table>${suggestions.map(x => `<tr><td>${x.all_day ? fmtDate(x.start) : new Date(x.start).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })}</td><td>${esc(x.title || 'Out of office (title not stored)')}</td><td class="muted small">${esc(x.source_name)}</td></tr>`).join('')}</table>` : '';
    return `<div class="card" id="calendars"><h2>Calendars: meetings reduce your capacity</h2>
      <p class="muted small">Busy time from your calendars is subtracted from your planned capacity, only inside your working hours (Settings → Team → your working time).</p>
      ${privacy()}${guide()}
      <div id="cal-err" class="banner" hidden></div>${error ? `<div class="banner">${esc(error)}</div>` : ''}
      ${list.length ? `<table>${rows}</table>` : '<p class="muted">No calendar connected yet.</p>'}
      <div id="cal-busy" class="small"></div>${sug}
      <div id="cal-preview"></div>${addForm()}</div>`;
  }

  async function redraw() {
    const old = document.getElementById('calendars');
    if (old) old.outerHTML = await calendarsCard();
  }

  // ---------------------------------------------------------------- actions
  A.calConnect = async () => {
    const name = document.getElementById('cal_name').value.trim();
    const link = document.getElementById('cal_link').value.trim();
    if (!name) throw new Error('Please give the calendar a name.');
    if (!link) throw new Error('Paste the ICS link, or upload an .ics file below.');
    showErr('');
    try {
      await api.post('calendars', {
        name, url: link, title_policy: document.getElementById('cal_policy').value,
        refresh_minutes: Number(document.getElementById('cal_refresh').value), all_day_blocks: document.getElementById('cal_allday').checked,
        include_keywords: words(document.getElementById('cal_inc').value), exclude_keywords: words(document.getElementById('cal_exc').value),
      });
    } catch (e) { showErr(e.message); return; }  // on failure the pasted link stays so it can be corrected
    document.getElementById('cal_link').value = '';
    toast('Calendar connected');
    await redraw();
  };

  A.calUpload = async () => {
    const file = document.getElementById('cal_file').files[0];
    const name = document.getElementById('cal_name').value.trim();
    if (!file) throw new Error('Choose an .ics file first.');
    if (!name) throw new Error('Please give the calendar a name.');
    if (file.size > 5 * 1024 * 1024) { showErr('The calendar file is too large (max 5 MB).'); return; }
    const fd = new FormData();
    fd.append('file', file); fd.append('name', name); fd.append('title_policy', document.getElementById('cal_policy').value);
    fd.append('refresh_minutes', document.getElementById('cal_refresh').value); fd.append('all_day_blocks', document.getElementById('cal_allday').checked ? 'true' : 'false');
    fd.append('busy_default', 'true');
    fd.append('include_keywords', document.getElementById('cal_inc').value); fd.append('exclude_keywords', document.getElementById('cal_exc').value);
    showErr('');
    try { await api.post('calendars/upload', fd); } catch (e) { showErr(e.message); return; }
    toast('Calendar imported');
    await redraw();
  };

  A.calSync = async el => {
    try { await api.post(`calendars/${el.dataset.id}/refresh`); toast('Synced'); } catch (e) { showErr(e.message); }
    await redraw();
  };

  A.calPreview = async el => {
    const me = await api.get('auth/me');
    const days = await api.get(`calendars/${el.dataset.id}/preview?days=14`);
    const today = new Date();
    const end = new Date(); end.setDate(end.getDate() + 14);
    const busy = await api.get(`calendars/busy?user_id=${encodeURIComponent(me.id)}&start=${iso(today)}&end=${iso(end)}`);
    const hours = Object.entries(busy).map(([d, h]) => `<tr><td>${fmtDate(d)}</td><td class="right">${num(h, 2)} h</td></tr>`).join('');
    document.getElementById('cal-busy').innerHTML = `<h3>Busy time, next 14 days</h3>${hours ? `<table>${hours}</table>` : '<p class="muted">No busy time inside your working hours.</p>'}`;
    document.getElementById('cal-preview').innerHTML = `<h3>Meetings from this calendar</h3>${days.length ? `<table>${days.map(x => `<tr><td>${x.all_day ? fmtDate(x.start) + ' (all day)' : new Date(x.start).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })}</td><td>${esc(x.title || 'Busy (title not stored)')}</td></tr>`).join('')}</table>` : '<p class="muted">Nothing in the next 14 days.</p>'}`;
  };

  A.calEdit = async el => {
    const list = await api.get('calendars');
    const s = list.find(x => x.id === el.dataset.id);
    if (!s) throw new Error('Calendar not found');
    const fields = [
      { k: 'name', l: 'Name' },
      { k: 'title_policy', l: 'Titles', t: 'select', o: POLICY },
      { k: 'refresh_minutes', l: 'Refresh', t: 'select', o: EVERY.map(([v, l]) => [String(v), l]), h: s.kind === 'ics_url' ? '' : 'Files are imported again by uploading them.' },
      { k: 'all_day_blocks', l: 'Whole-day events block the whole working day', t: 'check' },
      { k: 'busy_default', l: 'Counts toward my capacity (off: show only)', t: 'check' },
      { k: 'include_keywords', l: 'Only events containing (comma separated)', h: 'Changing this downloads the calendar again.' },
      { k: 'exclude_keywords', l: 'Ignore events containing (comma separated)', h: 'Changing this downloads the calendar again.' },
    ];
    const vals = { ...s, refresh_minutes: String(s.refresh_minutes), include_keywords: (s.include_keywords || []).join(', '), exclude_keywords: (s.exclude_keywords || []).join(', ') };
    openForm({
      title: 'Calendar options', fields, vals,
      save: async v => {
        const body = { ...v, refresh_minutes: Number(v.refresh_minutes), include_keywords: words(v.include_keywords), exclude_keywords: words(v.exclude_keywords) };
        await api.put(`calendars/${s.id}`, body);
        toast('Saved');
        await redraw();
      },
      del: async () => { await api.del(`calendars/${s.id}`); toast('Calendar removed'); await redraw(); },
    });
  };

  A.calDelete = async el => {
    if (!confirm('Remove this calendar and all its stored meetings? The link is deleted as well.')) return;
    try { await api.del(`calendars/${el.dataset.id}`); toast('Calendar removed'); } catch (e) { showErr(e.message); }
    await redraw();
  };

  window.calendarsCard = calendarsCard;
})();
