# Getting started with permitA38

Welcome! This guide takes you from zero to your first tracked hour, your first offer and your first invoice. Allow about 15 minutes.

## 1. Start permitA38

```bash
./start-permita38.sh --data ~/Sync/permitA38        # Linux
start-permita38.bat --data "C:\Users\you\Sync\permitA38"   # Windows
```

A browser tab opens. Create your account – you are the **boss**. The folder you chose with `--data` now holds all your data. If you use several devices, pick a folder inside your Syncthing share (see [Sync](#7-use-it-on-all-your-devices)).

## 2. Fill in your business (2 min)

Open **Settings** and enter:

- **Business details** – name, address, tax number *or* VAT ID, bank/IBAN. They appear on your invoices.
- **Company type** – Freiberufler, Gewerbetreibender or UG/GmbH. This drives the tax dashboard.
- **Federal state** – for the right public holidays.
- **Kleinunternehmer?** – switch on if you charge no VAT (§19 UStG).
- **Monthly health / pension insurance** – so permitA38 can show what is really left for you.
- **Default hourly rate** – the fallback rate when nothing more specific is set.

Press **Save settings**.

## 3. Your daily routine (the part you'll use 95 % of the time)

The **Today** page is built for it:

1. **Pick a project and press ▶ Start.** The clock runs. (Started on your laptop? You can stop it on your desktop.)
2. **Press ■ Stop** when you're done – the time is saved. Forgot to start? Use **+ Add time manually** and type `1:30`, `1.5` or `90m` – or enter from/to times.
3. **Glance at "This week"**: *"18:00 h to go – about 9:00 h per remaining working day."* Public holidays and time off are already subtracted.

That's it. Everything else is planning and billing, which you do when you need to.

> Fix a mistake: click any entry (on Today or in **Timesheet**) to edit or delete it.

## 4. Add customers and projects (3 min)

- **Customers → + New customer.** Company, address, tax number/VAT ID, point of contact, notes and an optional default hourly rate.
- **Save typing: prefill from the Impressum.** At the top of the form, enter the customer's website (e.g. `www.example.com`) and press **Look up**. permitA38 reads their legal notice and fills the empty fields (marked *from Impressum* – hover to see where each value came from). Check everything, change what is wrong, then **Save**. Fields you typed yourself are never overwritten unless you click **Use**. Note that the app requests the web page you entered; the switch is `features.impressum_lookup` in the config.
- **Projects → + New project.** Choose the customer (or tick *Personal project* for things like learning or admin – never billed). Optionally set a project rate and an hour budget.
- **Tasks (optional)** – open a project and add tasks when different work has a different price, e.g. *Consulting 130 €/h* vs *Development 95 €/h*.

**Which rate is used?** The most specific wins: **task → project → customer → person → default rate.**

## 5. From offer to invoice

1. **Offers & Invoices → + Offer** (or press **+ Offer** on a customer page). Choose the customer, add items (description, quantity, unit, price) and a template.
2. Under **Planning (optional)** enter when the work should happen: start, end and hours per week (or total hours). While the offer is open, it shows up as *tentative* load in your forecast.
3. **Finalize & number** → the offer gets its number (e.g. `AN-2026-0001`). **Preview PDF** and send it to your customer.
4. The customer says yes → **✓ Accepted → create project**. permitA38 creates the project *and* a contract so your forecast is now firm.
5. Track your time as usual. When it's time to bill, go to **Offers & Invoices → Invoice tracked time…** – permitA38 groups the unbilled hours by project, task and rate into a draft invoice. Or use **→ Invoice** on the offer to copy its items.
6. **Finalize & number**, send the PDF, and when the money arrives set the status to **Paid**. Only *paid* invoices count as revenue (EÜR).

Don't like the look? **Settings → Document templates** has three starting points you can copy and customise, and you can upload your logo (JPEG).

## 6. Plan your capacity

Open **Planning**:

- **Forecast** – bars show hours booked per week (solid = contracts, light = open offers); the green line is your capacity after holidays and time off. The table below lists free hours and expected revenue per week.
- **Contracts** – add work you've agreed outside of permitA38, too.
- **Time off & holidays** – add vacation or personal days. See at a glance whether there is room for a new project.

## 7. Use it on all your devices

1. Install [Syncthing](https://syncthing.net/) on every computer and share the data folder.
2. Run permitA38 on each with `--data <that folder>` (once – it remembers).
3. Done. Your data is one SQLite file (`permita38.sqlite3`) plus an `attachments/` folder, so work on **one device at a time**: quit permitA38, let Syncthing finish, then open it on the other device.
4. Safety nets: permitA38 makes a daily backup in `backups/` (and one before every upgrade of the database), and you should switch on Syncthing's *file versioning* for the folder. If two devices were edited offline at the same time, Syncthing creates a conflict copy of the whole database; permitA38 shows a warning banner – keep the version you want and delete the other.
5. Upgrading from the earlier JSON-file version? Run `permita38 import-json <old data folder>` once.

## 8. Expenses and your tax picture

- **Expenses → + Add bill.** Enter the amount on the bill (gross) and the VAT rate – permitA38 works out net and VAT. Attach the PDF or a photo. Choose a project or leave it as a general expense.
- **Money & Taxes** shows, for any year: revenue, expenses, profit, VAT, estimated income tax / trade tax and – the important number – **what to set aside this month** and **what you can take home**.

> These numbers are planning estimates, not tax advice. Check them with your Steuerberater.

## 9. Working with a team

Under **Team → + Add person** create members. Give each person a username and an initial password; they sign in on the login screen, can change the password from the account menu, track their own time, and book their own time off. You see everyone's week at a glance and can plan with their capacity in the forecast. Set a **cost rate** per person to see real project margins.

## Cheat sheet

| I want to… | Go to… |
|---|---|
| Start/stop the clock | **Today** |
| Fix or add time | **Timesheet** |
| See free capacity | **Planning → Forecast** |
| Add vacation | **Planning → Time off** |
| Send an offer | **Offers & Invoices → + Offer** |
| Bill my tracked hours | **Offers & Invoices → Invoice tracked time** |
| Record a bill I paid | **Expenses** |
| Know what to put aside | **Money & Taxes** |
| Add a colleague | **Team** |

## Troubleshooting

- **"Address already in use"** – start with another port: `--port 8766`.
- **Python not found on Windows** – install Python 3 from python.org and tick *Add to PATH*.
- **"No Impressum was found"** – open the customer's Impressum page in your browser and paste *that* address; sites that load the notice with JavaScript can't be read.
- **A change from another device is missing** – wait for Syncthing to finish, then reload the page.
- **⚠ banner about sync-conflict files** – open the named file next to its original, keep the version you want, delete the other.
- **PDF has no logo** – the logo must be a JPEG (Settings → Your business).
