"""Import of existing documents (invoices, offers), customers and expenses (see docs/import.md).

Modules: ``tables`` (reading and size guards), ``xlsx_reader`` (XLSX without third-party packages), ``mapping``
(column detection), ``values`` (dates, amounts, status), ``service`` (plan, run, undo).
"""

from .service import ImportService

__all__ = ["ImportService"]
