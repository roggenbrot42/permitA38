-- permitA38 migration 0004: double-entry accounting (sqlite)
-- accounts (chart of accounts), journal_entries + journal_lines (immutable once posted, integer cents),
-- period_locks (closed months/years). Generated from permita38/core/repository/schema.py.

CREATE TABLE "accounts" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "number" TEXT,
    "name" TEXT,
    "type" TEXT,
    "side" TEXT,
    "chart" TEXT,
    "role" TEXT,
    "tax_code" TEXT,
    "active" INTEGER,
    "system" INTEGER,
    "description" TEXT,
    "valid_for_year" INTEGER,
    "reviewed_on" TEXT,
    "extra" TEXT
);

CREATE UNIQUE INDEX "idx_accounts_number" ON "accounts" ("number");

CREATE INDEX "idx_accounts_role" ON "accounts" ("role");

CREATE TABLE "journal_entries" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "fiscal_year" INTEGER,
    "booking_no" INTEGER,
    "date" TEXT,
    "posting_date" TEXT,
    "description" TEXT,
    "source_type" TEXT,
    "source_id" TEXT,
    "source_part" TEXT,
    "reversal_of" TEXT,
    "created_by" TEXT,
    "prev_hash" TEXT,
    "hash" TEXT,
    "extra" TEXT
);

CREATE UNIQUE INDEX "idx_journal_entries_booking" ON "journal_entries" ("fiscal_year", "booking_no");

CREATE INDEX "idx_journal_entries_source" ON "journal_entries" ("source_type", "source_id");

CREATE INDEX "idx_journal_entries_date" ON "journal_entries" ("date");

CREATE INDEX "idx_journal_entries_reversal" ON "journal_entries" ("reversal_of");

CREATE TABLE "journal_lines" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "entry_id" TEXT,
    "line_no" INTEGER,
    "account" TEXT,
    "debit_cents" INTEGER,
    "credit_cents" INTEGER,
    "tax_code" TEXT,
    "tax_amount_cents" INTEGER,
    "memo" TEXT,
    "extra" TEXT
);

CREATE INDEX "idx_journal_lines_entry" ON "journal_lines" ("entry_id");

CREATE INDEX "idx_journal_lines_account" ON "journal_lines" ("account");

CREATE TABLE "period_locks" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "year" INTEGER,
    "month" INTEGER,
    "locked_by" TEXT,
    "note" TEXT,
    "extra" TEXT
);

CREATE UNIQUE INDEX "idx_period_locks_period" ON "period_locks" ("year", "month");
