-- migration: ae32358b
-- requires: 0aacb7a9
-- permitA38 migration: accounting-documents (postgresql)
-- accounting documents (docs/specs/accounting.md 8.1 and 8.2): nullable columns on invoices, expenses and trips
-- (no backfill: NULL reads as the default of the spec); vat_returns and zm_reports (internal collections)

ALTER TABLE "invoices" ADD COLUMN "kind" TEXT;
ALTER TABLE "invoices" ADD COLUMN "credit_kind" TEXT;
ALTER TABLE "invoices" ADD COLUMN "corrects_id" TEXT;
ALTER TABLE "invoices" ADD COLUMN "tax_treatment" TEXT;
ALTER TABLE "invoices" ADD COLUMN "issued" JSONB;
ALTER TABLE "expenses" ADD COLUMN "tax_treatment" TEXT;
ALTER TABLE "expenses" ADD COLUMN "service_date" TEXT;
ALTER TABLE "expenses" ADD COLUMN "bill_rev" BIGINT;
ALTER TABLE "expenses" ADD COLUMN "pay_rev" BIGINT;
ALTER TABLE "trips" ADD COLUMN "post_rev" BIGINT;

CREATE TABLE "vat_returns" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "year" BIGINT,
    "period_kind" TEXT,
    "period_no" BIGINT,
    "from_date" TEXT,
    "to_date" TEXT,
    "due_date" TEXT,
    "status" TEXT,
    "basis" TEXT,
    "frequency" TEXT,
    "dauerfrist" BOOLEAN,
    "small_business" BOOLEAN,
    "rules_version" TEXT,
    "boxes" JSONB,
    "source_lines" JSONB,
    "result_cents" BIGINT,
    "special_prepayment_credit_cents" BIGINT,
    "rounding_difference_cents" BIGINT,
    "filed_on" TEXT,
    "reference" TEXT,
    "note" TEXT,
    "amends_id" TEXT,
    "amends_key" TEXT,
    "replaced_by" TEXT,
    "external" BOOLEAN,
    "finalised_at" BIGINT,
    "finalised_by" TEXT,
    "extra" JSONB,
    CONSTRAINT "vat_returns_amends_key" CHECK ("amends_key" IS NOT NULL AND "amends_key" = COALESCE("amends_id", '')),
    CONSTRAINT "vat_returns_quarter_no" CHECK ("period_kind" <> 'quarter' OR "period_no" BETWEEN 1 AND 4)
);
CREATE UNIQUE INDEX "idx_vat_returns_period" ON "vat_returns" ("year", "period_kind", "period_no", "amends_key");

CREATE TABLE "zm_reports" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "year" BIGINT,
    "quarter" BIGINT,
    "status" TEXT,
    "due_date" TEXT,
    "lines" JSONB,
    "source_lines" JSONB,
    "rules_version" TEXT,
    "filed_on" TEXT,
    "reference" TEXT,
    "note" TEXT,
    "amends_id" TEXT,
    "amends_key" TEXT,
    "replaced_by" TEXT,
    "finalised_at" BIGINT,
    "finalised_by" TEXT,
    "extra" JSONB,
    CONSTRAINT "zm_reports_amends_key" CHECK ("amends_key" IS NOT NULL AND "amends_key" = COALESCE("amends_id", ''))
);
CREATE UNIQUE INDEX "idx_zm_reports_quarter" ON "zm_reports" ("year", "quarter", "amends_key");

