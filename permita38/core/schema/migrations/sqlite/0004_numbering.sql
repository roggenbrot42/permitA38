-- permitA38 migration 0004 (numbering and import)
-- permitA38 migration: document numbering sequences and document import (sqlite)
-- number_sequences: atomic counters per document type and period; numbering_log: audit trail of number changes;
-- import_batches: one row per import run;
-- import columns on offers/invoices/customers/expenses (external_id for idempotent re-import, import_batch for undo).

ALTER TABLE "offers" ADD COLUMN "imported" INTEGER;
ALTER TABLE "offers" ADD COLUMN "original_number" TEXT;
ALTER TABLE "offers" ADD COLUMN "external_id" TEXT;
ALTER TABLE "offers" ADD COLUMN "import_batch" TEXT;
ALTER TABLE "offers" ADD COLUMN "totals_unverified" INTEGER;
ALTER TABLE "offers" ADD COLUMN "original_pdf" TEXT;
ALTER TABLE "invoices" ADD COLUMN "imported" INTEGER;
ALTER TABLE "invoices" ADD COLUMN "original_number" TEXT;
ALTER TABLE "invoices" ADD COLUMN "external_id" TEXT;
ALTER TABLE "invoices" ADD COLUMN "import_batch" TEXT;
ALTER TABLE "invoices" ADD COLUMN "totals_unverified" INTEGER;
ALTER TABLE "invoices" ADD COLUMN "original_pdf" TEXT;
ALTER TABLE "customers" ADD COLUMN "external_id" TEXT;
ALTER TABLE "customers" ADD COLUMN "import_batch" TEXT;
ALTER TABLE "expenses" ADD COLUMN "external_id" TEXT;
ALTER TABLE "expenses" ADD COLUMN "import_batch" TEXT;

CREATE TABLE "number_sequences" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "doc_type" TEXT,
    "period" TEXT,
    "last_value" INTEGER,
    "extra" TEXT
);
CREATE UNIQUE INDEX "idx_number_sequences_key" ON "number_sequences" ("doc_type", "period");

CREATE TABLE "import_batches" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "kind" TEXT,
    "filename" TEXT,
    "status" TEXT,
    "counts" TEXT,
    "mapping" TEXT,
    "finished" INTEGER,
    "extra" TEXT
);

CREATE TABLE "numbering_log" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "doc_type" TEXT,
    "action" TEXT,
    "before" TEXT,
    "after" TEXT,
    "warnings" TEXT,
    "reason" TEXT,
    "extra" TEXT
);
