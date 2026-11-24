-- migration: 10bcb698
-- requires: ae32358b
-- permitA38 migration: accounting-row-state (sqlite)
-- row-state guards (docs/specs/accounting.md 8.1, docs/specs/db-constraints.md): a final vat_returns / zm_reports row
-- only takes filed_on, reference, note and replaced_by (reopen to draft only while filed_on is unset; external returns
-- are exempt until replaced_by is set); filed_on and fiscal_years.closed_at / closed_by are set once. No new columns.

CREATE TRIGGER "guard_vat_returns_frozen_upd" BEFORE UPDATE ON "vat_returns"
WHEN OLD."status" = 'final' AND NOT COALESCE((OLD."external" = 1 AND NOT (OLD."replaced_by" IS NOT NULL AND trim(OLD."replaced_by", ' ' || char(9, 10, 11, 12, 13)) <> '')), 0) AND (NEW."id" IS NOT OLD."id" OR NEW."created" IS NOT OLD."created" OR NEW."year" IS NOT OLD."year" OR NEW."period_kind" IS NOT OLD."period_kind" OR NEW."period_no" IS NOT OLD."period_no" OR NEW."from_date" IS NOT OLD."from_date" OR NEW."to_date" IS NOT OLD."to_date" OR NEW."due_date" IS NOT OLD."due_date" OR NEW."basis" IS NOT OLD."basis" OR NEW."frequency" IS NOT OLD."frequency" OR NEW."dauerfrist" IS NOT OLD."dauerfrist" OR NEW."small_business" IS NOT OLD."small_business" OR NEW."rules_version" IS NOT OLD."rules_version" OR NEW."result_cents" IS NOT OLD."result_cents" OR NEW."special_prepayment_credit_cents" IS NOT OLD."special_prepayment_credit_cents" OR NEW."rounding_difference_cents" IS NOT OLD."rounding_difference_cents" OR NEW."amends_id" IS NOT OLD."amends_id" OR NEW."amends_key" IS NOT OLD."amends_key" OR NEW."external" IS NOT OLD."external" OR NEW."extra" IS NOT OLD."extra" OR (NOT NEW."status" IS NOT OLD."status" AND (NEW."finalised_at" IS NOT OLD."finalised_at" OR NEW."finalised_by" IS NOT OLD."finalised_by" OR NEW."boxes" IS NOT OLD."boxes" OR NEW."source_lines" IS NOT OLD."source_lines")) OR (NEW."status" IS NOT OLD."status" AND ((OLD."filed_on" IS NOT NULL AND trim(OLD."filed_on", ' ' || char(9, 10, 11, 12, 13)) <> '') OR NEW."filed_on" IS NOT OLD."filed_on")))
BEGIN SELECT RAISE(ABORT, 'permita38:state.frozen'); END;
CREATE TRIGGER "guard_vat_returns_frozen_del" BEFORE DELETE ON "vat_returns"
WHEN OLD."status" = 'final' AND NOT COALESCE((OLD."external" = 1 AND NOT (OLD."replaced_by" IS NOT NULL AND trim(OLD."replaced_by", ' ' || char(9, 10, 11, 12, 13)) <> '')), 0)
BEGIN SELECT RAISE(ABORT, 'permita38:state.frozen'); END;
CREATE TRIGGER "guard_zm_reports_frozen_upd" BEFORE UPDATE ON "zm_reports"
WHEN OLD."status" = 'final' AND (NEW."id" IS NOT OLD."id" OR NEW."created" IS NOT OLD."created" OR NEW."year" IS NOT OLD."year" OR NEW."quarter" IS NOT OLD."quarter" OR NEW."due_date" IS NOT OLD."due_date" OR NEW."rules_version" IS NOT OLD."rules_version" OR NEW."amends_id" IS NOT OLD."amends_id" OR NEW."amends_key" IS NOT OLD."amends_key" OR NEW."extra" IS NOT OLD."extra" OR (NOT NEW."status" IS NOT OLD."status" AND (NEW."finalised_at" IS NOT OLD."finalised_at" OR NEW."finalised_by" IS NOT OLD."finalised_by" OR NEW."lines" IS NOT OLD."lines" OR NEW."source_lines" IS NOT OLD."source_lines")) OR (NEW."status" IS NOT OLD."status" AND ((OLD."filed_on" IS NOT NULL AND trim(OLD."filed_on", ' ' || char(9, 10, 11, 12, 13)) <> '') OR NEW."filed_on" IS NOT OLD."filed_on")))
BEGIN SELECT RAISE(ABORT, 'permita38:state.frozen'); END;
CREATE TRIGGER "guard_zm_reports_frozen_del" BEFORE DELETE ON "zm_reports"
WHEN OLD."status" = 'final'
BEGIN SELECT RAISE(ABORT, 'permita38:state.frozen'); END;
CREATE TRIGGER "guard_vat_returns_filed_on_once_upd" BEFORE UPDATE OF "filed_on" ON "vat_returns"
WHEN (OLD."filed_on" IS NOT NULL AND trim(OLD."filed_on", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."filed_on" IS NOT OLD."filed_on"
BEGIN SELECT RAISE(ABORT, 'permita38:state.set_once'); END;
CREATE TRIGGER "guard_zm_reports_filed_on_once_upd" BEFORE UPDATE OF "filed_on" ON "zm_reports"
WHEN (OLD."filed_on" IS NOT NULL AND trim(OLD."filed_on", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."filed_on" IS NOT OLD."filed_on"
BEGIN SELECT RAISE(ABORT, 'permita38:state.set_once'); END;
CREATE TRIGGER "guard_fiscal_years_closed_at_once_upd" BEFORE UPDATE OF "closed_at" ON "fiscal_years"
WHEN (OLD."closed_at" IS NOT NULL AND trim(OLD."closed_at", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."closed_at" IS NOT OLD."closed_at"
BEGIN SELECT RAISE(ABORT, 'permita38:state.set_once'); END;
CREATE TRIGGER "guard_fiscal_years_closed_by_once_upd" BEFORE UPDATE OF "closed_by" ON "fiscal_years"
WHEN (OLD."closed_by" IS NOT NULL AND trim(OLD."closed_by", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."closed_by" IS NOT OLD."closed_by"
BEGIN SELECT RAISE(ABORT, 'permita38:state.set_once'); END;
