# Company logo on documents (issue #202)

Status: decided (architect), revision 2. Review tier **Full** (uploads, parsing of outside input).

## Problem

`documents.render` draws the logo with `Pdf.jpeg` (`core/pdf.py`), a dependency-free JPEG embedder. Reproduced on
`main` (unchanged since 0.4.0-alpha.6):

1. **PNG (and any non-JPEG) logo: no logo, silently.** The upload field says `accept="image/jpeg"`, but the Windows
   file dialog offers "All files", and most logos are PNG or SVG.
2. Greyscale and CMYK JPEGs rendered as garbage (always `/DeviceRGB`). Fixed in revision 1 and kept.
3. The settings page never shows the stored logo, so there is no way to check an upload.

**Owner expectation (revision 2):**
- SVG and PNG logos with **transparency** are accepted and printed transparent. JPEG is a poor format for logos.
- The logo is shown in Settings once it is chosen and once it is stored.

## Decision

No new dependency.

- The **browser** normalises every chosen logo, SVG included, into an 8-bit RGBA **PNG** sized for print.
- The **server** embeds PNG with a soft mask (`/SMask`) for transparency, and JPEG as before.
- SVG is rasterised in the browser, because PDF has no SVG and a vector SVG→PDF renderer is out of scope. The print size
  gives about 500 dpi, so print quality is not visibly worse.
- The original SVG is not stored. Attachments are served from our origin, and an SVG there is active content.

### A. Web (`web/app2.js`)

- `logoAsJpeg` is replaced by `logoAsPng(file)`:
  - **Decode.**
    - SVG (`image/svg+xml`, or a name ending in `.svg`): load it into an `Image` from a `blob:` URL (the CSP allows
      `blob:` images), wait for `decode()`, then revoke the URL.
    - SVG without intrinsic size (`naturalWidth`/`naturalHeight` 0): use 900×360, and draw it into that box with its
      aspect ratio kept.
    - Every other file: `createImageBitmap(file)`. Close it in a `finally`.
  - **Target size.** Scale to fit inside **900×360 px**, keeping the aspect ratio, never upscaling (each side at least 1).
    The logo box on the page is 45×18 mm, so this is about 500 dpi.
    - SVG is the exception: it is always scaled to fill that box, because vector art has no "native" size.
  - **Draw.** Draw on a transparent canvas (no fill) and call `canvas.toBlob(cb, 'image/png')`. Return
    `new File([blob], 'logo.png', { type: 'image/png' })`.
  - **Errors.** On any decode or encode failure, throw `Error('This image cannot be used as a logo. Choose a PNG, SVG or JPEG file.')`.
    `saveSettings` shows it as an error toast and saves nothing (as in revision 1).
- **Upload field.** `accept="image/png,image/svg+xml,image/jpeg,image/webp,image/gif"`, label
  `Logo for documents (PNG, SVG or JPEG)`.
- **Preview in the "Bank and logo" card.** It sits on a light checkerboard background, so that transparency is visible.
  - **Stored logo.** When `s.logo` is set, an `<img id="logo_preview" alt="Current logo">` loads
    `api.url('attachments/' + encodeURIComponent(s.logo))`. The browser sends the session cookie, as for other
    downloads. Maximum display size is 240×96 CSS px, with `object-fit: contain`.
  - **Chosen file.** On `change` of `#logo_file`, the chosen file goes through `logoAsPng`. The result is shown in the
    same `<img>`, from a `blob:` URL that is revoked when replaced or re-rendered. The text below says
    `Not saved yet – click Save to use this logo.`
    - A decode failure shows the error text in the card instead, and clears the file input.
  - **Save.** The PNG produced for the preview is the one uploaded. Keep it in a module variable and do not encode
    twice.
- **Remove logo.** A button `Remove logo` (`data-a="removeLogo"`) is shown only when a logo is stored. It sets `logo`
  to `null`, saves the settings with the same `PUT`, and re-renders. The attachment file itself is left alone, because
  the integrity check reports unused files.
- **Hint.** When `s.logo` is set and ends in neither `.png`, `.jpg` nor `.jpeg`, the card shows `#logo_hint` with
  `The stored logo cannot be printed. Choose the file again; it is converted automatically.`
- **Styles.** The checkerboard and the preview box go into `web/style.css`, as small classes `logo-preview` and
  `logo-checker`. Use the existing `--warn` variable and a `.warn` text class, adding `.warn { color: var(--warn) }`
  if it is missing. No inline styles.
- **Docs.** In `docs/getting-started.md`, the logo is PNG, SVG or JPEG, transparency is kept, and the logo is shown in
  Settings.

### B. Server (`permita38/core/pdf.py`, `permita38/core/documents.py`)

