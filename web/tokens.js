/* permitA38 – API tokens: list, create (the secret is shown once), revoke, activity log. Contract: docs/contracts/api-tokens.md.
   The plaintext token lives only in the DOM of the "created" dialog: never in storage, a URL or a list row.
   Everything from the server goes through esc(). Vanilla JS, no build step. */

const TOKEN_MAX_ACTIVE = 20;
const TOKEN_DAYS = [7, 30, 90, 180, 365];
const TOK = { all: false, items: [], error: '' };   // list state of the card (never holds a secret)
let TOK_AUDIT = null;                                // {id, label, items, next} of the open activity dialog

const tokTime = s => (s ? new Date(s * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }) : '');
const tokStatus = { active: 'Active', expired: 'Expired', revoked: 'Revoked' };
/** Message for a failed token call: the server text, plus the wait time for a 429. */
const tokError = e => (e.retryAfter ? `${e.message} (try again in ${e.retryAfter} seconds)` : e.message);
const tokOwnActive = () => TOK.items.filter(t => t.status === 'active' && (!t.user_id || t.user_id === S.me?.id)).length;

async function tokensLoad() {
  try { TOK.items = await api('GET', 'tokens' + (TOK.all && isBoss() ? '?all=true' : '')); TOK.error = ''; }
  catch (e) { TOK.items = []; TOK.error = tokError(e); }
}
function tokRowHTML(t) {
  return `<tr data-token="${esc(t.id)}">
    <td><b>${esc(t.label)}</b><div class="small muted"><code>${esc(t.hint)}…</code></div></td>
    ${TOK.all && isBoss() ? `<td>${esc(t.owner_name || '')}</td>` : ''}
    <td>${t.read_only ? 'Read only' : 'Read and write'}</td>
    <td class="nowrap">${esc(tokTime(t.created))}</td><td class="nowrap">${esc(tokTime(t.expires_at))}</td>
    <td class="nowrap">${t.last_used_at ? esc(tokTime(t.last_used_at)) : 'never'}</td>
    <td><span class="pill ${t.status === 'active' ? 'good' : ''}">${esc(tokStatus[t.status] || t.status)}</span></td>
    <td class="right nowrap"><button class="sm" data-a="tokAudit" data-id="${esc(t.id)}">Activity</button>${t.status === 'active' ? ` <button class="sm" data-a="tokRevoke" data-id="${esc(t.id)}">Revoke</button>` : ''}</td></tr>`;
}
function tokensCardHTML() {
  const full = tokOwnActive() >= TOKEN_MAX_ACTIVE, boss = isBoss();
  const head = `<div class="row" style="align-items:center"><h2 style="margin:0">API tokens</h2><div class="right auto">
    ${boss ? `<label class="small"><input type="checkbox" data-change="tokShowAll" ${TOK.all ? 'checked' : ''}> Show all users</label> ` : ''}
    <button class="sm" data-a="tokRevokeAll">Revoke all my tokens</button>
    <button class="primary sm" data-a="tokNew" ${full ? 'disabled' : ''}>+ New token</button></div></div>`;
  const note = '<p class="small muted">An API token lets a script or another program use permitA38 with your rights, without your password. Create tokens read-only unless the program must change data.</p>';
  const body = TOK.error ? `<div class="banner" role="alert">${esc(TOK.error)}</div>`
    : TOK.items.length ? `<div class="table-wrap"><table data-testid="tokens-table"><caption class="sr-only">API tokens</caption><thead><tr><th scope="col">Label</th>${TOK.all && boss ? '<th scope="col">Owner</th>' : ''}<th scope="col">Access</th><th scope="col">Created</th><th scope="col">Expires</th><th scope="col">Last used</th><th scope="col">Status</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${TOK.items.map(tokRowHTML).join('')}</tbody></table></div>`
      : '<div class="empty">No API tokens yet</div>';
  return `<div class="card" id="tokens-card">${head}${full ? `<p class="small" id="tok_full">You already have ${TOKEN_MAX_ACTIVE} active tokens. Revoke one to create another.</p>` : ''}${note}${body}</div>`;
}
async function tokensCard() { await tokensLoad(); return tokensCardHTML(); }
async function tokensRefresh() { const c = $('#tokens-card'); await tokensLoad(); if ($('#tokens-card') === c && c) c.outerHTML = tokensCardHTML(); }

PAGES.tokens = async () => `<div class="head"><div><h1>API tokens</h1><div class="muted">Credentials for scripts and other programs. Each token has at most your rights.</div></div></div>${await tokensCard()}`;

