# Sanity checks (advisory warnings)

permitA38 distinguishes two kinds of checks:

- **Hard limits** (impossible input: `2026-13-45`, 25 hours in a day, a negative expense) are rejected with HTTP 422,
  see [`validation.md`](validation.md).
- **Sanity checks** (suspicious but possible input) never block anything. The record is saved first; the response
  then carries a response-only list `sanity_warnings` (never stored) and the web app shows it as a banner
  ("N warnings, saved - you can still continue"). The planner's `planning_warnings` use the same shape and the same
  banner.

Each warning is `{code, severity, field, message, details, confirmable}`. `severity` is `info` or `warning`;
`confirmable` is always `false` (there is nothing to confirm, the data is already saved). Checks only look at what
changed: re-saving an old invoice without touching its date does not repeat the date warning.

Where warnings appear: `POST`/`PUT` of `entries`, `offers`, `invoices`, `expenses`, `customers`; `PUT /settings`;
`POST /timer/stop` (for the saved entry); `POST /invoices|offers/{id}/finalize`, `/invoices/from-time`,
`/offers/{id}/to-invoice`. A rule that crashes is logged and ignored: a bug in a check cannot fail a save.

## Rules

| Code | Applies to | Triggers when |
|---|---|---|
| `invoice.date.far_future` / `offer.date.far_future` | date changed | more than 31 days after today |
| `invoice.date.far_past` / `offer.date.far_past` | date changed | more than 366 days before today |
| `invoice.due_date_before_date`, `invoice.paid_date_before_date`, `offer.valid_until_before_date` | invoices / offers | the later date is before the document date |
| `invoice.service_after_date` (info) | invoices | service period starts after the invoice date (advance invoice) |
| `invoice.total_zero`, `offer.total_zero` | documents with lines | net total is 0.00 |
| `offer.total_negative` / `invoice.total_negative` (info) | documents | net total below 0 (an invoice may be a credit note) |
| `*.line_free` (info) | documents | a line has a quantity but no price |
| `*.vat_zero_line` (info) | documents | a line has 0 % VAT although the document charges VAT |
| `*.vat_ignored` (info) | documents | a line has VAT but you are a small business (§19 UStG) |
| `*.possible_duplicate` | documents, expenses | same customer + date + net total (documents), same vendor + date + net (expenses) |
| `*.total_outlier`, `expense.amount_outlier` | documents, expenses | amount of at least 500 that is more than 20 times the median of at least 5 earlier ones (same category for expenses) |
| `invoice.seller_tax_id_missing` | finalize invoice | neither tax number nor VAT id in the settings (§14 UStG) |
| `expense.date.far_future` / `far_past` | expenses | after today / more than 366 days ago |
| (itemised bills) | expenses with `lines` | `expense.vat_mismatch`, `expense.vat_rate_implausible` and `expense.vat_exceeds_net` are skipped (mixed rates); outlier and duplicate use the summed fields |
| `expense.vat_mismatch` | expenses with a VAT rate | VAT differs from net x rate (half-up cents) by more than 1 cent |
| `expense.vat_rate_implausible` (info) | expenses without a rate, net at least 10 | VAT/net is not within 0.5 points of 0, 5, 7, 16, 19 or your configured rate |
| `expense.vat_exceeds_net` | expenses | VAT larger than net |
| `customer.vat_id_invalid`, `customer.tax_number_invalid`, `settings.vat_id_invalid`, `settings.tax_number_invalid`, `settings.iban_invalid`, `settings.bic_invalid` | customers, settings | format or check digit wrong (see below) |
| `entry.date_future` (info), `entry.date_old` (info) | time entries, date changed | after today / more than 366 days ago |
| `entry.minutes_exceed_span` | entries with start and end | minutes are more than the clock span plus one minute |
| `time.day_over_10h` | time entries | the person's entries of that day sum to more than 10 h (§3 ArbZG) |
| `time.break_missing` | entries of a day that all have start and end | more than 6 h of work with less than 30 min break, more than 9 h with less than 45 min, or more than 6 h in one block |
| `time.rest_short` | entries with start and end | less than 11 h between the last end of one day and the first start of the next (§5 ArbZG) |
| `time.sunday_holiday` (info) | employees, date changed | Sunday or public holiday of the configured state (§9 ArbZG) |

### Working-time rules

They are plausibility aids, not legal advice. For an **employee** (any user who is not a boss) the findings are
`warning`; for the **boss** (self-employed, not covered by the ArbZG) the same findings are `info`. Break
accounting: gaps of at least 15 minutes between entries count as breaks, shorter gaps and overlaps count as work.
Record breaks as gaps between entries; a day with an entry that has no clock times is not checked for breaks (the
minutes alone do not say when the break was). Night shifts (end before start) end on the next day.

### Identifiers

- German **USt-IdNr**: `DE` + 9 digits, first digit not 0, check digit by the BZSt procedure (ISO 7064 mod 11,10).
- Other EU VAT ids (and GB/CH): country prefix and format only, no check digit.
- **Steuernummer**: 10, 11 or 13 digits with `/`, space, `-` or `.` as separators, not all digits equal. The
  check digit differs per state and is not verified.
- **IBAN**: country length table and the ISO 13616 mod-97 check; **BIC**: 8 or 11 characters.

These are format checks. Whether a number is *registered* can only be confirmed by VIES / the BZSt service.

## Not covered (yet)

User-tunable thresholds and per-rule switches, a "data health" report over stored records, average-48-hours and
weekly rest rules, and a confirm-before-save flow. The thresholds are constants in
`permita38/core/service/sanity_rules.py`.
