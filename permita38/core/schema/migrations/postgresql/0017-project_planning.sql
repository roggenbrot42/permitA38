-- migration: 7ca84bdc
-- requires: e4b70dba
-- permitA38 migration: project_planning (postgresql)

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
INSERT INTO "projects" ("id", "name", "kind", "status", "created", "updated")
SELECT 'internaladmin', 'Administration', 'internal', 'active', CAST(EXTRACT(EPOCH FROM NOW()) AS BIGINT), CAST(EXTRACT(EPOCH FROM NOW()) AS BIGINT)
WHERE NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = 'internaladmin');

ALTER TABLE "projects" DROP COLUMN "personal";

-- 3. guards: generated from core/repository/schema.py GUARDS by Dialect.guard_sql (a test keeps them equal)
ALTER TABLE "projects" ADD CONSTRAINT "guard_projects_kind_required" CHECK (NOT COALESCE((NOT ("kind" IS NOT NULL AND btrim(CAST("kind" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "projects" ADD CONSTRAINT "guard_projects_kind_one_of" CHECK (NOT COALESCE((("kind" IS NOT NULL AND btrim(CAST("kind" AS TEXT), E' \t\n\r\f\v') <> '') AND "kind" NOT IN ('customer', 'internal')), FALSE));
ALTER TABLE "projects" ADD CONSTRAINT "guard_projects_status_one_of" CHECK (NOT COALESCE((("status" IS NOT NULL AND btrim(CAST("status" AS TEXT), E' \t\n\r\f\v') <> '') AND "status" NOT IN ('active', 'paused', 'done', 'archived')), FALSE));
ALTER TABLE "projects" ADD CONSTRAINT "guard_projects_name_required" CHECK (NOT COALESCE((NOT ("name" IS NOT NULL AND btrim(CAST("name" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "projects" ADD CONSTRAINT "guard_projects_internal_customer_implies" CHECK (NOT COALESCE((COALESCE("kind" = 'internal', FALSE) AND NOT COALESCE(NOT ("customer_id" IS NOT NULL AND btrim(CAST("customer_id" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE)), FALSE));
ALTER TABLE "projects" ADD CONSTRAINT "guard_projects_budget_hours_range" CHECK (NOT COALESCE(("budget_hours" IS NOT NULL AND ("budget_hours" <= 0 OR "budget_hours" > 100000)), FALSE));
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_project_id_required" CHECK (NOT COALESCE((NOT ("project_id" IS NOT NULL AND btrim(CAST("project_id" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_start_required" CHECK (NOT COALESCE((NOT ("start" IS NOT NULL AND btrim(CAST("start" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_end_required" CHECK (NOT COALESCE((NOT ("end" IS NOT NULL AND btrim(CAST("end" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_status_one_of" CHECK (NOT COALESCE((("status" IS NOT NULL AND btrim(CAST("status" AS TEXT), E' \t\n\r\f\v') <> '') AND "status" NOT IN ('active', 'cancelled')), FALSE));
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_hours_exactly_one" CHECK (NOT COALESCE(((("hours_per_week" IS NOT NULL AND btrim(CAST("hours_per_week" AS TEXT), E' \t\n\r\f\v') <> '') AND ("total_hours" IS NOT NULL AND btrim(CAST("total_hours" AS TEXT), E' \t\n\r\f\v') <> '')) OR (NOT ("hours_per_week" IS NOT NULL AND btrim(CAST("hours_per_week" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT ("total_hours" IS NOT NULL AND btrim(CAST("total_hours" AS TEXT), E' \t\n\r\f\v') <> ''))), FALSE));
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_hours_per_week_range" CHECK (NOT COALESCE(("hours_per_week" IS NOT NULL AND ("hours_per_week" <= 0 OR "hours_per_week" > 80)), FALSE));
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_total_hours_range" CHECK (NOT COALESCE(("total_hours" IS NOT NULL AND ("total_hours" <= 0 OR "total_hours" > 100000)), FALSE));
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_period_max_days" CHECK (NOT COALESCE((COALESCE((CASE WHEN "start" ~ '^(?!0000)(?:[0-9]{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12][0-9]|3[01])|(?:0[469]|11)-(?:0[1-9]|[12][0-9]|30)|02-(?:0[1-9]|1[0-9]|2[0-8]))|(?:[0-9]{2}(?:0[48]|[2468][048]|[13579][26])|(?:[02468][048]|[13579][26])00)-02-29)$' AND "end" ~ '^(?!0000)(?:[0-9]{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12][0-9]|3[01])|(?:0[469]|11)-(?:0[1-9]|[12][0-9]|30)|02-(?:0[1-9]|1[0-9]|2[0-8]))|(?:[0-9]{2}(?:0[48]|[2468][048]|[13579][26])|(?:[02468][048]|[13579][26])00)-02-29)$' THEN CAST("end" AS date) - CAST("start" AS date) END) > 1830, FALSE)), FALSE));
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_rate_range" CHECK (NOT COALESCE(("rate" IS NOT NULL AND ("rate" < 0 OR "rate" > 10000)), FALSE));
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_cancelled_from_implies" CHECK (NOT COALESCE((COALESCE("status" = 'cancelled', FALSE) AND NOT COALESCE(("cancelled_from" IS NOT NULL AND btrim(CAST("cancelled_from" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE)), FALSE));
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_active_no_cancelled_from_implies" CHECK (NOT COALESCE((COALESCE("status" = 'active', FALSE) AND NOT COALESCE(NOT ("cancelled_from" IS NOT NULL AND btrim(CAST("cancelled_from" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE)), FALSE));
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_supplier_rate_implies" CHECK (NOT COALESCE((COALESCE(("supplier_id" IS NOT NULL AND btrim(CAST("supplier_id" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE) AND NOT COALESCE(("rate" IS NOT NULL AND btrim(CAST("rate" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE)), FALSE));
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_user_no_incoming_offer_implies" CHECK (NOT COALESCE((COALESCE(("user_id" IS NOT NULL AND btrim(CAST("user_id" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE) AND NOT COALESCE(NOT ("incoming_offer_id" IS NOT NULL AND btrim(CAST("incoming_offer_id" AS TEXT), E' \t\n\r\f\v') <> ''), FALSE)), FALSE));
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_title_required" CHECK (NOT COALESCE((NOT ("title" IS NOT NULL AND btrim(CAST("title" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_start_required" CHECK (NOT COALESCE((NOT ("start" IS NOT NULL AND btrim(CAST("start" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_end_required" CHECK (NOT COALESCE((NOT ("end" IS NOT NULL AND btrim(CAST("end" AS TEXT), E' \t\n\r\f\v') <> '')), FALSE));
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_status_one_of" CHECK (NOT COALESCE((("status" IS NOT NULL AND btrim(CAST("status" AS TEXT), E' \t\n\r\f\v') <> '') AND "status" NOT IN ('confirmed', 'done')), FALSE));
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_hours_exactly_one" CHECK (NOT COALESCE(((("hours_per_week" IS NOT NULL AND btrim(CAST("hours_per_week" AS TEXT), E' \t\n\r\f\v') <> '') AND ("total_hours" IS NOT NULL AND btrim(CAST("total_hours" AS TEXT), E' \t\n\r\f\v') <> '')) OR (NOT ("hours_per_week" IS NOT NULL AND btrim(CAST("hours_per_week" AS TEXT), E' \t\n\r\f\v') <> '') AND NOT ("total_hours" IS NOT NULL AND btrim(CAST("total_hours" AS TEXT), E' \t\n\r\f\v') <> ''))), FALSE));
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_hours_per_week_range" CHECK (NOT COALESCE(("hours_per_week" IS NOT NULL AND ("hours_per_week" <= 0 OR "hours_per_week" > 10000)), FALSE));
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_total_hours_range" CHECK (NOT COALESCE(("total_hours" IS NOT NULL AND ("total_hours" <= 0 OR "total_hours" > 100000)), FALSE));
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_rate_range" CHECK (NOT COALESCE(("rate" IS NOT NULL AND ("rate" < 0 OR "rate" > 10000)), FALSE));
