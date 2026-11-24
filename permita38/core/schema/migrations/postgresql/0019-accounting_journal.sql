-- migration: 0aacb7a9
-- requires: 7ca84bdc
-- permitA38 migration: accounting-journal (postgresql)

-- accounting journal (docs/specs/accounting.md sections 7 and 8.1): internal tables, append-only journal

CREATE TABLE "fiscal_years" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "year" BIGINT,
    "chart" TEXT,
    "income_basis" TEXT,
    "vat_basis" TEXT,
    "vat_frequency" TEXT,
    "dauerfrist" BOOLEAN,
    "small_business" BOOLEAN,
    "vat_regular_from" TEXT,
    "rules_version" TEXT,
    "opened_at" BIGINT,
    "opened_by" TEXT,
    "closed_at" BIGINT,
    "closed_by" TEXT,
    "extra" JSONB
);
CREATE UNIQUE INDEX "idx_fiscal_years_year" ON "fiscal_years" ("year");

CREATE TABLE "journal_entries" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "fiscal_year" BIGINT,
    "entry_no" BIGINT,
    "document_date" TEXT,
    "booking_date" TEXT,
    "event" TEXT,
    "event_key" TEXT,
    "source_coll" TEXT,
    "source_id" TEXT,
    "source_number" TEXT,
    "reverses_id" TEXT,
    "counterparty_id" TEXT,
    "cp_vat_id" TEXT,
    "cp_country" TEXT,
    "text" TEXT,
    "origin" TEXT,
    "created_at" BIGINT,
    "created_by" TEXT,
    "prev_hash" TEXT,
    "hash" TEXT,
    "extra" JSONB
);
CREATE UNIQUE INDEX "idx_journal_entries_event_key" ON "journal_entries" ("event_key");
CREATE UNIQUE INDEX "idx_journal_entries_year_no" ON "journal_entries" ("fiscal_year", "entry_no");
CREATE INDEX "idx_journal_entries_source" ON "journal_entries" ("source_id");
CREATE INDEX "idx_journal_entries_booking" ON "journal_entries" ("booking_date");

CREATE TABLE "journal_lines" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "entry_id" TEXT,
    "line_no" BIGINT,
    "role" TEXT,
    "account" TEXT,
    "amount_cents" BIGINT,
    "tax_key" TEXT,
    "tax_kind" TEXT,
    "tax_rate" TEXT,
    "tax_base_cents" BIGINT,
    "vat_date" TEXT,
    "income_date" TEXT,
    "open_item_id" TEXT,
    "treatment" TEXT,
    "extra" JSONB
);
CREATE UNIQUE INDEX "idx_journal_lines_entry_line" ON "journal_lines" ("entry_id", "line_no");
CREATE INDEX "idx_journal_lines_vat" ON "journal_lines" ("vat_date", "tax_key");
CREATE INDEX "idx_journal_lines_income" ON "journal_lines" ("income_date");
CREATE INDEX "idx_journal_lines_open_item" ON "journal_lines" ("open_item_id");

CREATE TABLE "payments" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "document_id" TEXT,
    "date" TEXT,
    "amount_cents" BIGINT,
    "method" TEXT,
    "kind" TEXT,
    "note" TEXT,
    "reverses_id" TEXT,
    "created_at" BIGINT,
    "created_by" TEXT,
    "extra" JSONB,
    CONSTRAINT "payments_amount_nonzero" CHECK ("amount_cents" <> 0)
);
CREATE INDEX "idx_payments_document" ON "payments" ("document_id");

CREATE TABLE "period_locks" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "year" BIGINT,
    "month" BIGINT,
    "locked_at" BIGINT,
    "locked_by" TEXT,
    "reason" TEXT,
    "extra" JSONB
);
CREATE UNIQUE INDEX "idx_period_locks_year_month" ON "period_locks" ("year", "month");

