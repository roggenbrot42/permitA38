-- migration: 522011c0
-- requires: 10bcb698
-- permitA38 migration: year-closing-hash (sqlite)
-- year closing (docs/specs/accounting.md 7.6, 13): the closed year's last journal hash and entry count, set once.
ALTER TABLE "fiscal_years" ADD COLUMN "closing_hash" TEXT;
ALTER TABLE "fiscal_years" ADD COLUMN "closing_entry_no" INTEGER;
CREATE TRIGGER "guard_fiscal_years_closing_entry_no_range_ins" BEFORE INSERT ON "fiscal_years"
WHEN (NEW."closing_entry_no" IS NOT NULL AND NEW."closing_entry_no" <> '' AND (NEW."closing_entry_no" < 0 OR NEW."closing_entry_no" > 2000000000))
 AND NOT EXISTS (SELECT 1 FROM "fiscal_years" WHERE "id" = NEW."id" AND "closing_entry_no" IS NEW."closing_entry_no")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_fiscal_years_closing_entry_no_range_upd" BEFORE UPDATE OF "closing_entry_no" ON "fiscal_years"
WHEN (NEW."closing_entry_no" IS NOT NULL AND NEW."closing_entry_no" <> '' AND (NEW."closing_entry_no" < 0 OR NEW."closing_entry_no" > 2000000000))
 AND (NEW."closing_entry_no" IS NOT OLD."closing_entry_no")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_fiscal_years_closing_hash_once_upd" BEFORE UPDATE OF "closing_hash" ON "fiscal_years"
WHEN (OLD."closing_hash" IS NOT NULL AND trim(OLD."closing_hash", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."closing_hash" IS NOT OLD."closing_hash"
BEGIN SELECT RAISE(ABORT, 'permita38:state.set_once'); END;
CREATE TRIGGER "guard_fiscal_years_closing_entry_no_once_upd" BEFORE UPDATE OF "closing_entry_no" ON "fiscal_years"
WHEN (OLD."closing_entry_no" IS NOT NULL AND trim(OLD."closing_entry_no", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."closing_entry_no" IS NOT OLD."closing_entry_no"
BEGIN SELECT RAISE(ABORT, 'permita38:state.set_once'); END;
