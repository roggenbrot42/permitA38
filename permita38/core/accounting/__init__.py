"""Double-entry bookkeeping: chart of accounts, posting rules, reports and exports (pure, storage-free logic).

The storage-facing part lives in :mod:`permita38.core.service.accounting`. Everything here works on plain data and
integer cents, so it can be tested exhaustively without a database.
"""
