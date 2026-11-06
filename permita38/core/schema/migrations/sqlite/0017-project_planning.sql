-- migration: 7ca84bdc
-- requires: e4b70dba
-- permitA38 migration: project_planning (sqlite)

-- 1. schema
ALTER TABLE "projects" ADD COLUMN "kind" TEXT;

-- 2. data fixes (the new guards do not exist yet)
UPDATE "projects" SET "kind" = CASE WHEN ("customer_id" IS NOT NULL AND "customer_id" <> '')
  OR EXISTS (SELECT 1 FROM "offers" WHERE "project_id" = "projects"."id") OR EXISTS (SELECT 1 FROM "contracts" WHERE "project_id" = "projects"."id") OR EXISTS (SELECT 1 FROM "invoices" WHERE "project_id" = "projects"."id") THEN 'customer' ELSE 'internal' END;
UPDATE "contracts" SET "total_hours" = NULL WHERE "hours_per_week" IS NOT NULL AND "total_hours" IS NOT NULL;
UPDATE "allocations" SET "total_hours" = NULL WHERE "hours_per_week" IS NOT NULL AND "total_hours" IS NOT NULL;
UPDATE "projects" SET "status" = 'active' WHERE "status" IS NULL;
UPDATE "allocations" SET "status" = 'active' WHERE "status" IS NULL;
UPDATE "contracts" SET "status" = 'confirmed' WHERE "status" IS NULL;

ALTER TABLE "projects" DROP COLUMN "personal";
ALTER TABLE "projects" DROP COLUMN "member_ids";

