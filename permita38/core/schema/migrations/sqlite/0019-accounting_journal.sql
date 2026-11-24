-- migration: 0aacb7a9
-- requires: 7ca84bdc
-- permitA38 migration: accounting-journal (sqlite)

-- accounting journal (docs/specs/accounting.md sections 7 and 8.1): internal tables, append-only journal

CREATE TABLE "fiscal_years" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "year" INTEGER,
    "chart" TEXT,
    "income_basis" TEXT,
    "vat_basis" TEXT,
    "vat_frequency" TEXT,
    "dauerfrist" INTEGER,
    "small_business" INTEGER,
    "vat_regular_from" TEXT,
    "rules_version" TEXT,
    "opened_at" INTEGER,
    "opened_by" TEXT,
    "closed_at" INTEGER,
    "closed_by" TEXT,
    "extra" TEXT
);
CREATE UNIQUE INDEX "idx_fiscal_years_year" ON "fiscal_years" ("year");

CREATE TABLE "journal_entries" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "fiscal_year" INTEGER,
    "entry_no" INTEGER,
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
    "created_at" INTEGER,
    "created_by" TEXT,
    "prev_hash" TEXT,
    "hash" TEXT,
    "extra" TEXT
);
CREATE UNIQUE INDEX "idx_journal_entries_event_key" ON "journal_entries" ("event_key");
CREATE UNIQUE INDEX "idx_journal_entries_year_no" ON "journal_entries" ("fiscal_year", "entry_no");
CREATE INDEX "idx_journal_entries_source" ON "journal_entries" ("source_id");
CREATE INDEX "idx_journal_entries_booking" ON "journal_entries" ("booking_date");

CREATE TABLE "journal_lines" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "entry_id" TEXT,
    "line_no" INTEGER,
    "role" TEXT,
    "account" TEXT,
    "amount_cents" INTEGER,
    "tax_key" TEXT,
    "tax_kind" TEXT,
    "tax_rate" TEXT,
    "tax_base_cents" INTEGER,
    "vat_date" TEXT,
    "income_date" TEXT,
    "open_item_id" TEXT,
    "treatment" TEXT,
    "extra" TEXT
);
CREATE UNIQUE INDEX "idx_journal_lines_entry_line" ON "journal_lines" ("entry_id", "line_no");
CREATE INDEX "idx_journal_lines_vat" ON "journal_lines" ("vat_date", "tax_key");
CREATE INDEX "idx_journal_lines_income" ON "journal_lines" ("income_date");
CREATE INDEX "idx_journal_lines_open_item" ON "journal_lines" ("open_item_id");

CREATE TABLE "payments" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "document_id" TEXT,
    "date" TEXT,
    "amount_cents" INTEGER,
    "method" TEXT,
    "kind" TEXT,
    "note" TEXT,
    "reverses_id" TEXT,
    "created_at" INTEGER,
    "created_by" TEXT,
    "extra" TEXT,
    CONSTRAINT "payments_amount_nonzero" CHECK ("amount_cents" <> 0)
);
CREATE INDEX "idx_payments_document" ON "payments" ("document_id");

CREATE TABLE "period_locks" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "year" INTEGER,
    "month" INTEGER,
    "locked_at" INTEGER,
    "locked_by" TEXT,
    "reason" TEXT,
    "extra" TEXT,
    CONSTRAINT "period_locks_reason_vocab" CHECK ("reason" IN ('manual', 'year_close') OR "reason" LIKE 'vat_return:%')
);
CREATE UNIQUE INDEX "idx_period_locks_year_month" ON "period_locks" ("year", "month");