-- guards: generated from core/repository/schema.py GUARDS by Dialect.guard_sql (a test keeps them equal)
ALTER TABLE "expenses" ADD CONSTRAINT "guard_expenses_service_date_date" CHECK ("service_date" IS NULL OR "service_date" = '' OR "service_date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "invoices" ADD CONSTRAINT "guard_invoices_kind_one_of" CHECK (NOT COALESCE((("kind" IS NOT NULL AND btrim(CAST("kind" AS TEXT), E' \t\n\r\f\v') <> '') AND "kind" NOT IN ('invoice', 'credit_note')), FALSE));
ALTER TABLE "invoices" ADD CONSTRAINT "guard_invoices_credit_kind_one_of" CHECK (NOT COALESCE((("credit_kind" IS NOT NULL AND btrim(CAST("credit_kind" AS TEXT), E' \t\n\r\f\v') <> '') AND "credit_kind" NOT IN ('storno', 'partial', 'discount', 'write_off', 'write_off_reversal')), FALSE));
ALTER TABLE "invoices" ADD CONSTRAINT "guard_invoices_credit_kind_credit_note_implies" CHECK (NOT COALESCE((COALESCE(("credit_kind" IS NOT NULL AND btrim(CAST("credit_kind" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE) AND NOT COALESCE("kind" = 'credit_note', FALSE)), FALSE));
ALTER TABLE "invoices" ADD CONSTRAINT "guard_invoices_tax_treatment_one_of" CHECK (NOT COALESCE((("tax_treatment" IS NOT NULL AND btrim(CAST("tax_treatment" AS TEXT), E' \t\n\r\f\v') <> '') AND "tax_treatment" NOT IN ('standard', 'eu_b2b_service', 'small_business')), FALSE));
CREATE FUNCTION "guard_invoices_corrects_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."corrects_id" IS NOT NULL AND btrim(CAST(NEW."corrects_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."corrects_id") AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "corrects_id" IS NOT DISTINCT FROM NEW."corrects_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_invoices_corrects_id_ref_ins" BEFORE INSERT ON "invoices" FOR EACH ROW EXECUTE FUNCTION "guard_invoices_corrects_id_ref_chk"();
CREATE TRIGGER "guard_invoices_corrects_id_ref_upd" BEFORE UPDATE OF "corrects_id" ON "invoices" FOR EACH ROW
WHEN (OLD."corrects_id" IS DISTINCT FROM NEW."corrects_id") EXECUTE FUNCTION "guard_invoices_corrects_id_ref_chk"();
CREATE FUNCTION "guard_invoices_corrects_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "invoices" WHERE "corrects_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_invoices_corrects_id_ref_del" BEFORE DELETE ON "invoices" FOR EACH ROW EXECUTE FUNCTION "guard_invoices_corrects_id_ref_use"();
ALTER TABLE "expenses" ADD CONSTRAINT "guard_expenses_tax_treatment_one_of" CHECK (NOT COALESCE((("tax_treatment" IS NOT NULL AND btrim(CAST("tax_treatment" AS TEXT), E' \t\n\r\f\v') <> '') AND "tax_treatment" NOT IN ('standard', 'no_input_vat', 'reverse_charge_eu_service', 'reverse_charge_third_country')), FALSE));
ALTER TABLE "expenses" ADD CONSTRAINT "guard_expenses_bill_rev_range" CHECK (NOT COALESCE(("bill_rev" IS NOT NULL AND ("bill_rev" < 0)), FALSE));
ALTER TABLE "expenses" ADD CONSTRAINT "guard_expenses_pay_rev_range" CHECK (NOT COALESCE(("pay_rev" IS NOT NULL AND ("pay_rev" < 0)), FALSE));
ALTER TABLE "trips" ADD CONSTRAINT "guard_trips_post_rev_range" CHECK (NOT COALESCE(("post_rev" IS NOT NULL AND ("post_rev" < 0)), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_year_required" CHECK (NOT COALESCE((NOT ("year" IS NOT NULL AND btrim(CAST("year" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_period_kind_required" CHECK (NOT COALESCE((NOT ("period_kind" IS NOT NULL AND btrim(CAST("period_kind" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_period_no_required" CHECK (NOT COALESCE((NOT ("period_no" IS NOT NULL AND btrim(CAST("period_no" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_from_date_required" CHECK (NOT COALESCE((NOT ("from_date" IS NOT NULL AND btrim(CAST("from_date" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_to_date_required" CHECK (NOT COALESCE((NOT ("to_date" IS NOT NULL AND btrim(CAST("to_date" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_due_date_required" CHECK (NOT COALESCE((NOT ("due_date" IS NOT NULL AND btrim(CAST("due_date" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_status_required" CHECK (NOT COALESCE((NOT ("status" IS NOT NULL AND btrim(CAST("status" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_from_date_date" CHECK ("from_date" IS NULL OR "from_date" = '' OR "from_date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_to_date_date" CHECK ("to_date" IS NULL OR "to_date" = '' OR "to_date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_due_date_date" CHECK ("due_date" IS NULL OR "due_date" = '' OR "due_date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_filed_on_date" CHECK ("filed_on" IS NULL OR "filed_on" = '' OR "filed_on" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_period_kind_one_of" CHECK (NOT COALESCE((("period_kind" IS NOT NULL AND btrim(CAST("period_kind" AS TEXT), E' \t\n\r\f\v') <> '') AND "period_kind" NOT IN ('month', 'quarter')), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_status_one_of" CHECK (NOT COALESCE((("status" IS NOT NULL AND btrim(CAST("status" AS TEXT), E' \t\n\r\f\v') <> '') AND "status" NOT IN ('draft', 'final')), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_basis_one_of" CHECK (NOT COALESCE((("basis" IS NOT NULL AND btrim(CAST("basis" AS TEXT), E' \t\n\r\f\v') <> '') AND "basis" NOT IN ('soll', 'ist')), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_frequency_one_of" CHECK (NOT COALESCE((("frequency" IS NOT NULL AND btrim(CAST("frequency" AS TEXT), E' \t\n\r\f\v') <> '') AND "frequency" NOT IN ('month', 'quarter', 'none')), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_year_range" CHECK (NOT COALESCE(("year" IS NOT NULL AND ("year" < 2000 OR "year" > 2100)), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_period_no_range" CHECK (NOT COALESCE(("period_no" IS NOT NULL AND ("period_no" < 1 OR "period_no" > 12)), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_period_order" CHECK ("from_date" IS NULL OR "from_date" = '' OR "to_date" IS NULL OR "to_date" = '' OR "to_date" >= "from_date");
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_final_finalised_at_implies" CHECK (NOT COALESCE((COALESCE("status" = 'final', FALSE) AND NOT COALESCE(("finalised_at" IS NOT NULL AND btrim(CAST("finalised_at" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE)), FALSE));
ALTER TABLE "vat_returns" ADD CONSTRAINT "guard_vat_returns_final_finalised_by_implies" CHECK (NOT COALESCE((COALESCE("status" = 'final', FALSE) AND NOT COALESCE(("finalised_by" IS NOT NULL AND btrim(CAST("finalised_by" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE)), FALSE));
CREATE FUNCTION "guard_vat_returns_amends_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."amends_id" IS NOT NULL AND btrim(CAST(NEW."amends_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."amends_id") AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "amends_id" IS NOT DISTINCT FROM NEW."amends_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_vat_returns_amends_id_ref_ins" BEFORE INSERT ON "vat_returns" FOR EACH ROW EXECUTE FUNCTION "guard_vat_returns_amends_id_ref_chk"();
CREATE TRIGGER "guard_vat_returns_amends_id_ref_upd" BEFORE UPDATE OF "amends_id" ON "vat_returns" FOR EACH ROW
WHEN (OLD."amends_id" IS DISTINCT FROM NEW."amends_id") EXECUTE FUNCTION "guard_vat_returns_amends_id_ref_chk"();
CREATE FUNCTION "guard_vat_returns_amends_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "vat_returns" WHERE "amends_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_vat_returns_amends_id_ref_del" BEFORE DELETE ON "vat_returns" FOR EACH ROW EXECUTE FUNCTION "guard_vat_returns_amends_id_ref_use"();
CREATE FUNCTION "guard_vat_returns_replaced_by_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."replaced_by" IS NOT NULL AND btrim(CAST(NEW."replaced_by" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."replaced_by") AND NOT EXISTS (SELECT 1 FROM "vat_returns" WHERE "id" = NEW."id" AND "replaced_by" IS NOT DISTINCT FROM NEW."replaced_by") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_vat_returns_replaced_by_ref_ins" BEFORE INSERT ON "vat_returns" FOR EACH ROW EXECUTE FUNCTION "guard_vat_returns_replaced_by_ref_chk"();
CREATE TRIGGER "guard_vat_returns_replaced_by_ref_upd" BEFORE UPDATE OF "replaced_by" ON "vat_returns" FOR EACH ROW
WHEN (OLD."replaced_by" IS DISTINCT FROM NEW."replaced_by") EXECUTE FUNCTION "guard_vat_returns_replaced_by_ref_chk"();
CREATE FUNCTION "guard_vat_returns_replaced_by_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "vat_returns" WHERE "replaced_by" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_vat_returns_replaced_by_ref_del" BEFORE DELETE ON "vat_returns" FOR EACH ROW EXECUTE FUNCTION "guard_vat_returns_replaced_by_ref_use"();
ALTER TABLE "zm_reports" ADD CONSTRAINT "guard_zm_reports_year_required" CHECK (NOT COALESCE((NOT ("year" IS NOT NULL AND btrim(CAST("year" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "zm_reports" ADD CONSTRAINT "guard_zm_reports_quarter_required" CHECK (NOT COALESCE((NOT ("quarter" IS NOT NULL AND btrim(CAST("quarter" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "zm_reports" ADD CONSTRAINT "guard_zm_reports_status_required" CHECK (NOT COALESCE((NOT ("status" IS NOT NULL AND btrim(CAST("status" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "zm_reports" ADD CONSTRAINT "guard_zm_reports_due_date_required" CHECK (NOT COALESCE((NOT ("due_date" IS NOT NULL AND btrim(CAST("due_date" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "zm_reports" ADD CONSTRAINT "guard_zm_reports_due_date_date" CHECK ("due_date" IS NULL OR "due_date" = '' OR "due_date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "zm_reports" ADD CONSTRAINT "guard_zm_reports_filed_on_date" CHECK ("filed_on" IS NULL OR "filed_on" = '' OR "filed_on" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "zm_reports" ADD CONSTRAINT "guard_zm_reports_status_one_of" CHECK (NOT COALESCE((("status" IS NOT NULL AND btrim(CAST("status" AS TEXT), E' \t\n\r\f\v') <> '') AND "status" NOT IN ('draft', 'final')), FALSE));
ALTER TABLE "zm_reports" ADD CONSTRAINT "guard_zm_reports_year_range" CHECK (NOT COALESCE(("year" IS NOT NULL AND ("year" < 2000 OR "year" > 2100)), FALSE));
ALTER TABLE "zm_reports" ADD CONSTRAINT "guard_zm_reports_quarter_range" CHECK (NOT COALESCE(("quarter" IS NOT NULL AND ("quarter" < 1 OR "quarter" > 4)), FALSE));
ALTER TABLE "zm_reports" ADD CONSTRAINT "guard_zm_reports_final_finalised_at_implies" CHECK (NOT COALESCE((COALESCE("status" = 'final', FALSE) AND NOT COALESCE(("finalised_at" IS NOT NULL AND btrim(CAST("finalised_at" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE)), FALSE));
ALTER TABLE "zm_reports" ADD CONSTRAINT "guard_zm_reports_final_finalised_by_implies" CHECK (NOT COALESCE((COALESCE("status" = 'final', FALSE) AND NOT COALESCE(("finalised_by" IS NOT NULL AND btrim(CAST("finalised_by" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE)), FALSE));
CREATE FUNCTION "guard_zm_reports_amends_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."amends_id" IS NOT NULL AND btrim(CAST(NEW."amends_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."amends_id") AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."id" AND "amends_id" IS NOT DISTINCT FROM NEW."amends_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_zm_reports_amends_id_ref_ins" BEFORE INSERT ON "zm_reports" FOR EACH ROW EXECUTE FUNCTION "guard_zm_reports_amends_id_ref_chk"();
CREATE TRIGGER "guard_zm_reports_amends_id_ref_upd" BEFORE UPDATE OF "amends_id" ON "zm_reports" FOR EACH ROW
WHEN (OLD."amends_id" IS DISTINCT FROM NEW."amends_id") EXECUTE FUNCTION "guard_zm_reports_amends_id_ref_chk"();
CREATE FUNCTION "guard_zm_reports_amends_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "zm_reports" WHERE "amends_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_zm_reports_amends_id_ref_del" BEFORE DELETE ON "zm_reports" FOR EACH ROW EXECUTE FUNCTION "guard_zm_reports_amends_id_ref_use"();
CREATE FUNCTION "guard_zm_reports_replaced_by_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."replaced_by" IS NOT NULL AND btrim(CAST(NEW."replaced_by" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."replaced_by") AND NOT EXISTS (SELECT 1 FROM "zm_reports" WHERE "id" = NEW."id" AND "replaced_by" IS NOT DISTINCT FROM NEW."replaced_by") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_zm_reports_replaced_by_ref_ins" BEFORE INSERT ON "zm_reports" FOR EACH ROW EXECUTE FUNCTION "guard_zm_reports_replaced_by_ref_chk"();
CREATE TRIGGER "guard_zm_reports_replaced_by_ref_upd" BEFORE UPDATE OF "replaced_by" ON "zm_reports" FOR EACH ROW
WHEN (OLD."replaced_by" IS DISTINCT FROM NEW."replaced_by") EXECUTE FUNCTION "guard_zm_reports_replaced_by_ref_chk"();
CREATE FUNCTION "guard_zm_reports_replaced_by_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "zm_reports" WHERE "replaced_by" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_zm_reports_replaced_by_ref_del" BEFORE DELETE ON "zm_reports" FOR EACH ROW EXECUTE FUNCTION "guard_zm_reports_replaced_by_ref_use"();