-- 3. guards: generated from core/repository/schema.py GUARDS by Dialect.guard_sql (a test keeps them equal)
CREATE TRIGGER "guard_projects_kind_required_ins" BEFORE INSERT ON "projects"
WHEN (NOT (NEW."kind" IS NOT NULL AND trim(NEW."kind", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."id" AND "kind" IS NEW."kind")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_projects_kind_required_upd" BEFORE UPDATE OF "kind" ON "projects"
WHEN (NOT (NEW."kind" IS NOT NULL AND trim(NEW."kind", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."kind" IS NOT OLD."kind")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_projects_kind_one_of_ins" BEFORE INSERT ON "projects"
WHEN ((NEW."kind" IS NOT NULL AND trim(NEW."kind", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."kind" NOT IN ('customer', 'internal'))
 AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."id" AND "kind" IS NEW."kind")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_projects_kind_one_of_upd" BEFORE UPDATE OF "kind" ON "projects"
WHEN ((NEW."kind" IS NOT NULL AND trim(NEW."kind", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."kind" NOT IN ('customer', 'internal'))
 AND (NEW."kind" IS NOT OLD."kind")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_projects_status_one_of_ins" BEFORE INSERT ON "projects"
WHEN ((NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."status" NOT IN ('active', 'paused', 'done', 'archived'))
 AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."id" AND "status" IS NEW."status")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_projects_status_one_of_upd" BEFORE UPDATE OF "status" ON "projects"
WHEN ((NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."status" NOT IN ('active', 'paused', 'done', 'archived'))
 AND (NEW."status" IS NOT OLD."status")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_projects_name_required_ins" BEFORE INSERT ON "projects"
WHEN (NOT (NEW."name" IS NOT NULL AND trim(NEW."name", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."id" AND "name" IS NEW."name")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_projects_name_required_upd" BEFORE UPDATE OF "name" ON "projects"
WHEN (NOT (NEW."name" IS NOT NULL AND trim(NEW."name", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."name" IS NOT OLD."name")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_projects_internal_customer_implies_ins" BEFORE INSERT ON "projects"
WHEN (COALESCE(NEW."kind" = 'internal', 0) AND NOT COALESCE(NOT (NEW."customer_id" IS NOT NULL AND trim(NEW."customer_id", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."id" AND "kind" IS NEW."kind" AND "customer_id" IS NEW."customer_id")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_projects_internal_customer_implies'); END;
CREATE TRIGGER "guard_projects_internal_customer_implies_upd" BEFORE UPDATE OF "kind", "customer_id" ON "projects"
WHEN (COALESCE(NEW."kind" = 'internal', 0) AND NOT COALESCE(NOT (NEW."customer_id" IS NOT NULL AND trim(NEW."customer_id", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND (NEW."kind" IS NOT OLD."kind" OR NEW."customer_id" IS NOT OLD."customer_id")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_projects_internal_customer_implies'); END;
CREATE TRIGGER "guard_projects_budget_hours_range_ins" BEFORE INSERT ON "projects"
WHEN (NEW."budget_hours" IS NOT NULL AND NEW."budget_hours" <> '' AND (NEW."budget_hours" <= 0 OR NEW."budget_hours" > 100000))
 AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."id" AND "budget_hours" IS NEW."budget_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_projects_budget_hours_range_upd" BEFORE UPDATE OF "budget_hours" ON "projects"
WHEN (NEW."budget_hours" IS NOT NULL AND NEW."budget_hours" <> '' AND (NEW."budget_hours" <= 0 OR NEW."budget_hours" > 100000))
 AND (NEW."budget_hours" IS NOT OLD."budget_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_allocations_project_id_required_ins" BEFORE INSERT ON "allocations"
WHEN (NOT (NEW."project_id" IS NOT NULL AND trim(NEW."project_id", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "project_id" IS NEW."project_id")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_allocations_project_id_required_upd" BEFORE UPDATE OF "project_id" ON "allocations"
WHEN (NOT (NEW."project_id" IS NOT NULL AND trim(NEW."project_id", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."project_id" IS NOT OLD."project_id")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_allocations_start_required_ins" BEFORE INSERT ON "allocations"
WHEN (NOT (NEW."start" IS NOT NULL AND trim(NEW."start", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "start" IS NEW."start")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_allocations_start_required_upd" BEFORE UPDATE OF "start" ON "allocations"
WHEN (NOT (NEW."start" IS NOT NULL AND trim(NEW."start", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."start" IS NOT OLD."start")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_allocations_end_required_ins" BEFORE INSERT ON "allocations"
WHEN (NOT (NEW."end" IS NOT NULL AND trim(NEW."end", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "end" IS NEW."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_allocations_end_required_upd" BEFORE UPDATE OF "end" ON "allocations"
WHEN (NOT (NEW."end" IS NOT NULL AND trim(NEW."end", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."end" IS NOT OLD."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_allocations_status_one_of_ins" BEFORE INSERT ON "allocations"
WHEN ((NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."status" NOT IN ('active', 'cancelled'))
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "status" IS NEW."status")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_allocations_status_one_of_upd" BEFORE UPDATE OF "status" ON "allocations"
WHEN ((NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."status" NOT IN ('active', 'cancelled'))
 AND (NEW."status" IS NOT OLD."status")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_allocations_hours_exactly_one_ins" BEFORE INSERT ON "allocations"
WHEN (((NEW."hours_per_week" IS NOT NULL AND trim(NEW."hours_per_week", ' ' || char(9, 10, 11, 12, 13)) <> '') AND (NEW."total_hours" IS NOT NULL AND trim(NEW."total_hours", ' ' || char(9, 10, 11, 12, 13)) <> '')) OR (NOT (NEW."hours_per_week" IS NOT NULL AND trim(NEW."hours_per_week", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT (NEW."total_hours" IS NOT NULL AND trim(NEW."total_hours", ' ' || char(9, 10, 11, 12, 13)) <> '')))
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "hours_per_week" IS NEW."hours_per_week" AND "total_hours" IS NEW."total_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:state.exactly_one'); END;
CREATE TRIGGER "guard_allocations_hours_exactly_one_upd" BEFORE UPDATE OF "hours_per_week", "total_hours" ON "allocations"
WHEN (((NEW."hours_per_week" IS NOT NULL AND trim(NEW."hours_per_week", ' ' || char(9, 10, 11, 12, 13)) <> '') AND (NEW."total_hours" IS NOT NULL AND trim(NEW."total_hours", ' ' || char(9, 10, 11, 12, 13)) <> '')) OR (NOT (NEW."hours_per_week" IS NOT NULL AND trim(NEW."hours_per_week", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT (NEW."total_hours" IS NOT NULL AND trim(NEW."total_hours", ' ' || char(9, 10, 11, 12, 13)) <> '')))
 AND (NEW."hours_per_week" IS NOT OLD."hours_per_week" OR NEW."total_hours" IS NOT OLD."total_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:state.exactly_one'); END;
CREATE TRIGGER "guard_allocations_hours_per_week_range_ins" BEFORE INSERT ON "allocations"
WHEN (NEW."hours_per_week" IS NOT NULL AND NEW."hours_per_week" <> '' AND (NEW."hours_per_week" <= 0 OR NEW."hours_per_week" > 80))
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "hours_per_week" IS NEW."hours_per_week")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_allocations_hours_per_week_range_upd" BEFORE UPDATE OF "hours_per_week" ON "allocations"
WHEN (NEW."hours_per_week" IS NOT NULL AND NEW."hours_per_week" <> '' AND (NEW."hours_per_week" <= 0 OR NEW."hours_per_week" > 80))
 AND (NEW."hours_per_week" IS NOT OLD."hours_per_week")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_allocations_total_hours_range_ins" BEFORE INSERT ON "allocations"
WHEN (NEW."total_hours" IS NOT NULL AND NEW."total_hours" <> '' AND (NEW."total_hours" <= 0 OR NEW."total_hours" > 100000))
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "total_hours" IS NEW."total_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_allocations_total_hours_range_upd" BEFORE UPDATE OF "total_hours" ON "allocations"
WHEN (NEW."total_hours" IS NOT NULL AND NEW."total_hours" <> '' AND (NEW."total_hours" <= 0 OR NEW."total_hours" > 100000))
 AND (NEW."total_hours" IS NOT OLD."total_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_allocations_period_max_days_ins" BEFORE INSERT ON "allocations"
WHEN (COALESCE((CASE WHEN NEW."start" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]' AND julianday(NEW."start") IS NOT NULL AND NEW."end" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]' AND julianday(NEW."end") IS NOT NULL THEN julianday(NEW."end") - julianday(NEW."start") END) > 1830, 0))
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "start" IS NEW."start" AND "end" IS NEW."end")
BEGIN SELECT RAISE(ABORT, 'permita38:range.too_long'); END;
CREATE TRIGGER "guard_allocations_period_max_days_upd" BEFORE UPDATE OF "start", "end" ON "allocations"
WHEN (COALESCE((CASE WHEN NEW."start" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]' AND julianday(NEW."start") IS NOT NULL AND NEW."end" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]' AND julianday(NEW."end") IS NOT NULL THEN julianday(NEW."end") - julianday(NEW."start") END) > 1830, 0))
 AND (NEW."start" IS NOT OLD."start" OR NEW."end" IS NOT OLD."end")
BEGIN SELECT RAISE(ABORT, 'permita38:range.too_long'); END;
CREATE TRIGGER "guard_allocations_rate_range_ins" BEFORE INSERT ON "allocations"
WHEN (NEW."rate" IS NOT NULL AND NEW."rate" <> '' AND (NEW."rate" < 0 OR NEW."rate" > 10000))
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "rate" IS NEW."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_allocations_rate_range_upd" BEFORE UPDATE OF "rate" ON "allocations"
WHEN (NEW."rate" IS NOT NULL AND NEW."rate" <> '' AND (NEW."rate" < 0 OR NEW."rate" > 10000))
 AND (NEW."rate" IS NOT OLD."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_allocations_cancelled_from_implies_ins" BEFORE INSERT ON "allocations"
WHEN (COALESCE(NEW."status" = 'cancelled', 0) AND NOT COALESCE((NEW."cancelled_from" IS NOT NULL AND trim(NEW."cancelled_from", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "status" IS NEW."status" AND "cancelled_from" IS NEW."cancelled_from")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_allocations_cancelled_from_implies'); END;
CREATE TRIGGER "guard_allocations_cancelled_from_implies_upd" BEFORE UPDATE OF "status", "cancelled_from" ON "allocations"
WHEN (COALESCE(NEW."status" = 'cancelled', 0) AND NOT COALESCE((NEW."cancelled_from" IS NOT NULL AND trim(NEW."cancelled_from", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND (NEW."status" IS NOT OLD."status" OR NEW."cancelled_from" IS NOT OLD."cancelled_from")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_allocations_cancelled_from_implies'); END;
CREATE TRIGGER "guard_allocations_active_no_cancelled_from_implies_ins" BEFORE INSERT ON "allocations"
WHEN (COALESCE(NEW."status" = 'active', 0) AND NOT COALESCE(NOT (NEW."cancelled_from" IS NOT NULL AND trim(NEW."cancelled_from", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "status" IS NEW."status" AND "cancelled_from" IS NEW."cancelled_from")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_allocations_active_no_cancelled_from_implies'); END;
CREATE TRIGGER "guard_allocations_active_no_cancelled_from_implies_upd" BEFORE UPDATE OF "status", "cancelled_from" ON "allocations"
WHEN (COALESCE(NEW."status" = 'active', 0) AND NOT COALESCE(NOT (NEW."cancelled_from" IS NOT NULL AND trim(NEW."cancelled_from", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND (NEW."status" IS NOT OLD."status" OR NEW."cancelled_from" IS NOT OLD."cancelled_from")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_allocations_active_no_cancelled_from_implies'); END;
CREATE TRIGGER "guard_allocations_supplier_rate_implies_ins" BEFORE INSERT ON "allocations"
WHEN (COALESCE((NEW."supplier_id" IS NOT NULL AND trim(NEW."supplier_id", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0) AND NOT COALESCE((NEW."rate" IS NOT NULL AND trim(NEW."rate", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "supplier_id" IS NEW."supplier_id" AND "rate" IS NEW."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_allocations_supplier_rate_implies'); END;
CREATE TRIGGER "guard_allocations_supplier_rate_implies_upd" BEFORE UPDATE OF "supplier_id", "rate" ON "allocations"
WHEN (COALESCE((NEW."supplier_id" IS NOT NULL AND trim(NEW."supplier_id", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0) AND NOT COALESCE((NEW."rate" IS NOT NULL AND trim(NEW."rate", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND (NEW."supplier_id" IS NOT OLD."supplier_id" OR NEW."rate" IS NOT OLD."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_allocations_supplier_rate_implies'); END;
CREATE TRIGGER "guard_allocations_user_no_incoming_offer_implies_ins" BEFORE INSERT ON "allocations"
WHEN (COALESCE((NEW."user_id" IS NOT NULL AND trim(NEW."user_id", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0) AND NOT COALESCE(NOT (NEW."incoming_offer_id" IS NOT NULL AND trim(NEW."incoming_offer_id", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "user_id" IS NEW."user_id" AND "incoming_offer_id" IS NEW."incoming_offer_id")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_allocations_user_no_incoming_offer_implies'); END;
CREATE TRIGGER "guard_allocations_user_no_incoming_offer_implies_upd" BEFORE UPDATE OF "user_id", "incoming_offer_id" ON "allocations"
WHEN (COALESCE((NEW."user_id" IS NOT NULL AND trim(NEW."user_id", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0) AND NOT COALESCE(NOT (NEW."incoming_offer_id" IS NOT NULL AND trim(NEW."incoming_offer_id", ' ' || char(9, 10, 11, 12, 13)) <> ''), 0))
 AND (NEW."user_id" IS NOT OLD."user_id" OR NEW."incoming_offer_id" IS NOT OLD."incoming_offer_id")
BEGIN SELECT RAISE(ABORT, 'permita38:state.implies:guard_allocations_user_no_incoming_offer_implies'); END;
CREATE TRIGGER "guard_contracts_title_required_ins" BEFORE INSERT ON "contracts"
WHEN (NOT (NEW."title" IS NOT NULL AND trim(NEW."title", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "title" IS NEW."title")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_contracts_title_required_upd" BEFORE UPDATE OF "title" ON "contracts"
WHEN (NOT (NEW."title" IS NOT NULL AND trim(NEW."title", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."title" IS NOT OLD."title")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_contracts_start_required_ins" BEFORE INSERT ON "contracts"
WHEN (NOT (NEW."start" IS NOT NULL AND trim(NEW."start", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "start" IS NEW."start")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_contracts_start_required_upd" BEFORE UPDATE OF "start" ON "contracts"
WHEN (NOT (NEW."start" IS NOT NULL AND trim(NEW."start", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."start" IS NOT OLD."start")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_contracts_end_required_ins" BEFORE INSERT ON "contracts"
WHEN (NOT (NEW."end" IS NOT NULL AND trim(NEW."end", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "end" IS NEW."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_contracts_end_required_upd" BEFORE UPDATE OF "end" ON "contracts"
WHEN (NOT (NEW."end" IS NOT NULL AND trim(NEW."end", ' ' || char(9, 10, 11, 12, 13)) <> ''))
 AND (NEW."end" IS NOT OLD."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;
CREATE TRIGGER "guard_contracts_status_one_of_ins" BEFORE INSERT ON "contracts"
WHEN ((NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."status" NOT IN ('confirmed', 'done'))
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "status" IS NEW."status")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_contracts_status_one_of_upd" BEFORE UPDATE OF "status" ON "contracts"
WHEN ((NEW."status" IS NOT NULL AND trim(NEW."status", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NEW."status" NOT IN ('confirmed', 'done'))
 AND (NEW."status" IS NOT OLD."status")
BEGIN SELECT RAISE(ABORT, 'permita38:vocab.unknown'); END;
CREATE TRIGGER "guard_contracts_hours_exactly_one_ins" BEFORE INSERT ON "contracts"
WHEN (((NEW."hours_per_week" IS NOT NULL AND trim(NEW."hours_per_week", ' ' || char(9, 10, 11, 12, 13)) <> '') AND (NEW."total_hours" IS NOT NULL AND trim(NEW."total_hours", ' ' || char(9, 10, 11, 12, 13)) <> '')) OR (NOT (NEW."hours_per_week" IS NOT NULL AND trim(NEW."hours_per_week", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT (NEW."total_hours" IS NOT NULL AND trim(NEW."total_hours", ' ' || char(9, 10, 11, 12, 13)) <> '')))
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "hours_per_week" IS NEW."hours_per_week" AND "total_hours" IS NEW."total_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:state.exactly_one'); END;
CREATE TRIGGER "guard_contracts_hours_exactly_one_upd" BEFORE UPDATE OF "hours_per_week", "total_hours" ON "contracts"
WHEN (((NEW."hours_per_week" IS NOT NULL AND trim(NEW."hours_per_week", ' ' || char(9, 10, 11, 12, 13)) <> '') AND (NEW."total_hours" IS NOT NULL AND trim(NEW."total_hours", ' ' || char(9, 10, 11, 12, 13)) <> '')) OR (NOT (NEW."hours_per_week" IS NOT NULL AND trim(NEW."hours_per_week", ' ' || char(9, 10, 11, 12, 13)) <> '') AND NOT (NEW."total_hours" IS NOT NULL AND trim(NEW."total_hours", ' ' || char(9, 10, 11, 12, 13)) <> '')))
 AND (NEW."hours_per_week" IS NOT OLD."hours_per_week" OR NEW."total_hours" IS NOT OLD."total_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:state.exactly_one'); END;
CREATE TRIGGER "guard_contracts_hours_per_week_range_ins" BEFORE INSERT ON "contracts"
WHEN (NEW."hours_per_week" IS NOT NULL AND NEW."hours_per_week" <> '' AND (NEW."hours_per_week" <= 0 OR NEW."hours_per_week" > 10000))
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "hours_per_week" IS NEW."hours_per_week")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_contracts_hours_per_week_range_upd" BEFORE UPDATE OF "hours_per_week" ON "contracts"
WHEN (NEW."hours_per_week" IS NOT NULL AND NEW."hours_per_week" <> '' AND (NEW."hours_per_week" <= 0 OR NEW."hours_per_week" > 10000))
 AND (NEW."hours_per_week" IS NOT OLD."hours_per_week")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_contracts_total_hours_range_ins" BEFORE INSERT ON "contracts"
WHEN (NEW."total_hours" IS NOT NULL AND NEW."total_hours" <> '' AND (NEW."total_hours" <= 0 OR NEW."total_hours" > 100000))
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "total_hours" IS NEW."total_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_contracts_total_hours_range_upd" BEFORE UPDATE OF "total_hours" ON "contracts"
WHEN (NEW."total_hours" IS NOT NULL AND NEW."total_hours" <> '' AND (NEW."total_hours" <= 0 OR NEW."total_hours" > 100000))
 AND (NEW."total_hours" IS NOT OLD."total_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_contracts_rate_range_ins" BEFORE INSERT ON "contracts"
WHEN (NEW."rate" IS NOT NULL AND NEW."rate" <> '' AND (NEW."rate" < 0 OR NEW."rate" > 10000))
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "rate" IS NEW."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
CREATE TRIGGER "guard_contracts_rate_range_upd" BEFORE UPDATE OF "rate" ON "contracts"
WHEN (NEW."rate" IS NOT NULL AND NEW."rate" <> '' AND (NEW."rate" < 0 OR NEW."rate" > 10000))
 AND (NEW."rate" IS NOT OLD."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.bounds'); END;
