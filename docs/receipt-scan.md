# Receipt scan

Upload a photo or PDF of a bill and permitA38 reads it for you. A bill that adds up is booked as an expense at once.
Anything doubtful waits in a list called **Needs your check** until you look at it. All reading happens on your own
computer; no receipt is sent to a web service.

> All amounts and names on this page are invented examples.

## Uploading

On **Expenses** press **Upload receipts** (you can pick several files) or drag files anywhere onto the page. A grey
overlay appears while you drag. Accepted are PDF, PNG, JPG, WebP (up to 15 MB each) and e-invoices as `.xml`
(XRechnung, or the XML inside a ZUGFeRD PDF). You need the right to write expenses and attachments, which means the boss
and the accountant.

Each file gets a row in the progress panel: *Scanning*, then *Added: Beispiel Hotel GmbH, 196,00 €*, or *Check needed*,
or the reason it was refused. Files are sent one after the other; one failing file does not stop the rest.

**Duplicates are refused.** The same file twice, or the same invoice number from the same vendor (a photo and the PDF of
one bill), shows an error with a link to the bill that exists. The same vendor, date and amount without invoice numbers
is not refused but flagged *Maybe already booked*, because two identical coffees are possible.

## What is booked automatically

A bill is booked without asking only when **nothing is flagged and at least one independent calculation on the bill
confirms the total**: for example the printed VAT matches the VAT computed from the amount, or the items add up to the
total. A bill that prints only a total and a rate cannot be confirmed and goes to your check. The comparison is exact to
the cent, there is no tolerance.

What a booked bill contains:

- **Bill date** and **paid date**. If the bill says it was paid (cash, card, a printed paid-on date), the paid date is
  that day. For a bill to be paid later, or when nothing is stated, the paid date stays empty and the bill date counts;
  the list then shows a small *paid date assumed* label until you enter a real date.
- **VAT**: one VAT rate becomes a normal expense; several rates become one line per rate with the printed gross. The
  booked net and VAT are computed by permitA38 from the gross, the printed values are only compared.
- **Category**: the category you used last time for the same vendor, otherwise guessed from words (hotel, petrol station,
  taxi, restaurant, ...), otherwise *Other*. A category you correct is remembered for the next bill of that vendor.
- **Invoice number**, vendor, and the file as attachment. A *scanned* label marks these bills in the list.

### Restaurants

A restaurant or cafe receipt is booked as **Business meals** with **70 % deductible** and the VAT fully deductible, the
same as when you enter such a bill by hand. This is a guess from the word *Restaurant* or similar: it does not know
whether the meal was business or private. **For a private meal, open the bill and set the category and the deductible
share** (a private meal is 0 % and is not an expense at all). The bill is saved with your correction.

### Tips

A tip is paid money, so the booked total must contain it. A bill with a tip gets an extra line **Tip** at 0 % VAT with the
bill's deductible share; the total then equals what you paid by card or cash. A bill with a tip is therefore booked with
several lines even when it has only one VAT rate.

## Flags: what needs your check

A flagged bill is **not an expense yet**. It shows in the section **Needs your check (n)** above the table, newest first,
with the file name, what was read, and a warning for each reason. These bills are not counted in the figures, VAT or
reports until you save them. The common reasons:

| Message | What it means |
|---|---|
| Could not read this receipt | No usable text, for example a photo and no OCR installed. The file is kept. |
| Only part of this bill was read | A long PDF; the total might be on a page that was not read. |
| No total / Several possible totals | No total, or two different candidates. |
| No date / Several possible dates / Check the date | Missing, ambiguous, in the future or older than last year. |
| No vendor found, No VAT rate found | Fill them in. |
| No invoice number found | Required above 250,00 € gross; see below. |
| Printed VAT does not match the amounts | Even one cent of difference. |
| The amounts do not add up to the total / The items do not add up | The printed numbers disagree with each other (or a line was misread). |
| The payment does not match the total | Change, card amount or amount due disagree with the total. |
| Nothing on the bill confirms this total | No independent check passed. |
| Check how and when this was paid | Prepayment, or cash and open-invoice signals together. |
| Unusual VAT rate | A rate other than 0, 7 or 19 %. **Luxembourg and other foreign VAT rates (for example 17 % or 14 %) always stay flagged** so that you decide how to treat them with your tax adviser. |
| Not in euros | Another currency. |
| Looks like a refund or credit note, Reverse charge bill | Always checked by a person. |
| Maybe already booked | Same vendor, date and amount as an existing bill. |

