"""Tiny dependency-free PDF writer (A4, Helvetica, optional JPEG/PNG logo)."""

import hashlib
import math
import unicodedata
import zlib
from collections import OrderedDict

# Helvetica / Helvetica-Bold advance widths for ASCII 32..126
_W = {
    False: [
        278,
        278,
        355,
        556,
        556,
        889,
        667,
        191,
        333,
        333,
        389,
        584,
        278,
        333,
        278,
        278,
        556,
        556,
        556,
        556,
        556,
        556,
        556,
        556,
        556,
        556,
        278,
        278,
        584,
        584,
        584,
        556,
        1015,
        667,
        667,
        722,
        722,
        667,
        611,
        778,
        722,
        278,
        500,
        667,
        556,
        833,
        722,
        778,
        667,
        778,
        722,
        667,
        611,
        722,
        667,
        944,
        667,
        667,
        611,
        278,
        278,
        278,
        469,
        556,
        333,
        556,
        556,
        500,
        556,
        556,
        278,
        556,
        556,
        222,
        222,
        500,
        222,
        833,
        556,
        556,
        556,
        556,
        333,
        500,
        278,
        556,
        500,
        722,
        500,
        500,
        500,
        334,
        260,
        334,
        584,
    ],
    True: [
        278,
        333,
        474,
        556,
        556,
        889,
        722,
        238,
        333,
        333,
        389,
        584,
        278,
        333,
        278,
        278,
        556,
        556,
        556,
        556,
        556,
        556,
        556,
        556,
        556,
        556,
        333,
        333,
        584,
        584,
        584,
        611,
        975,
        722,
        722,
        722,
        722,
        667,
        611,
        778,
        722,
        278,
        556,
        722,
        611,
        833,
        722,
        778,
        667,
        778,
        722,
        667,
        611,
        722,
        667,
        944,
        667,
        667,
        611,
        333,
        278,
        333,
        584,
        556,
        333,
        556,
        611,
        556,
        611,
        556,
        333,
        611,
        611,
        278,
        278,
        556,
        278,
        889,
        611,
        611,
        611,
        611,
        389,
        556,
        333,
        611,
        556,
        778,
        556,
        556,
        500,
        389,
        280,
        389,
        584,
    ],
}


def _ch(c):
    if 32 <= ord(c) < 127:
        return c
    base = unicodedata.normalize("NFD", c)[0]
    return base if 32 <= ord(base) < 127 else "n"


def text_width(s, size, bold=False):
    return sum(_W[bold][ord(_ch(c)) - 32] for c in s) * size / 1000.0


def _esc(s):
    b = s.encode("cp1252", "replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _rgb(h):
    h = (h or "#000000").lstrip("#")
    return " ".join("%.3f" % (int(h[i : i + 2], 16) / 255) for i in (0, 2, 4))


MM = 72 / 25.4

# JPEG start-of-frame markers (C4, C8 and CC are not frames)
_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})


_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_PNG_MAX_SIDE = 4000
_PNG_MAX_PIXELS = 4_000_000
_PNG_MAX_IDAT = 15 * 1024 * 1024
_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_PNG_CACHE_MAX = 8
_PNG_CACHE: "OrderedDict[bytes, dict | None]" = (
    OrderedDict()
)  # sha256 digest -> image record (None: unusable), at most _PNG_CACHE_MAX entries


def _unpack_table(depth):
    per = 8 // depth
    mask = (1 << depth) - 1
    return [bytes((b >> (8 - depth * (i + 1))) & mask for i in range(per)) for b in range(256)]


