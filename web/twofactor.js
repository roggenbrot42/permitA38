/* permitA38 – two-factor authentication (TOTP): login second step, account dialog, boss reset. */

// a wrong password/code answers 401 but must not sign the user out
const NA = { noAuthRedirect: true };

/* ---------- login, second step ---------- */
function renderMfa(app, mfaToken) {
  let useRecovery = false;
  const draw = () => {
    app.innerHTML = `<div class="auth-wrap"><form class="auth-card" id="mfa-form" autocomplete="off" novalidate>${BRAND}<h1>Two-factor code</h1>
      <p class="muted">${useRecovery ? 'Enter one of your recovery codes. Each code works only once.' : 'Enter the 6-digit code from your authenticator app.'}</p>
      <div class="field"><label for="m_code">${useRecovery ? 'Recovery code' : 'Code'}</label>
      ${useRecovery
        ? '<input id="m_code" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="xxxx-xxxx-xxxx-xxxx" autofocus>'
        : '<input id="m_code" inputmode="numeric" pattern="[0-9]*" maxlength="7" autocomplete="one-time-code" placeholder="123456" autofocus>'}</div>
      <div class="banner" id="auth-err" role="alert" hidden></div>
      <button class="primary" id="m_go" type="submit" style="width:100%;justify-content:center">Verify</button>
      <p class="small"><a href="#" id="m_switch">${useRecovery ? 'Use the authenticator app instead' : 'Use a recovery code instead'}</a> · <a href="#" id="m_back">Back</a></p></form></div>`;
    const input = $('#m_code'); let busy = false;
    const submit = async () => {
      const v = input.value.trim(); if (!v || busy) return;
      busy = true; authError(''); authBusy($('#m_go'), true);
      try {
        const r = await api.post('auth/login/totp', { mfa_token: mfaToken, ...(useRecovery ? { recovery_code: v } : { code: v }) }, { noAuthRedirect: true });
        await enterApp(r.token);
        if (r.recovery_codes_left !== null && r.recovery_codes_left !== undefined && r.recovery_codes_left <= 3) toast(`Only ${r.recovery_codes_left} recovery code(s) left. Create new ones in Two-factor authentication.`);
      } catch (err) {
        busy = false; authError(err.message); authBusy($('#m_go'), false, 'Verify'); input.select();
        if (err.status === 401 && /start again/i.test(err.message)) setTimeout(() => render(), 1500);   // challenge expired
      }
    };
    $('#mfa-form').onsubmit = e => { e.preventDefault(); submit(); };
    if (!useRecovery) input.oninput = () => { if (/^\d{6}$/.test(input.value.replace(/\s/g, ''))) submit(); };   // auto-submit on 6 digits
    $('#m_switch').onclick = e => { e.preventDefault(); useRecovery = !useRecovery; draw(); };
    $('#m_back').onclick = e => { e.preventDefault(); render(); };
  };
  draw();
}

/* ---------- account dialog ---------- */
function tfaSecretText(secret) { return secret.replace(/(.{4})/g, '$1 ').trim(); }
A.twoFactor = async () => {
  const st = await api.get('auth/totp/status');
  if (st.enabled) return tfaManage(st);
  modal(`<h2>Two-factor authentication</h2><p class="muted">Protect your account with a code from an authenticator app (e.g. FreeOTP, Aegis, Google Authenticator). Confirm your password to start.</p>
    ${pwField('tf_pw', 'Current password', 'current-password')}<div class="banner" id="tf-err" role="alert" hidden></div>
    <div class="actions"><button id="tf-cancel">Cancel</button><button class="primary" id="tf-go">Continue</button></div>`);
  $('#tf-cancel').onclick = closeModal;
  const go = guard(async () => {
    try { tfaScan(await api.post('auth/totp/setup', { password: $('#tf_pw').value }, NA)); }
    catch (e) { tfaError(e.message); }
  });
  $('#tf-go').onclick = go; $('#tf_pw').onkeydown = e => { if (e.key === 'Enter') go(); };
};
function tfaError(msg) { const e = $('#tf-err'); if (e) { e.textContent = msg; e.hidden = !msg; } }

function tfaScan(setup) {
  modal(`<h2>Scan the QR code</h2><p class="muted">Open your authenticator app, add an account and scan this code. Then enter the 6-digit code it shows.</p>
    <div style="text-align:center"><img src="${esc(setup.qr_svg)}" alt="QR code for ${esc(setup.account)}" width="200" height="200" style="background:#fff;border-radius:8px"></div>
    <p class="small muted">Cannot scan? Enter this key manually (time-based, 6 digits):<br><code id="tf-secret" style="user-select:all;word-break:break-all">${esc(tfaSecretText(setup.secret))}</code></p>
    <div class="field"><label for="tf_code">6-digit code</label><input id="tf_code" inputmode="numeric" pattern="[0-9]*" maxlength="7" autocomplete="one-time-code" autofocus></div>
    <div class="banner" id="tf-err" role="alert" hidden></div>
    <div class="actions"><button id="tf-cancel">Cancel</button><button class="primary" id="tf-go">Turn on</button></div>`);
  $('#tf-cancel').onclick = closeModal;
  const go = guard(async () => {
    try {
      const r = await api.post('auth/totp/enable', { code: $('#tf_code').value.trim() }, NA);
      api.token.set(r.token); S.me.totp_enabled = true; tfaCodes(r.recovery_codes, true);
    } catch (e) { tfaError(e.message); }
  });
  $('#tf-go').onclick = go; $('#tf_code').onkeydown = e => { if (e.key === 'Enter') go(); };
}

