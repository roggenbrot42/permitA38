"""Tiny dependency-free PDF writer (A4, Helvetica, optional JPEG logo)."""

import unicodedata
import zlib

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


class Pdf:
    W, H = 595.28, 841.89

    def __init__(self):
        self.pages = []
        self.images = []  # (data, w, h)
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

    def jpeg(self, data, x, y, max_w, max_h):
        """Place a JPEG; returns False if the data is not a readable JPEG."""
        w = h = None
        i = 2
        try:
            while i < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                m = data[i + 1]
                if m in (0xC0, 0xC1, 0xC2):
                    h, w = int.from_bytes(data[i + 5 : i + 7], "big"), int.from_bytes(data[i + 7 : i + 9], "big")
                    break
                i += 2 + int.from_bytes(data[i + 2 : i + 4], "big")
        except Exception:
            return False
        if not w or data[:2] != b"\xff\xd8":
            return False
        k = min(max_w / w, max_h / h)
        self.images.append((data, w, h))
        n = len(self.images)
        self.cur.append(("q %.2f 0 0 %.2f %.2f %.2f cm /Im%d Do Q" % (w * k, h * k, x, self.H - y - h * k, n)).encode())
        return True

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
        for data, w, h in self.images:
            img_ids.append(
                add(
                    b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
                    b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n"
                    % (w, h, len(data))
                    + data
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
