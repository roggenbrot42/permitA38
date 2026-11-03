-- migration: f3fcc516
-- requires: f12b52ff
-- permitA38 migration: receipt_scans (sqlite)

-- expenses: bill date vs paid date and the printed invoice number (docs/specs/receipt-scan.md)
ALTER TABLE "expenses" ADD COLUMN "paid_on" TEXT;
ALTER TABLE "expenses" ADD COLUMN "invoice_no" TEXT;

-- scanned receipts: flagged bills (not yet expenses), booked ones and hash-only rows
CREATE TABLE "receipt_scans" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "status" TEXT,
    "sha256" TEXT,
    "invoice_key" TEXT,
    "attachment" TEXT,
    "filename" TEXT,
    "engine" TEXT,
    "draft" TEXT,
    "doc" TEXT,
    "flags" TEXT,
    "expense_id" TEXT,
    "created_by" TEXT,
    "extra" TEXT
);
CREATE UNIQUE INDEX "idx_receipt_scans_sha256" ON "receipt_scans" ("sha256");
CREATE INDEX "idx_receipt_scans_status" ON "receipt_scans" ("status");
CREATE INDEX "idx_receipt_scans_invoice_key" ON "receipt_scans" ("invoice_key");
CREATE INDEX "idx_receipt_scans_expense_id" ON "receipt_scans" ("expense_id");
