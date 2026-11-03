/* permitA38 – My settings (#/me) and the personal tax profile route (#/me/tax).
   This file holds PAGES.me and mySettingsPage; the personal tax profile page (taxProfilePage) is a placeholder until
   WP-A5 fills it. Contract: docs/specs/tax-burden-and-settings.md ("New grouping", "UI"). Relies on helpers from app.js. */

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

async function mySettingsPage() {
  const cal = can('planning', 'write') && scopeOf('planning') === 'all' ? await calendarsCard() : '';
  return `<div class="head"><div><h1>My settings</h1><div class="muted">Your profile, sign-in and personal connections.</div></div></div>
  ${await mySettingsProfileCard()}
  ${isBoss() ? await mySettingsTaxCard() : ''}
  ${cal}
  ${await tokensCard()}`;
}

/* placeholder until WP-A5 replaces it */
async function taxProfilePage() {
  return `<div class="head"><div><h1>Personal tax profile</h1></div><a href="#/me" class="small">← My settings</a></div>
    ${isBoss() ? '<div class="card" id="tax-profile-placeholder"><p class="muted">The personal tax profile page is coming soon.</p></div>' : '<div class="card"><p>Only the boss can see and change the personal tax profile.</p></div>'}`;
}

PAGES.me = async sub => (sub === 'tax' ? taxProfilePage() : mySettingsPage());
