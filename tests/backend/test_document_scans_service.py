"""DocumentScanService (docs/specs/document-scan.md): upload, flags, confirm, discard, duplicates, batch undo.

All names, numbers and amounts are invented. Oracle numbers are computed by hand in the comments."""

import threading
import time
from datetime import date

import pytest

from permita38.core.errors import ServiceError
from permita38.core.models import DocumentScanConfirmIn
from permita38.core.receipts import runner
from permita38.core.service import document_scans as ds_mod
from permita38.core.service.document_scans import DocumentScanService
from permita38.core.service.importer import ImportService
from permita38.core.service.numbering import NumberingService

from .test_docrules import INVOICE, OFFER_BODY, doc, read, swap

TODAY = date(2026, 11, 3)
ME = {"id": "u1"}
real_read_file = runner.read_file


class Reader:
    """Stands in for the worker: the content of a stored file decides what was 'read' from it."""

    def __init__(self):
        self.by_content = {}
        self.delay = 0.0

    def add(self, rows, tag: str = "") -> bytes:
        content = b"%PDF-1.4 fake " + (tag or "|".join(map(str, rows))).encode()
        self.by_content[content] = read(doc(rows))
        return content

    def __call__(self, path, config):
        with open(path, "rb") as f:
            data = f.read()
        if self.delay:
            time.sleep(self.delay)
        return self.by_content.get(data) or real_read_file(path, config)


@pytest.fixture
def reader(monkeypatch):
    r = Reader()
    monkeypatch.setattr(ds_mod.runner, "read_file", r)
    return r


@pytest.fixture
def env(boss_client, app, reader):
    store = app.state.services.store
    store.save_settings(
        {
            **store.settings(),
            "company_name": "Max Muster IT-Beratung",
            "owner_name": "Max Muster",
            "vat_id": "DE123456789",
            "payment_days": 30,
        }
    )
    numbering = NumberingService(store, clock=lambda: date(2024, 3, 10))
    svc = DocumentScanService(store, app.state.services.config, numbering, today=lambda: TODAY)
    return svc


def next_number(svc, coll="invoices"):
    return svc.numbering.preview(coll, 1)[0]


def set_shared(svc, shared: bool):
    st = svc.store.settings()
    svc.store.save_settings({**st, "numbering_shared": shared})


def codes(scan):
    return [f["code"] for f in scan["flags"]]


def body(**over):
    data = {
        "kind": "invoice",
        "number": "RE-2024-007",
        "date": "2024-03-05",
        "customer": {"name": "Beispiel Kunde GmbH", "street": "Kundenweg 5", "zip": "54321", "city": "Kundenstadt"},
        "vat_rate": "19",
        "lines": [
            {"description": "Beratung", "quantity": "10", "unit": "Std", "price": "50.00"},
            {"description": "Entwicklung", "quantity": "4", "unit": "Std", "price": "62.50"},
        ],
    }
    data.update(over)
    return DocumentScanConfirmIn.model_validate(data)


# Net 750,00 = 10 x 50,00 + 4 x 62,50; VAT 19 % = 142,50; gross 892,50.
def test_clean_invoice_is_added_and_moves_the_counter(env, reader):
    out = env.upload(ME, "alt.pdf", reader.add(INVOICE))
    assert out["status"] == "added"
    assert out["record"] == {"collection": "invoices", "id": out["record"]["id"], "number": "RE-2024-007", "year": 2024}
    inv = env.store.get("invoices", out["record"]["id"])
    assert (inv["number"], inv["original_number"], inv["imported"], inv["status"]) == (
        "RE-2024-007",
        "RE-2024-007",
        True,
        "sent",
    )
    assert inv["date"] == "2024-03-05" and inv["due_date"] == "2024-03-19"  # printed "14 Tagen"
    assert (inv["service_from"], inv["service_to"]) == ("2024-02-01", "2024-02-29")
    assert inv["vat_rate"] == 19 and [(ln["qty"], ln["price"]) for ln in inv["lines"]] == [(10, 50), (4, 62.5)]
    assert not inv.get("paid_date") and not inv.get("totals_unverified")
    assert inv["external_id"] == f"scan:{out['scan']['id']}" and inv["original_pdf"] == out["scan"]["attachment"]
    assert out["scan"]["status"] == "added" and "sha256" not in out["scan"] and "doc_key" not in out["scan"]
    assert out["scan"]["record_id"] == inv["id"] and out["scan"]["notes"] == [
        {"code": "customer_new", "message": ds_mod.NOTES["customer_new"]},
        {"code": "added_open", "message": ds_mod.NOTES["added_open"]},
    ]
    customer = env.store.get("customers", inv["customer_id"])
    assert (customer["name"], customer["zip"], customer["city"]) == ("Beispiel Kunde GmbH", "54321", "Kundenstadt")
    assert next_number(env) == "RE-2024-0008"  # the counter follows the original number 7


