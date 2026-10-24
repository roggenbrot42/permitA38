# Adopting permitA38: what do I have to change?

permitA38 ships with demo values. Before you send your first invoice, go through this list. The web app
does not show it yet; the numbering and import steps are available through the API and the CLI (see the linked
pages).

## 1. Company data

- [ ] Settings: company name, owner name, address, bank (IBAN, BIC), contact details
- [ ] Tax number and/or VAT ID as your tax office and invoices need them (Settings and the template)
- [ ] Company type (freelancer, trade, GmbH) and federal state (for the tax estimate and public holidays)
- [ ] Logo and letterhead/template (Settings); check the PDF of a test invoice

## 2. Numbering

- [ ] Invoice and offer patterns (for example `RE-{year}-{n:04d}`), see [numbering](numbering.md)
- [ ] Reset scope (yearly by default)
- [ ] Moving from another permitA38 instance: restore a full export archive into the empty new instance (`permita38 import`, see the [data export guide](data-export.md)); numbers and counters are kept, then check the next number
- [ ] Next number: if you switch from another tool, enter its last number (`PUT /api/numbering`, see numbering)

## 3. Money rules

- [ ] VAT status: standard VAT, or Kleinunternehmer (§ 19 UStG, no VAT on invoices)
- [ ] Default VAT rate (19 %, or the reduced rate for your services)
- [ ] Accounting method (Ist or Soll)
- [ ] Payment terms (days until due) and offer validity (days)
- [ ] Default hourly rate and per-customer rates

## 4. Working time and leave

- [ ] Working days and weekly hours
- [ ] Public-holiday state
- [ ] Vacation entitlement per person

## 5. Users, roles and security

- [ ] Your user (boss) and the users of your team (member role)
- [ ] Passwords; turn on two-factor authentication
- [ ] Remove the demo users if you created any

## 6. Master data

- [ ] Customers: import from a file ([import](import.md)) or enter them in the app
- [ ] Projects and task templates, if you use them
- [ ] Expense categories

## 7. Existing documents

- [ ] Import your existing offers and invoices (`permita38 import-documents`, see [import](import.md)); check the dry run first
- [ ] Check the next number after the import
- [ ] Attach the original PDFs, so the old invoices are served as they were issued

## 8. Accounting (when the accounting module is available)

- [ ] Chart of accounts and the account for each expense category
- [ ] Opening balances

## 9. Data, backups and sync

- [ ] Data folder: choose a folder that is backed up (or synced with Syncthing, see [docker](docker.md))
- [ ] Automatic backups are kept in `backups/` next to the database; test one restore
- [ ] Docker or local installation, and where the configuration lives (never in the data folder)

## 10. Test run with sample data

- [ ] Create a test customer, a draft invoice, finalize it, mark it paid, and check the PDF
- [ ] Delete the test data afterwards (numbered invoices cannot be deleted: use a draft for tests, or start with a
      fresh data folder)
- [ ] Only then start with real data

Questions about tax rules belong to your tax advisor; permitA38 computes, it does not advise.
