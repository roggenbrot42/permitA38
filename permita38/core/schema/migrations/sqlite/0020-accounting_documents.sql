-- migration: ae32358b
-- requires: 0aacb7a9
-- permitA38 migration: accounting-documents (sqlite)
-- accounting documents (docs/specs/accounting.md 8.1 and 8.2): nullable columns on invoices, expenses and trips
-- (no backfill: NULL reads as the default of the spec); vat_returns and zm_reports (internal collections)

ALTER TABLE "invoices" ADD COLUMN "kind" TEXT;
ALTER TABLE "invoices" ADD COLUMN "credit_kind" TEXT;
ALTER TABLE "invoices" ADD COLUMN "corrects_id" TEXT;
ALTER TABLE "invoices" ADD COLUMN "tax_treatment" TEXT;
ALTER TABLE "invoices" ADD COLUMN "issued" TEXT;
ALTER TABLE "expenses" ADD COLUMN "tax_treatment" TEXT;
ALTER TABLE "expenses" ADD COLUMN "service_date" TEXT;
ALTER TABLE "expenses" ADD COLUMN "bill_rev" INTEGER;
ALTER TABLE "expenses" ADD COLUMN "pay_rev" INTEGER;
ALTER TABLE "trips" ADD COLUMN "post_rev" INTEGER;

CREATE TABLE "vat_returns" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "year" INTEGER,
    "period_kind" TEXT,
    "period_no" INTEGER,
    "from_date" TEXT,
    "to_date" TEXT,
    "due_date" TEXT,
    "status" TEXT,
    "basis" TEXT,
    "frequency" TEXT,
    "dauerfrist" INTEGER,
    "small_business" INTEGER,
    "rules_version" TEXT,
    "boxes" TEXT,
    "source_lines" TEXT,
    "result" TEXT,
    "special_prepayment_credit" TEXT,
    "rounding_difference" TEXT,
    "filed_on" TEXT,
    "reference" TEXT,
    "note" TEXT,
    "amends_id" TEXT,
    "amends_key" TEXT,
    "replaced_by" TEXT,
    "external" INTEGER,
    "finalised_at" INTEGER,
    "finalised_by" TEXT,
    "extra" TEXT,
    CONSTRAINT "vat_returns_amends_key" CHECK ("amends_key" IS NOT NULL AND "amends_key" = COALESCE("amends_id", ''))
);
CREATE UNIQUE INDEX "idx_vat_returns_period" ON "vat_returns" ("year", "period_kind", "period_no", "amends_key");

CREATE TABLE "zm_reports" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "year" INTEGER,
    "quarter" INTEGER,
    "status" TEXT,
    "due_date" TEXT,
    "lines" TEXT,
    "source_lines" TEXT,
    "rules_version" TEXT,
    "filed_on" TEXT,
    "reference" TEXT,
    "note" TEXT,
    "amends_id" TEXT,
    "amends_key" TEXT,
    "replaced_by" TEXT,
    "finalised_at" INTEGER,
    "finalised_by" TEXT,
    "extra" TEXT,
    CONSTRAINT "zm_reports_amends_key" CHECK ("amends_key" IS NOT NULL AND "amends_key" = COALESCE("amends_id", ''))
);
CREATE UNIQUE INDEX "idx_zm_reports_quarter" ON "zm_reports" ("year", "quarter", "amends_key");

