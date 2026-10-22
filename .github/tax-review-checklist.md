Quarterly review of the statutory rules built into permitA38. Check each item against the primary source, update
`permita38/core/tax.py` (rate tables carry *source* and *valid from*), add or adjust tests, and add a CHANGELOG entry.
Close this issue when everything is checked or split into follow-up issues.

- [ ] **Income tax tariff** (Einkommensteuertarif, Grundfreibetrag, brackets) and prepayment rules
- [ ] **Solidarity surcharge** (Solidaritaetszuschlag) thresholds
- [ ] **VAT**: standard/reduced rates, special rates, **Kleinunternehmer** limits (§ 19 UStG), reverse charge cases
- [ ] **UStVA**: form boxes (Kennzahlen), filing deadlines, Dauerfristverlaengerung, ELSTER changes
- [ ] **Trade tax** (Gewerbesteuer): allowance, base rate, change of municipal rates used as defaults
- [ ] **Social-security related** reference values used in estimates (if any)
- [ ] **Bookkeeping standards**: SKR03/SKR04 changes, DATEV format version, GoBD updates, e-invoice obligations
      (XRechnung / ZUGFeRD, EN 16931 schema versions)
- [ ] **New laws and BMF letters** since the last review (Jahressteuergesetz, Wachstumschancengesetz, ...)
- [ ] Public holidays per federal state for the coming year (`permita38/core/holidays.py`)
- [ ] Document templates still match legal invoice requirements (§ 14 UStG)