## The check dialog

Click a flagged bill. The normal bill dialog opens with the draft filled in, titled **Check receipt**. Above the form you
see the reasons and a table of the **checks** (printed value against computed value, with failed rows marked in words).
The stored file is shown next to the form.

- **Save** books the bill with what is in the form.
- **Discard receipt** deletes the draft and the stored file.
- Above 250,00 € without an invoice number a checkbox *This bill has no invoice number* must be ticked before you can
  save (up to 250,00 € a bill without a number is fine, see § 33 UStDV in [compliance.md](compliance.md)).
- If you change the amounts so that the total differs from the printed total, a checkbox *Book this although it differs
  from the printed total of X* must be ticked.

### Detected fields on the bill

The preview draws a labelled box around every value that was read (vendor, date, invoice number, VAT id, totals, VAT
rows, due date, paid date, tendered, change). A box of a failed check says so in its label, not only by colour. Click or
focus a form field and its box is highlighted; hover or focus a box to see the value read. The toggle *Show detected
fields* in the zoom bar switches the boxes off (on by default in Check receipt). Values read without a position, and
fields that were expected but not found, are listed under the preview as *Not marked on the bill*. PDFs are shown as page
images with page buttons; the original stays one click away. E-invoices have no picture and show only the checks table.

## Text recognition (OCR)

PDFs with a text layer and e-invoices need nothing extra. For **photos and scanned PDFs without text** permitA38 uses the
free program [Tesseract](https://github.com/tesseract-ocr/tesseract) with German and English. Without it, photos are still
kept and flagged *Could not read this receipt*; you enter the values by hand. A note on the Expenses page says when OCR is
not installed. Tesseract is weaker than a PDF text layer on creased till receipts and crooked photos, so such bills are
more often flagged. Hold the phone straight above the receipt in good light.

- **Docker**: the image contains Tesseract; nothing to do.
- **Windows (downloaded release)**: the release stays small. A boss sees **Download OCR** on the Expenses page, with the
  size (about 59 MB) and the host (GitHub) shown before anything happens. The program is downloaded once, checked against a fixed
  checksum that is part of the permitA38 release, and unpacked for this device only (`%LOCALAPPDATA%\permitA38\ocr`, never
  into your data folder). Nothing is downloaded automatically, and no other network request is made for this feature. A
  new permitA38 release with a newer package offers the download again; the old copy is deleted afterwards. The button
  appears only once a release ships with a package.
- **Linux**: install the package, for example `sudo dnf install tesseract tesseract-langpack-deu` or
  `sudo apt install tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng`.
- **Anywhere**: if you installed Tesseract somewhere unusual, set the path in the per-device `config.yaml`:

```yaml
scan:
  enabled: true            # false switches the receipt scan off; uploads then answer 503
  tesseract_path: null     # null: search PATH, then the downloaded copy
  languages: deu+eng       # OCR counts as available only if these are installed
  timeout_s: 120
  max_concurrent: 2
```

## Privacy and limits

Reading runs in a separate process on your machine with a time limit; the text it reads is not stored or logged, only the
fields shown in the dialog are. A receipt can never delete a file or change other records. Foreign currencies, itemised
expense lines, supplier matching and assignment to a project or trip are not part of the scan; use the bill dialog after
saving. Developer details: [specs/receipt-scan.md](specs/receipt-scan.md).
