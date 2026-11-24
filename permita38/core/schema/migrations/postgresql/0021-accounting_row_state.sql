-- migration: 10bcb698
-- requires: ae32358b
-- permitA38 migration: accounting-row-state (postgresql)
-- row-state guards (docs/specs/accounting.md 8.1, docs/specs/db-constraints.md): a final vat_returns / zm_reports row
-- only takes filed_on, reference, note and replaced_by (reopen to draft only while filed_on is unset; external returns
-- are exempt until replaced_by is set); filed_on and fiscal_years.closed_at / closed_by are set once. No new columns.

CREATE FUNCTION "guard_vat_returns_frozen_upd_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD."status" = 'final' AND NOT COALESCE((OLD."external" = TRUE AND NOT (OLD."replaced_by" IS NOT NULL AND btrim(CAST(OLD."replaced_by" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE) AND (NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created" IS DISTINCT FROM OLD."created" OR NEW."year" IS DISTINCT FROM OLD."year" OR NEW."period_kind" IS DISTINCT FROM OLD."period_kind" OR NEW."period_no" IS DISTINCT FROM OLD."period_no" OR NEW."from_date" IS DISTINCT FROM OLD."from_date" OR NEW."to_date" IS DISTINCT FROM OLD."to_date" OR NEW."due_date" IS DISTINCT FROM OLD."due_date" OR NEW."basis" IS DISTINCT FROM OLD."basis" OR NEW."frequency" IS DISTINCT FROM OLD."frequency" OR NEW."dauerfrist" IS DISTINCT FROM OLD."dauerfrist" OR NEW."small_business" IS DISTINCT FROM OLD."small_business" OR NEW."rules_version" IS DISTINCT FROM OLD."rules_version" OR NEW."result_cents" IS DISTINCT FROM OLD."result_cents" OR NEW."special_prepayment_credit_cents" IS DISTINCT FROM OLD."special_prepayment_credit_cents" OR NEW."rounding_difference_cents" IS DISTINCT FROM OLD."rounding_difference_cents" OR NEW."amends_id" IS DISTINCT FROM OLD."amends_id" OR NEW."amends_key" IS DISTINCT FROM OLD."amends_key" OR NEW."external" IS DISTINCT FROM OLD."external" OR NEW."extra" IS DISTINCT FROM OLD."extra" OR (NOT NEW."status" IS DISTINCT FROM OLD."status" AND (NEW."finalised_at" IS DISTINCT FROM OLD."finalised_at" OR NEW."finalised_by" IS DISTINCT FROM OLD."finalised_by" OR NEW."boxes" IS DISTINCT FROM OLD."boxes" OR NEW."source_lines" IS DISTINCT FROM OLD."source_lines")) OR (NEW."status" IS DISTINCT FROM OLD."status" AND ((OLD."filed_on" IS NOT NULL AND btrim(CAST(OLD."filed_on" AS TEXT), E' \t\n\r\f\v') <> '') OR NEW."filed_on" IS DISTINCT FROM OLD."filed_on"))) THEN
    RAISE EXCEPTION 'permita38:state.frozen' USING ERRCODE = '23000';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_vat_returns_frozen_upd" BEFORE UPDATE ON "vat_returns" FOR EACH ROW EXECUTE FUNCTION "guard_vat_returns_frozen_upd_fn"();
CREATE FUNCTION "guard_vat_returns_frozen_del_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD."status" = 'final' AND NOT COALESCE((OLD."external" = TRUE AND NOT (OLD."replaced_by" IS NOT NULL AND btrim(CAST(OLD."replaced_by" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE) THEN
    RAISE EXCEPTION 'permita38:state.frozen' USING ERRCODE = '23000';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_vat_returns_frozen_del" BEFORE DELETE ON "vat_returns" FOR EACH ROW EXECUTE FUNCTION "guard_vat_returns_frozen_del_fn"();