- **`Pdf.jpeg`.** Unchanged from revision 1: colour space from `Nf`, Adobe `/Decode`, only C0–C2 at precision 8.
- **New `Pdf.png(data, x, y, max_w, max_h) -> bool`.** Same placement maths as `jpeg`. It returns `False` and never
  raises on anything it does not support.
  - **Container.**
    - Signature `89 50 4E 47 0D 0A 1A 0A`.
    - Read chunks by length. Do not check CRCs; a wrong CRC is ignored.
    - Stop at `IEND`, or `False` when the data ends before `IEND`.
    - `IHDR` must be the first chunk, with length 13.
    - Concatenate the `IDAT` chunks.
    - Use `PLTE` and `tRNS` when present.
    - Ignore every other chunk.
  - **Supported formats.**
    - Bit depth 8 with colour type 0, 2, 4 or 6.
    - Colour type 3 (palette) with bit depth 1, 2, 4 or 8.
    - Compression 0, filter 0, interlace 0.
    - Anything else → `False`, including 16-bit and Adam7.
  - **Limits.**
    - Width and height between 1 and 4000.
    - `width × height ≤ 4,000,000`.
    - The total `IDAT` length is at most 15 MB, the upload limit.
    - Decompress with `zlib.decompressobj().decompress(data, expected + 1)`, where
      `expected = height × (1 + ceil(width × channels × bitdepth / 8))`.
    - More than `expected` bytes, or fewer → `False`. This prevents decompression bombs.
  - **Unfilter.** Row filters 0–4 (None, Sub, Up, Average, Paeth) per the PNG spec, with `bpp = max(1, channels × bitdepth // 8)`.
    An unknown filter byte → `False`.
  - **Output.**
    - Opaque types: type 0 → `/DeviceGray`, 2 → `/DeviceRGB`.
    - Palette: `[/Indexed /DeviceRGB hival <hex palette>]`, with the packed indices unpacked to one byte per pixel.
    - Colour data is recompressed with `zlib.compress(…, 6)` and written as `/Filter /FlateDecode`, without a predictor.
    - Alpha comes from type 4 or 6, from a palette's `tRNS`, or from a type 0/2 `tRNS` colour key. It is split into a
      separate 8-bit `/DeviceGray` image object referenced as `/SMask n 0 R` from the colour image.
    - When every alpha value is 255, no SMask is written.
  - **Caching.** Results are kept in a module-level cache keyed by `hashlib.sha256(data).digest()`, holding at most 8
    entries. Each entry holds the colour stream, the colour space, the optional SMask stream, and the width and height.
    The cache exists because the logo is decoded once and then reused across renders.
- **`self.images`.** Entries become small records (a dict or dataclass) with `data`, `w`, `h`, `colour space`,
  `filter`, an optional `decode` array and an optional `smask`, where `smask` holds the gray data and its filter.
  - `output()` writes the SMask object before the colour image and references it.
  - The PDF header stays at 1.4, since SMask is PDF 1.4.
- **New `Pdf.image(data, …) -> bool`.** Dispatches on the magic bytes, to `jpeg` or `png`, else `False`.
  - `documents.render` calls `pdf.image(...)` instead of `pdf.jpeg(...)`.
  - The placement box (right-aligned 45×18 mm at the top) is unchanged.

### Out of scope

- Vector SVG in the PDF.
- Server-side SVG handling of any kind: the upload route stays as it is, and the web never uploads SVG for the logo.
- Pillow.
- A settings sanity warning.
- Rejecting a logo on `PUT /settings`.

## Tests

- **Backend** (`tests/backend/test_pdf_logo.py`, extended, stdlib only, no PIL):
  - **PNG fixtures** come from a helper that writes valid PNGs with `zlib` and `struct`. The helper takes the type,
    depth, rows and filter bytes, plus an optional `PLTE`/`tRNS`.
  - **Formats.**
    - Each supported type and depth embeds with the right `/ColorSpace`.
    - Types 4 and 6, palette with `tRNS`, and RGB with a `tRNS` colour key produce `/SMask`.
    - Fully opaque RGBA produces no `/SMask`.
  - **Exact unfilter oracle.** A 3×2 RGBA image is written once with each filter type 0–4, computed by hand in the test
    with literal expected bytes. Decompressing the emitted colour and SMask streams gives exactly the literal RGB and
    alpha bytes.
  - **Palette.** A 2-bit palette image unpacks to the literal index bytes.
  - **Refused, returning `False` with no image added:**
    - 16-bit;
    - interlaced;
    - an unknown filter byte;
    - an `IHDR` that is not first;
    - truncated data or a missing `IEND`;
    - width 0 or 4001;
    - a decompression bomb (`IHDR` 10×10 with an `IDAT` that inflates to more than `expected`);
    - too few bytes;
    - JPEG bytes passed to `png()`.
  - **`Pdf.image`** dispatches JPEG and PNG and refuses `GIF89a…`.
  - **API test.** Upload an RGBA PNG as `logo.png`, set it as the logo, and fetch an invoice PDF. The PDF contains
    `/SMask`. The existing JPEG API test stays.
- **Web** (`tests/web/test_settings_logo_ui.py`, rewritten):
  - **RGBA PNG** with a transparent half: choosing it shows `#logo_preview` with a `blob:` src and the "Not saved yet"
    text.
    - After saving, the stored logo ends in `.png`.
    - Decoding it in the page gives a transparent pixel (alpha ≤ 10) in the transparent half and the fixture colour
      (±12) in the opaque half.
    - After a reload, `#logo_preview` src points at `attachments/`, and the image has loaded (`naturalWidth > 0`).
    - The invoice PDF contains `/SMask`.
  - **SVG.** A small inline SVG with `width="200" height="80"`, a coloured rect over half of it and nothing in the
    other half, becomes a 900×360 PNG. One pixel in the empty half is transparent, and one in the rect has the rect's
    colour.
  - **Scaling.** A 1800×100 PNG becomes 900×50.
  - **Bad file.** Text bytes named `logo.png` show the error, and the settings stay unchanged.
  - **Remove logo.** The button clears `logo`, and the preview disappears.
  - **Hint.** It is shown for a stored `.gif` attachment, set through the API.
