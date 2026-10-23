-- permitA38 migration 0003: planning (sqlite)
-- RENUMBERING NOTE: this branch numbers it 0003 because it is the next free number here; the parent renumbers
-- it to 0009 when the planner branch is merged after the other branches (update CURRENT_REVISION accordingly).
-- projects.start / end: project period; backfilled from the earliest/latest contract and time entry where known.
-- projects.extensions: JSON list of period extensions (manual or by offer). plan_scenarios: named what-if scenarios.

ALTER TABLE "projects" ADD COLUMN "start" TEXT;
ALTER TABLE "projects" ADD COLUMN "end" TEXT;
ALTER TABLE "projects" ADD COLUMN "extensions" TEXT;

-- Backfill without derived tables (a derived table may not see the outer row in MariaDB): contracts first, then
-- time entries widen the period where they lie outside it.
UPDATE "projects" SET "start" = (SELECT MIN(contracts."start") FROM "contracts" WHERE contracts."project_id" = "projects"."id"), "end" = (SELECT MAX(contracts."end") FROM "contracts" WHERE contracts."project_id" = "projects"."id");
UPDATE "projects" SET "start" = (SELECT MIN(entries."date") FROM "entries" WHERE entries."project_id" = "projects"."id")
WHERE EXISTS (SELECT 1 FROM "entries" e0 WHERE e0."project_id" = "projects"."id")
  AND ("start" IS NULL OR "start" > (SELECT MIN(e1."date") FROM "entries" e1 WHERE e1."project_id" = "projects"."id"));
UPDATE "projects" SET "end" = (SELECT MAX(entries."date") FROM "entries" WHERE entries."project_id" = "projects"."id")
WHERE EXISTS (SELECT 1 FROM "entries" e0 WHERE e0."project_id" = "projects"."id")
  AND ("end" IS NULL OR "end" < (SELECT MAX(e1."date") FROM "entries" e1 WHERE e1."project_id" = "projects"."id"));

CREATE TABLE "plan_scenarios" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "name" TEXT,
    "user_id" TEXT,
    "changes" TEXT,
    "extra" TEXT
);
CREATE INDEX "idx_plan_scenarios_user" ON "plan_scenarios" ("user_id");