function tfaCodes(codes, enabling) {
  const text = 'permitA38 recovery codes (each works once)\n\n' + codes.join('\n') + '\n';
  modal(`<h2>${enabling ? 'Two-factor authentication is on' : 'New recovery codes'}</h2>
    <p>Store these recovery codes somewhere safe, e.g. in a password manager. Each code works <b>once</b> if you lose your phone. They are shown only now.</p>
    <pre id="tf-codes" style="columns:2;user-select:all;padding:12px;background:var(--bg);border-radius:8px">${codes.map(esc).join('\n')}</pre>
    <div class="actions" style="justify-content:flex-start"><button id="tf-copy">Copy</button><button id="tf-dl">Download as text</button></div>
    <label style="display:block;margin:12px 0"><input type="checkbox" id="tf-ok"> I stored them safely</label>
    <div class="actions"><button class="primary" id="tf-done" disabled>Done</button></div>`);
  $('#tf-ok').onchange = () => { $('#tf-done').disabled = !$('#tf-ok').checked; };
  $('#tf-done').onclick = () => { closeModal(); render(); };
  $('#tf-copy').onclick = async () => { try { await navigator.clipboard.writeText(text); toast('Copied'); } catch { toast('Select the codes and copy them manually', true); } };
  $('#tf-dl').onclick = () => {
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
    a.download = 'permita38-recovery-codes.txt'; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
}

function tfaManage(st) {
  modal(`<h2>Two-factor authentication</h2><p>Status: <span class="pill good">on</span> · ${st.recovery_codes_left} recovery code(s) left.</p>
    <p class="muted small">To change anything, confirm your password and a current code from your authenticator app.</p>
    ${pwField('tf_pw', 'Current password', 'current-password')}
    <div class="field"><label for="tf_code">6-digit code</label><input id="tf_code" inputmode="numeric" pattern="[0-9]*" maxlength="7" autocomplete="one-time-code"></div>
    <details><summary class="small">Lost your phone? Use a recovery code to turn it off</summary><div class="field"><label for="tf_rec">Recovery code</label><input id="tf_rec" autocomplete="off" spellcheck="false"></div></details>
    <div class="banner" id="tf-err" role="alert" hidden></div>
    <div class="actions"><button id="tf-cancel" style="margin-right:auto">Close</button><button id="tf-regen">New recovery codes</button><button class="danger" id="tf-off">Turn off</button></div>`);
  $('#tf-cancel').onclick = closeModal;
  $('#tf-regen').onclick = guard(async () => {
    try { const r = await api.post('auth/totp/recovery-codes/regenerate', { password: $('#tf_pw').value, code: $('#tf_code').value.trim() }, NA); tfaCodes(r.recovery_codes, false); }
    catch (e) { tfaError(e.message); }
  });
  $('#tf-off').onclick = guard(async () => {
    const code = $('#tf_code').value.trim(), rec = $('#tf_rec').value.trim();
    try {
      const r = await api.post('auth/totp/disable', { password: $('#tf_pw').value, ...(rec ? { recovery_code: rec } : { code }) }, NA);
      api.token.set(r.token); S.me.totp_enabled = false; closeModal(); toast('Two-factor authentication is off'); render();
    } catch (e) { tfaError(e.message); }
  });
}

/* ---------- boss: reset another person's 2FA ---------- */
A.resetTotp = el => {
  const u = S.d.users.find(x => x.id === el.dataset.id); if (!u) return;
  modal(`<h2>Reset two-factor authentication</h2><p>${esc(u.name)} will be signed out everywhere and can sign in with just a password until they set 2FA up again. Confirm with <b>your</b> password.</p>
    ${pwField('rt_pw', 'Your password', 'current-password')}<div class="banner" id="tf-err" role="alert" hidden></div>
    <div class="actions"><button id="tf-cancel">Cancel</button><button class="danger" id="tf-go">Reset 2FA</button></div>`);
  $('#tf-cancel').onclick = closeModal;
  $('#tf-go').onclick = guard(async () => {
    try { await api.post(`users/${u.id}/totp/reset`, { password: $('#rt_pw').value }, NA); closeModal(); toast('Two-factor authentication reset'); render(); }
    catch (e) { tfaError(e.message); }
  });
};
