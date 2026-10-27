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