-- guards: generated from core/repository/schema.py GUARDS by Dialect.guard_sql (a test keeps them equal)
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_year_required" CHECK (NOT COALESCE((NOT ("year" IS NOT NULL AND btrim(CAST("year" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_chart_required" CHECK (NOT COALESCE((NOT ("chart" IS NOT NULL AND btrim(CAST("chart" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_income_basis_required" CHECK (NOT COALESCE((NOT ("income_basis" IS NOT NULL AND btrim(CAST("income_basis" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_vat_basis_required" CHECK (NOT COALESCE((NOT ("vat_basis" IS NOT NULL AND btrim(CAST("vat_basis" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_vat_frequency_required" CHECK (NOT COALESCE((NOT ("vat_frequency" IS NOT NULL AND btrim(CAST("vat_frequency" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_dauerfrist_required" CHECK (NOT COALESCE((NOT ("dauerfrist" IS NOT NULL AND btrim(CAST("dauerfrist" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_small_business_required" CHECK (NOT COALESCE((NOT ("small_business" IS NOT NULL AND btrim(CAST("small_business" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_rules_version_required" CHECK (NOT COALESCE((NOT ("rules_version" IS NOT NULL AND btrim(CAST("rules_version" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_opened_at_required" CHECK (NOT COALESCE((NOT ("opened_at" IS NOT NULL AND btrim(CAST("opened_at" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_opened_by_required" CHECK (NOT COALESCE((NOT ("opened_by" IS NOT NULL AND btrim(CAST("opened_by" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_vat_regular_from_date" CHECK ("vat_regular_from" IS NULL OR "vat_regular_from" = '' OR "vat_regular_from" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_chart_one_of" CHECK (NOT COALESCE((("chart" IS NOT NULL AND btrim(CAST("chart" AS TEXT), E' \t\n\r\f\v') <> '') AND "chart" NOT IN ('skr03', 'skr04')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_income_basis_one_of" CHECK (NOT COALESCE((("income_basis" IS NOT NULL AND btrim(CAST("income_basis" AS TEXT), E' \t\n\r\f\v') <> '') AND "income_basis" NOT IN ('eur', 'accrual')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_vat_basis_one_of" CHECK (NOT COALESCE((("vat_basis" IS NOT NULL AND btrim(CAST("vat_basis" AS TEXT), E' \t\n\r\f\v') <> '') AND "vat_basis" NOT IN ('soll', 'ist')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_vat_frequency_one_of" CHECK (NOT COALESCE((("vat_frequency" IS NOT NULL AND btrim(CAST("vat_frequency" AS TEXT), E' \t\n\r\f\v') <> '') AND "vat_frequency" NOT IN ('month', 'quarter', 'none')), FALSE));
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_closed_by_implies" CHECK (NOT COALESCE((COALESCE(("closed_at" IS NOT NULL AND btrim(CAST("closed_at" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE) AND NOT COALESCE(("closed_by" IS NOT NULL AND btrim(CAST("closed_by" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE)), FALSE));
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_fiscal_year_required" CHECK (NOT COALESCE((NOT ("fiscal_year" IS NOT NULL AND btrim(CAST("fiscal_year" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_entry_no_required" CHECK (NOT COALESCE((NOT ("entry_no" IS NOT NULL AND btrim(CAST("entry_no" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_document_date_required" CHECK (NOT COALESCE((NOT ("document_date" IS NOT NULL AND btrim(CAST("document_date" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_booking_date_required" CHECK (NOT COALESCE((NOT ("booking_date" IS NOT NULL AND btrim(CAST("booking_date" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_event_required" CHECK (NOT COALESCE((NOT ("event" IS NOT NULL AND btrim(CAST("event" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_event_key_required" CHECK (NOT COALESCE((NOT ("event_key" IS NOT NULL AND btrim(CAST("event_key" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_text_required" CHECK (NOT COALESCE((NOT ("text" IS NOT NULL AND btrim(CAST("text" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_origin_required" CHECK (NOT COALESCE((NOT ("origin" IS NOT NULL AND btrim(CAST("origin" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_created_at_required" CHECK (NOT COALESCE((NOT ("created_at" IS NOT NULL AND btrim(CAST("created_at" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_created_by_required" CHECK (NOT COALESCE((NOT ("created_by" IS NOT NULL AND btrim(CAST("created_by" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_prev_hash_required" CHECK (NOT COALESCE((NOT ("prev_hash" IS NOT NULL AND btrim(CAST("prev_hash" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_hash_required" CHECK (NOT COALESCE((NOT ("hash" IS NOT NULL AND btrim(CAST("hash" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_document_date_date" CHECK ("document_date" IS NULL OR "document_date" = '' OR "document_date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_booking_date_date" CHECK ("booking_date" IS NULL OR "booking_date" = '' OR "booking_date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "journal_entries" ADD CONSTRAINT "guard_journal_entries_origin_one_of" CHECK (NOT COALESCE((("origin" IS NOT NULL AND btrim(CAST("origin" AS TEXT), E' \t\n\r\f\v') <> '') AND "origin" NOT IN ('auto', 'manual', 'opening')), FALSE));
CREATE FUNCTION "guard_journal_entries_reverses_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."reverses_id" IS NOT NULL AND btrim(CAST(NEW."reverses_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."reverses_id") AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "reverses_id" IS NOT DISTINCT FROM NEW."reverses_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_journal_entries_reverses_id_ref_ins" BEFORE INSERT ON "journal_entries" FOR EACH ROW EXECUTE FUNCTION "guard_journal_entries_reverses_id_ref_chk"();
CREATE TRIGGER "guard_journal_entries_reverses_id_ref_upd" BEFORE UPDATE OF "reverses_id" ON "journal_entries" FOR EACH ROW
WHEN (OLD."reverses_id" IS DISTINCT FROM NEW."reverses_id") EXECUTE FUNCTION "guard_journal_entries_reverses_id_ref_chk"();
CREATE FUNCTION "guard_journal_entries_reverses_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "journal_entries" WHERE "reverses_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_journal_entries_reverses_id_ref_del" BEFORE DELETE ON "journal_entries" FOR EACH ROW EXECUTE FUNCTION "guard_journal_entries_reverses_id_ref_use"();
CREATE FUNCTION "guard_journal_entries_append_only_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'permita38:state.append_only' USING ERRCODE = '23000';
END;
$$;
CREATE TRIGGER "guard_journal_entries_append_only_upd" BEFORE UPDATE ON "journal_entries" FOR EACH ROW EXECUTE FUNCTION "guard_journal_entries_append_only_fn"();
CREATE TRIGGER "guard_journal_entries_append_only_del" BEFORE DELETE ON "journal_entries" FOR EACH ROW EXECUTE FUNCTION "guard_journal_entries_append_only_fn"();
CREATE TRIGGER "guard_journal_entries_append_only_trunc" BEFORE TRUNCATE ON "journal_entries" FOR EACH STATEMENT EXECUTE FUNCTION "guard_journal_entries_append_only_fn"();
ALTER TABLE "journal_lines" ADD CONSTRAINT "guard_journal_lines_entry_id_required" CHECK (NOT COALESCE((NOT ("entry_id" IS NOT NULL AND btrim(CAST("entry_id" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_lines" ADD CONSTRAINT "guard_journal_lines_line_no_required" CHECK (NOT COALESCE((NOT ("line_no" IS NOT NULL AND btrim(CAST("line_no" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_lines" ADD CONSTRAINT "guard_journal_lines_account_required" CHECK (NOT COALESCE((NOT ("account" IS NOT NULL AND btrim(CAST("account" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_lines" ADD CONSTRAINT "guard_journal_lines_amount_cents_required" CHECK (NOT COALESCE((NOT ("amount_cents" IS NOT NULL AND btrim(CAST("amount_cents" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "journal_lines" ADD CONSTRAINT "guard_journal_lines_vat_date_date" CHECK ("vat_date" IS NULL OR "vat_date" = '' OR "vat_date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "journal_lines" ADD CONSTRAINT "guard_journal_lines_income_date_date" CHECK ("income_date" IS NULL OR "income_date" = '' OR "income_date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "journal_lines" ADD CONSTRAINT "guard_journal_lines_tax_kind_one_of" CHECK (NOT COALESCE((("tax_kind" IS NOT NULL AND btrim(CAST("tax_kind" AS TEXT), E' \t\n\r\f\v') <> '') AND "tax_kind" NOT IN ('tax', 'input', 'base_only')), FALSE));
CREATE FUNCTION "guard_journal_lines_entry_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."entry_id" IS NOT NULL AND btrim(CAST(NEW."entry_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."entry_id") AND NOT EXISTS (SELECT 1 FROM "journal_lines" WHERE "id" = NEW."id" AND "entry_id" IS NOT DISTINCT FROM NEW."entry_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_journal_lines_entry_id_ref_ins" BEFORE INSERT ON "journal_lines" FOR EACH ROW EXECUTE FUNCTION "guard_journal_lines_entry_id_ref_chk"();
CREATE TRIGGER "guard_journal_lines_entry_id_ref_upd" BEFORE UPDATE OF "entry_id" ON "journal_lines" FOR EACH ROW
WHEN (OLD."entry_id" IS DISTINCT FROM NEW."entry_id") EXECUTE FUNCTION "guard_journal_lines_entry_id_ref_chk"();
CREATE FUNCTION "guard_journal_lines_entry_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "journal_lines" WHERE "entry_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_journal_lines_entry_id_ref_del" BEFORE DELETE ON "journal_entries" FOR EACH ROW EXECUTE FUNCTION "guard_journal_lines_entry_id_ref_use"();
ALTER TABLE "journal_lines" ADD CONSTRAINT "guard_journal_lines_tax_kind_key_implies" CHECK (NOT COALESCE((COALESCE(("tax_kind" IS NOT NULL AND btrim(CAST("tax_kind" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE) AND NOT COALESCE(("tax_key" IS NOT NULL AND btrim(CAST("tax_key" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE)), FALSE));
ALTER TABLE "journal_lines" ADD CONSTRAINT "guard_journal_lines_tax_key_vat_date_implies" CHECK (NOT COALESCE((COALESCE("tax_key" IN ('S19', 'S7', 'SX', 'KU', 'EU_SVC', 'VST', 'RC_EU_SVC', 'RC_3C_SVC'), FALSE) AND NOT COALESCE(("vat_date" IS NOT NULL AND btrim(CAST("vat_date" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE)), FALSE));
CREATE FUNCTION "guard_journal_lines_append_only_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'permita38:state.append_only' USING ERRCODE = '23000';
END;
$$;
CREATE TRIGGER "guard_journal_lines_append_only_upd" BEFORE UPDATE ON "journal_lines" FOR EACH ROW EXECUTE FUNCTION "guard_journal_lines_append_only_fn"();
CREATE TRIGGER "guard_journal_lines_append_only_del" BEFORE DELETE ON "journal_lines" FOR EACH ROW EXECUTE FUNCTION "guard_journal_lines_append_only_fn"();
CREATE TRIGGER "guard_journal_lines_append_only_trunc" BEFORE TRUNCATE ON "journal_lines" FOR EACH STATEMENT EXECUTE FUNCTION "guard_journal_lines_append_only_fn"();
ALTER TABLE "payments" ADD CONSTRAINT "guard_payments_document_id_required" CHECK (NOT COALESCE((NOT ("document_id" IS NOT NULL AND btrim(CAST("document_id" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "payments" ADD CONSTRAINT "guard_payments_date_required" CHECK (NOT COALESCE((NOT ("date" IS NOT NULL AND btrim(CAST("date" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "payments" ADD CONSTRAINT "guard_payments_amount_cents_required" CHECK (NOT COALESCE((NOT ("amount_cents" IS NOT NULL AND btrim(CAST("amount_cents" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "payments" ADD CONSTRAINT "guard_payments_method_required" CHECK (NOT COALESCE((NOT ("method" IS NOT NULL AND btrim(CAST("method" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "payments" ADD CONSTRAINT "guard_payments_kind_required" CHECK (NOT COALESCE((NOT ("kind" IS NOT NULL AND btrim(CAST("kind" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "payments" ADD CONSTRAINT "guard_payments_created_at_required" CHECK (NOT COALESCE((NOT ("created_at" IS NOT NULL AND btrim(CAST("created_at" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "payments" ADD CONSTRAINT "guard_payments_created_by_required" CHECK (NOT COALESCE((NOT ("created_by" IS NOT NULL AND btrim(CAST("created_by" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "payments" ADD CONSTRAINT "guard_payments_date_date" CHECK ("date" IS NULL OR "date" = '' OR "date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "payments" ADD CONSTRAINT "guard_payments_method_one_of" CHECK (NOT COALESCE((("method" IS NOT NULL AND btrim(CAST("method" AS TEXT), E' \t\n\r\f\v') <> '') AND "method" NOT IN ('bank', 'cash')), FALSE));
ALTER TABLE "payments" ADD CONSTRAINT "guard_payments_kind_one_of" CHECK (NOT COALESCE((("kind" IS NOT NULL AND btrim(CAST("kind" AS TEXT), E' \t\n\r\f\v') <> '') AND "kind" NOT IN ('payment', 'refund', 'reversal')), FALSE));
CREATE FUNCTION "guard_payments_document_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."document_id" IS NOT NULL AND btrim(CAST(NEW."document_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."document_id") AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "document_id" IS NOT DISTINCT FROM NEW."document_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_payments_document_id_ref_ins" BEFORE INSERT ON "payments" FOR EACH ROW EXECUTE FUNCTION "guard_payments_document_id_ref_chk"();
CREATE TRIGGER "guard_payments_document_id_ref_upd" BEFORE UPDATE OF "document_id" ON "payments" FOR EACH ROW
WHEN (OLD."document_id" IS DISTINCT FROM NEW."document_id") EXECUTE FUNCTION "guard_payments_document_id_ref_chk"();
CREATE FUNCTION "guard_payments_document_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "payments" WHERE "document_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_payments_document_id_ref_del" BEFORE DELETE ON "invoices" FOR EACH ROW EXECUTE FUNCTION "guard_payments_document_id_ref_use"();
CREATE FUNCTION "guard_payments_reverses_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."reverses_id" IS NOT NULL AND btrim(CAST(NEW."reverses_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."reverses_id") AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "reverses_id" IS NOT DISTINCT FROM NEW."reverses_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_payments_reverses_id_ref_ins" BEFORE INSERT ON "payments" FOR EACH ROW EXECUTE FUNCTION "guard_payments_reverses_id_ref_chk"();
CREATE TRIGGER "guard_payments_reverses_id_ref_upd" BEFORE UPDATE OF "reverses_id" ON "payments" FOR EACH ROW
WHEN (OLD."reverses_id" IS DISTINCT FROM NEW."reverses_id") EXECUTE FUNCTION "guard_payments_reverses_id_ref_chk"();
CREATE FUNCTION "guard_payments_reverses_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "payments" WHERE "reverses_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_payments_reverses_id_ref_del" BEFORE DELETE ON "payments" FOR EACH ROW EXECUTE FUNCTION "guard_payments_reverses_id_ref_use"();
ALTER TABLE "payments" ADD CONSTRAINT "guard_payments_reversal_reverses_implies" CHECK (NOT COALESCE((COALESCE("kind" = 'reversal', FALSE) AND NOT COALESCE(("reverses_id" IS NOT NULL AND btrim(CAST("reverses_id" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE)), FALSE));
CREATE FUNCTION "guard_payments_append_only_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'permita38:state.append_only' USING ERRCODE = '23000';
END;
$$;
CREATE TRIGGER "guard_payments_append_only_upd" BEFORE UPDATE ON "payments" FOR EACH ROW EXECUTE FUNCTION "guard_payments_append_only_fn"();
CREATE TRIGGER "guard_payments_append_only_del" BEFORE DELETE ON "payments" FOR EACH ROW EXECUTE FUNCTION "guard_payments_append_only_fn"();
CREATE TRIGGER "guard_payments_append_only_trunc" BEFORE TRUNCATE ON "payments" FOR EACH STATEMENT EXECUTE FUNCTION "guard_payments_append_only_fn"();
ALTER TABLE "period_locks" ADD CONSTRAINT "guard_period_locks_year_required" CHECK (NOT COALESCE((NOT ("year" IS NOT NULL AND btrim(CAST("year" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "period_locks" ADD CONSTRAINT "guard_period_locks_month_required" CHECK (NOT COALESCE((NOT ("month" IS NOT NULL AND btrim(CAST("month" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "period_locks" ADD CONSTRAINT "guard_period_locks_locked_at_required" CHECK (NOT COALESCE((NOT ("locked_at" IS NOT NULL AND btrim(CAST("locked_at" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "period_locks" ADD CONSTRAINT "guard_period_locks_locked_by_required" CHECK (NOT COALESCE((NOT ("locked_by" IS NOT NULL AND btrim(CAST("locked_by" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "period_locks" ADD CONSTRAINT "guard_period_locks_reason_required" CHECK (NOT COALESCE((NOT ("reason" IS NOT NULL AND btrim(CAST("reason" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "period_locks" ADD CONSTRAINT "guard_period_locks_month_range" CHECK (NOT COALESCE(("month" IS NOT NULL AND ("month" < 1 OR "month" > 12)), FALSE));
CREATE FUNCTION "guard_period_locks_append_only_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'permita38:state.append_only' USING ERRCODE = '23000';
END;
$$;
CREATE TRIGGER "guard_period_locks_append_only_upd" BEFORE UPDATE ON "period_locks" FOR EACH ROW EXECUTE FUNCTION "guard_period_locks_append_only_fn"();
CREATE TRIGGER "guard_period_locks_append_only_del" BEFORE DELETE ON "period_locks" FOR EACH ROW EXECUTE FUNCTION "guard_period_locks_append_only_fn"();
CREATE TRIGGER "guard_period_locks_append_only_trunc" BEFORE TRUNCATE ON "period_locks" FOR EACH STATEMENT EXECUTE FUNCTION "guard_period_locks_append_only_fn"();
