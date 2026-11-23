# Specification: issued invoices, payments and credit notes (API contract)

Status: **draft for maintainer sign-off** (release "VAT + accounting", v0.4.0-alpha.7). Author: architect (Opus).
Review tier: **Full** (money, numbering, existing clients). This is the **contract** that the web UI, the MCP server,
API-token clients, the importer and the randomized test models depend on; it is written first so backend and web can
work in parallel. Postings are in [`accounting.md`](accounting.md) (sections 9.1–9.3, 10). All figures are fake.

## 1. What changes for clients

| Before | From this release |
|---|---|
| An issued invoice (status ≠ draft) can be changed and deleted through `PUT/DELETE /api/invoices/{id}` | **Frozen.** Only the fields in 2.1 may change; everything else 409 |
| `status: "paid"` + `paid_date` marks an invoice paid | Still accepted (2.2): translated into a payment of the open amount; `paid` and `paid_date` become **derived** |
| `status: "cancelled"` cancels an invoice | 409 "issue a cancellation (credit note)"; `cancelled` is derived from a full storno |
| Totals follow the current settings | Totals of issued documents come from the `issued` snapshot |
| No partial payments, no credit notes | `payments` and credit notes (3, 4) |

Offers are unchanged. Drafts are unchanged.

## 2. Issued invoices

### 2.1 Changeable fields

`status` among the labels `sent` / `accepted` / `rejected`, `due_date`, `notes`, `reference`; attachments through the
attachment routes; unknown extra fields (round-trip). `title`, `intro`, `outro`, lines, customer, dates and rates are
frozen (they are printed on the issued document). Any other
change → **409** `{"detail": "The invoice is issued and cannot be changed; issue a credit note"}`. Sending the
unchanged value of a frozen field is not a change (old clients PUT whole records).

### 2.2 Legacy `paid`

* `PUT` with `status: "paid"` and `paid_date` on an invoice with an open amount > 0 → creates one payment of the
  **open amount** dated `paid_date` (method `bank`, note "recorded as paid"), returns 200 with the updated invoice.
* Same request when the open amount is already 0 → no-op, 200.
* `status: "paid"` without `paid_date` → 422.
* `status` from `paid` back to `sent`/`accepted` → 409 "reverse the payment instead".
* `paid_date` alone changed on a paid invoice → 409.

### 2.3 Derived fields (responses, lenient model)

`kind` (`invoice` / `credit_note`), `credit_kind`, `corrects_id`, `tax_treatment`, `issued` (snapshot),
`paid_amount`, `credited_amount`, `open_amount` (= gross − credited − paid, may be negative = customer credit),
`payments` (list of `{id, date, amount, kind, method}`), `credit_notes` (list of `{id, number, date, credit_kind,
gross}`), `paid_date` (date of the last cash event when `open_amount` ≤ 0 and `paid_amount` > 0, else null),
`status` derived in this order: `cancelled` when a `storno` credited everything (also after a payment); else `paid`
when `open_amount` ≤ 0 and `paid_amount` > 0; else the stored label.

`DELETE` of an issued invoice or a credit note → 409.

## 3. Payments

| Route | Body / result |
|---|---|
| `GET /api/invoices/{id}/payments` | list |
| `POST /api/invoices/{id}/payments` | `{date, amount, kind?: "payment"|"refund", method?: "bank"|"cash", note?}` → 201 the payment and the updated invoice |
| `POST /api/payments/{id}/reverse` | `{date?, note?}` → 201 a row of `kind = reversal` with the opposite sign and `reverses_id` (for a payment entered by mistake; a returned transfer is a refund); a reversal cannot be reversed |
| `POST /api/invoices/{id}/settle-discount` | `{date, amount_paid, note?}` → first a `discount` credit note for the rest, split per rate group as the complement of the cumulative allocation (accounting 9.3), then the payment of `amount_paid`, so the invoice settles exactly |

