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
    // like the server: null and the empty string both mean "use the default" (document rate, then settings, then 19)
    const unset = x => x === null || x === undefined || x === '';
    const dv = klein ? 0 : (unset(doc.vat_rate) ? (unset(settings?.vat_rate) ? 19 : settings.vat_rate) : doc.vat_rate);
    const lines = [], groups = {};
    let net = 0n;
    for (const l of doc.lines || []) {
      const c = lineCents(l.qty, l.price);
      lines.push(c); net += c;
      const rate = String(klein ? 0 : (unset(l.vat) ? dv : l.vat));
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

  /* ---- decimal-string helpers (docs/specs/decimal-money-api.md rule 6): the API sends money as decimal strings and the
   * page never does float arithmetic on them. Every function accepts a decimal string, a number, null or ''. ---- */
  /** {n, s} -> canonical decimal text with at least two decimals ("12.50", "-0.125", "0.00"). */
  function decStr(d) {
    const neg = d.n < 0n, a = neg ? -d.n : d.n, s = Math.max(d.s, 2);
    const digits = (a * pow10(s - d.s)).toString().padStart(s + 1, '0');
    return (neg ? '-' : '') + digits.slice(0, digits.length - s) + '.' + digits.slice(digits.length - s);
  }
  /** Canonical decimal text of any money value ("0.00" for null, '' and garbage). */
  const str = x => decStr(parse(x));
  /** Whole cents (BigInt), rounded half away from zero. */
  const cents = x => { const d = parse(x); return roundDiv(d.n * 100n, pow10(d.s)); };
  /** Whole cents (BigInt) -> "12.50". */
  const fromCents = c => decStr({ n: BigInt(c), s: 2 });
  /** Display text for the German UI ("1.234,50 €"); the value is rounded half-up to cents first. */
  const fmt = x => new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' }).format(toNumber(cents(x)));
  /** Exact comparison: -1, 0 or 1. */
  function cmp(a, b) {
    const x = parse(a), y = parse(b), S = Math.max(x.s, y.s);
    const l = x.n * pow10(S - x.s), r = y.n * pow10(S - y.s);
    return l < r ? -1 : l > r ? 1 : 0;
  }
  const isZero = x => parse(x).n === 0n;
  const isNeg = x => parse(x).n < 0n;
  const abs = x => { const d = parse(x); return decStr({ n: d.n < 0n ? -d.n : d.n, s: d.s }); };
  /** Exact sum of a list (array or arguments) as a decimal string. */
  const sum = list => decStr(sumDec(list.map(parse)));
  /** amount x percent / 100 in whole cents (BigInt), half-up: pct("10.00", 70) = 700n. */
  const pct = (amount, percent) => { const a = parse(amount), p = parse(percent); return roundDiv(a.n * p.n, pow10(a.s + p.s)); };
  /**
   * Strict parser for what a person types into a money field (rule 6): one comma OR one dot as decimal separator,
   * at most 10 integer and 6 decimal digits, an optional leading minus. Both separators, thousands separators
   * ("1.234,56", "1,2,3"), exponents, letters and blank text are refused with a message.
   * Returns {ok: true, value: "12.50"-style canonical text or null for an empty field} or {ok: false, error}.
   */
  function parseInput(text) {
    const raw = text === null || text === undefined ? '' : String(text);
    if (raw === '') return { ok: true, value: null };
    const t = raw.trim();
    if (t === '') return { ok: false, error: 'Enter a number' };
    const seps = (t.match(/[.,]/g) || []).length;
    if (t.includes('.') && t.includes(',')) return { ok: false, error: 'Use one decimal separator and no thousands separators (e.g. 1234,56)' };
    if (seps > 1) return { ok: false, error: 'Use one decimal separator and no thousands separators (e.g. 1234,56)' };
    const m = /^(-?)(\d*)(?:[.,](\d*))?$/.exec(t);
    if (!m || (m[2] === '' && !m[3])) return { ok: false, error: 'Not a valid amount' };
    if (m[2].length > 10) return { ok: false, error: 'The amount is too large' };
    if ((m[3] || '').length > 6) return { ok: false, error: 'At most 6 decimals' };
    const value = m[1] + (m[2] || '0') + (m[3] ? '.' + m[3] : '');
    return { ok: true, value: value === '-0' ? '0' : value };
  }
  /** Split a gross amount into net and VAT as decimal strings (cent-exact, see splitGross). */
  function splitGrossStr(gross, rate) {
    const g = parse(gross), r = parse(rate);
    const grossCents = roundDiv(g.n * 100n, pow10(g.s));
    const netCents = roundDiv(grossCents * 100n * pow10(r.s), 100n * pow10(r.s) + r.n);
    return { net: fromCents(netCents), vat: fromCents(grossCents - netCents) };
  }
  /** Gross of a stored document as a decimal string, for list views and sums. */
  const docGrossStr = (doc, settings) => fromCents(totals(doc, settings).gross);

  return { parse, roundDiv, lineCents, vatCents, totals, docGross, splitGross, toNumber, rateKey, splitLines, lineTotals,
    decNum, sumDec, decStr, str, cents, fromCents, fmt, cmp, isZero, isNeg, abs, sum, pct, parseInput, splitGrossStr, docGrossStr };
})();
