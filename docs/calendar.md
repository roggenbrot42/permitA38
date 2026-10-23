# Calendars: meetings reduce your capacity

permitA38 can read your personal calendar and subtract the time you spend in meetings from the capacity used for
planning. Meetings from Proton Calendar, Nextcloud, Google, Outlook, Apple or Fastmail work the same way, because all
of them can export an **ICS** file or publish a read-only **ICS link**.

> **Status: alpha.** Proton does not offer a public API for apps. The steps below use Proton's sharing and export
> features. Proton changes its menus from time to time, so the steps may not match your screen exactly. If they do
> not, use the export route (a file), which works everywhere.

## What it does and does not do

- **Read-only.** permitA38 never writes to your calendar and never creates meetings there.
- **One direction.** Changes in your calendar appear in permitA38 at the next sync (every 6 hours by default).
- **No account login.** There is no OAuth or CalDAV connection. You give permitA38 a link or a file.
- **Busy time only.** Only the time a meeting takes inside your working hours counts. Titles are not stored by
  default.
- **Suggestions, not decisions.** Entries that look like vacation or out-of-office time are listed as possible
  absences. Nothing is created automatically; you enter absences yourself under *Time off* if you want them.

## Connect Proton Calendar

*Settings → Calendars → Connect Proton Calendar* shows these steps:

1. Open Proton Calendar in a browser.
2. Click the gear icon, then **Settings**, then **Calendars**.
3. Choose the calendar you want to use and select **Share via link** (the wording may differ, for example *Share
   calendar* or *Get link*).
4. Create the link and **copy** it. Anyone with the link can read that calendar, see the privacy notes below.
5. Paste the link into permitA38 and press **Connect link**.

**Or export a file:** choose **Export** (or *Download calendar*) for the calendar, then upload the `.ics` file with
*Upload file*. A file is a snapshot: to update it, export and upload it again.

Other providers work the same way: paste their ICS or *secret address* / *publish* link, or upload their export. Links
must use `https://` (a `webcal://` link is converted automatically). Plain `http://` links are refused.

## Options per calendar

| Option | Meaning | Default |
|---|---|---|
| Titles | `none`: no titles are stored. `redacted`: every meeting is stored as "Busy". `full`: titles and categories are stored (needed only for your own reference). | `none` |
| Whole-day events block the working day | An all-day entry (for example a company event) takes the whole working day. Off means it is shown but does not reduce capacity. | off |
| Counts toward my capacity | Off means the calendar is shown in previews but never reduces capacity. | on |
| Refresh | How often the link is downloaded again (15 minutes to once a week). | every 6 hours |
| Only / ignore events containing | Keywords matched against the title and categories, case-insensitive. Changing them downloads the calendar again (or asks for a new file). | none |

## How busy time is computed

For each day, permitA38 takes every meeting from every counting calendar and:

1. keeps only the part inside your **working hours** (default 09:00–17:00 in Europe/Berlin, on your working days; set
   *Working day from / until* and *Time zone* in *Team* → edit the person);
2. merges overlapping meetings, so two overlapping meetings count once;
3. caps the day at your **daily target** (weekly hours divided by working days, 8 h for a 40 h week);
4. treats time-zone changes and daylight saving correctly (a 09:00 meeting stays at 09:00 local time).

Meetings marked **free** (transparent, or Microsoft's *free* status) are ignored. Cancelled meetings are ignored.
Recurring meetings are expanded (daily, weekly, monthly, yearly, with exceptions and changed single occurrences).

The planning page uses these hours. *Settings → Calendars → Preview 14 days* shows the busy hours per day and the
meetings found, so you can check the result.

## Privacy and security

- **The link is a secret.** Anyone who has it can read your whole calendar. permitA38 keeps it only in the server
  database, never returns it (the screen shows only the host, for example `calendar.proton.me/…/****`), and never
  writes it to logs or error messages. Revoke the link in your calendar provider if it leaks, then connect a new one.
  The link is stored in plain text in the database (it must be, to download the calendar again). That means it
  travels with the data folder: if the folder is synced between devices, or copied into a backup, the link goes
  with it. Treat the data folder and its backups like the calendar itself, and do not share them.
- **Other people's data.** Meeting attendees and titles are personal data of third parties. The default stores no
  titles and no categories; UIDs are stored as hashes. Keep it that way unless you need the titles.
- **Deleting.** *Delete* removes the calendar and its stored meetings at once.
- **Downloads are checked.** Only public web addresses are used (no local network, no redirects into it), with size
  and time limits.
- **Who can use it.** Only the boss account can connect calendars in this alpha; each calendar belongs to the person
  who connected it and nobody else can see it.
- **Failures keep the old data.** If a download fails, the previously stored meetings are kept and the error is shown.

## Limits

- Calendars are imported for a window from 30 days in the past to about 18 months ahead. The planner can look further
  ahead (up to 156 weeks); beyond the stored window no meetings are known, so no busy time is counted there (it is
  never an error). Up to 20,000 events per
  calendar are read; larger calendars are refused with a message.
- Recurrence rules: daily, weekly, monthly and yearly rules are expanded; hourly and minute rules, and the rarer
  week-number and year-day rules, keep only the first occurrence and are reported as a warning.
- Time zones: IANA names (for example `Europe/Berlin`) are used exactly. Common Windows names such as
  `W. Europe Standard Time` are mapped to the matching IANA zone; any other zone is evaluated from the rules in the
  calendar's own definition. A zone that cannot be understood falls back to your time zone and is reported.
- An uploaded file is read once and its text is not kept (it would contain meeting titles), so repeating meetings are
  not extended later. After about six months *Settings → Calendars* advises uploading the file again; a link
  subscription is re-read automatically.
- Changes to "this and all following" occurrences (`RANGE=THISANDFUTURE`) are not supported: only the named
  occurrence is changed.
- A single repeating meeting that is too complex to expand (for example February 31st) is skipped and reported; the
  rest of the calendar is still imported.
- Uploads are limited to 5 MB; the size is checked from the request header before the file is read, but a client that
  hides the real size can still make the server receive the data before it is refused.
- Notes about the last import (unsupported rules, unknown zones, skipped meetings) are shown under the calendar in
  *Settings → Calendars*.
- Members cannot connect calendars in this alpha (the rights name `calendar.manage_own` is ready for the rights work).
- Attendee status (accepted/declined) and reminders are not used.

## Command line

`permita38 calendar refresh` downloads every connected link now (useful from a scheduled task). It prints the calendar
names and their status, never the links.

## For developers

- Parser: `permita38/core/service/calendar/ics.py` (stdlib only, bounded) and `recurrence.py`.
- Service and API: `permita38/core/service/calendar/service.py`, `permita38/api/routers/calendars.py`. The right
  `calendar.manage_own` is defined in `rights.py` (boss only for now).
- Busy hours for the planner: `CalendarBusyProvider` implements the shared `BusyTimeProvider` protocol
  (`permita38/core/service/busy.py`); `services.busy` is the provider in use.
- Frontend: `web/calendars.js` (loaded after `app2.js`).
