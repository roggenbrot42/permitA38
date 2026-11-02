"""Real end-to-end check of the published OCR package (CI on windows-latest only, see .github/workflows/ci.yaml)."""

import os

import pytest

from permita38.core.receipts import ocr, ocr_tools, pagepng, pdfread, worker
from tests.backend.receipt_helpers import receipt_pdf

pytestmark = pytest.mark.skipif(os.environ.get("PERMITA38_OCR_DOWNLOAD") != "1", reason="downloads 59 MB; CI only")


def test_download_install_and_read_a_fake_png(tmp_path):
    pkg = ocr_tools.package()
    assert pkg is not None
    exe = ocr_tools.install(pkg, tmp_path / "ocr")
    assert exe.is_file() and ocr_tools.find_installed(tmp_path / "ocr") == exe
    assert ocr.available(str(exe), "eng")
    src = tmp_path / "r.pdf"
    src.write_bytes(receipt_pdf(lines=[("Cafe Total 12,50", 60, 120, 24), ("Invoice 2026 Thanks", 60, 300, 24)]))
    w, h, ch, pixels = pdfread.render_raster(pdfread.open_pdf(str(src))[0], 300, gray=False)
    png = tmp_path / "r.png"
    png.write_bytes(pagepng.encode_png(w, h, ch, pixels))
    res = worker.read(str(png), languages="deu+eng", timeout_s=120, tesseract=str(exe))
    assert res.engine == "ocr"
    assert {"Total", "Invoice"} <= {x.text for x in res.words}
