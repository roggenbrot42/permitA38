# Roles and rights

permitA38 has three roles. The role of a person is set by the boss under **Team**. The server checks the role on
every request (the web app only hides what you cannot use), so a role cannot be bypassed by calling the API directly.

| Role | For | In one sentence |
|---|---|---|
| `boss` | the owner | Everything, including settings and managing people. At least one active boss must exist. |
| `employee` | people who work with you | Track own time and time off, see the projects they are assigned to (without rates, budgets or notes). |
| `accountant` | bookkeeper / tax adviser | Read access to customers, projects, time, contracts, offers, invoices, finance and settings; may enter expenses and upload bills; cannot change anything else and tracks no time. |

Older data folders used the role name `member`; it is migrated to `employee` (migration `0006-roles`) and still accepted
wherever a role is given.

## Permission matrix

Per area there is a read scope and a write scope (`read / write`):

* `none` – no access (the API answers `403 Forbidden`),
* `limited` – a restricted view: own records only (timer, time entries, time off), assigned projects and their
  tasks without rates (projects), business name and federal state only (settings), names and roles only (team; the web app needs the role),
* `all` – full access.

| Area | Covers | boss | employee | accountant |
|---|---|---|---|---|
| `timer` | Own timer and weekly dashboard | all / all | limited / limited | none / none |
| `time` | Time entries (the timesheet) | all / all | limited / limited | all / none |
| `absences` | Vacation and other time off | all / all | limited / limited | none / none |
| `projects` | Projects and tasks | all / all | limited / none | all / none |
| `customers` | Customers (and the Impressum lookup) | all / all | none / none | all / none |
| `contracts` | Contracts | all / all | none / none | all / none |
| `planning` | Planning: forecast, what-if scenarios, vacation planner | all / all | none / none | none / none |
| `offers` | Offers | all / all | none / none | all / none |
| `invoices` | Invoices | all / all | none / none | all / none |
| `expenses` | Expenses | all / all | none / none | all / all |
| `trips` | Business trips: meal allowance, mileage and trip statements (own trips only for employees) | all / all | limited / limited | all / none |
| `finance` | Revenue, profit, VAT and project reports | all / all | none / none | all / none |
| `templates` | Document templates | all / all | none / none | all / none |
| `settings` | Business settings | all / all | limited / none | all / none |
| `attachments` | Uploaded files (bills, logo) | all / all | none / none | all / all |
| `team` | Users, passwords and two-factor resets | all / all | limited / none | limited / none |

Notes

* Planning, scenarios and the vacation planner are boss only. Accepting offers, finalising and numbering invoices,
  billing tracked time and deleting documents belong to the *offers* / *invoices* write access (boss only).
* The accountant can download PDFs of offers and invoices (read access) but not change their status.
* Account-level endpoints (own password, own two-factor authentication, bootstrap, holidays) are open to every
  signed-in person; resetting someone else's two-factor authentication needs `team` write access.
* The table lives in `permita38/core/rights.py` and is the single source of truth; a test checks this page against it
  and another test calls every API route as every role and expects `403` exactly where the table says so.
