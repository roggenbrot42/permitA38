-- permitA38 migration 0004: double-entry accounting (postgresql)
-- accounts (chart of accounts), journal_entries + journal_lines (immutable once posted, integer cents),
-- period_locks (closed months/years). Generated from permita38/core/repository/schema.py.

CREATE TABLE "accounts" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "number" TEXT,
    "name" TEXT,
    "type" TEXT,
    "side" TEXT,
    "chart" TEXT,
    "role" TEXT,
    "tax_code" TEXT,
    "active" BOOLEAN,
    "system" BOOLEAN,
    "description" TEXT,
    "valid_for_year" BIGINT,
    "reviewed_on" TEXT,
    "extra" JSONB
);

CREATE UNIQUE INDEX "idx_accounts_number" ON "accounts" ("number");

CREATE INDEX "idx_accounts_role" ON "accounts" ("role");

CREATE TABLE "journal_entries" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "fiscal_year" BIGINT,
    "booking_no" BIGINT,
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
    "extra" JSONB
);

CREATE UNIQUE INDEX "idx_journal_entries_booking" ON "journal_entries" ("fiscal_year", "booking_no");

CREATE INDEX "idx_journal_entries_source" ON "journal_entries" ("source_type", "source_id");

CREATE INDEX "idx_journal_entries_date" ON "journal_entries" ("date");

CREATE INDEX "idx_journal_entries_reversal" ON "journal_entries" ("reversal_of");

CREATE TABLE "journal_lines" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "entry_id" TEXT,
    "line_no" BIGINT,
    "account" TEXT,
    "debit_cents" BIGINT,
    "credit_cents" BIGINT,
    "tax_code" TEXT,
    "tax_amount_cents" BIGINT,
    "memo" TEXT,
    "extra" JSONB
);

CREATE INDEX "idx_journal_lines_entry" ON "journal_lines" ("entry_id");

CREATE INDEX "idx_journal_lines_account" ON "journal_lines" ("account");

CREATE TABLE "period_locks" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "year" BIGINT,
    "month" BIGINT,
    "locked_by" TEXT,
    "note" TEXT,
    "extra" JSONB
);

CREATE UNIQUE INDEX "idx_period_locks_period" ON "period_locks" ("year", "month");