def test_clean_offer_is_added_as_sent_with_valid_until(env, reader):
    out = env.upload(ME, "alt.pdf", reader.add(OFFER_BODY))
    assert out["status"] == "added" and out["record"]["collection"] == "offers"
    offer = env.store.get("offers", out["record"]["id"])
    assert (offer["number"], offer["status"], offer["valid_until"]) == ("A-2024-012", "sent", "2024-04-30")
    assert "due_date" not in offer
    assert offer["lines"][0]["price"] == 100  # net 100,00; 19 % = 19,00; gross 119,00


def test_an_existing_customer_is_used_and_no_new_one_is_created(env, reader):
    cid = env.store.save("customers", {"name": "Beispiel Kunde GmbH", "city": "Kundenstadt"})["id"]
    out = env.upload(ME, "a.pdf", reader.add(INVOICE))
    assert out["status"] == "added"
    assert env.store.get("invoices", out["record"]["id"])["customer_id"] == cid
    assert len(env.store.list("customers")) == 1 and [n["code"] for n in out["scan"]["notes"]] == ["added_open"]


def test_two_equal_customers_flag_customer_ambiguous(env, reader):
    for _ in range(2):  # two customers with exactly the same name: no unique exact match
        env.store.save("customers", {"name": "Beispiel Kunde GmbH"})
    out = env.upload(ME, "a.pdf", reader.add(INVOICE))
    assert out["status"] == "flagged" and codes(out["scan"]) == ["customer_ambiguous"]
    assert out["scan"]["draft"]["customer_match"]["action"] == "ambiguous"
    assert env.store.list("invoices") == []


def test_a_single_fuzzy_customer_is_only_a_suggestion(env, reader):
    cid = env.store.save("customers", {"name": "Beispiel Kunde AG"})["id"]  # same name, other legal form
    out = env.upload(ME, "a.pdf", reader.add(INVOICE))
    assert out["status"] == "flagged" and codes(out["scan"]) == ["customer_ambiguous"]
    draft = out["scan"]["draft"]
    assert draft["customer_id"] is None and draft["customer_match"] == {"action": "ambiguous", "ids": [cid]}
    assert env.store.list("invoices") == [] and len(env.store.list("customers")) == 1


def test_exact_name_and_vat_id_are_linked(env, reader):
    cid = env.store.save("customers", {"name": "beispiel  kunde GmbH"})["id"]  # exact after normalising
    out = env.upload(ME, "a.pdf", reader.add(INVOICE))
    assert out["status"] == "added" and env.store.get("invoices", out["record"]["id"])["customer_id"] == cid
    # a VAT id match links even with another name
    other = env.store.save("customers", {"name": "Ganz Anders", "vat_id": "DE999999999"})["id"]
    rows = swap(INVOICE, "Rechnungsnummer", "Rechnungsnummer: RE-2024-008")
    rows = swap(rows, "54321 Kundenstadt", ["54321 Kundenstadt", "Kunden USt-IdNr. DE999999999"])
    rows = swap(rows, "USt-IdNr. DE123456789", "USt-IdNr. DE123456789")
    out = env.upload(ME, "b.pdf", reader.add(rows, tag="vat"))
    assert out["status"] == "added"
    assert env.store.get("invoices", out["record"]["id"])["customer_id"] == other
    assert len(env.store.list("customers")) == 2