A.tokShowAll = async el => { TOK.all = el.checked; await tokensRefresh(); };
A.tokNew = () => {
  modal(`<h2>New API token</h2><form id="tk_form" novalidate>
    <div class="field"><label for="tk_label">Label</label><input id="tk_label" maxlength="80" autocomplete="off" required><div class="muted small">What the token is for, e.g. "Backup script".</div></div>
    <div class="field"><label for="tk_days">Expires after</label><select id="tk_days">${TOKEN_DAYS.map(d => `<option value="${d}" ${d === 90 ? 'selected' : ''}>${d} days</option>`).join('')}</select></div>
    <div class="field"><label><input type="checkbox" id="tk_ro" data-change="tokReadOnly" checked> Read only</label>
      <div class="muted small">A token has the rights of its owner (your role: ${esc(ROLE_LABEL[S.me.role] || S.me.role)}). It can never create tokens or change passwords or settings.</div>
      <div id="tk_warn" class="small" role="alert"></div></div>
    <div id="tk_err" class="banner" role="alert" hidden></div>
    <div class="actions"><button type="button" data-a="tokCloseDialog">Cancel</button><button class="primary" type="submit" id="tk_submit">Create token</button></div></form>`);
  $('#tk_form').addEventListener('submit', e => { e.preventDefault(); guard(A.tokCreate)(); });
};
A.tokCloseDialog = () => closeModal();
A.tokReadOnly = el => { $('#tk_warn').textContent = el.checked ? '' : 'This token can change data.'; };
A.tokCreate = async () => {
  const btn = $('#tk_submit'), err = $('#tk_err');
  const label = $('#tk_label').value.trim();
  err.hidden = true;
  if (!label) { err.textContent = 'Enter a label'; err.hidden = false; return; }
  btn.disabled = true;
  let res;
  try { res = await api('POST', 'tokens', { label, expires_in_days: +$('#tk_days').value, read_only: $('#tk_ro').checked }); }
  catch (e) { btn.disabled = false; err.textContent = tokError(e); err.hidden = false; return; }
  showTokenSecret(res.token, res.item);
  tokensRefresh();   // the list shows the hint only
};
/** The one place that holds the plaintext. closeModal() removes it from the DOM. */
function showTokenSecret(secret, item) {
  modal(`<h2>Token created</h2><p><b>${esc(item.label)}</b></p>
    <div class="banner" role="alert">This is the only time the token is shown. Copy it now and keep it safe; it cannot be shown again.</div>
    <div class="field"><label for="tk_secret">Your token</label><input id="tk_secret" data-testid="token-secret" readonly autocomplete="off" spellcheck="false"></div>
    <div class="row" style="align-items:center"><button type="button" data-a="tokCopy">Copy</button><span id="tk_copied" class="small" aria-live="polite"></span></div>
    <div class="field"><label><input type="checkbox" id="tk_saved" data-change="tokSaved"> I have saved it</label></div>
    <div class="actions"><button class="primary" id="tk_close" data-a="tokCloseSecret" disabled>Close</button></div>`);
  const input = $('#tk_secret');
  input.value = secret;   // set as a property, not interpolated into markup
  input.select();
}
A.tokCopy = async () => {
  const input = $('#tk_secret'), out = $('#tk_copied');
  try { await navigator.clipboard.writeText(input.value); }
  catch { input.select(); if (!document.execCommand('copy')) { out.textContent = 'Copy failed: select the text and copy it by hand'; return; } }
  out.textContent = 'Copied';
};
A.tokSaved = el => { $('#tk_close').disabled = !el.checked; };
A.tokCloseSecret = () => {
  if ($('#tk_close').disabled) return;
  $('#tk_secret').value = '';
  closeModal();
};
A.tokRevoke = async el => {
  const t = TOK.items.find(x => x.id === el.dataset.id);
  if (!confirm(`Revoke the token "${t?.label ?? ''}"? Programs using it stop working at once.`)) return;
  await api('POST', `tokens/${encodeURIComponent(el.dataset.id)}/revoke`, {});
  toast('Token revoked');
  await tokensRefresh();
};
A.tokRevokeAll = async () => {
  if (!confirm('Revoke all your active API tokens? Programs using them stop working at once.')) return;
  const r = await api('POST', 'tokens/revoke-all', {});
  toast(`${r.revoked} token(s) revoked`);
  await tokensRefresh();
};

/* ---------- activity (audit) dialog ---------- */
function tokAuditHTML() {
  const a = TOK_AUDIT;
  return `<h2>Activity: ${esc(a.label)}</h2>
    ${a.error ? `<div class="banner" role="alert">${esc(a.error)}</div>` : ''}
    ${a.items.length ? `<div class="table-wrap"><table data-testid="audit-table"><caption class="sr-only">Requests made with this token</caption><thead><tr><th scope="col">Time</th><th scope="col">Method</th><th scope="col">Route</th><th scope="col">Status</th></tr></thead><tbody>${a.items.map(r => `<tr><td class="nowrap">${esc(tokTime(r.at))}</td><td>${esc(r.method)}</td><td><code>${esc(r.route)}</code></td><td>${esc(r.status)}</td></tr>`).join('')}</tbody></table></div>`
      : a.error ? '' : '<div class="empty">No requests yet</div>'}
    <div class="actions">${a.next ? '<button data-a="tokAuditMore">Load more</button>' : ''}<button data-a="tokCloseDialog">Close</button></div>`;
}
async function tokAuditLoad() {
  const a = TOK_AUDIT, q = a.next ? `&before=${encodeURIComponent(a.next)}` : '';
  try {
    const r = await api('GET', `tokens/${encodeURIComponent(a.id)}/audit?limit=50${q}`);
    a.items = a.items.concat(r.items); a.next = r.next; a.error = '';
  } catch (e) { a.error = tokError(e); }
  modal(tokAuditHTML(), true);
  closeModal.onClose = () => { TOK_AUDIT = null; };   // registered after modal(), which runs the previous dialog's hook
}
A.tokAudit = async el => {
  const t = TOK.items.find(x => x.id === el.dataset.id);
  TOK_AUDIT = { id: el.dataset.id, label: t?.label ?? '', items: [], next: null, error: '' };
  await tokAuditLoad();
};
A.tokAuditMore = async () => { if (TOK_AUDIT) await tokAuditLoad(); };
