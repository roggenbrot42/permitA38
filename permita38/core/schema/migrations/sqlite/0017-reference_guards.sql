-- migration: dea6e12b
-- requires: e4b70dba
-- permitA38 migration: reference_guards (sqlite)

-- lookup indexes for the delete-side reference checks
CREATE INDEX "idx_contracts_project" ON "contracts" ("project_id");
CREATE INDEX "idx_offers_project" ON "offers" ("project_id");
CREATE INDEX "idx_invoices_project" ON "invoices" ("project_id");

-- reference guards: generated from core/repository/schema.py GUARDS by Dialect.guard_sql (a test keeps them equal)
CREATE TRIGGER "guard_allocations_project_id_ref_ins" BEFORE INSERT ON "allocations"
WHEN (NEW."project_id" IS NOT NULL AND trim(NEW."project_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id")
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "project_id" IS NEW."project_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_allocations_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "allocations"
WHEN (NEW."project_id" IS NOT NULL AND trim(NEW."project_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id")
 AND (NEW."project_id" IS NOT OLD."project_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_allocations_project_id_ref_del" BEFORE DELETE ON "projects"
WHEN EXISTS (SELECT 1 FROM "allocations" WHERE "project_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_allocations_user_id_ref_ins" BEFORE INSERT ON "allocations"
WHEN (NEW."user_id" IS NOT NULL AND trim(NEW."user_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "users" WHERE "id" = NEW."user_id")
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "user_id" IS NEW."user_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_allocations_user_id_ref_upd" BEFORE UPDATE OF "user_id" ON "allocations"
WHEN (NEW."user_id" IS NOT NULL AND trim(NEW."user_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "users" WHERE "id" = NEW."user_id")
 AND (NEW."user_id" IS NOT OLD."user_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_allocations_user_id_ref_del" BEFORE DELETE ON "users"
WHEN EXISTS (SELECT 1 FROM "allocations" WHERE "user_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_allocations_supplier_id_ref_ins" BEFORE INSERT ON "allocations"
WHEN (NEW."supplier_id" IS NOT NULL AND trim(NEW."supplier_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "suppliers" WHERE "id" = NEW."supplier_id")
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "supplier_id" IS NEW."supplier_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_allocations_supplier_id_ref_upd" BEFORE UPDATE OF "supplier_id" ON "allocations"
WHEN (NEW."supplier_id" IS NOT NULL AND trim(NEW."supplier_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "suppliers" WHERE "id" = NEW."supplier_id")
 AND (NEW."supplier_id" IS NOT OLD."supplier_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_allocations_supplier_id_ref_del" BEFORE DELETE ON "suppliers"
WHEN EXISTS (SELECT 1 FROM "allocations" WHERE "supplier_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_contracts_project_id_ref_ins" BEFORE INSERT ON "contracts"
WHEN (NEW."project_id" IS NOT NULL AND trim(NEW."project_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id")
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "project_id" IS NEW."project_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_contracts_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "contracts"
WHEN (NEW."project_id" IS NOT NULL AND trim(NEW."project_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id")
 AND (NEW."project_id" IS NOT OLD."project_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_contracts_project_id_ref_del" BEFORE DELETE ON "projects"
WHEN EXISTS (SELECT 1 FROM "contracts" WHERE "project_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_entries_project_id_ref_ins" BEFORE INSERT ON "entries"
WHEN (NEW."project_id" IS NOT NULL AND trim(NEW."project_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id")
 AND NOT EXISTS (SELECT 1 FROM "entries" WHERE "id" = NEW."id" AND "project_id" IS NEW."project_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_entries_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "entries"
WHEN (NEW."project_id" IS NOT NULL AND trim(NEW."project_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id")
 AND (NEW."project_id" IS NOT OLD."project_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_entries_project_id_ref_del" BEFORE DELETE ON "projects"
WHEN EXISTS (SELECT 1 FROM "entries" WHERE "project_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_offers_project_id_ref_ins" BEFORE INSERT ON "offers"
WHEN (NEW."project_id" IS NOT NULL AND trim(NEW."project_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id")
 AND NOT EXISTS (SELECT 1 FROM "offers" WHERE "id" = NEW."id" AND "project_id" IS NEW."project_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_offers_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "offers"
WHEN (NEW."project_id" IS NOT NULL AND trim(NEW."project_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id")
 AND (NEW."project_id" IS NOT OLD."project_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_offers_project_id_ref_del" BEFORE DELETE ON "projects"
WHEN EXISTS (SELECT 1 FROM "offers" WHERE "project_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
CREATE TRIGGER "guard_invoices_project_id_ref_ins" BEFORE INSERT ON "invoices"
WHEN (NEW."project_id" IS NOT NULL AND trim(NEW."project_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id")
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "project_id" IS NEW."project_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_invoices_project_id_ref_upd" BEFORE UPDATE OF "project_id" ON "invoices"
WHEN (NEW."project_id" IS NOT NULL AND trim(NEW."project_id", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."project_id")
 AND (NEW."project_id" IS NOT OLD."project_id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.dangling'); END;
CREATE TRIGGER "guard_invoices_project_id_ref_del" BEFORE DELETE ON "projects"
WHEN EXISTS (SELECT 1 FROM "invoices" WHERE "project_id" = OLD."id")
BEGIN SELECT RAISE(ABORT, 'permita38:ref.in_use'); END;
