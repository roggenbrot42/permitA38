-- migration: dea6e12b
-- requires: e4b70dba
-- permitA38 migration: reference_guards (postgresql)

-- lookup indexes for the delete-side reference checks
CREATE INDEX "idx_contracts_project" ON "contracts" ("project_id");
CREATE INDEX "idx_offers_project" ON "offers" ("project_id");
CREATE INDEX "idx_invoices_project" ON "invoices" ("project_id");
CREATE INDEX "idx_incoming_offers_project" ON "incoming_offers" ("project_id");
CREATE INDEX "idx_trips_project" ON "trips" ("project_id");
CREATE INDEX "idx_timers_project" ON "timers" ("project_id");

-- reference guards: generated from core/repository/schema.py GUARDS by Dialect.guard_sql (a test keeps them equal)
CREATE FUNCTION "guard_allocations_project_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."project_id" IS NOT NULL AND btrim(CAST(NEW."project_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id") AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "project_id" IS NOT DISTINCT FROM NEW."project_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_allocations_project_id_ref_ins" BEFORE INSERT ON "allocations" FOR EACH ROW EXECUTE FUNCTION "guard_allocations_project_id_ref_chk"();
CREATE TRIGGER "guard_allocations_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "allocations" FOR EACH ROW
WHEN (OLD."project_id" IS DISTINCT FROM NEW."project_id") EXECUTE FUNCTION "guard_allocations_project_id_ref_chk"();
CREATE FUNCTION "guard_allocations_project_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "allocations" WHERE "project_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_allocations_project_id_ref_del" BEFORE DELETE ON "projects" FOR EACH ROW EXECUTE FUNCTION "guard_allocations_project_id_ref_use"();
CREATE FUNCTION "guard_allocations_user_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."user_id" IS NOT NULL AND btrim(CAST(NEW."user_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "users" WHERE "id" = NEW."user_id") AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "user_id" IS NOT DISTINCT FROM NEW."user_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_allocations_user_id_ref_ins" BEFORE INSERT ON "allocations" FOR EACH ROW EXECUTE FUNCTION "guard_allocations_user_id_ref_chk"();
CREATE TRIGGER "guard_allocations_user_id_ref_upd" BEFORE UPDATE OF "user_id" ON "allocations" FOR EACH ROW
WHEN (OLD."user_id" IS DISTINCT FROM NEW."user_id") EXECUTE FUNCTION "guard_allocations_user_id_ref_chk"();
CREATE FUNCTION "guard_allocations_user_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "allocations" WHERE "user_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_allocations_user_id_ref_del" BEFORE DELETE ON "users" FOR EACH ROW EXECUTE FUNCTION "guard_allocations_user_id_ref_use"();
CREATE FUNCTION "guard_allocations_supplier_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."supplier_id" IS NOT NULL AND btrim(CAST(NEW."supplier_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "suppliers" WHERE "id" = NEW."supplier_id") AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "supplier_id" IS NOT DISTINCT FROM NEW."supplier_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_allocations_supplier_id_ref_ins" BEFORE INSERT ON "allocations" FOR EACH ROW EXECUTE FUNCTION "guard_allocations_supplier_id_ref_chk"();
CREATE TRIGGER "guard_allocations_supplier_id_ref_upd" BEFORE UPDATE OF "supplier_id" ON "allocations" FOR EACH ROW
WHEN (OLD."supplier_id" IS DISTINCT FROM NEW."supplier_id") EXECUTE FUNCTION "guard_allocations_supplier_id_ref_chk"();
CREATE FUNCTION "guard_allocations_supplier_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "allocations" WHERE "supplier_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_allocations_supplier_id_ref_del" BEFORE DELETE ON "suppliers" FOR EACH ROW EXECUTE FUNCTION "guard_allocations_supplier_id_ref_use"();
CREATE FUNCTION "guard_contracts_project_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."project_id" IS NOT NULL AND btrim(CAST(NEW."project_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id") AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "project_id" IS NOT DISTINCT FROM NEW."project_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_contracts_project_id_ref_ins" BEFORE INSERT ON "contracts" FOR EACH ROW EXECUTE FUNCTION "guard_contracts_project_id_ref_chk"();
CREATE TRIGGER "guard_contracts_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "contracts" FOR EACH ROW
WHEN (OLD."project_id" IS DISTINCT FROM NEW."project_id") EXECUTE FUNCTION "guard_contracts_project_id_ref_chk"();
CREATE FUNCTION "guard_contracts_project_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "contracts" WHERE "project_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_contracts_project_id_ref_del" BEFORE DELETE ON "projects" FOR EACH ROW EXECUTE FUNCTION "guard_contracts_project_id_ref_use"();
CREATE FUNCTION "guard_entries_project_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."project_id" IS NOT NULL AND btrim(CAST(NEW."project_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id") AND NOT EXISTS (SELECT 1 FROM "entries" WHERE "id" = NEW."id" AND "project_id" IS NOT DISTINCT FROM NEW."project_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_entries_project_id_ref_ins" BEFORE INSERT ON "entries" FOR EACH ROW EXECUTE FUNCTION "guard_entries_project_id_ref_chk"();
CREATE TRIGGER "guard_entries_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "entries" FOR EACH ROW
WHEN (OLD."project_id" IS DISTINCT FROM NEW."project_id") EXECUTE FUNCTION "guard_entries_project_id_ref_chk"();
CREATE FUNCTION "guard_entries_project_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "entries" WHERE "project_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_entries_project_id_ref_del" BEFORE DELETE ON "projects" FOR EACH ROW EXECUTE FUNCTION "guard_entries_project_id_ref_use"();
CREATE FUNCTION "guard_offers_project_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."project_id" IS NOT NULL AND btrim(CAST(NEW."project_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id") AND NOT EXISTS (SELECT 1 FROM "offers" WHERE "id" = NEW."id" AND "project_id" IS NOT DISTINCT FROM NEW."project_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_offers_project_id_ref_ins" BEFORE INSERT ON "offers" FOR EACH ROW EXECUTE FUNCTION "guard_offers_project_id_ref_chk"();
CREATE TRIGGER "guard_offers_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "offers" FOR EACH ROW
WHEN (OLD."project_id" IS DISTINCT FROM NEW."project_id") EXECUTE FUNCTION "guard_offers_project_id_ref_chk"();
CREATE FUNCTION "guard_offers_project_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "offers" WHERE "project_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_offers_project_id_ref_del" BEFORE DELETE ON "projects" FOR EACH ROW EXECUTE FUNCTION "guard_offers_project_id_ref_use"();
CREATE FUNCTION "guard_invoices_project_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."project_id" IS NOT NULL AND btrim(CAST(NEW."project_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id") AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "project_id" IS NOT DISTINCT FROM NEW."project_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_invoices_project_id_ref_ins" BEFORE INSERT ON "invoices" FOR EACH ROW EXECUTE FUNCTION "guard_invoices_project_id_ref_chk"();
CREATE TRIGGER "guard_invoices_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "invoices" FOR EACH ROW
WHEN (OLD."project_id" IS DISTINCT FROM NEW."project_id") EXECUTE FUNCTION "guard_invoices_project_id_ref_chk"();
CREATE FUNCTION "guard_invoices_project_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "invoices" WHERE "project_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_invoices_project_id_ref_del" BEFORE DELETE ON "projects" FOR EACH ROW EXECUTE FUNCTION "guard_invoices_project_id_ref_use"();
CREATE FUNCTION "guard_tasks_project_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."project_id" IS NOT NULL AND btrim(CAST(NEW."project_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id") AND NOT EXISTS (SELECT 1 FROM "tasks" WHERE "id" = NEW."id" AND "project_id" IS NOT DISTINCT FROM NEW."project_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_tasks_project_id_ref_ins" BEFORE INSERT ON "tasks" FOR EACH ROW EXECUTE FUNCTION "guard_tasks_project_id_ref_chk"();
CREATE TRIGGER "guard_tasks_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "tasks" FOR EACH ROW
WHEN (OLD."project_id" IS DISTINCT FROM NEW."project_id") EXECUTE FUNCTION "guard_tasks_project_id_ref_chk"();
CREATE FUNCTION "guard_tasks_project_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "tasks" WHERE "project_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_tasks_project_id_ref_del" BEFORE DELETE ON "projects" FOR EACH ROW EXECUTE FUNCTION "guard_tasks_project_id_ref_use"();
CREATE FUNCTION "guard_expenses_project_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."project_id" IS NOT NULL AND btrim(CAST(NEW."project_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id") AND NOT EXISTS (SELECT 1 FROM "expenses" WHERE "id" = NEW."id" AND "project_id" IS NOT DISTINCT FROM NEW."project_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_expenses_project_id_ref_ins" BEFORE INSERT ON "expenses" FOR EACH ROW EXECUTE FUNCTION "guard_expenses_project_id_ref_chk"();
CREATE TRIGGER "guard_expenses_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "expenses" FOR EACH ROW
WHEN (OLD."project_id" IS DISTINCT FROM NEW."project_id") EXECUTE FUNCTION "guard_expenses_project_id_ref_chk"();
CREATE FUNCTION "guard_expenses_project_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "expenses" WHERE "project_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_expenses_project_id_ref_del" BEFORE DELETE ON "projects" FOR EACH ROW EXECUTE FUNCTION "guard_expenses_project_id_ref_use"();
CREATE FUNCTION "guard_incoming_offers_project_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."project_id" IS NOT NULL AND btrim(CAST(NEW."project_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id") AND NOT EXISTS (SELECT 1 FROM "incoming_offers" WHERE "id" = NEW."id" AND "project_id" IS NOT DISTINCT FROM NEW."project_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_incoming_offers_project_id_ref_ins" BEFORE INSERT ON "incoming_offers" FOR EACH ROW EXECUTE FUNCTION "guard_incoming_offers_project_id_ref_chk"();
CREATE TRIGGER "guard_incoming_offers_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "incoming_offers" FOR EACH ROW
WHEN (OLD."project_id" IS DISTINCT FROM NEW."project_id") EXECUTE FUNCTION "guard_incoming_offers_project_id_ref_chk"();
CREATE FUNCTION "guard_incoming_offers_project_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "incoming_offers" WHERE "project_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_incoming_offers_project_id_ref_del" BEFORE DELETE ON "projects" FOR EACH ROW EXECUTE FUNCTION "guard_incoming_offers_project_id_ref_use"();
CREATE FUNCTION "guard_trips_project_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."project_id" IS NOT NULL AND btrim(CAST(NEW."project_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id") AND NOT EXISTS (SELECT 1 FROM "trips" WHERE "id" = NEW."id" AND "project_id" IS NOT DISTINCT FROM NEW."project_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_trips_project_id_ref_ins" BEFORE INSERT ON "trips" FOR EACH ROW EXECUTE FUNCTION "guard_trips_project_id_ref_chk"();
CREATE TRIGGER "guard_trips_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "trips" FOR EACH ROW
WHEN (OLD."project_id" IS DISTINCT FROM NEW."project_id") EXECUTE FUNCTION "guard_trips_project_id_ref_chk"();
CREATE FUNCTION "guard_trips_project_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "trips" WHERE "project_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_trips_project_id_ref_del" BEFORE DELETE ON "projects" FOR EACH ROW EXECUTE FUNCTION "guard_trips_project_id_ref_use"();
CREATE FUNCTION "guard_timers_project_id_ref_chk"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW."project_id" IS NOT NULL AND btrim(CAST(NEW."project_id" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id") AND NOT EXISTS (SELECT 1 FROM "timers" WHERE "id" = NEW."id" AND "project_id" IS NOT DISTINCT FROM NEW."project_id") THEN
    RAISE EXCEPTION 'permita38:ref.dangling' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER "guard_timers_project_id_ref_ins" BEFORE INSERT ON "timers" FOR EACH ROW EXECUTE FUNCTION "guard_timers_project_id_ref_chk"();
CREATE TRIGGER "guard_timers_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "timers" FOR EACH ROW
WHEN (OLD."project_id" IS DISTINCT FROM NEW."project_id") EXECUTE FUNCTION "guard_timers_project_id_ref_chk"();
CREATE FUNCTION "guard_timers_project_id_ref_use"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM "timers" WHERE "project_id" = OLD."id") THEN
    RAISE EXCEPTION 'permita38:ref.in_use' USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$;
CREATE TRIGGER "guard_timers_project_id_ref_del" BEFORE DELETE ON "projects" FOR EACH ROW EXECUTE FUNCTION "guard_timers_project_id_ref_use"();