-- guards: generated from core/repository/schema.py GUARDS by Dialect.guard_sql (a test keeps them equal)
CREATE TRIGGER "guard_fiscal_years_year_required_ins" BEFORE INSERT ON "fiscal_years"
WHEN (NOT (NEW."year" IS NOT NULL AND trim(NEW."year", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "year" IS NEW."year")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_year_required_upd" BEFORE UPDATE OF "year" ON "fiscal_years"
WHEN (NOT (NEW."year" IS NOT NULL AND trim(NEW."year", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."year" IS NOT OLD."year")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_chart_required_ins" BEFORE INSERT ON "fiscal_years"
WHEN (NOT (NEW."chart" IS NOT NULL AND trim(NEW."chart", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "chart" IS NEW."chart")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_chart_required_upd" BEFORE UPDATE OF "chart" ON "fiscal_years"
WHEN (NOT (NEW."chart" IS NOT NULL AND trim(NEW."chart", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."chart" IS NOT OLD."chart")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_income_basis_required_ins" BEFORE INSERT ON "fiscal_years"
WHEN (NOT (NEW."income_basis" IS NOT NULL AND trim(NEW."income_basis", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "income_basis" IS NEW."income_basis")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_income_basis_required_upd" BEFORE UPDATE OF "income_basis" ON "fiscal_years"
WHEN (NOT (NEW."income_basis" IS NOT NULL AND trim(NEW."income_basis", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."income_basis" IS NOT OLD."income_basis")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_vat_basis_required_ins" BEFORE INSERT ON "fiscal_years"
WHEN (NOT (NEW."vat_basis" IS NOT NULL AND trim(NEW."vat_basis", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "vat_basis" IS NEW."vat_basis")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_vat_basis_required_upd" BEFORE UPDATE OF "vat_basis" ON "fiscal_years"
WHEN (NOT (NEW."vat_basis" IS NOT NULL AND trim(NEW."vat_basis", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."vat_basis" IS NOT OLD."vat_basis")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_vat_frequency_required_ins" BEFORE INSERT ON "fiscal_years"
WHEN (NOT (NEW."vat_frequency" IS NOT NULL AND trim(NEW."vat_frequency", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "vat_frequency" IS NEW."vat_frequency")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_vat_frequency_required_upd" BEFORE UPDATE OF "vat_frequency" ON "fiscal_years"
WHEN (NOT (NEW."vat_frequency" IS NOT NULL AND trim(NEW."vat_frequency", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."vat_frequency" IS NOT OLD."vat_frequency")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_dauerfrist_required_ins" BEFORE INSERT ON "fiscal_years"
WHEN (NOT (NEW."dauerfrist" IS NOT NULL AND trim(NEW."dauerfrist", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "dauerfrist" IS NEW."dauerfrist")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_dauerfrist_required_upd" BEFORE UPDATE OF "dauerfrist" ON "fiscal_years"
WHEN (NOT (NEW."dauerfrist" IS NOT NULL AND trim(NEW."dauerfrist", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."dauerfrist" IS NOT OLD."dauerfrist")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_small_business_required_ins" BEFORE INSERT ON "fiscal_years"
WHEN (NOT (NEW."small_business" IS NOT NULL AND trim(NEW."small_business", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "small_business" IS NEW."small_business")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_small_business_required_upd" BEFORE UPDATE OF "small_business" ON "fiscal_years"
WHEN (NOT (NEW."small_business" IS NOT NULL AND trim(NEW."small_business", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."small_business" IS NOT OLD."small_business")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_rules_version_required_ins" BEFORE INSERT ON "fiscal_years"
WHEN (NOT (NEW."rules_version" IS NOT NULL AND trim(NEW."rules_version", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "rules_version" IS NEW."rules_version")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_rules_version_required_upd" BEFORE UPDATE OF "rules_version" ON "fiscal_years"
WHEN (NOT (NEW."rules_version" IS NOT NULL AND trim(NEW."rules_version", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."rules_version" IS NOT OLD."rules_version")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_opened_at_required_ins" BEFORE INSERT ON "fiscal_years"
WHEN (NOT (NEW."opened_at" IS NOT NULL AND trim(NEW."opened_at", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "opened_at" IS NEW."opened_at")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_opened_at_required_upd" BEFORE UPDATE OF "opened_at" ON "fiscal_years"
WHEN (NOT (NEW."opened_at" IS NOT NULL AND trim(NEW."opened_at", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."opened_at" IS NOT OLD."opened_at")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_opened_by_required_ins" BEFORE INSERT ON "fiscal_years"
WHEN (NOT (NEW."opened_by" IS NOT NULL AND trim(NEW."opened_by", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "opened_by" IS NEW."opened_by")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_opened_by_required_upd" BEFORE UPDATE OF "opened_by" ON "fiscal_years"
WHEN (NOT (NEW."opened_by" IS NOT NULL AND trim(NEW."opened_by", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."opened_by" IS NOT OLD."opened_by")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_fiscal_years_vat_regular_from_date_ins" BEFORE INSERT ON "fiscal_years"
WHEN NEW."vat_regular_from" IS NOT NULL AND NEW."vat_regular_from" <> '' AND NOT (NEW."vat_regular_from" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "vat_regular_from" IS NEW."vat_regular_from")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_fiscal_years_vat_regular_from_date_upd" BEFORE UPDATE OF "vat_regular_from" ON "fiscal_years"
WHEN NEW."vat_regular_from" IS NOT NULL AND NEW."vat_regular_from" <> '' AND NOT (NEW."vat_regular_from" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."vat_regular_from" IS NOT OLD."vat_regular_from")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_fiscal_years_chart_one_of_ins" BEFORE INSERT ON "fiscal_years"
WHEN ((NEW."chart" IS NOT NULL AND trim(NEW."chart", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."chart" NOT IN ('skr03', 'skr04'))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "chart" IS NEW."chart")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_fiscal_years_chart_one_of_upd" BEFORE UPDATE OF "chart" ON "fiscal_years"
WHEN ((NEW."chart" IS NOT NULL AND trim(NEW."chart", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."chart" NOT IN ('skr03', 'skr04'))
 AND (NEW."chart" IS NOT OLD."chart")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_fiscal_years_income_basis_one_of_ins" BEFORE INSERT ON "fiscal_years"
WHEN ((NEW."income_basis" IS NOT NULL AND trim(NEW."income_basis", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."income_basis" NOT IN ('eur', 'accrual'))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "income_basis" IS NEW."income_basis")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_fiscal_years_income_basis_one_of_upd" BEFORE UPDATE OF "income_basis" ON "fiscal_years"
WHEN ((NEW."income_basis" IS NOT NULL AND trim(NEW."income_basis", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."income_basis" NOT IN ('eur', 'accrual'))
 AND (NEW."income_basis" IS NOT OLD."income_basis")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_fiscal_years_vat_basis_one_of_ins" BEFORE INSERT ON "fiscal_years"
WHEN ((NEW."vat_basis" IS NOT NULL AND trim(NEW."vat_basis", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."vat_basis" NOT IN ('soll', 'ist'))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "vat_basis" IS NEW."vat_basis")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_fiscal_years_vat_basis_one_of_upd" BEFORE UPDATE OF "vat_basis" ON "fiscal_years"
WHEN ((NEW."vat_basis" IS NOT NULL AND trim(NEW."vat_basis", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."vat_basis" NOT IN ('soll', 'ist'))
 AND (NEW."vat_basis" IS NOT OLD."vat_basis")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_fiscal_years_vat_frequency_one_of_ins" BEFORE INSERT ON "fiscal_years"
WHEN ((NEW."vat_frequency" IS NOT NULL AND trim(NEW."vat_frequency", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."vat_frequency" NOT IN ('month', 'quarter', 'none'))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "vat_frequency" IS NEW."vat_frequency")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_fiscal_years_vat_frequency_one_of_upd" BEFORE UPDATE OF "vat_frequency" ON "fiscal_years"
WHEN ((NEW."vat_frequency" IS NOT NULL AND trim(NEW."vat_frequency", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."vat_frequency" NOT IN ('month', 'quarter', 'none'))
 AND (NEW."vat_frequency" IS NOT OLD."vat_frequency")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_fiscal_years_closed_by_implies_ins" BEFORE INSERT ON "fiscal_years"
WHEN (COALESCE((NEW."closed_at" IS NOT NULL AND trim(NEW."closed_at", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0) AND NOT COALESCE((NEW."closed_by" IS NOT NULL AND trim(NEW."closed_by", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "closed_at" IS NEW."closed_at" AND "closed_by" IS NEW."closed_by")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_fiscal_years_closed_by_implies'); END;
CREATE TRIGGER "guard_fiscal_years_closed_by_implies_upd" BEFORE UPDATE OF "closed_at", "closed_by" ON "fiscal_years"
WHEN (COALESCE((NEW."closed_at" IS NOT NULL AND trim(NEW."closed_at", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0) AND NOT COALESCE((NEW."closed_by" IS NOT NULL AND trim(NEW."closed_by", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND (NEW."closed_at" IS NOT OLD."closed_at" OR NEW."closed_by" IS NOT OLD."closed_by")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_fiscal_years_closed_by_implies'); END;
CREATE TRIGGER "guard_fiscal_years_closed_at_implies_ins" BEFORE INSERT ON "fiscal_years"
WHEN (COALESCE((NEW."closed_by" IS NOT NULL AND trim(NEW."closed_by", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0) AND NOT COALESCE((NEW."closed_at" IS NOT NULL AND trim(NEW."closed_at", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "closed_by" IS NEW."closed_by" AND "closed_at" IS NEW."closed_at")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_fiscal_years_closed_at_implies'); END;
CREATE TRIGGER "guard_fiscal_years_closed_at_implies_upd" BEFORE UPDATE OF "closed_by", "closed_at" ON "fiscal_years"
WHEN (COALESCE((NEW."closed_by" IS NOT NULL AND trim(NEW."closed_by", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0) AND NOT COALESCE((NEW."closed_at" IS NOT NULL AND trim(NEW."closed_at", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND (NEW."closed_by" IS NOT OLD."closed_by" OR NEW."closed_at" IS NOT OLD."closed_at")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_fiscal_years_closed_at_implies'); END;
CREATE TRIGGER "guard_fiscal_years_year_range_ins" BEFORE INSERT ON "fiscal_years"
WHEN (NEW."year" IS NOT NULL AND NEW."year" <> '' AND (NEW."year" < 2000 OR NEW."year" > 2100))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "year" IS NEW."year")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_fiscal_years_year_range_upd" BEFORE UPDATE OF "year" ON "fiscal_years"
WHEN (NEW."year" IS NOT NULL AND NEW."year" <> '' AND (NEW."year" < 2000 OR NEW."year" > 2100))
 AND (NEW."year" IS NOT OLD."year")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_journal_entries_fiscal_year_required_ins" BEFORE INSERT ON "journal_entries"
WHEN (NOT (NEW."fiscal_year" IS NOT NULL AND trim(NEW."fiscal_year", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "fiscal_year" IS NEW."fiscal_year")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_fiscal_year_required_upd" BEFORE UPDATE OF "fiscal_year" ON "journal_entries"
WHEN (NOT (NEW."fiscal_year" IS NOT NULL AND trim(NEW."fiscal_year", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."fiscal_year" IS NOT OLD."fiscal_year")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_entry_no_required_ins" BEFORE INSERT ON "journal_entries"
WHEN (NOT (NEW."entry_no" IS NOT NULL AND trim(NEW."entry_no", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "entry_no" IS NEW."entry_no")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_entry_no_required_upd" BEFORE UPDATE OF "entry_no" ON "journal_entries"
WHEN (NOT (NEW."entry_no" IS NOT NULL AND trim(NEW."entry_no", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."entry_no" IS NOT OLD."entry_no")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_document_date_required_ins" BEFORE INSERT ON "journal_entries"
WHEN (NOT (NEW."document_date" IS NOT NULL AND trim(NEW."document_date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "document_date" IS NEW."document_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_document_date_required_upd" BEFORE UPDATE OF "document_date" ON "journal_entries"
WHEN (NOT (NEW."document_date" IS NOT NULL AND trim(NEW."document_date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."document_date" IS NOT OLD."document_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_booking_date_required_ins" BEFORE INSERT ON "journal_entries"
WHEN (NOT (NEW."booking_date" IS NOT NULL AND trim(NEW."booking_date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "booking_date" IS NEW."booking_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_booking_date_required_upd" BEFORE UPDATE OF "booking_date" ON "journal_entries"
WHEN (NOT (NEW."booking_date" IS NOT NULL AND trim(NEW."booking_date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."booking_date" IS NOT OLD."booking_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_event_required_ins" BEFORE INSERT ON "journal_entries"
WHEN (NOT (NEW."event" IS NOT NULL AND trim(NEW."event", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "event" IS NEW."event")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_event_required_upd" BEFORE UPDATE OF "event" ON "journal_entries"
WHEN (NOT (NEW."event" IS NOT NULL AND trim(NEW."event", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."event" IS NOT OLD."event")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_event_key_required_ins" BEFORE INSERT ON "journal_entries"
WHEN (NOT (NEW."event_key" IS NOT NULL AND trim(NEW."event_key", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "event_key" IS NEW."event_key")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_event_key_required_upd" BEFORE UPDATE OF "event_key" ON "journal_entries"
WHEN (NOT (NEW."event_key" IS NOT NULL AND trim(NEW."event_key", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."event_key" IS NOT OLD."event_key")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_text_required_ins" BEFORE INSERT ON "journal_entries"
WHEN (NOT (NEW."text" IS NOT NULL AND trim(NEW."text", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "text" IS NEW."text")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_text_required_upd" BEFORE UPDATE OF "text" ON "journal_entries"
WHEN (NOT (NEW."text" IS NOT NULL AND trim(NEW."text", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."text" IS NOT OLD."text")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_origin_required_ins" BEFORE INSERT ON "journal_entries"
WHEN (NOT (NEW."origin" IS NOT NULL AND trim(NEW."origin", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "origin" IS NEW."origin")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_origin_required_upd" BEFORE UPDATE OF "origin" ON "journal_entries"
WHEN (NOT (NEW."origin" IS NOT NULL AND trim(NEW."origin", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."origin" IS NOT OLD."origin")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_created_at_required_ins" BEFORE INSERT ON "journal_entries"
WHEN (NOT (NEW."created_at" IS NOT NULL AND trim(NEW."created_at", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "created_at" IS NEW."created_at")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_created_at_required_upd" BEFORE UPDATE OF "created_at" ON "journal_entries"
WHEN (NOT (NEW."created_at" IS NOT NULL AND trim(NEW."created_at", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."created_at" IS NOT OLD."created_at")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_created_by_required_ins" BEFORE INSERT ON "journal_entries"
WHEN (NOT (NEW."created_by" IS NOT NULL AND trim(NEW."created_by", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "created_by" IS NEW."created_by")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_created_by_required_upd" BEFORE UPDATE OF "created_by" ON "journal_entries"
WHEN (NOT (NEW."created_by" IS NOT NULL AND trim(NEW."created_by", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."created_by" IS NOT OLD."created_by")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_prev_hash_required_ins" BEFORE INSERT ON "journal_entries"
WHEN (NOT (NEW."prev_hash" IS NOT NULL AND trim(NEW."prev_hash", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "prev_hash" IS NEW."prev_hash")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_prev_hash_required_upd" BEFORE UPDATE OF "prev_hash" ON "journal_entries"
WHEN (NOT (NEW."prev_hash" IS NOT NULL AND trim(NEW."prev_hash", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."prev_hash" IS NOT OLD."prev_hash")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_hash_required_ins" BEFORE INSERT ON "journal_entries"
WHEN (NOT (NEW."hash" IS NOT NULL AND trim(NEW."hash", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "hash" IS NEW."hash")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_hash_required_upd" BEFORE UPDATE OF "hash" ON "journal_entries"
WHEN (NOT (NEW."hash" IS NOT NULL AND trim(NEW."hash", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."hash" IS NOT OLD."hash")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_entries_document_date_date_ins" BEFORE INSERT ON "journal_entries"
WHEN NEW."document_date" IS NOT NULL AND NEW."document_date" <> '' AND NOT (NEW."document_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "document_date" IS NEW."document_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_journal_entries_document_date_date_upd" BEFORE UPDATE OF "document_date" ON "journal_entries"
WHEN NEW."document_date" IS NOT NULL AND NEW."document_date" <> '' AND NOT (NEW."document_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."document_date" IS NOT OLD."document_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_journal_entries_booking_date_date_ins" BEFORE INSERT ON "journal_entries"
WHEN NEW."booking_date" IS NOT NULL AND NEW."booking_date" <> '' AND NOT (NEW."booking_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "booking_date" IS NEW."booking_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_journal_entries_booking_date_date_upd" BEFORE UPDATE OF "booking_date" ON "journal_entries"
WHEN NEW."booking_date" IS NOT NULL AND NEW."booking_date" <> '' AND NOT (NEW."booking_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."booking_date" IS NOT OLD."booking_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_journal_entries_origin_one_of_ins" BEFORE INSERT ON "journal_entries"
WHEN ((NEW."origin" IS NOT NULL AND trim(NEW."origin", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."origin" NOT IN ('auto', 'manual', 'opening'))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "origin" IS NEW."origin")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_journal_entries_origin_one_of_upd" BEFORE UPDATE OF "origin" ON "journal_entries"
WHEN ((NEW."origin" IS NOT NULL AND trim(NEW."origin", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."origin" NOT IN ('auto', 'manual', 'opening'))
 AND (NEW."origin" IS NOT OLD."origin")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_journal_entries_fiscal_year_range_ins" BEFORE INSERT ON "journal_entries"
WHEN (NEW."fiscal_year" IS NOT NULL AND NEW."fiscal_year" <> '' AND (NEW."fiscal_year" < 2000 OR NEW."fiscal_year" > 2100))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "fiscal_year" IS NEW."fiscal_year")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_journal_entries_fiscal_year_range_upd" BEFORE UPDATE OF "fiscal_year" ON "journal_entries"
WHEN (NEW."fiscal_year" IS NOT NULL AND NEW."fiscal_year" <> '' AND (NEW."fiscal_year" < 2000 OR NEW."fiscal_year" > 2100))
 AND (NEW."fiscal_year" IS NOT OLD."fiscal_year")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_journal_entries_entry_no_range_ins" BEFORE INSERT ON "journal_entries"
WHEN (NEW."entry_no" IS NOT NULL AND NEW."entry_no" <> '' AND (NEW."entry_no" < 1))
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "entry_no" IS NEW."entry_no")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_journal_entries_entry_no_range_upd" BEFORE UPDATE OF "entry_no" ON "journal_entries"
WHEN (NEW."entry_no" IS NOT NULL AND NEW."entry_no" <> '' AND (NEW."entry_no" < 1))
 AND (NEW."entry_no" IS NOT OLD."entry_no")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_journal_entries_reverses_id_ref_ins" BEFORE INSERT ON "journal_entries"
WHEN (NEW."reverses_id" IS NOT NULL AND trim(NEW."reverses_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."reverses_id")
 AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."id" AND "reverses_id" IS NEW."reverses_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_journal_entries_reverses_id_ref_upd" BEFORE UPDATE OF "reverses_id" ON "journal_entries"
WHEN (NEW."reverses_id" IS NOT NULL AND trim(NEW."reverses_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."reverses_id")
 AND (NEW."reverses_id" IS NOT OLD."reverses_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_journal_entries_reverses_id_ref_del" BEFORE DELETE ON "journal_entries"
WHEN EXISTS (SELECT 1 FROM "journal_entries" WHERE "reverses_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_journal_entries_append_only_upd" BEFORE UPDATE ON "journal_entries"
BEGIN SELECT RAISE(ABORT, 'permita38:state.append_only'); END;
CREATE TRIGGER "guard_journal_entries_append_only_del" BEFORE DELETE ON "journal_entries"
BEGIN SELECT RAISE(ABORT, 'permita38:state.append_only'); END;
CREATE TRIGGER "guard_journal_lines_entry_id_required_ins" BEFORE INSERT ON "journal_lines"
WHEN (NOT (NEW."entry_id" IS NOT NULL AND trim(NEW."entry_id", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_lines" WHERE "id" = NEW."id" AND "entry_id" IS NEW."entry_id")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_lines_entry_id_required_upd" BEFORE UPDATE OF "entry_id" ON "journal_lines"
WHEN (NOT (NEW."entry_id" IS NOT NULL AND trim(NEW."entry_id", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."entry_id" IS NOT OLD."entry_id")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_lines_line_no_required_ins" BEFORE INSERT ON "journal_lines"
WHEN (NOT (NEW."line_no" IS NOT NULL AND trim(NEW."line_no", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_lines" WHERE "id" = NEW."id" AND "line_no" IS NEW."line_no")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_lines_line_no_required_upd" BEFORE UPDATE OF "line_no" ON "journal_lines"
WHEN (NOT (NEW."line_no" IS NOT NULL AND trim(NEW."line_no", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."line_no" IS NOT OLD."line_no")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_lines_account_required_ins" BEFORE INSERT ON "journal_lines"
WHEN (NOT (NEW."account" IS NOT NULL AND trim(NEW."account", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_lines" WHERE "id" = NEW."id" AND "account" IS NEW."account")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_lines_account_required_upd" BEFORE UPDATE OF "account" ON "journal_lines"
WHEN (NOT (NEW."account" IS NOT NULL AND trim(NEW."account", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."account" IS NOT OLD."account")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_lines_amount_cents_required_ins" BEFORE INSERT ON "journal_lines"
WHEN (NOT (NEW."amount_cents" IS NOT NULL AND trim(NEW."amount_cents", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "journal_lines" WHERE "id" = NEW."id" AND "amount_cents" IS NEW."amount_cents")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_lines_amount_cents_required_upd" BEFORE UPDATE OF "amount_cents" ON "journal_lines"
WHEN (NOT (NEW."amount_cents" IS NOT NULL AND trim(NEW."amount_cents", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."amount_cents" IS NOT OLD."amount_cents")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_journal_lines_vat_date_date_ins" BEFORE INSERT ON "journal_lines"
WHEN NEW."vat_date" IS NOT NULL AND NEW."vat_date" <> '' AND NOT (NEW."vat_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "journal_lines" WHERE "id" = NEW."id" AND "vat_date" IS NEW."vat_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_journal_lines_vat_date_date_upd" BEFORE UPDATE OF "vat_date" ON "journal_lines"
WHEN NEW."vat_date" IS NOT NULL AND NEW."vat_date" <> '' AND NOT (NEW."vat_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."vat_date" IS NOT OLD."vat_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_journal_lines_income_date_date_ins" BEFORE INSERT ON "journal_lines"
WHEN NEW."income_date" IS NOT NULL AND NEW."income_date" <> '' AND NOT (NEW."income_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "journal_lines" WHERE "id" = NEW."id" AND "income_date" IS NEW."income_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_journal_lines_income_date_date_upd" BEFORE UPDATE OF "income_date" ON "journal_lines"
WHEN NEW."income_date" IS NOT NULL AND NEW."income_date" <> '' AND NOT (NEW."income_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."income_date" IS NOT OLD."income_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_journal_lines_tax_kind_one_of_ins" BEFORE INSERT ON "journal_lines"
WHEN ((NEW."tax_kind" IS NOT NULL AND trim(NEW."tax_kind", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."tax_kind" NOT IN ('tax', 'input', 'base_only'))
 AND NOT EXISTS (SELECT 1 FROM "journal_lines" WHERE "id" = NEW."id" AND "tax_kind" IS NEW."tax_kind")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_journal_lines_tax_kind_one_of_upd" BEFORE UPDATE OF "tax_kind" ON "journal_lines"
WHEN ((NEW."tax_kind" IS NOT NULL AND trim(NEW."tax_kind", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."tax_kind" NOT IN ('tax', 'input', 'base_only'))
 AND (NEW."tax_kind" IS NOT OLD."tax_kind")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_journal_lines_tax_key_one_of_ins" BEFORE INSERT ON "journal_lines"
WHEN ((NEW."tax_key" IS NOT NULL AND trim(NEW."tax_key", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."tax_key" NOT IN ('EU_SVC', 'KU', 'NONE', 'RC_3C_SVC', 'RC_EU_SVC', 'S19', 'S7', 'SX', 'VST'))
 AND NOT EXISTS (SELECT 1 FROM "journal_lines" WHERE "id" = NEW."id" AND "tax_key" IS NEW."tax_key")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_journal_lines_tax_key_one_of_upd" BEFORE UPDATE OF "tax_key" ON "journal_lines"
WHEN ((NEW."tax_key" IS NOT NULL AND trim(NEW."tax_key", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."tax_key" NOT IN ('EU_SVC', 'KU', 'NONE', 'RC_3C_SVC', 'RC_EU_SVC', 'S19', 'S7', 'SX', 'VST'))
 AND (NEW."tax_key" IS NOT OLD."tax_key")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_journal_lines_line_no_range_ins" BEFORE INSERT ON "journal_lines"
WHEN (NEW."line_no" IS NOT NULL AND NEW."line_no" <> '' AND (NEW."line_no" < 1))
 AND NOT EXISTS (SELECT 1 FROM "journal_lines" WHERE "id" = NEW."id" AND "line_no" IS NEW."line_no")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_journal_lines_line_no_range_upd" BEFORE UPDATE OF "line_no" ON "journal_lines"
WHEN (NEW."line_no" IS NOT NULL AND NEW."line_no" <> '' AND (NEW."line_no" < 1))
 AND (NEW."line_no" IS NOT OLD."line_no")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_journal_lines_entry_id_ref_ins" BEFORE INSERT ON "journal_lines"
WHEN (NEW."entry_id" IS NOT NULL AND trim(NEW."entry_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."entry_id")
 AND NOT EXISTS (SELECT 1 FROM "journal_lines" WHERE "id" = NEW."id" AND "entry_id" IS NEW."entry_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_journal_lines_entry_id_ref_upd" BEFORE UPDATE OF "entry_id" ON "journal_lines"
WHEN (NEW."entry_id" IS NOT NULL AND trim(NEW."entry_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "journal_entries" WHERE "id" = NEW."entry_id")
 AND (NEW."entry_id" IS NOT OLD."entry_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_journal_lines_entry_id_ref_del" BEFORE DELETE ON "journal_entries"
WHEN EXISTS (SELECT 1 FROM "journal_lines" WHERE "entry_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_journal_lines_tax_kind_key_implies_ins" BEFORE INSERT ON "journal_lines"
WHEN (COALESCE((NEW."tax_kind" IS NOT NULL AND trim(NEW."tax_kind", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0) AND NOT COALESCE((NEW."tax_key" IS NOT NULL AND trim(NEW."tax_key", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND NOT EXISTS (SELECT 1 FROM "journal_lines" WHERE "id" = NEW."id" AND "tax_kind" IS NEW."tax_kind" AND "tax_key" IS NEW."tax_key")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_journal_lines_tax_kind_key_implies'); END;
CREATE TRIGGER "guard_journal_lines_tax_kind_key_implies_upd" BEFORE UPDATE OF "tax_kind", "tax_key" ON "journal_lines"
WHEN (COALESCE((NEW."tax_kind" IS NOT NULL AND trim(NEW."tax_kind", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0) AND NOT COALESCE((NEW."tax_key" IS NOT NULL AND trim(NEW."tax_key", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND (NEW."tax_kind" IS NOT OLD."tax_kind" OR NEW."tax_key" IS NOT OLD."tax_key")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_journal_lines_tax_kind_key_implies'); END;
CREATE TRIGGER "guard_journal_lines_tax_key_vat_date_implies_ins" BEFORE INSERT ON "journal_lines"
WHEN (COALESCE(NEW."tax_key" IN ('EU_SVC', 'KU', 'RC_3C_SVC', 'RC_EU_SVC', 'S19', 'S7', 'SX', 'VST'), 0) AND NOT COALESCE((NEW."vat_date" IS NOT NULL AND trim(NEW."vat_date", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND NOT EXISTS (SELECT 1 FROM "journal_lines" WHERE "id" = NEW."id" AND "tax_key" IS NEW."tax_key" AND "vat_date" IS NEW."vat_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_journal_lines_tax_key_vat_date_implies'); END;
CREATE TRIGGER "guard_journal_lines_tax_key_vat_date_implies_upd" BEFORE UPDATE OF "tax_key", "vat_date" ON "journal_lines"
WHEN (COALESCE(NEW."tax_key" IN ('EU_SVC', 'KU', 'RC_3C_SVC', 'RC_EU_SVC', 'S19', 'S7', 'SX', 'VST'), 0) AND NOT COALESCE((NEW."vat_date" IS NOT NULL AND trim(NEW."vat_date", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND (NEW."tax_key" IS NOT OLD."tax_key" OR NEW."vat_date" IS NOT OLD."vat_date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_journal_lines_tax_key_vat_date_implies'); END;
CREATE TRIGGER "guard_journal_lines_append_only_upd" BEFORE UPDATE ON "journal_lines"
BEGIN SELECT RAISE(ABORT, 'permita38:state.append_only'); END;
CREATE TRIGGER "guard_journal_lines_append_only_del" BEFORE DELETE ON "journal_lines"
BEGIN SELECT RAISE(ABORT, 'permita38:state.append_only'); END;
CREATE TRIGGER "guard_payments_document_id_required_ins" BEFORE INSERT ON "payments"
WHEN (NOT (NEW."document_id" IS NOT NULL AND trim(NEW."document_id", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "document_id" IS NEW."document_id")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_document_id_required_upd" BEFORE UPDATE OF "document_id" ON "payments"
WHEN (NOT (NEW."document_id" IS NOT NULL AND trim(NEW."document_id", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."document_id" IS NOT OLD."document_id")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_date_required_ins" BEFORE INSERT ON "payments"
WHEN (NOT (NEW."date" IS NOT NULL AND trim(NEW."date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "date" IS NEW."date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_date_required_upd" BEFORE UPDATE OF "date" ON "payments"
WHEN (NOT (NEW."date" IS NOT NULL AND trim(NEW."date", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."date" IS NOT OLD."date")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_amount_cents_required_ins" BEFORE INSERT ON "payments"
WHEN (NOT (NEW."amount_cents" IS NOT NULL AND trim(NEW."amount_cents", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "amount_cents" IS NEW."amount_cents")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_amount_cents_required_upd" BEFORE UPDATE OF "amount_cents" ON "payments"
WHEN (NOT (NEW."amount_cents" IS NOT NULL AND trim(NEW."amount_cents", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."amount_cents" IS NOT OLD."amount_cents")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_method_required_ins" BEFORE INSERT ON "payments"
WHEN (NOT (NEW."method" IS NOT NULL AND trim(NEW."method", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "method" IS NEW."method")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_method_required_upd" BEFORE UPDATE OF "method" ON "payments"
WHEN (NOT (NEW."method" IS NOT NULL AND trim(NEW."method", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."method" IS NOT OLD."method")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_kind_required_ins" BEFORE INSERT ON "payments"
WHEN (NOT (NEW."kind" IS NOT NULL AND trim(NEW."kind", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "kind" IS NEW."kind")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_kind_required_upd" BEFORE UPDATE OF "kind" ON "payments"
WHEN (NOT (NEW."kind" IS NOT NULL AND trim(NEW."kind", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."kind" IS NOT OLD."kind")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_created_at_required_ins" BEFORE INSERT ON "payments"
WHEN (NOT (NEW."created_at" IS NOT NULL AND trim(NEW."created_at", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "created_at" IS NEW."created_at")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_created_at_required_upd" BEFORE UPDATE OF "created_at" ON "payments"
WHEN (NOT (NEW."created_at" IS NOT NULL AND trim(NEW."created_at", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."created_at" IS NOT OLD."created_at")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_created_by_required_ins" BEFORE INSERT ON "payments"
WHEN (NOT (NEW."created_by" IS NOT NULL AND trim(NEW."created_by", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "created_by" IS NEW."created_by")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_created_by_required_upd" BEFORE UPDATE OF "created_by" ON "payments"
WHEN (NOT (NEW."created_by" IS NOT NULL AND trim(NEW."created_by", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."created_by" IS NOT OLD."created_by")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_payments_date_date_ins" BEFORE INSERT ON "payments"
WHEN NEW."date" IS NOT NULL AND NEW."date" <> '' AND NOT (NEW."date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "date" IS NEW."date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_payments_date_date_upd" BEFORE UPDATE OF "date" ON "payments"
WHEN NEW."date" IS NOT NULL AND NEW."date" <> '' AND NOT (NEW."date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."date" IS NOT OLD."date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_payments_method_one_of_ins" BEFORE INSERT ON "payments"
WHEN ((NEW."method" IS NOT NULL AND trim(NEW."method", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."method" NOT IN ('bank', 'cash'))
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "method" IS NEW."method")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_payments_method_one_of_upd" BEFORE UPDATE OF "method" ON "payments"
WHEN ((NEW."method" IS NOT NULL AND trim(NEW."method", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."method" NOT IN ('bank', 'cash'))
 AND (NEW."method" IS NOT OLD."method")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_payments_kind_one_of_ins" BEFORE INSERT ON "payments"
WHEN ((NEW."kind" IS NOT NULL AND trim(NEW."kind", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."kind" NOT IN ('payment', 'refund', 'reversal'))
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "kind" IS NEW."kind")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_payments_kind_one_of_upd" BEFORE UPDATE OF "kind" ON "payments"
WHEN ((NEW."kind" IS NOT NULL AND trim(NEW."kind", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."kind" NOT IN ('payment', 'refund', 'reversal'))
 AND (NEW."kind" IS NOT OLD."kind")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_payments_document_id_ref_ins" BEFORE INSERT ON "payments"
WHEN (NEW."document_id" IS NOT NULL AND trim(NEW."document_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."document_id")
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "document_id" IS NEW."document_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_payments_document_id_ref_upd" BEFORE UPDATE OF "document_id" ON "payments"
WHEN (NEW."document_id" IS NOT NULL AND trim(NEW."document_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."document_id")
 AND (NEW."document_id" IS NOT OLD."document_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_payments_document_id_ref_del" BEFORE DELETE ON "invoices"
WHEN EXISTS (SELECT 1 FROM "payments" WHERE "document_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_payments_reverses_id_ref_ins" BEFORE INSERT ON "payments"
WHEN (NEW."reverses_id" IS NOT NULL AND trim(NEW."reverses_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."reverses_id")
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "reverses_id" IS NEW."reverses_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_payments_reverses_id_ref_upd" BEFORE UPDATE OF "reverses_id" ON "payments"
WHEN (NEW."reverses_id" IS NOT NULL AND trim(NEW."reverses_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."reverses_id")
 AND (NEW."reverses_id" IS NOT OLD."reverses_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_payments_reverses_id_ref_del" BEFORE DELETE ON "payments"
WHEN EXISTS (SELECT 1 FROM "payments" WHERE "reverses_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_payments_reversal_reverses_implies_ins" BEFORE INSERT ON "payments"
WHEN (COALESCE(NEW."kind" = 'reversal', 0) AND NOT COALESCE((NEW."reverses_id" IS NOT NULL AND trim(NEW."reverses_id", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "kind" IS NEW."kind" AND "reverses_id" IS NEW."reverses_id")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_payments_reversal_reverses_implies'); END;
CREATE TRIGGER "guard_payments_reversal_reverses_implies_upd" BEFORE UPDATE OF "kind", "reverses_id" ON "payments"
WHEN (COALESCE(NEW."kind" = 'reversal', 0) AND NOT COALESCE((NEW."reverses_id" IS NOT NULL AND trim(NEW."reverses_id", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND (NEW."kind" IS NOT OLD."kind" OR NEW."reverses_id" IS NOT OLD."reverses_id")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_payments_reversal_reverses_implies'); END;
CREATE TRIGGER "guard_payments_reverses_reversal_implies_ins" BEFORE INSERT ON "payments"
WHEN (COALESCE((NEW."reverses_id" IS NOT NULL AND trim(NEW."reverses_id", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0) AND NOT COALESCE(NEW."kind" = 'reversal', 0))
 AND NOT EXISTS (SELECT 1 FROM "payments" WHERE "id" = NEW."id" AND "reverses_id" IS NEW."reverses_id" AND "kind" IS NEW."kind")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_payments_reverses_reversal_implies'); END;
CREATE TRIGGER "guard_payments_reverses_reversal_implies_upd" BEFORE UPDATE OF "reverses_id", "kind" ON "payments"
WHEN (COALESCE((NEW."reverses_id" IS NOT NULL AND trim(NEW."reverses_id", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0) AND NOT COALESCE(NEW."kind" = 'reversal', 0))
 AND (NEW."reverses_id" IS NOT OLD."reverses_id" OR NEW."kind" IS NOT OLD."kind")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_payments_reverses_reversal_implies'); END;
CREATE TRIGGER "guard_payments_append_only_upd" BEFORE UPDATE ON "payments"
BEGIN SELECT RAISE(ABORT, 'permita38:state.append_only'); END;
CREATE TRIGGER "guard_payments_append_only_del" BEFORE DELETE ON "payments"
BEGIN SELECT RAISE(ABORT, 'permita38:state.append_only'); END;
CREATE TRIGGER "guard_period_locks_year_required_ins" BEFORE INSERT ON "period_locks"
WHEN (NOT (NEW."year" IS NOT NULL AND trim(NEW."year", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "period_locks" WHERE "id" = NEW."id" AND "year" IS NEW."year")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_period_locks_year_required_upd" BEFORE UPDATE OF "year" ON "period_locks"
WHEN (NOT (NEW."year" IS NOT NULL AND trim(NEW."year", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."year" IS NOT OLD."year")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_period_locks_month_required_ins" BEFORE INSERT ON "period_locks"
WHEN (NOT (NEW."month" IS NOT NULL AND trim(NEW."month", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "period_locks" WHERE "id" = NEW."id" AND "month" IS NEW."month")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_period_locks_month_required_upd" BEFORE UPDATE OF "month" ON "period_locks"
WHEN (NOT (NEW."month" IS NOT NULL AND trim(NEW."month", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."month" IS NOT OLD."month")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_period_locks_locked_at_required_ins" BEFORE INSERT ON "period_locks"
WHEN (NOT (NEW."locked_at" IS NOT NULL AND trim(NEW."locked_at", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "period_locks" WHERE "id" = NEW."id" AND "locked_at" IS NEW."locked_at")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_period_locks_locked_at_required_upd" BEFORE UPDATE OF "locked_at" ON "period_locks"
WHEN (NOT (NEW."locked_at" IS NOT NULL AND trim(NEW."locked_at", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."locked_at" IS NOT OLD."locked_at")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_period_locks_locked_by_required_ins" BEFORE INSERT ON "period_locks"
WHEN (NOT (NEW."locked_by" IS NOT NULL AND trim(NEW."locked_by", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "period_locks" WHERE "id" = NEW."id" AND "locked_by" IS NEW."locked_by")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_period_locks_locked_by_required_upd" BEFORE UPDATE OF "locked_by" ON "period_locks"
WHEN (NOT (NEW."locked_by" IS NOT NULL AND trim(NEW."locked_by", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."locked_by" IS NOT OLD."locked_by")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_period_locks_reason_required_ins" BEFORE INSERT ON "period_locks"
WHEN (NOT (NEW."reason" IS NOT NULL AND trim(NEW."reason", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "period_locks" WHERE "id" = NEW."id" AND "reason" IS NEW."reason")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_period_locks_reason_required_upd" BEFORE UPDATE OF "reason" ON "period_locks"
WHEN (NOT (NEW."reason" IS NOT NULL AND trim(NEW."reason", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."reason" IS NOT OLD."reason")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_period_locks_month_range_ins" BEFORE INSERT ON "period_locks"
WHEN (NEW."month" IS NOT NULL AND NEW."month" <> '' AND (NEW."month" < 1 OR NEW."month" > 12))
 AND NOT EXISTS (SELECT 1 FROM "period_locks" WHERE "id" = NEW."id" AND "month" IS NEW."month")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_period_locks_month_range_upd" BEFORE UPDATE OF "month" ON "period_locks"
WHEN (NEW."month" IS NOT NULL AND NEW."month" <> '' AND (NEW."month" < 1 OR NEW."month" > 12))
 AND (NEW."month" IS NOT OLD."month")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_period_locks_append_only_upd" BEFORE UPDATE ON "period_locks"
BEGIN SELECT RAISE(ABORT, 'permita38:state.append_only'); END;
CREATE TRIGGER "guard_period_locks_append_only_del" BEFORE DELETE ON "period_locks"
BEGIN SELECT RAISE(ABORT, 'permita38:state.append_only'); END;