def _png_unfilter(raw, height, stride, bpp):
    """Undo the PNG row filters; returns height*stride bytes, or None for an unknown filter byte."""
    out = bytearray(height * stride)
    prev: bytes | bytearray = bytes(stride)
    pos = 0
    for y in range(height):
        ft = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += 1 + stride
        if ft == 2:
            line = bytearray((v + b) & 255 for v, b in zip(line, prev))
        elif ft in (1, 3, 4):
            for ch in range(bpp):  # each channel is an independent sequence of left neighbours
                cur = line[ch::bpp]
                up = prev[ch::bpp]
                res = bytearray(len(cur))
                a = c = i = 0
                if ft == 1:
                    for v in cur:
                        a = (a + v) & 255
                        res[i] = a
                        i += 1
                elif ft == 3:
                    for v, b in zip(cur, up):
                        a = (v + ((a + b) >> 1)) & 255
                        res[i] = a
                        i += 1
                else:
                    for v, b in zip(cur, up):
                        pa = b - c
                        pb = a - c
                        pc = pa + pb
                        if pa < 0:
                            pa = -pa
                        if pb < 0:
                            pb = -pb
                        if pc < 0:
                            pc = -pc
                        a = (v + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 255
                        res[i] = a
                        c = b
                        i += 1
                line[ch::bpp] = res
        elif ft != 0:
            return None
        out[y * stride : (y + 1) * stride] = line
        prev = line
    return out


def _png_parse(data):
    """Read the PNG container; returns (IHDR body, concatenated IDAT, PLTE, tRNS) or None."""
    n = len(data)
    if n < 8 or data[:8] != _PNG_SIG:
        return None
    pos = 8
    first = True
    ihdr = plte = trns = None
    idat = []
    idat_len = 0
    while True:
        if pos + 12 > n:
            return None  # data ends before IEND
        length = int.from_bytes(data[pos : pos + 4], "big")
        ctype = data[pos + 4 : pos + 8]
        if pos + 12 + length > n:
            return None
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length  # the CRC is not checked
        if first:
            if ctype != b"IHDR" or length != 13:
                return None
            ihdr = body
            first = False
        elif ctype == b"IDAT":
            idat_len += length
            if idat_len > _PNG_MAX_IDAT:
                return None
            idat.append(body)
        elif ctype == b"PLTE" and plte is None:
            plte = body
        elif ctype == b"tRNS" and trns is None:
            trns = body
        elif ctype == b"IEND":
            break
    return ihdr, b"".join(idat), plte, trns


def _png_decode(data):
    parsed = _png_parse(data)
    if parsed is None:
        return None
    ihdr, idat, plte, trns = parsed
    w = int.from_bytes(ihdr[0:4], "big")
    h = int.from_bytes(ihdr[4:8], "big")
    depth, ctype, comp, filt, inter = ihdr[8:13]
    if not (1 <= w <= _PNG_MAX_SIDE and 1 <= h <= _PNG_MAX_SIDE and w * h <= _PNG_MAX_PIXELS):
        return None
    if comp or filt or inter or ctype not in _PNG_CHANNELS or not idat:
        return None
    if depth != 8 and not (ctype == 3 and depth in (1, 2, 4)):
        return None
    ch = _PNG_CHANNELS[ctype]
    stride = math.ceil(w * ch * depth / 8)
    expected = h * (1 + stride)
    d = zlib.decompressobj()
    raw = d.decompress(idat, expected + 1)  # the cap stops decompression bombs
    if len(raw) != expected or d.unconsumed_tail:
        return None
    pix = _png_unfilter(raw, h, stride, max(1, ch * depth // 8))
    if pix is None:
        return None
    alpha = None
    if ctype == 3:
        if not plte or len(plte) % 3 or len(plte) > 768:
            return None
        if depth == 8:
            idx = bytes(pix)
        else:
            tab = _unpack_table(depth)
            idx = b"".join(b"".join(tab[b] for b in pix[y * stride : (y + 1) * stride])[:w] for y in range(h))
        if max(idx) >= len(plte) // 3:
            return None
        colour = idx
        space = b"[/Indexed /DeviceRGB %d <%s>]" % (len(plte) // 3 - 1, plte.hex().encode())
        if trns:
            t = bytes(trns[:256])
            alpha = idx.translate(t + b"\xff" * (256 - len(t)))
    elif ctype == 0:
        colour = bytes(pix)
        space = b"/DeviceGray"
        if trns and len(trns) >= 2:
            gkey = int.from_bytes(trns[:2], "big")
            if gkey < 256:
                alpha = colour.translate(bytes(0 if i == gkey else 255 for i in range(256)))
    elif ctype == 2:
        colour = bytes(pix)
        space = b"/DeviceRGB"
        if trns and len(trns) >= 6:
            key = [int.from_bytes(trns[i : i + 2], "big") for i in (0, 2, 4)]
            if all(k < 256 for k in key):
                m = -1
                for c in range(3):
                    tab = bytes(1 if i == key[c] else 0 for i in range(256))
                    m &= int.from_bytes(colour[c::3].translate(tab), "big")
                alpha = m.to_bytes(w * h, "big").translate(bytes([255, 0]) + b"\xff" * 254)
    elif ctype == 4:
        colour = bytes(pix[0::2])
        space = b"/DeviceGray"
        alpha = bytes(pix[1::2])
    else:
        rgb = bytearray(w * h * 3)
        for c in range(3):
            rgb[c::3] = pix[c::4]
        colour = bytes(rgb)
        space = b"/DeviceRGB"
        alpha = bytes(pix[3::4])
    smask = None
    if alpha is not None and alpha.count(255) != len(alpha):
        smask = zlib.compress(alpha, 6)
    return {
        "data": zlib.compress(colour, 6),
        "w": w,
        "h": h,
        "space": space,
        "filter": b"/FlateDecode",
        "decode": b"",
        "smask": smask,
    }


def _png_record(data):
    """Decoded PNG as an image record, cached by content hash; None if unsupported. Never raises."""
    try:
        key = hashlib.sha256(data).digest()
        if key in _PNG_CACHE:
            _PNG_CACHE.move_to_end(key)
            rec = _PNG_CACHE[key]
        else:
            try:
                rec = _png_decode(data)
            except Exception:
                rec = None
            _PNG_CACHE[key] = rec
            while len(_PNG_CACHE) > _PNG_CACHE_MAX:
                _PNG_CACHE.popitem(last=False)
        return dict(rec) if rec else None
    except Exception:
        return None


class Pdf:
    W, H = 595.28, 841.89

    def __init__(self):
        self.pages = []
        self.images = []  # dicts: data, w, h, space, filter, decode, smask (compressed gray stream or None)
        self.new_page()

    def new_page(self):
        self.pages.append([])
        self.cur = self.pages[-1]

    # y is measured from the TOP of the page in points
    def text(self, x, y, s, size=10, bold=False, color="#000000", align="l"):
        w = text_width(s, size, bold)
        if align == "r":
            x -= w
        elif align == "c":
            x -= w / 2
        self.cur.append(
            b"BT /%s %.2f Tf %s rg %.2f %.2f Td ("
            % (b"F2" if bold else b"F1", size, _rgb(color).encode(), x, self.H - y)
            + _esc(s)
            + b") Tj ET"
        )

    def line(self, x1, y1, x2, y2, color="#000000", width=0.5):
        self.cur.append(
            ("%s RG %.2f w %.2f %.2f m %.2f %.2f l S" % (_rgb(color), width, x1, self.H - y1, x2, self.H - y2)).encode()
        )

    def rect(self, x, y, w, h, fill):
        self.cur.append(("%s rg %.2f %.2f %.2f %.2f re f" % (_rgb(fill), x, self.H - y - h, w, h)).encode())

    def wrap(self, s, width, size=10, bold=False):
        lines = []
        for para in str(s).split("\n"):
            cur = ""
            for word in para.split(" "):
                t = (cur + " " + word) if cur else word
                if text_width(t, size, bold) <= width or not cur:
                    cur = t
                else:
                    lines.append(cur)
                    cur = word
            lines.append(cur)
        return lines

    @staticmethod
    def _jpeg_info(data):
        """Read (width, height, components, adobe) from the JPEG markers; None if unusable. Never raises."""
        n = len(data)
        if n < 4 or data[:2] != b"\xff\xd8":
            return None
        adobe = False
        i = 2
        while i < n:
            if data[i] != 0xFF:
                return None  # garbage between segments
            while i < n and data[i] == 0xFF:  # fill bytes
                i += 1
            if i >= n:
                return None
            m = data[i]
            i += 1
            if m == 0x01 or m == 0x00 or 0xD0 <= m <= 0xD7:
                continue  # standalone marker without a length
            if m in (0xD9, 0xDA):  # EOI / SOS before any SOF
                return None
            if i + 2 > n:
                return None
            seg = int.from_bytes(data[i : i + 2], "big")
            if seg < 2 or i + seg > n:
                return None
            body = data[i + 2 : i + seg]
            if m == 0xEE and body[:5] == b"Adobe":
                adobe = True
            if m in _SOF_MARKERS:
                if len(body) < 6 or m not in (0xC0, 0xC1, 0xC2) or body[0] != 8:
                    return None  # only baseline/extended/progressive Huffman with 8-bit samples fit DCTDecode
                h = int.from_bytes(body[1:3], "big")
                w = int.from_bytes(body[3:5], "big")
                if not w or not h:
                    return None
                return w, h, body[5], adobe
            i += seg
        return None

    def _place(self, rec, x, y, max_w, max_h):
        k = min(max_w / rec["w"], max_h / rec["h"])
        self.images.append(rec)
        n = len(self.images)
        self.cur.append(
            (
                "q %.2f 0 0 %.2f %.2f %.2f cm /Im%d Do Q"
                % (rec["w"] * k, rec["h"] * k, x, self.H - y - rec["h"] * k, n)
            ).encode()
        )
        return True

    def jpeg(self, data, x, y, max_w, max_h):
        """Place a JPEG; returns False if the data is not a readable JPEG."""
        info = self._jpeg_info(data)
        if info is None:
            return False
        w, h, nf, adobe = info
        space = {1: b"/DeviceGray", 3: b"/DeviceRGB", 4: b"/DeviceCMYK"}.get(nf)
        if space is None:
            return False
        decode = b" /Decode [1 0 1 0 1 0 1 0]" if nf == 4 and adobe else b""
        rec = {"data": data, "w": w, "h": h, "space": space, "filter": b"/DCTDecode", "decode": decode, "smask": None}
        return self._place(rec, x, y, max_w, max_h)

    def png(self, data, x, y, max_w, max_h):
        """Place a PNG (transparency becomes a soft mask); False if unsupported. Never raises."""
        rec = _png_record(data)
        if rec is None:
            return False
        return self._place(rec, x, y, max_w, max_h)

    def image(self, data, x, y, max_w, max_h):
        """Place a JPEG or PNG logo, chosen by its magic bytes; False for anything else."""
        if data[:8] == _PNG_SIG:
            return self.png(data, x, y, max_w, max_h)
        if data[:2] == b"\xff\xd8":
            return self.jpeg(data, x, y, max_w, max_h)
        return False

    def output(self):
        objs = []  # list of bytes bodies; object numbers are index+1

        def add(b):
            objs.append(b)
            return len(objs)

        add(b"")  # 1 catalog placeholder
        add(b"")  # 2 pages placeholder
        f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        img_ids = []
        for rec in self.images:
            sm = b""
            if rec["smask"] is not None:
                sid = add(
                    b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
                    b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n"
                    % (rec["w"], rec["h"], len(rec["smask"]))
                    + rec["smask"]
                    + b"\nendstream"
                )
                sm = b" /SMask %d 0 R" % sid
            img_ids.append(
                add(
                    b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s "
                    b"/BitsPerComponent 8%s%s /Filter %s /Length %d >>\nstream\n"
                    % (rec["w"], rec["h"], rec["space"], rec["decode"], sm, rec["filter"], len(rec["data"]))
                    + rec["data"]
                    + b"\nendstream"
                )
            )
        xo = b" ".join(b"/Im%d %d 0 R" % (i + 1, n) for i, n in enumerate(img_ids))
        page_ids = []
        for content in self.pages:
            raw = zlib.compress(b"\n".join(content))
            cid = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(raw) + raw + b"\nendstream")
            page_ids.append(
                add(
                    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents %d 0 R "
                    b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> /XObject << %s >> >> >>"
                    % (self.W, self.H, cid, f1, f2, xo)
                )
            )
        objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objs[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
            b" ".join(b"%d 0 R" % p for p in page_ids),
            len(page_ids),
        )
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offs = []
        for i, b in enumerate(objs):
            offs.append(len(out))
            out += b"%d 0 obj\n" % (i + 1) + b + b"\nendobj\n"
        x = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        for o in offs:
            out += b"%010d 00000 n \n" % o
        out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
        return bytes(out)