CREATE FUNCTION "guard_vat_returns_frozen_trunc_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'permita38:state.frozen' USING ERRCODE = '23000';
END;
$$;
CREATE TRIGGER "guard_vat_returns_frozen_trunc" BEFORE TRUNCATE ON "vat_returns" FOR EACH STATEMENT EXECUTE FUNCTION "guard_vat_returns_frozen_trunc_fn"();
CREATE FUNCTION "guard_zm_reports_frozen_upd_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD."status" = 'final' AND (NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created" IS DISTINCT FROM OLD."created" OR NEW."year" IS DISTINCT FROM OLD."year" OR NEW."quarter" IS DISTINCT FROM OLD."quarter" OR NEW."due_date" IS DISTINCT FROM OLD."due_date" OR NEW."rules_version" IS DISTINCT FROM OLD."rules_version" OR NEW."amends_id" IS DISTINCT FROM OLD."amends_id" OR NEW."amends_key" IS DISTINCT FROM OLD."amends_key" OR NEW."extra" IS DISTINCT FROM OLD."extra" OR (NOT NEW."status" IS DISTINCT FROM OLD."status" AND (NEW."finalised_at" IS DISTINCT FROM OLD."finalised_at" OR NEW."finalised_by" IS DISTINCT FROM OLD."finalised_by" OR NEW."lines" IS DISTINCT FROM OLD."lines" OR NEW."source_lines" IS DISTINCT FROM OLD."source_lines")) OR (NEW."status" IS DISTINCT FROM OLD."status" AND ((OLD."filed_on" IS NOT NULL AND btrim(CAST(OLD."filed_on" AS TEXT), E' \t\n\r\f\v') <> '') OR NEW."filed_on" IS DISTINCT FROM OLD."filed_on"))) THEN
    RAISE EXCEPTION 'permita38:state.frozen' USING ERRCODE = '23000';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_zm_reports_frozen_upd" BEFORE UPDATE ON "zm_reports" FOR EACH ROW EXECUTE FUNCTION "guard_zm_reports_frozen_upd_fn"();
CREATE FUNCTION "guard_zm_reports_frozen_del_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD."status" = 'final' THEN
    RAISE EXCEPTION 'permita38:state.frozen' USING ERRCODE = '23000';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_zm_reports_frozen_del" BEFORE DELETE ON "zm_reports" FOR EACH ROW EXECUTE FUNCTION "guard_zm_reports_frozen_del_fn"();
CREATE FUNCTION "guard_zm_reports_frozen_trunc_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'permita38:state.frozen' USING ERRCODE = '23000';
END;
$$;
CREATE TRIGGER "guard_zm_reports_frozen_trunc" BEFORE TRUNCATE ON "zm_reports" FOR EACH STATEMENT EXECUTE FUNCTION "guard_zm_reports_frozen_trunc_fn"();
CREATE FUNCTION "guard_vat_returns_filed_on_once_upd_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (OLD."filed_on" IS NOT NULL AND btrim(CAST(OLD."filed_on" AS TEXT), E' \t\n\r\f\v') <> '') AND NEW."filed_on" IS DISTINCT FROM OLD."filed_on" THEN
    RAISE EXCEPTION 'permita38:state.set_once' USING ERRCODE = '23000';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_vat_returns_filed_on_once_upd" BEFORE UPDATE OF "filed_on" ON "vat_returns" FOR EACH ROW EXECUTE FUNCTION "guard_vat_returns_filed_on_once_upd_fn"();
CREATE FUNCTION "guard_zm_reports_filed_on_once_upd_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (OLD."filed_on" IS NOT NULL AND btrim(CAST(OLD."filed_on" AS TEXT), E' \t\n\r\f\v') <> '') AND NEW."filed_on" IS DISTINCT FROM OLD."filed_on" THEN
    RAISE EXCEPTION 'permita38:state.set_once' USING ERRCODE = '23000';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_zm_reports_filed_on_once_upd" BEFORE UPDATE OF "filed_on" ON "zm_reports" FOR EACH ROW EXECUTE FUNCTION "guard_zm_reports_filed_on_once_upd_fn"();
CREATE FUNCTION "guard_fiscal_years_closed_at_once_upd_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (OLD."closed_at" IS NOT NULL AND btrim(CAST(OLD."closed_at" AS TEXT), E' \t\n\r\f\v') <> '') AND NEW."closed_at" IS DISTINCT FROM OLD."closed_at" THEN
    RAISE EXCEPTION 'permita38:state.set_once' USING ERRCODE = '23000';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_fiscal_years_closed_at_once_upd" BEFORE UPDATE OF "closed_at" ON "fiscal_years" FOR EACH ROW EXECUTE FUNCTION "guard_fiscal_years_closed_at_once_upd_fn"();
CREATE FUNCTION "guard_fiscal_years_closed_by_once_upd_fn"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (OLD."closed_by" IS NOT NULL AND btrim(CAST(OLD."closed_by" AS TEXT), E' \t\n\r\f\v') <> '') AND NEW."closed_by" IS DISTINCT FROM OLD."closed_by" THEN
    RAISE EXCEPTION 'permita38:state.set_once' USING ERRCODE = '23000';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_fiscal_years_closed_by_once_upd" BEFORE UPDATE OF "closed_by" ON "fiscal_years" FOR EACH ROW EXECUTE FUNCTION "guard_fiscal_years_closed_by_once_upd_fn"();
