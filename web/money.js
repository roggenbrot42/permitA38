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

  return { parse, roundDiv, lineCents, vatCents, totals, docGross, splitGross, toNumber };
})();