def test_small_business_settings_conflict_is_flagged(env, reader):
    env.store.save_settings({**env.store.settings(), "kleinunternehmer": True})
    out = env.upload(
        ME, "a.pdf", reader.add(INVOICE)
    )  # printed 750,00 + 142,50 = 892,50; calc gives VAT 0, gross 750,00
    assert out["status"] == "flagged" and codes(out["scan"]) == ["small_business_conflict"]
    assert env.store.list("invoices") == []


def test_totals_mismatch_is_flagged_and_confirm_needs_acceptance(env, reader):
    rows = swap(INVOICE, "Gesamtbetrag", "Gesamtbetrag 900,00 EUR")  # printed 900,00, computed 892,50
    out = env.upload(ME, "a.pdf", reader.add(rows))
    assert out["status"] == "flagged" and codes(out["scan"]) == ["totals_mismatch"]
    assert env.store.list("invoices") == [] and env.store.list("customers") == []
    assert [s["id"] for s in env.list_flagged()] == [out["scan"]["id"]]
    assert env.get(out["scan"]["id"])["draft"]["number"] == "RE-2024-007"
    assert out["scan"]["doc"]["printed_total"] == "900.00" and out["scan"]["doc"]["printed_net"] == "750.00"
    with pytest.raises(ServiceError) as e:
        env.confirm(ME, out["scan"]["id"], body())
    assert e.value.status == 409 and e.value.body == {"code": "needs_acceptance", "missing": ["totals_mismatch"]}
    assert env.store.list("invoices") == [] and env.store.list("customers") == []  # nothing half written
    res = env.confirm(ME, out["scan"]["id"], body(accept_totals_mismatch=True))
    inv = env.store.get("invoices", res["record"]["id"])
    assert inv["totals_unverified"] is True and inv["given_totals"]["gross"] == 900
    assert res["scan"]["status"] == "added" and env.list_flagged() == []
    assert next_number(env) == "RE-2024-0008"
    with pytest.raises(ServiceError) as e2:  # a second click
        env.confirm(ME, out["scan"]["id"], body(accept_totals_mismatch=True))
    assert e2.value.status == 409 and len(env.store.list("invoices")) == 1


def test_confirm_with_an_existing_customer_and_corrected_values(env, reader):
    rows = swap(INVOICE, "Rechnungsdatum", None)  # no date read
    out = env.upload(ME, "a.pdf", reader.add(rows))
    assert codes(out["scan"]) == ["no_date"]
    cid = env.store.save("customers", {"name": "Anderer Kunde"})["id"]
    res = env.confirm(ME, out["scan"]["id"], body(date="2024-03-05", customer=None, customer_id=cid))
    inv = env.store.get("invoices", res["record"]["id"])
    assert inv["customer_id"] == cid and not inv.get("totals_unverified")
    assert inv["due_date"] == "2024-04-04"  # no printed due date for this body: 05.03. + 30 days from Settings
    assert [c["name"] for c in env.store.list("customers")] == ["Anderer Kunde"]
    assert res["record"]["year"] == 2024


def test_confirm_rejects_a_missing_customer(env, reader):
    out = env.upload(ME, "a.pdf", reader.add(swap(INVOICE, "Rechnungsdatum", None)))
    with pytest.raises(ServiceError) as e:
        env.confirm(ME, out["scan"]["id"], body(customer=None, customer_id="abcdef123456"))
    assert e.value.status == 422 and env.store.list("invoices") == []


def test_confirm_model_is_strict():
    with pytest.raises(ValueError):
        body(customer_id="abcdef123456")  # customer and customer_id together
    with pytest.raises(ValueError):
        body(vat_rate="12")
    with pytest.raises(ValueError):
        body(lines=[])
    with pytest.raises(ValueError):
        body(number="")


def test_not_my_document_is_flagged(env, reader):
    env.store.save_settings({**env.store.settings(), "company_name": "Fremde Firma", "owner_name": "", "vat_id": ""})
    out = env.upload(ME, "a.pdf", reader.add(INVOICE))
    assert out["status"] == "flagged" and codes(out["scan"]) == ["not_my_document"]


