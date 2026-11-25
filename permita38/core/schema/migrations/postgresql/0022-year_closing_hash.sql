-- migration: 522011c0
-- requires: 10bcb698
-- permitA38 migration: year-closing-hash (postgresql)
-- year closing (docs/specs/accounting.md 7.6, 13): the closed year's last journal hash and entry count, set once.
ALTER TABLE "fiscal_years" ADD COLUMN "closing_hash" TEXT;
ALTER TABLE "fiscal_years" ADD COLUMN "closing_entry_no" BIGINT;
ALTER TABLE "fiscal_years" ADD CONSTRAINT "guard_fiscal_years_closing_entry_no_range" CHECK (NOT COALESCE(("closing_entry_no" IS NOT NULL AND ("closing_entry_no" < 0 OR "closing_entry_no" > 2000000000)), FALSE));
CREATE FUNCTION "guard_fiscal_years_closing_hash_once_upd_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (OLD."closing_hash" IS NOT NULL AND btrim(CAST(OLD."closing_hash" AS TEXT), E' \t\n\r\f\v') <> '') AND NEW."closing_hash" IS DISTINCT FROM OLD."closing_hash" THEN
    RAISE EXCEPTION 'permita38:state.set_once' USING ERRCODE = '23000';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_fiscal_years_closing_hash_once_upd" BEFORE UPDATE OF "closing_hash" ON "fiscal_years" FOR EACH ROW EXECUTE FUNCTION "guard_fiscal_years_closing_hash_once_upd_fn"();
CREATE FUNCTION "guard_fiscal_years_closing_entry_no_once_upd_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (OLD."closing_entry_no" IS NOT NULL AND btrim(CAST(OLD."closing_entry_no" AS TEXT), E' \t\n\r\f\v') <> '') AND NEW."closing_entry_no" IS DISTINCT FROM OLD."closing_entry_no" THEN
    RAISE EXCEPTION 'permita38:state.set_once' USING ERRCODE = '23000';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_fiscal_years_closing_entry_no_once_upd" BEFORE UPDATE OF "closing_entry_no" ON "fiscal_years" FOR EACH ROW EXECUTE FUNCTION "guard_fiscal_years_closing_entry_no_once_upd_fn"();
