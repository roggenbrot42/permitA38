# Company logo on documents (issue #202)

Status: decided (architect), review tier **Full** (uploads, parsing of outside input).

## Problem

`documents.render` draws the logo with `Pdf.jpeg` (`core/pdf.py`), a dependency-free JPEG embedder. Reproduced on
`main` (unchanged since 0.4.0-alpha.6):

1. **PNG (and any non-JPEG) logo: no logo, silently.** `Pdf.jpeg` returns `False` and nothing tells the user. The
   upload field says `accept="image/jpeg"`, but the Windows file dialog offers "All files", and most logos are PNG.
2. **Greyscale and CMYK JPEGs render as garbage** (stripes, wrong colours, or nothing depending on the viewer): the image
   XObject always says `/ColorSpace /DeviceRGB`, whatever the number of components in the JPEG.
3. The marker scan only knows SOF0–SOF2 and does not skip `0xFF` fill bytes.

## Decision

No new dependency (no Pillow) and no PNG decoder on the server. Two parts:

### A. Web: convert the chosen logo to JPEG in the browser (`web/app2.js`)

- New helper `logoAsJpeg(file)` (module-level in `app2.js`):
  - Decode with `createImageBitmap(file)` (the CSP allows `blob:`/`data:` images, but `createImageBitmap` needs neither).
  - Long edge capped at **1200 px**, never upscaled; aspect ratio kept.
  - Draw on a canvas that is first filled **white** (transparency is flattened onto white).
  - `canvas.toBlob(cb, 'image/jpeg', 0.92)`; return `new File([blob], 'logo.jpg', { type: 'image/jpeg' })`.
  - Every chosen file is re-encoded, JPEGs included (this also turns CMYK JPEGs into RGB and drops EXIF).
  - If decoding or encoding fails (SVG, HEIC, a broken file), it throws an `Error` with the message
    `This image cannot be used as a logo. Choose a PNG or JPEG file.`
- `A.saveSettings`: `if (lf) s.logo = (await api.upload(await logoAsJpeg(lf))).name;`. When `logoAsJpeg` throws, show
  the message with the existing error toast and **do not save** the settings (return early).
- Upload field: `accept="image/png,image/jpeg,image/webp,image/gif"`, label `Logo for documents (PNG or JPEG)`.
- Hint for a logo that cannot be printed: when `s.logo` is set and does **not** end in `.jpg` or `.jpeg`
  (case-insensitive), the card shows, instead of "A logo is set…", a paragraph with id `logo_hint` and class
  `small warn` (use an existing warning class if `warn` does not exist):
  `The stored logo is not a JPEG and is not printed on documents. Choose the file again; it is converted automatically.`
- `docs/getting-started.md`: lines that say the logo must be a JPEG now say PNG or JPEG are accepted and converted.

### B. Server: correct JPEG embedding (`permita38/core/pdf.py`)

- Marker scan in `Pdf.jpeg`:
  - Must start with `FF D8`, else `False`.
  - Walk the segments from offset 2. Skip any number of `0xFF` fill bytes before a marker code.
  - Standalone markers without a length (`D0`–`D7`, `01`) are stepped over.
  - SOF markers are `C0`–`C3`, `C5`–`C7`, `C9`–`CB`, `CD`–`CF`. `C4`, `C8` and `CC` are not SOF.
  - SOF payload: precision (1 byte), height (2), width (2), `Nf` (1, the number of components).
  - Stop with `False` on SOS (`DA`) or EOI (`D9`) before any SOF, on truncation, and on width/height 0.
  - Never raise. Any out-of-range read returns `False`.
- Colour space from `Nf`: 1 → `/DeviceGray`, 3 → `/DeviceRGB`, 4 → `/DeviceCMYK`, anything else → `False`.
- CMYK with an Adobe `APP14` segment (marker `EE`, payload starting with `b"Adobe"`): add `/Decode [1 0 1 0 1 0 1 0]`
  (Adobe writes inverted CMYK). CMYK without APP14: no `/Decode`.
- `self.images` entries carry the colour space and the optional decode array, and `output()` writes them.
- `/BitsPerComponent 8` stays. The public signature `jpeg(data, x, y, max_w, max_h) -> bool` stays.
- Placement in `documents.render` is unchanged.

### Out of scope

- Server-side PNG decoding.
- Pillow.
- A settings sanity warning (the web deletes `sanity_warnings` after a settings save).
- Rejecting non-JPEG logos on `PUT /settings`. A hard error would block every later settings save for someone who has a
  PNG stored.

## Tests

- **Backend** (`tests/backend/test_pdf_logo.py`, new, stdlib only: **no PIL**):
  - Hand-built JPEG byte strings (SOI, optional APP0/APP14/fill bytes, SOFn with `Nf`, SOS, EOI). Only markers are
    parsed, so no real scan data is needed.
  - Cases:
    - `Nf` 1, 3 and 4 give the right `/ColorSpace` in `Pdf().output()`.
    - Adobe CMYK gives `/Decode [1 0 1 0 1 0 1 0]`; plain CMYK does not.
    - SOF2 (progressive) and SOF1 are accepted.
    - Fill bytes before a marker are accepted.
    - PNG bytes, an empty string, truncated data, SOS before any SOF, `Nf` 2, and width 0 all return `False` without
      raising, and add no image.
  - One API test (`boss_client`): upload a hand-built 3-component JPEG as `logo.jpg`, set it as the settings logo,
    create an invoice and fetch its PDF. The PDF contains `/Subtype /Image` and `/ColorSpace /DeviceRGB`.
- **Web** (`tests/web/test_settings_logo_ui.py`, new):
  - The PNG fixture is built with `zlib`/`struct`: a 40×20 RGBA image with a transparent area.
  - Pick it in Settings → Bank and logo, then save. `GET /api/settings` → `logo` ends in `.jpg`. The attachment bytes
    start with `FF D8`. An invoice PDF contains `/DeviceRGB`.
  - The hint `#logo_hint` is visible when the stored logo is a `.png` attachment (set through the API) and absent
    after the conversion.
  - A non-image file (e.g. text bytes named `logo.png`) shows the error toast and leaves the settings unchanged.