def test_unreadable_file_is_flagged_and_kept(env):
    out = env.upload(ME, "a.pdf", b"%PDF-1.4 nothing readable here")
    assert out["status"] == "flagged" and "unreadable" in codes(out["scan"])
    assert out["scan"]["engine"] == "none" and env.store.attachment_path(out["scan"]["attachment"])
    assert env.store.list("invoices") == [] and env.store.list("customers") == []
    assert len(env.store.list("document_scans")) == 1 and len(env.store.list_attachments()) == 1


def test_discard(env, reader):
    out = env.upload(ME, "a.pdf", reader.add(swap(INVOICE, "Rechnungsdatum", None)))
    scan_id = out["scan"]["id"]
    attachment = out["scan"]["attachment"]
    env.discard(scan_id)
    assert env.get(scan_id)["status"] == "discarded" and env.list_flagged() == []
    assert env.store.attachment_path(attachment) is None
    stored = env.store.get("document_scans", scan_id)
    assert not stored.get("sha256") and not stored.get("doc_key")
    again = env.upload(ME, "a.pdf", reader.add(swap(INVOICE, "Rechnungsdatum", None)))  # the same file may come again
    assert again["status"] == "flagged" and again["scan"]["id"] != scan_id
    with pytest.raises(ServiceError) as e2:
        env.discard(scan_id)
    assert e2.value.status == 409


def test_an_added_scan_cannot_be_discarded(env, reader):
    out = env.upload(ME, "a.pdf", reader.add(INVOICE))
    with pytest.raises(ServiceError) as e:
        env.discard(out["scan"]["id"])
    assert e.value.status == 409 and len(env.store.list("invoices")) == 1


def test_unknown_scan_is_404(env):
    for call in (lambda: env.get("abcdef123456"), lambda: env.discard("abcdef123456"), lambda: env.page_png("zz", 0)):
        with pytest.raises(ServiceError) as e:
            call()
        assert e.value.status == 404


# ------------------------------------------------------------------ duplicates
def test_same_file_twice_is_409_and_stores_nothing(env, reader):
    content = reader.add(INVOICE)
    first = env.upload(ME, "a.pdf", content)
    files = env.store.list_attachments()
    with pytest.raises(ServiceError) as e:
        env.upload(ME, "b.pdf", content)
    assert e.value.status == 409 and e.value.body["code"] == "duplicate_file"
    assert e.value.body["existing"] == {"collection": "invoices", "id": first["record"]["id"], "number": "RE-2024-007"}
    assert env.store.list_attachments() == files and len(env.store.list("document_scans")) == 1


def test_same_number_in_another_file_is_409_and_the_file_is_removed(env, reader):
    first = env.upload(ME, "a.pdf", reader.add(INVOICE))
    files = env.store.list_attachments()
    other = [*INVOICE, "Seite 1"]  # same number, other bytes
    with pytest.raises(ServiceError) as e:
        env.upload(ME, "b.pdf", reader.add(other))
    assert e.value.status == 409 and e.value.body["code"] == "duplicate_number"
    assert e.value.body["existing"]["id"] == first["record"]["id"]
    assert env.store.list_attachments() == files and len(env.store.list("document_scans")) == 1


def test_number_is_compared_without_case_and_extra_spaces_but_with_dashes(env, reader):
    env.upload(ME, "a.pdf", reader.add(INVOICE))
    assert ds_mod.doc_key("invoices", "RE  2024-007") == ds_mod.doc_key("invoices", "re 2024-007")
    assert ds_mod.doc_key("invoices", "2024-1-23") != ds_mod.doc_key("invoices", "2024-12-3")
    other = swap(INVOICE, "Rechnungsnummer", "Rechnungsnummer: re-2024-007")
    with pytest.raises(ServiceError) as e:
        env.upload(ME, "b.pdf", reader.add(other))
    assert e.value.body["code"] == "duplicate_number"
    nodash = swap(INVOICE, "Rechnungsnummer", "Rechnungsnummer: RE2024007")
    assert env.upload(ME, "c.pdf", reader.add(nodash))["status"] == "added"  # another number


