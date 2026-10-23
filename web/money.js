/* permitA38 – exact money arithmetic in the browser (mirrors permita38/core/money.py).
 *
 * Rule: every line amount = qty x price rounded half-up (ties away from zero) to whole cents; VAT is computed per
 * VAT rate on the SUM of the net amounts of that rate and rounded half-up; gross = net + VAT. All maths is done on
 * the decimal representation of the numbers with BigInt, never on binary floats, so 19 % of 621.50 is 118.09. */
const Money = (() => {
  const ZERO = { n: 0n, s: 0 };

  /** Parse a number or numeric string into {n: BigInt digits, s: decimal places}; garbage counts as 0. */
  function parse(x) {
    if (x === null || x === undefined || x === '') return ZERO;
    const str = (typeof x === 'number' ? String(x) : String(x).trim().replace(',', '.'));
    const m = /^([+-]?)(\d*)\.?(\d*)(?:[eE]([+-]?\d+))?$/.exec(str);
    if (!m || (m[2] === '' && m[3] === '')) return ZERO;
    const exp = m[4] ? parseInt(m[4], 10) : 0;
    if (Math.abs(exp) > 30) return ZERO;
    let digits = (m[2] + m[3]).replace(/^0+(?=\d)/, '') || '0';
    let scale = m[3].length - exp;
    if (scale < 0) { digits += '0'.repeat(-scale); scale = 0; }
    const n = BigInt(digits);
    return { n: m[1] === '-' ? -n : n, s: scale };
  }

  /** num / den rounded half away from zero. */
  function roundDiv(num, den) {
    const neg = (num < 0n) !== (den < 0n);
    const a = num < 0n ? -num : num, b = den < 0n ? -den : den;
    const q = (2n * a + b) / (2n * b);
    return neg ? -q : q;
  }

  const pow10 = k => 10n ** BigInt(k);
  /** qty x price in whole cents (BigInt). */
  const lineCents = (qty, price) => { const a = parse(qty), b = parse(price); return roundDiv(a.n * b.n * 100n, pow10(a.s + b.s)); };
  /** VAT in cents for a group's net cents. */
  const vatCents = (netCents, rate) => { const r = parse(rate); return roundDiv(netCents * r.n, 100n * pow10(r.s)); };
  const toNumber = cents => Number(cents) / 100;

  /** Totals of an offer/invoice as BigInt cents: {lines:[…], net, vat:{rate: cents}, vatTotal, gross}. */
  function totals(doc, settings) {
    const klein = !!(settings && settings.kleinunternehmer);
    const dv = klein ? 0 : ((doc.vat_rate ?? settings?.vat_rate ?? 19) || 0);
    const lines = [], groups = {};
    let net = 0n;
    for (const l of doc.lines || []) {
      const c = lineCents(l.qty, l.price);
      lines.push(c); net += c;
      const rate = String(klein ? 0 : (l.vat ?? dv));
      groups[rate] = (groups[rate] || 0n) + c;
    }
    const vat = {};
    let vatTotal = 0n;
    for (const [rate, c] of Object.entries(groups)) { vat[rate] = vatCents(c, rate); vatTotal += vat[rate]; }
    return { lines, net, vat, vatTotal, gross: net + vatTotal };
  }

  /** Gross amount (Number) of a stored document, for list views. */
  const docGross = (doc, settings) => toNumber(totals(doc, settings).gross);

  /** Split a gross amount into net and VAT (Numbers): net = gross / (1 + rate/100), rounded half-up. */
  function splitGross(gross, rate) {
    const g = parse(gross), r = parse(rate);
    const grossCents = roundDiv(g.n * 100n, pow10(g.s));
    const den = 100n * pow10(r.s) + r.n;
    const netCents = roundDiv(grossCents * 100n * pow10(r.s), den);
    return { net: toNumber(netCents), vat: toNumber(grossCents - netCents) };
  }

  /** Floor of a / b for b > 0 (BigInt division truncates towards zero, so fix negatives). */
  const floorDiv = (a, b) => { const q = a / b; return (a % b !== 0n && a < 0n) ? q - 1n : q; };
  /** Exact decimal {n, s} -> Number (correctly rounded, like Python's float(Decimal)). */
  const decNum = d => Number(d.n) / Math.pow(10, d.s);
  /** Sum of exact decimals, aligned to the largest scale. */
  function sumDec(ds) {
    const S = Math.max(0, ...ds.map(d => d.s));
    return { n: ds.reduce((a, d) => a + d.n * pow10(S - d.s), 0n), s: S };
  }
  /** Canonical text of a VAT rate ("19", "7", "0", "7.5"): the group key and the label. */
  function rateKey(rate) {
    let { n, s } = parse(rate);
    while (s > 0 && n % 10n === 0n) { n /= 10n; s -= 1; }
    const neg = n < 0n, a = neg ? -n : n;
    const int = s > 0 ? a / pow10(s) : a;
    const frac = s > 0 ? (a % pow10(s)).toString().padStart(s, '0') : '';
    return (neg ? '-' : '') + String(int) + (frac ? '.' + frac : '');
  }
  /**
   * Largest-remainder split of an itemised bill (port of permita38/core/money.py split_gross, money rules 1 and 2 of
   * docs/specs/itemised-expenses.md). pairs = [[gross, rate], ...]; returns [{net, vat}] as exact decimals {n, s}.
   * Per rate group: the group VAT is q2(sum(gross) * r / (100 + r)); each line starts at floor(e_i) cents with
   * e_i = gross_i * r / (100 + r); the missing cents go to the lines with the largest remainder, ties to the later line.
   * Everything is kept over one common denominator per group, so no binary floats are involved.
   */
  function splitLinesExact(pairs) {
    const items = pairs.map(([g, r]) => ({ g: parse(g), key: rateKey(r) }));
    const groups = new Map();
    items.forEach((it, i) => { if (!groups.has(it.key)) groups.set(it.key, []); groups.get(it.key).push(i); });
    const out = new Array(items.length);
    for (const [key, idx] of groups) {
      const R = parse(key);
      const S = Math.max(...idx.map(i => items[i].g.s));
      const D = 100n * pow10(R.s) + R.n;            // 100 + r = D / 10^R.s, so r / (100 + r) = R.n / D
      const den = pow10(S) * D;                      // e_i = gn_i * R.n / den, gn_i = gross_i scaled to 10^-S
      const gn = idx.map(i => items[i].g.n * pow10(S - items[i].g.s));
      const groupVat = roundDiv(gn.reduce((a, b) => a + b, 0n) * R.n * 100n, den);   // cents, half-up
      const parts = idx.map((i, k) => { const num = gn[k] * R.n * 100n; const f = floorDiv(num, den); return { i, f, rem: num - f * den }; });
      const rest = groupVat - parts.reduce((a, p) => a + p.f, 0n);
      if (rest < 0n || rest > BigInt(parts.length)) throw new Error('largest-remainder split failed for rate ' + key);
      const order = [...parts].sort((a, b) => (a.rem === b.rem ? b.i - a.i : (a.rem < b.rem ? 1 : -1)));
      const extra = new Set(order.slice(0, Number(rest)).map(p => p.i));
      for (const p of parts) {
        const vat = p.f + (extra.has(p.i) ? 1n : 0n);
        const g = items[p.i].g, NS = Math.max(g.s, 2);
        out[p.i] = { net: { n: g.n * pow10(NS - g.s) - vat * pow10(NS - 2), s: NS }, vat: { n: vat, s: 2 } };
      }
    }
    return out;
  }
  /** [[gross, rate], ...] -> [{net, vat}] as Numbers (cent-exact; the same values as the server's split_gross). */
  const splitLines = pairs => splitLinesExact(pairs).map(p => ({ net: decNum(p.net), vat: decNum(p.vat) }));
  /** Totals of an itemised bill: {net, vat, gross} and byRate {rateKey: VAT of that rate group} as Numbers. */
  function lineTotals(pairs) {
    const parts = splitLinesExact(pairs);
    const net = sumDec(parts.map(p => p.net)), vat = sumDec(parts.map(p => p.vat));
    const keyed = {};
    pairs.forEach(([, r], i) => { const k = rateKey(r); (keyed[k] = keyed[k] || []).push(parts[i].vat); });
    const byRate = {};
    for (const k of Object.keys(keyed)) byRate[k] = decNum(sumDec(keyed[k]));
    return { net: decNum(net), vat: decNum(vat), gross: decNum(sumDec([net, vat])), byRate };
  }

  return { parse, roundDiv, lineCents, vatCents, totals, docGross, splitGross, toNumber, rateKey, splitLines, lineTotals };
})();