Validation (strict `PaymentIn`): `amount` > 0 and ≤ 10,000,000.00 (cents exact), `kind` `payment` (default) or
`refund`; a refund is stored negative and may not exceed the cash kept (422); a payment beyond the open amount is
allowed (overpayment → customer credit, warning in the response); a payment on a written-off invoice first reverses
the write-off (accounting 9.2); `date` not before the invoice date (an advance payment is out of scope, 422 "advance
payments are not supported") and not in the future; a date in a locked month is **accepted** and booked into the first
open month (accounting 7.8; under Ist its VAT drifts onto the locked month's return, the response says so); only issued invoices
of `kind = invoice` (credit notes are settled by refunds on their invoice); `note` ≤ 200, escaped on output.
Rights: write on `invoices` **or** `accounting`. API tokens: session-only routes in v1 (added to the session-only
table of `tests/backend/test_rbac.py`).

Before accounting is initialised, payments are stored and drive the derived fields; their journal entries are
written by the initialisation (accounting section 16, step 5). The legacy `paid` translation (2.2) follows the same
date rules.

## 4. Credit notes

| Route | Body / result |
|---|---|
| `POST /api/invoices/{id}/credit-notes` | `{credit_kind: "storno"|"partial"|"write_off", date, reason, amounts?: {"19": "500.00", ...}}` → 201 the credit note (a document in `invoices` with `kind = credit_note`) |
| `GET /api/invoices/{id}/credit-notes` | list |
| `GET /api/invoices/{id}/pdf` | works for credit notes too |

* `storno`: credits the whole **remaining** gross (all lines not yet credited); copies the invoice lines with the
  same quantities and prices. `partial`: one line per given rate group with the net amount and the `reason` as the
  description. `write_off`: the open amount, per rate group the complement `C_r − T_r` of the cumulative allocation
  (accounting 9.2), only when `open_amount` > 0. `discount` only through `settle-discount`.
* The credited net per rate group may not exceed the invoice's remaining net of that group (422). `reason` is
  required (1–500 chars).
* Credit notes are **issued on creation**: number from the new sequence `credit_note` (settings
  `credit_note_pattern`, default `"RK-{year}-{n:04d}"`, `credit_note_reset`, numbering page shows it), snapshot,
  date not in a locked month (409), in one transaction.
* Write-offs (and their automatic reversals) are internal vouchers: no number from the sequence, no PDF; they are
  identified by their journal entry number.
* The PDF of storno, partial and discount credit notes says "Stornorechnung" / "Cancellation invoice" (storno) or
  "Rechnungskorrektur" / "Credit note" (partial, discount), names the original invoice number and date, and shows the
  credited lines and the VAT per rate as **negative** amounts ("Minderung"), so it cannot be read as a new invoice
  with VAT shown (§ 14c); it keeps the regime text of the original (small business, EU reverse-charge note for
  `eu_b2b_service`) *(verify wording with the adviser)*. The stored lines stay positive ("credited").
* A credit note inherits `tax_treatment`, customer and project from the invoice.
* Rights: write on `invoices` or `accounting`; API tokens: session-only in v1.

## 5. Tax treatment on documents

* `tax_treatment` on invoices (`standard` default, `small_business`, `eu_b2b_service`; the reserved values are refused
  with 422 in this release) and expenses (`standard`, `no_input_vat`, `reverse_charge_eu_service`,
  `reverse_charge_third_country`). `small_business` is set by the year's regime, not chosen.
* `eu_b2b_service` requires the customer's `vat_id` with a non-DE EU prefix (422) and forces every line rate to 0;
  the PDF prints the reverse-charge note *(verify wording)*.
* Expense `reverse_charge` (old field): output derived (`true` for the two reverse-charge treatments); input mapped
  as [`accounting.md`](accounting.md) section 16 step 10. For reverse-charge treatments the stored `vat` is the
  self-assessed tax, computed by the server from `net` and `vat_rate` (a client-sent `vat` is ignored with a
  warning).
* Expense `service_date` (optional date, not after `date` + 366 days).

## 6. Clients to update in the same milestone

`web/app2.js` (invoice dialog: read-only issued invoices, payments panel, credit-note actions; expense dialog:
treatment, service date), `web/suppliers.js`, `web/receipts.js`, the MCP read-only server's invoice schema
(new fields only), the importer (imported issued invoices: snapshot from the imported totals; imported `paid` → a
payment; imported `cancelled` → review list like the initialisation), `tests/backend/randomized` (the stateful
model: frozen invoices, payments, refunds, credit notes, derived status), `tests/web/e2e_random` models, and
`api/openapi.json`.

## 7. Worked examples (oracle)

* **P1.** Invoice gross 1,190.00; PUT `status: paid, paid_date: 2026-02-03` → payment 1,190.00 on 02-03;
  `open_amount` 0, `status` paid, `paid_date` 2026-02-03. A second identical PUT → no change.
* **P2.** Invoice gross 1,725.00 (accounting A2); payments 1,000.00 and 725.00 → `paid_amount` 1,725.00, `paid_date`
  = the second date. Reversing the second payment → `open_amount` 725.00, status back to the label (`sent`),
  `paid_date` null.
* **P3.** Settle with discount: invoice gross 1,190.00 (19 %), `amount_paid` 1,166.20 → payment 1,166.20 and a
  `discount` credit note gross 23.80 = net 20.00 + VAT 3.80; `open_amount` 0.
* **P4.** Storno of an invoice with a partial credit note of net 500.00 (gross 595.00) from an invoice of net 2,000.00
  @ 19 %: the storno credits the remaining net 1,500.00 / gross 1,785.00; status `cancelled`.
* **P5.** Partial credit note net 2,100.00 on that invoice → 422 (exceeds 2,000.00).
* **P6.** PUT changing `lines` of an issued invoice → 409; PUT with the unchanged record plus `notes` → 200.

## 8. Test plan

Every row of section 1 and 2 as API tests; P1–P6; rights (boss, accountant, employee) for every new route;
session-only for tokens; the stateful randomized model and the web e2e_random models updated; OpenAPI regenerated;
the importer cases.

## Open design decisions (for the implementer's report)

Response envelope of `POST …/payments` (payment + invoice), the PDF layout of the correction line, wording of
warnings.
