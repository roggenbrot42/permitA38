"""One rule for every record that has both a customer and a project: the project must belong to that customer."""

from permita38.core.errors import ServiceError

#: collections (besides trips, which have their own service) that carry both ``customer_id`` and ``project_id``
PROJECT_CUSTOMER_COLLECTIONS = frozenset({"offers", "invoices", "contracts"})


def check_project_customer(store, data: dict, old: dict | None) -> None:
    """Refuse a project that belongs to another customer.

    ``data`` is the write as sent. A PUT replaces the record, so a field the write leaves out is checked against its
    stored value in ``old`` (the saved record does not get that value back: it is simply absent). E.g. stored
    (customer A, project of A), a PUT with only ``project_id`` of customer B is refused, because A is still checked.

    Only checked when both fields are set and the project has a customer. When the customer and the project are both
    unchanged versus ``old`` (after the stored values are filled in for omitted fields), nothing is checked, so a
    legacy mismatch can still be edited. A project without a customer is general.
    """
    cid = data["customer_id"] if "customer_id" in data else (old or {}).get("customer_id")
    pid = data["project_id"] if "project_id" in data else (old or {}).get("project_id")
    if not cid or not pid:
        return
    if old is not None and old.get("customer_id") == cid and old.get("project_id") == pid:
        return
    project = store.get("projects", pid)
    if project and project.get("customer_id") and project["customer_id"] != cid:
        raise ServiceError(422, "Project belongs to another customer")


def project_kind(project: dict) -> str:
    """``customer`` or ``internal``. Rows stored before ``kind`` existed: customer when they have a customer_id."""
    kind = project.get("kind")
    if kind in ("customer", "internal"):
        return kind
    return "customer" if project.get("customer_id") else "internal"


def check_billable_project(store, data: dict, old: dict | None) -> None:
    """Refuse an internal project on an offer or invoice: internal projects are never billed.

    Same convention as :func:`check_project_customer`: a field left out of the write keeps its stored value, and an
    unchanged project (legacy data) is not checked again, so such a document can still be edited.
    """
    pid = data["project_id"] if "project_id" in data else (old or {}).get("project_id")
    if not pid or (old is not None and old.get("project_id") == pid):
        return
    project = store.get("projects", pid)
    if project and project_kind(project) == "internal":
        raise ServiceError(422, "Internal projects cannot be billed")
