-- permitA38 migration 0005: VAT return (Umsatzsteuer-Voranmeldung) (postgresql)
-- vat_returns: frozen snapshots of prepared / submitted VAT returns (data = the complete computed return).
-- invoices.vat_treatment / offers.vat_treatment: how a document is reported (reverse charge, EU service, export ...).
-- expenses.vat_treatment / supplier_country: how a bill is reported (reverse charge, EU acquisition, import ...).

CREATE TABLE "vat_returns" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "period" TEXT,
    "year" BIGINT,
    "status" TEXT,
    "submitted_on" TEXT,
    "reference" TEXT,
    "note" TEXT,
    "total" DOUBLE PRECISION,
    "data" JSONB,
    "extra" JSONB
);
CREATE INDEX "idx_vat_returns_period" ON "vat_returns" ("period");
CREATE INDEX "idx_vat_returns_year" ON "vat_returns" ("year");

ALTER TABLE "invoices" ADD COLUMN "vat_treatment" TEXT;
ALTER TABLE "offers" ADD COLUMN "vat_treatment" TEXT;
ALTER TABLE "expenses" ADD COLUMN "vat_treatment" TEXT;
ALTER TABLE "expenses" ADD COLUMN "supplier_country" TEXT;
