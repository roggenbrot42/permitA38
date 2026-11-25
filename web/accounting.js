/* permitA38 – accounting status: the "set up accounting" panel (before initialisation) and the adviser banner
   (while the tax rules are not confirmed by a tax adviser). Contract: docs/specs/accounting.md 16, 22 (W9).
   Everything from the server goes through esc(). Vanilla JS, no build step. */

const ACC = { status: null };
const ACC_DISMISS_KEY = 'permita38.accounting.banner.dismissed';
const ACC_BANNER_FALLBACK = 'Tax rules not confirmed by a tax adviser — check the figures before filing';

function accDismissed() { try { return sessionStorage.getItem(ACC_DISMISS_KEY) === '1'; } catch { return false; } }
async function accStatusLoad() { ACC.status = await api('GET', 'accounting/status'); return ACC.status; }

/** The adviser banner (dismissible per session); empty when the rules are confirmed or the banner was dismissed. Also used by the VAT export dialogs. */
function accBannerHTML(st = ACC.status) {
  if (!st || !st.initialised || !st.rules_unconfirmed || accDismissed()) return '';
  return `<div class="banner" role="status" data-testid="adviser-banner"><span>${esc(st.banner_text || ACC_BANNER_FALLBACK)}</span> <button type="button" class="ghost sm" data-a="accDismissBanner" aria-label="Dismiss the notice">✕</button></div>`;
}
A.accDismissBanner = () => {
  try { sessionStorage.setItem(ACC_DISMISS_KEY, '1'); } catch { /* ignore */ }
  document.querySelectorAll('[data-testid="adviser-banner"]').forEach(el => el.remove());
};

function accSetupPanelHTML() {
  return `<div class="card" data-testid="accounting-setup"><h2>Set up accounting</h2>
    <p class="muted">Accounting has not been set up yet, so nothing is posted. Issued invoices stay frozen; credit notes and payments are recorded and post when accounting is set up.</p>
    ${isBoss() ? '<a class="btn primary" href="#/accounting/setup" data-testid="accounting-setup-link">Set up accounting</a>'
      : '<p data-testid="accounting-setup-ask">Ask the boss to set up accounting.</p>'}</div>`;
}

async function accPage(title, lead, body) {
  const st = await accStatusLoad();
  return `<div class="head"><div><h1>${title}</h1><div class="muted">${lead}</div></div></div>${st.initialised ? accBannerHTML(st) + body : accSetupPanelHTML()}`;
}

PAGES.accounting = async (sub) => {
  if (sub === 'setup') {
    // placeholder: the set-up wizard is built in work package W8
    if (!isBoss()) return denyMember();
    return `<div class="head"><div><h1>Set up accounting</h1></div></div><div class="card" data-testid="accounting-setup-coming"><p>The set-up wizard is coming soon.</p><a href="#/accounting">Back</a></div>`;
  }
  return accPage('Accounting', 'Journal, reports and year-end closing.', '<div class="card"><p class="muted">Accounting is set up.</p></div>');
};
PAGES.vat = async () => accPage('VAT', 'VAT returns and recapitulative statements.', '<div class="card"><p class="muted">Accounting is set up.</p></div>');
