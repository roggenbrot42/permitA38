# Personal tax profile

The personal tax profile tells permitA38 about your private situation so the **Money & Taxes** page can estimate your
income tax and social insurance. Only the boss can see and change it. Open it under **My settings**, card *Personal
tax profile*, link *Open tax profile* (address `#/me/tax`).

> **Estimate only – not tax advice.** The figures are planning aids computed from the official rate tables. Confirm
> them with your Steuerberater. All amounts on this page are invented examples.

## Not saved yet

Until you press **Save tax profile** the page shows the banner *Not saved yet – the estimate uses the defaults shown
here*: single, statutory health insurance, no children, no pension or unemployment insurance, no employment.

## Sections

- **Assessment**: single or joint, church tax in percent (0 for none; 8 in Bayern and Baden-Württemberg, 9 elsewhere),
  other income, other deductions and other insurance (liability, disability, term life) per year.
- **Children**: number with Kindergeld, whether the full child allowances were transferred to you, whether you have or
  had a child (no childless surcharge in care insurance) and how many are under 25.
- **Health and care insurance**: *Statutory* asks for Krankengeld and the Zusatzbeitrag (leave empty for the average).
  *Private* asks for the monthly premium, the basic-cover share from your insurer's annual statement (not more than the
  premium), care insurance and the employer subsidy. *None modelled* shows no further fields.
- **Pension**: none, statutory (on income or regular amount) or a fixed monthly amount (Rürup, Versorgungswerk).
  **Unemployment insurance**: none, voluntary, or half rate in the founding years.
- **Employment**: tick *I also have employment income* to enter the gross wage, social insurance liability,
  Steuerklasse (only affects monthly withholding, not the annual tax), bAV, VWL, Werbungskosten and the tax withheld
  for the whole year.

## Saving and warnings

Save replaces the whole stored profile. Values outside their allowed range are refused in the browser before anything
is sent. After saving, hints appear as banners, for example *Steuerklasse III/IV/V means married; check the
assessment*. Hints never block saving.

## What is not modelled

Capital gains, foreign income, loss carry-forwards, the spouse's church tax and individual special cases. Rates of a
year that are not yet confirmed are listed with the estimate.

Rates and rules that still need a professional review: see [`docs/tax-adviser-review.md`](tax-adviser-review.md).
