-- migration: c6d8d0be
-- requires: 72306069
-- permitA38 migration: suppliers (sqlite)

-- Suppliers and subcontractors, offers received from suppliers, and links from expenses (docs/contracts/subcontractors.md)
ALTER TABLE "expenses" ADD COLUMN "supplier_id" TEXT;
ALTER TABLE "expenses" ADD COLUMN "allocation_id" TEXT;
ALTER TABLE "expenses" ADD COLUMN "reverse_charge" INTEGER;

CREATE TABLE "suppliers" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "name" TEXT,
    "supplier_no" TEXT,
    "kind" TEXT,
    "active" INTEGER,
    "street" TEXT,
    "zip" TEXT,
    "city" TEXT,
    "country" TEXT,
    "vat_id" TEXT,
    "tax_number" TEXT,
    "vat_mode" TEXT,
    "contact" TEXT,
    "email" TEXT,
    "phone" TEXT,
    "notes" TEXT,
    "iban" TEXT,
    "bic" TEXT,
    "bank_name" TEXT,
    "payment_terms_days" INTEGER,
    "payment_terms_note" TEXT,
    "tags" TEXT,
    "weekly_hours" REAL,
    "workdays" TEXT,
    "rate" REAL,
    "unavailable" TEXT,
    "login_user_id" TEXT,
    "external_id" TEXT,
    "import_batch" TEXT,
    "extra" TEXT
);
CREATE INDEX "idx_suppliers_name" ON "suppliers" ("name");
CREATE INDEX "idx_suppliers_external_id" ON "suppliers" ("external_id");

CREATE TABLE "incoming_offers" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "supplier_id" TEXT,
    "reference" TEXT,
    "title" TEXT,
    "received_on" TEXT,
    "valid_until" TEXT,
    "lines" TEXT,
    "net_total" REAL,
    "vat" REAL,
    "vat_rate" REAL,
    "vat_mode" TEXT,
    "gross_total" REAL,
    "plan" TEXT,
    "project_id" TEXT,
    "contract_id" TEXT,
    "customer_id" TEXT,
    "attachment" TEXT,
    "status" TEXT,
    "status_changed_at" TEXT,
    "decided_by" TEXT,
    "decision_note" TEXT,
    "allocation_id" TEXT,
    "notes" TEXT,
    "external_id" TEXT,
    "import_batch" TEXT,
    "extra" TEXT
);
CREATE INDEX "idx_incoming_offers_supplier" ON "incoming_offers" ("supplier_id");
CREATE INDEX "idx_incoming_offers_status" ON "incoming_offers" ("status");

CREATE INDEX "idx_allocations_supplier" ON "allocations" ("supplier_id");
CREATE INDEX "idx_allocations_status" ON "allocations" ("status");
CREATE INDEX "idx_expenses_supplier" ON "expenses" ("supplier_id");
CREATE INDEX "idx_expenses_allocation" ON "expenses" ("allocation_id");