def test_numbers_that_differ_only_in_dash_position_do_not_collide(env, reader):
    one = swap(INVOICE, "Rechnungsnummer", "Rechnungsnummer: 2024-1-23")
    two = swap(INVOICE, "Rechnungsnummer", "Rechnungsnummer: 2024-12-3")
    assert env.upload(ME, "a.pdf", reader.add(one))["status"] == "added"
    assert env.upload(ME, "b.pdf", reader.add(two))["status"] == "added"


def test_shared_sequence_checks_offers_against_invoices(env, reader):
    set_shared(env, True)
    inv = swap(INVOICE, "Rechnungsnummer", "Rechnungsnummer: AN-9")
    off = swap(OFFER_BODY, "Angebotsnummer", "Angebotsnummer: an-9")
    first = env.upload(ME, "a.pdf", reader.add(inv))
    assert first["status"] == "added"
    with pytest.raises(ServiceError) as e:
        env.upload(ME, "b.pdf", reader.add(off))
    assert e.value.body["code"] == "duplicate_number"
    assert (
        e.value.body["existing"]["collection"] == "invoices" and e.value.body["existing"]["id"] == first["record"]["id"]
    )
    assert env.store.list("offers") == []


def test_separate_sequences_allow_the_same_number_in_offers_and_invoices(env, reader):
    set_shared(env, False)
    assert (
        env.upload(ME, "a.pdf", reader.add(swap(INVOICE, "Rechnungsnummer", "Rechnungsnummer: AN-9")))["status"]
        == "added"
    )
    out = env.upload(ME, "b.pdf", reader.add(swap(OFFER_BODY, "Angebotsnummer", "Angebotsnummer: an-9")))
    assert out["status"] == "added" and out["record"]["collection"] == "offers"


def test_a_flagged_scan_blocks_its_number_and_confirm_rechecks(env, reader):
    flagged = env.upload(ME, "a.pdf", reader.add(swap(INVOICE, "Rechnungsdatum", None)))
    assert flagged["status"] == "flagged"
    with pytest.raises(ServiceError) as e:
        env.upload(ME, "b.pdf", reader.add([*INVOICE, "Seite 1"]))
    assert e.value.body["code"] == "duplicate_number" and e.value.body["existing"] == {"scan_id": flagged["scan"]["id"]}
    # a document with that number appears meanwhile (made by hand): confirm is refused
    env.store.save("invoices", {"number": "RE-2024-007", "date": "2024-03-01", "lines": []})
    with pytest.raises(ServiceError) as e2:
        env.confirm(ME, flagged["scan"]["id"], body())
    assert e2.value.status == 409 and e2.value.body["code"] == "duplicate_number"
    assert env.store.list("customers") == []