-- guards: generated from core/repository/schema.py GUARDS by Dialect.guard_sql (a test keeps them equal)
CREATE TRIGGER "guard_expenses_service_date_date_ins" BEFORE INSERT ON "expenses"
WHEN NEW."service_date" IS NOT NULL AND NEW."service_date" <> '' AND NOT (NEW."service_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "expenses" WHERE "id" = NEW."id" AND "service_date" IS NEW."service_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_expenses_service_date_date_upd" BEFORE UPDATE OF "service_date" ON "expenses"
WHEN NEW."service_date" IS NOT NULL AND NEW."service_date" <> '' AND NOT (NEW."service_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."service_date" IS NOT OLD."service_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_invoices_kind_one_of_ins" BEFORE INSERT ON "invoices"
WHEN ((NEW."kind" IS NOT NULL AND trim(NEW."kind", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."kind" NOT IN ('invoice', 'credit_note'))
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "kind" IS NEW."kind")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_invoices_kind_one_of_upd" BEFORE UPDATE OF "kind" ON "invoices"
WHEN ((NEW."kind" IS NOT NULL AND trim(NEW."kind", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."kind" NOT IN ('invoice', 'credit_note'))
 AND (NEW."kind" IS NOT OLD."kind")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_invoices_credit_kind_one_of_ins" BEFORE INSERT ON "invoices"
WHEN ((NEW."credit_kind" IS NOT NULL AND trim(NEW."credit_kind", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."credit_kind" NOT IN ('storno', 'partial', 'discount', 'write_off', 'write_off_reversal'))
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "credit_kind" IS NEW."credit_kind")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_invoices_credit_kind_one_of_upd" BEFORE UPDATE OF "credit_kind" ON "invoices"
WHEN ((NEW."credit_kind" IS NOT NULL AND trim(NEW."credit_kind", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."credit_kind" NOT IN ('storno', 'partial', 'discount', 'write_off', 'write_off_reversal'))
 AND (NEW."credit_kind" IS NOT OLD."credit_kind")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_invoices_tax_treatment_one_of_ins" BEFORE INSERT ON "invoices"
WHEN ((NEW."tax_treatment" IS NOT NULL AND trim(NEW."tax_treatment", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."tax_treatment" NOT IN ('standard', 'eu_b2b_service', 'small_business'))
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "tax_treatment" IS NEW."tax_treatment")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_invoices_tax_treatment_one_of_upd" BEFORE UPDATE OF "tax_treatment" ON "invoices"
WHEN ((NEW."tax_treatment" IS NOT NULL AND trim(NEW."tax_treatment", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."tax_treatment" NOT IN ('standard', 'eu_b2b_service', 'small_business'))
 AND (NEW."tax_treatment" IS NOT OLD."tax_treatment")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_invoices_corrects_id_ref_ins" BEFORE INSERT ON "invoices"
WHEN (NEW."corrects_id" IS NOT NULL AND trim(NEW."corrects_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."corrects_id")
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "corrects_id" IS NEW."corrects_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_invoices_corrects_id_ref_upd" BEFORE UPDATE OF "corrects_id" ON "invoices"
WHEN (NEW."corrects_id" IS NOT NULL AND trim(NEW."corrects_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."corrects_id")
 AND (NEW."corrects_id" IS NOT OLD."corrects_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_invoices_corrects_id_ref_del" BEFORE DELETE ON "invoices"
WHEN EXISTS (SELECT 1 FROM "invoices" WHERE "corrects_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_expenses_tax_treatment_one_of_ins" BEFORE INSERT ON "expenses"
WHEN ((NEW."tax_treatment" IS NOT NULL AND trim(NEW."tax_treatment", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."tax_treatment" NOT IN ('standard', 'no_input_vat', 'reverse_charge_eu_service', 'reverse_charge_third_country'))
 AND NOT EXISTS (SELECT 1 FROM "expenses" WHERE "id" = NEW."id" AND "tax_treatment" IS NEW."tax_treatment")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_expenses_tax_treatment_one_of_upd" BEFORE UPDATE OF "tax_treatment" ON "expenses"
WHEN ((NEW."tax_treatment" IS NOT NULL AND trim(NEW."tax_treatment", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."tax_treatment" NOT IN ('standard', 'no_input_vat', 'reverse_charge_eu_service', 'reverse_charge_third_country'))
 AND (NEW."tax_treatment" IS NOT OLD."tax_treatment")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_expenses_bill_rev_range_ins" BEFORE INSERT ON "expenses"
WHEN (NEW."bill_rev" IS NOT NULL AND NEW."bill_rev" <> '' AND (NEW."bill_rev" < 0))
 AND NOT EXISTS (SELECT 1 FROM "expenses" WHERE "id" = NEW."id" AND "bill_rev" IS NEW."bill_rev")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_expenses_bill_rev_range_upd" BEFORE UPDATE OF "bill_rev" ON "expenses"
WHEN (NEW."bill_rev" IS NOT NULL AND NEW."bill_rev" <> '' AND (NEW."bill_rev" < 0))
 AND (NEW."bill_rev" IS NOT OLD."bill_rev")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_expenses_pay_rev_range_ins" BEFORE INSERT ON "expenses"
WHEN (NEW."pay_rev" IS NOT NULL AND NEW."pay_rev" <> '' AND (NEW."pay_rev" < 0))
 AND NOT EXISTS (SELECT 1 FROM "expenses" WHERE "id" = NEW."id" AND "pay_rev" IS NEW."pay_rev")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_expenses_pay_rev_range_upd" BEFORE UPDATE OF "pay_rev" ON "expenses"
WHEN (NEW."pay_rev" IS NOT NULL AND NEW."pay_rev" <> '' AND (NEW."pay_rev" < 0))
 AND (NEW."pay_rev" IS NOT OLD."pay_rev")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_trips_post_rev_range_ins" BEFORE INSERT ON "trips"
WHEN (NEW."post_rev" IS NOT NULL AND NEW."post_rev" <> '' AND (NEW."post_rev" < 0))
 AND NOT EXISTS (SELECT 1 FROM "trips" WHERE "id" = NEW."id" AND "post_rev" IS NEW."post_rev")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_trips_post_rev_range_upd" BEFORE UPDATE OF "post_rev" ON "trips"
WHEN (NEW."post_rev" IS NOT NULL AND NEW."post_rev" <> '' AND (NEW."post_rev" < 0))
 AND (NEW."post_rev" IS NOT OLD."post_rev")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_vat_returns_year_required_ins" BEFORE INSERT ON "vat_returns"
WHEN (NOT (NEW."year" IS NOT NULL AND trim(NEW."year", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "year" IS NEW."year")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_year_required_upd" BEFORE UPDATE OF "year" ON "vat_returns"
WHEN (NOT (NEW."year" IS NOT NULL AND trim(NEW."year", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."year" IS NOT OLD."year")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_period_kind_required_ins" BEFORE INSERT ON "vat_returns"
WHEN (NOT (NEW."period_kind" IS NOT NULL AND trim(NEW."period_kind", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "period_kind" IS NEW."period_kind")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_period_kind_required_upd" BEFORE UPDATE OF "period_kind" ON "vat_returns"
WHEN (NOT (NEW."period_kind" IS NOT NULL AND trim(NEW."period_kind", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."period_kind" IS NOT OLD."period_kind")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_period_no_required_ins" BEFORE INSERT ON "vat_returns"
WHEN (NOT (NEW."period_no" IS NOT NULL AND trim(NEW."period_no", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "period_no" IS NEW."period_no")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_period_no_required_upd" BEFORE UPDATE OF "period_no" ON "vat_returns"
WHEN (NOT (NEW."period_no" IS NOT NULL AND trim(NEW."period_no", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."period_no" IS NOT OLD."period_no")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_from_date_required_ins" BEFORE INSERT ON "vat_returns"
WHEN (NOT (NEW."from_date" IS NOT NULL AND trim(NEW."from_date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "from_date" IS NEW."from_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_from_date_required_upd" BEFORE UPDATE OF "from_date" ON "vat_returns"
WHEN (NOT (NEW."from_date" IS NOT NULL AND trim(NEW."from_date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."from_date" IS NOT OLD."from_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_to_date_required_ins" BEFORE INSERT ON "vat_returns"
WHEN (NOT (NEW."to_date" IS NOT NULL AND trim(NEW."to_date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "to_date" IS NEW."to_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_to_date_required_upd" BEFORE UPDATE OF "to_date" ON "vat_returns"
WHEN (NOT (NEW."to_date" IS NOT NULL AND trim(NEW."to_date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."to_date" IS NOT OLD."to_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_due_date_required_ins" BEFORE INSERT ON "vat_returns"
WHEN (NOT (NEW."due_date" IS NOT NULL AND trim(NEW."due_date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "due_date" IS NEW."due_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_due_date_required_upd" BEFORE UPDATE OF "due_date" ON "vat_returns"
WHEN (NOT (NEW."due_date" IS NOT NULL AND trim(NEW."due_date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."due_date" IS NOT OLD."due_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_status_required_ins" BEFORE INSERT ON "vat_returns"
WHEN (NOT (NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "status" IS NEW."status")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_status_required_upd" BEFORE UPDATE OF "status" ON "vat_returns"
WHEN (NOT (NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."status" IS NOT OLD."status")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_vat_returns_from_date_date_ins" BEFORE INSERT ON "vat_returns"
WHEN NEW."from_date" IS NOT NULL AND NEW."from_date" <> '' AND NOT (NEW."from_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "from_date" IS NEW."from_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_vat_returns_from_date_date_upd" BEFORE UPDATE OF "from_date" ON "vat_returns"
WHEN NEW."from_date" IS NOT NULL AND NEW."from_date" <> '' AND NOT (NEW."from_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."from_date" IS NOT OLD."from_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_vat_returns_to_date_date_ins" BEFORE INSERT ON "vat_returns"
WHEN NEW."to_date" IS NOT NULL AND NEW."to_date" <> '' AND NOT (NEW."to_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "to_date" IS NEW."to_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_vat_returns_to_date_date_upd" BEFORE UPDATE OF "to_date" ON "vat_returns"
WHEN NEW."to_date" IS NOT NULL AND NEW."to_date" <> '' AND NOT (NEW."to_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."to_date" IS NOT OLD."to_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_vat_returns_due_date_date_ins" BEFORE INSERT ON "vat_returns"
WHEN NEW."due_date" IS NOT NULL AND NEW."due_date" <> '' AND NOT (NEW."due_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "due_date" IS NEW."due_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_vat_returns_due_date_date_upd" BEFORE UPDATE OF "due_date" ON "vat_returns"
WHEN NEW."due_date" IS NOT NULL AND NEW."due_date" <> '' AND NOT (NEW."due_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."due_date" IS NOT OLD."due_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_vat_returns_filed_on_date_ins" BEFORE INSERT ON "vat_returns"
WHEN NEW."filed_on" IS NOT NULL AND NEW."filed_on" <> '' AND NOT (NEW."filed_on" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "filed_on" IS NEW."filed_on")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_vat_returns_filed_on_date_upd" BEFORE UPDATE OF "filed_on" ON "vat_returns"
WHEN NEW."filed_on" IS NOT NULL AND NEW."filed_on" <> '' AND NOT (NEW."filed_on" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."filed_on" IS NOT OLD."filed_on")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_vat_returns_period_kind_one_of_ins" BEFORE INSERT ON "vat_returns"
WHEN ((NEW."period_kind" IS NOT NULL AND trim(NEW."period_kind", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."period_kind" NOT IN ('month', 'quarter'))
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "period_kind" IS NEW."period_kind")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_vat_returns_period_kind_one_of_upd" BEFORE UPDATE OF "period_kind" ON "vat_returns"
WHEN ((NEW."period_kind" IS NOT NULL AND trim(NEW."period_kind", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."period_kind" NOT IN ('month', 'quarter'))
 AND (NEW."period_kind" IS NOT OLD."period_kind")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_vat_returns_status_one_of_ins" BEFORE INSERT ON "vat_returns"
WHEN ((NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."status" NOT IN ('draft', 'final'))
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "status" IS NEW."status")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_vat_returns_status_one_of_upd" BEFORE UPDATE OF "status" ON "vat_returns"
WHEN ((NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."status" NOT IN ('draft', 'final'))
 AND (NEW."status" IS NOT OLD."status")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_vat_returns_basis_one_of_ins" BEFORE INSERT ON "vat_returns"
WHEN ((NEW."basis" IS NOT NULL AND trim(NEW."basis", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."basis" NOT IN ('soll', 'ist'))
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "basis" IS NEW."basis")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_vat_returns_basis_one_of_upd" BEFORE UPDATE OF "basis" ON "vat_returns"
WHEN ((NEW."basis" IS NOT NULL AND trim(NEW."basis", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."basis" NOT IN ('soll', 'ist'))
 AND (NEW."basis" IS NOT OLD."basis")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_vat_returns_frequency_one_of_ins" BEFORE INSERT ON "vat_returns"
WHEN ((NEW."frequency" IS NOT NULL AND trim(NEW."frequency", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."frequency" NOT IN ('month', 'quarter', 'none'))
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "frequency" IS NEW."frequency")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_vat_returns_frequency_one_of_upd" BEFORE UPDATE OF "frequency" ON "vat_returns"
WHEN ((NEW."frequency" IS NOT NULL AND trim(NEW."frequency", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."frequency" NOT IN ('month', 'quarter', 'none'))
 AND (NEW."frequency" IS NOT OLD."frequency")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_vat_returns_year_range_ins" BEFORE INSERT ON "vat_returns"
WHEN (NEW."year" IS NOT NULL AND NEW."year" <> '' AND (NEW."year" < 2000 OR NEW."year" > 2100))
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "year" IS NEW."year")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_vat_returns_year_range_upd" BEFORE UPDATE OF "year" ON "vat_returns"
WHEN (NEW."year" IS NOT NULL AND NEW."year" <> '' AND (NEW."year" < 2000 OR NEW."year" > 2100))
 AND (NEW."year" IS NOT OLD."year")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_vat_returns_period_no_range_ins" BEFORE INSERT ON "vat_returns"
WHEN (NEW."period_no" IS NOT NULL AND NEW."period_no" <> '' AND (NEW."period_no" < 1 OR NEW."period_no" > 12))
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "period_no" IS NEW."period_no")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_vat_returns_period_no_range_upd" BEFORE UPDATE OF "period_no" ON "vat_returns"
WHEN (NEW."period_no" IS NOT NULL AND NEW."period_no" <> '' AND (NEW."period_no" < 1 OR NEW."period_no" > 12))
 AND (NEW."period_no" IS NOT OLD."period_no")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_vat_returns_amends_id_ref_ins" BEFORE INSERT ON "vat_returns"
WHEN (NEW."amends_id" IS NOT NULL AND trim(NEW."amends_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."amends_id")
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "amends_id" IS NEW."amends_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_vat_returns_amends_id_ref_upd" BEFORE UPDATE OF "amends_id" ON "vat_returns"
WHEN (NEW."amends_id" IS NOT NULL AND trim(NEW."amends_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."amends_id")
 AND (NEW."amends_id" IS NOT OLD."amends_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_vat_returns_amends_id_ref_del" BEFORE DELETE ON "vat_returns"
WHEN EXISTS (SELECT 1 FROM "vat_returns" WHERE "amends_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_vat_returns_replaced_by_ref_ins" BEFORE INSERT ON "vat_returns"
WHEN (NEW."replaced_by" IS NOT NULL AND trim(NEW."replaced_by", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."replaced_by")
 AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "replaced_by" IS NEW."replaced_by")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_vat_returns_replaced_by_ref_upd" BEFORE UPDATE OF "replaced_by" ON "vat_returns"
WHEN (NEW."replaced_by" IS NOT NULL AND trim(NEW."replaced_by", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."replaced_by")
 AND (NEW."replaced_by" IS NOT OLD."replaced_by")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_vat_returns_replaced_by_ref_del" BEFORE DELETE ON "vat_returns"
WHEN EXISTS (SELECT 1 FROM "vat_returns" WHERE "replaced_by" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_zm_reports_year_required_ins" BEFORE INSERT ON "zm_reports"
WHEN (NOT (NEW."year" IS NOT NULL AND trim(NEW."year", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."id" AND "year" IS NEW."year")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_zm_reports_year_required_upd" BEFORE UPDATE OF "year" ON "zm_reports"
WHEN (NOT (NEW."year" IS NOT NULL AND trim(NEW."year", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."year" IS NOT OLD."year")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_zm_reports_quarter_required_ins" BEFORE INSERT ON "zm_reports"
WHEN (NOT (NEW."quarter" IS NOT NULL AND trim(NEW."quarter", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."id" AND "quarter" IS NEW."quarter")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_zm_reports_quarter_required_upd" BEFORE UPDATE OF "quarter" ON "zm_reports"
WHEN (NOT (NEW."quarter" IS NOT NULL AND trim(NEW."quarter", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."quarter" IS NOT OLD."quarter")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_zm_reports_status_required_ins" BEFORE INSERT ON "zm_reports"
WHEN (NOT (NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."id" AND "status" IS NEW."status")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_zm_reports_status_required_upd" BEFORE UPDATE OF "status" ON "zm_reports"
WHEN (NOT (NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."status" IS NOT OLD."status")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_zm_reports_due_date_required_ins" BEFORE INSERT ON "zm_reports"
WHEN (NOT (NEW."due_date" IS NOT NULL AND trim(NEW."due_date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."id" AND "due_date" IS NEW."due_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_zm_reports_due_date_required_upd" BEFORE UPDATE OF "due_date" ON "zm_reports"
WHEN (NOT (NEW."due_date" IS NOT NULL AND trim(NEW."due_date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."due_date" IS NOT OLD."due_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_zm_reports_due_date_date_ins" BEFORE INSERT ON "zm_reports"
WHEN NEW."due_date" IS NOT NULL AND NEW."due_date" <> '' AND NOT (NEW."due_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."id" AND "due_date" IS NEW."due_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_zm_reports_due_date_date_upd" BEFORE UPDATE OF "due_date" ON "zm_reports"
WHEN NEW."due_date" IS NOT NULL AND NEW."due_date" <> '' AND NOT (NEW."due_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."due_date" IS NOT OLD."due_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_zm_reports_filed_on_date_ins" BEFORE INSERT ON "zm_reports"
WHEN NEW."filed_on" IS NOT NULL AND NEW."filed_on" <> '' AND NOT (NEW."filed_on" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."id" AND "filed_on" IS NEW."filed_on")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_zm_reports_filed_on_date_upd" BEFORE UPDATE OF "filed_on" ON "zm_reports"
WHEN NEW."filed_on" IS NOT NULL AND NEW."filed_on" <> '' AND NOT (NEW."filed_on" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."filed_on" IS NOT OLD."filed_on")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_zm_reports_status_one_of_ins" BEFORE INSERT ON "zm_reports"
WHEN ((NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."status" NOT IN ('draft', 'final'))
 AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."id" AND "status" IS NEW."status")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_zm_reports_status_one_of_upd" BEFORE UPDATE OF "status" ON "zm_reports"
WHEN ((NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."status" NOT IN ('draft', 'final'))
 AND (NEW."status" IS NOT OLD."status")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_zm_reports_year_range_ins" BEFORE INSERT ON "zm_reports"
WHEN (NEW."year" IS NOT NULL AND NEW."year" <> '' AND (NEW."year" < 2000 OR NEW."year" > 2100))
 AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."id" AND "year" IS NEW."year")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_zm_reports_year_range_upd" BEFORE UPDATE OF "year" ON "zm_reports"
WHEN (NEW."year" IS NOT NULL AND NEW."year" <> '' AND (NEW."year" < 2000 OR NEW."year" > 2100))
 AND (NEW."year" IS NOT OLD."year")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_zm_reports_quarter_range_ins" BEFORE INSERT ON "zm_reports"
WHEN (NEW."quarter" IS NOT NULL AND NEW."quarter" <> '' AND (NEW."quarter" < 1 OR NEW."quarter" > 4))
 AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."id" AND "quarter" IS NEW."quarter")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_zm_reports_quarter_range_upd" BEFORE UPDATE OF "quarter" ON "zm_reports"
WHEN (NEW."quarter" IS NOT NULL AND NEW."quarter" <> '' AND (NEW."quarter" < 1 OR NEW."quarter" > 4))
 AND (NEW."quarter" IS NOT OLD."quarter")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_zm_reports_amends_id_ref_ins" BEFORE INSERT ON "zm_reports"
WHEN (NEW."amends_id" IS NOT NULL AND trim(NEW."amends_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."amends_id")
 AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."id" AND "amends_id" IS NEW."amends_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_zm_reports_amends_id_ref_upd" BEFORE UPDATE OF "amends_id" ON "zm_reports"
WHEN (NEW."amends_id" IS NOT NULL AND trim(NEW."amends_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."amends_id")
 AND (NEW."amends_id" IS NOT OLD."amends_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_zm_reports_amends_id_ref_del" BEFORE DELETE ON "zm_reports"
WHEN EXISTS (SELECT 1 FROM "zm_reports" WHERE "amends_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_zm_reports_replaced_by_ref_ins" BEFORE INSERT ON "zm_reports"
WHEN (NEW."replaced_by" IS NOT NULL AND trim(NEW."replaced_by", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."replaced_by")
 AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."id" AND "replaced_by" IS NEW."replaced_by")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_zm_reports_replaced_by_ref_upd" BEFORE UPDATE OF "replaced_by" ON "zm_reports"
WHEN (NEW."replaced_by" IS NOT NULL AND trim(NEW."replaced_by", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."replaced_by")
 AND (NEW."replaced_by" IS NOT OLD."replaced_by")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_zm_reports_replaced_by_ref_del" BEFORE DELETE ON "zm_reports"
WHEN EXISTS (SELECT 1 FROM "zm_reports" WHERE "replaced_by" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