def test_two_parallel_uploads_of_one_number_add_one(env, reader):
    reader.delay = 0.2
    a = reader.add(INVOICE, tag="a")
    b = reader.add(INVOICE, tag="b")
    barrier = threading.Barrier(2)
    out: list = []

    def go(blob):
        barrier.wait()
        try:
            out.append(env.upload(ME, "x.pdf", blob)["status"])
        except ServiceError as err:
            out.append(err.body["code"])

    threads = [threading.Thread(target=go, args=(blob,)) for blob in (a, b)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(out) == ["added", "duplicate_number"]
    assert len(env.store.list("invoices")) == 1 and len(env.store.list("document_scans")) == 1
    assert len(env.store.list_attachments()) == 1


# ------------------------------------------------------------------ batch undo
def test_batch_undo_removes_the_document_and_its_new_customer(env, reader):
    out = env.upload(ME, "a.pdf", reader.add(INVOICE))
    batch_id = out["scan"]["batch_id"]
    batch = env.store.get("import_batches", batch_id)
    assert batch["kind"] == "scan" and batch["status"] == "imported" and len(batch["records"]) == 2
    res = ImportService(env.store, numbering=env.numbering).undo(batch_id)
    assert res["removed"] == 2
    assert env.store.list("invoices") == [] and env.store.list("customers") == []


def test_import_hooks_run_for_a_scanned_document(env, reader, monkeypatch):
    from permita38.core.service.importer import shared

    seen = []
    monkeypatch.setattr(shared, "IMPORT_HOOKS", [lambda coll, rec: seen.append((coll, rec["number"]))])
    env.upload(ME, "a.pdf", reader.add(INVOICE))
    assert seen == [("invoices", "RE-2024-007")]


def test_scanning_switched_off_is_503(env, reader):
    env.config.scan.enabled = False
    with pytest.raises(ServiceError) as e:
        env.upload(ME, "a.pdf", reader.add(INVOICE))
    assert e.value.status == 503


def test_upload_type_checks(env):
    for name, blob, status in (("a.txt", b"x", 415), ("a.pdf", b"not a pdf", 415)):
        with pytest.raises(ServiceError) as e:
            env.upload(ME, name, blob)
        assert e.value.status == status


def test_batch_undo_frees_the_file_and_the_number(env, reader):
    content = reader.add(INVOICE)
    out = env.upload(ME, "a.pdf", content)
    ImportService(env.store, numbering=env.numbering).undo(out["scan"]["batch_id"])
    again = env.upload(ME, "a.pdf", content)  # the same file
    assert again["status"] == "added" and again["scan"]["id"] != out["scan"]["id"]
    old = env.store.get("document_scans", out["scan"]["id"])
    assert old["status"] == "undone" and not old.get("sha256") and not old.get("doc_key")
    ImportService(env.store, numbering=env.numbering).undo(again["scan"]["batch_id"])
    other = env.upload(ME, "b.pdf", reader.add([*INVOICE, "Seite 1"]))  # the same number in another file
    assert other["status"] == "added" and len(env.store.list("invoices")) == 1


def test_confirm_of_a_discarded_scan_is_409(env, reader):
    out = env.upload(ME, "a.pdf", reader.add(swap(INVOICE, "Rechnungsdatum", None)))
    env.discard(out["scan"]["id"])
    with pytest.raises(ServiceError) as e:
        env.confirm(ME, out["scan"]["id"], body())
    assert e.value.status == 409 and env.store.list("invoices") == []


def test_confirm_with_an_existing_number_is_409_and_writes_nothing(env, reader):
    env.upload(ME, "a.pdf", reader.add(INVOICE))  # RE-2024-007 exists
    flagged = env.upload(ME, "b.pdf", reader.add(swap(OFFER_BODY, "Angebotsnummer", "Angebotsnummer: A-1")))
    assert flagged["status"] == "added"
    pending = env.upload(
        ME,
        "c.pdf",
        reader.add(swap(swap(INVOICE, "Rechnungsnummer", "Rechnungsnummer: RE-2024-050"), "Rechnungsdatum", None)),
    )
    assert pending["status"] == "flagged"
    before = (len(env.store.list("invoices")), len(env.store.list("customers")), len(env.store.list("import_batches")))
    with pytest.raises(ServiceError) as e:
        env.confirm(ME, pending["scan"]["id"], body(number="RE-2024-007"))
    assert e.value.status == 409 and e.value.body["code"] == "duplicate_number"
    assert before == (
        len(env.store.list("invoices")),
        len(env.store.list("customers")),
        len(env.store.list("import_batches")),
    )
    assert env.get(pending["scan"]["id"])["status"] == "flagged"


def test_matching_totals_with_acceptance_are_not_unverified(env, reader):
    out = env.upload(ME, "a.pdf", reader.add(swap(INVOICE, "Rechnungsdatum", None)))
    res = env.confirm(ME, out["scan"]["id"], body(accept_totals_mismatch=True))  # printed 892,50 = computed
    inv = env.store.get("invoices", res["record"]["id"])
    assert not inv.get("totals_unverified") and "given_totals" not in inv


def test_implausible_dates_are_flagged(env, reader):
    d = {"date": "2024-03-05", "due_date": "2024-03-01"}  # due before the invoice date
    assert env._implausible(d) and not env._implausible({"date": "2024-03-05", "due_date": "2024-03-05"})
    assert env._implausible({"date": "2024-03-05", "valid_until": "2024-03-04"})
    assert env._implausible({"service_from": "2024-03-05", "service_to": "2024-03-04"})


def test_a_broken_photo_is_refused_with_422_and_leaves_nothing(env):
    with pytest.raises(ServiceError) as e:
        env.upload(ME, "p.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 40)
    assert e.value.status == 422 and env.store.list_attachments() == []


def test_a_jpg_without_jpeg_magic_is_refused_with_415(env):
    with pytest.raises(ServiceError) as e:
        env.upload(ME, "p.jpg", b"not a jpeg at all" * 4)
    assert e.value.status == 415 and env.store.list_attachments() == []


def test_upload_with_due_date_before_date_is_flagged_and_writes_nothing(env, reader):
    rows = swap(INVOICE, "Zahlbar innerhalb", "Fälligkeitsdatum: 01.03.2024")  # due 01.03. before date 05.03.
    out = env.upload(ME, "a.pdf", reader.add(rows))
    assert out["status"] == "flagged" and codes(out["scan"]) == ["date_implausible"]
    assert env.store.list("invoices") == [] and env.store.list("customers") == []


def test_upload_with_a_unit_over_30_characters_is_flagged_invalid_values(env, reader, monkeypatch):
    original = env._draft

    def long_unit(x):  # the parser caps units at 14 characters, so the draft is made to fail the strict body
        draft = original(x)
        draft["lines"][0]["unit"] = "S" * 31
        return draft

    monkeypatch.setattr(env, "_draft", long_unit)
    out = env.upload(ME, "a.pdf", reader.add(INVOICE))
    assert out["status"] == "flagged" and codes(out["scan"]) == ["invalid_values"]
    assert env.store.list("invoices") == [] and env.store.list("customers") == []


# ------------------------------------------------------------------ counters
def offer_next(svc):
    return svc.numbering.preview("offers", 1)[0]


def test_an_offer_moves_only_the_offer_counter(env, reader):
    set_shared(env, False)
    inv_before, off_before = next_number(env), offer_next(env)
    out = env.upload(ME, "a.pdf", reader.add(swap(OFFER_BODY, "Angebotsnummer", "Angebotsnummer: AN-2024-0012")))
    assert out["status"] == "added" and next_number(env) == inv_before
    assert offer_next(env) != off_before and offer_next(env) == "AN-2024-0013"  # 12 + 1


def test_a_lower_number_other_year_or_format_does_not_move_the_counter(env, reader):
    set_shared(env, False)
    env.upload(ME, "a.pdf", reader.add(INVOICE))
    assert next_number(env) == "RE-2024-0008"
    low = swap(INVOICE, "Rechnungsnummer", "Rechnungsnummer: RE-2024-003")
    assert env.upload(ME, "b.pdf", reader.add(low))["status"] == "added"
    assert next_number(env) == "RE-2024-0008"  # 3 < 7: not moved back
    other_year = swap(
        swap(INVOICE, "Rechnungsnummer", "Rechnungsnummer: RE-2023-040"), "Rechnungsdatum", "Rechnungsdatum: 05.03.2023"
    )
    env.upload(ME, "c.pdf", reader.add(other_year))
    assert next_number(env) == "RE-2024-0008"
    other_format = swap(INVOICE, "Rechnungsnummer", "Rechnungsnummer: XY-99")
    env.upload(ME, "d.pdf", reader.add(other_format))
    assert next_number(env) == "RE-2024-0008"


def test_a_confirmed_flagged_offer_moves_the_offer_counter(env, reader):
    set_shared(env, False)
    flagged = env.upload(
        ME,
        "a.pdf",
        reader.add(swap(swap(OFFER_BODY, "Angebotsnummer", "Angebotsnummer: AN-2024-0020"), "Angebotsdatum", None)),
    )
    assert flagged["status"] == "flagged"
    before = offer_next(env)
    env.confirm(
        ME,
        flagged["scan"]["id"],
        body(
            kind="offer",
            number="AN-2024-0020",
            date="2024-04-01",
            valid_until="2024-04-30",
            lines=[
                {"description": "Konzept", "quantity": "1", "unit": "Pausch", "price": "100.00"}
            ],  # 100,00 + 19,00 = 119,00
        ),
    )
    assert offer_next(env) != before and offer_next(env) == "AN-2024-0021"  # 20 + 1
