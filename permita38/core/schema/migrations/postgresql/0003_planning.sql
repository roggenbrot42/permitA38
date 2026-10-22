-- permitA38 migration 0003: planning (postgresql)
-- RENUMBERING NOTE: this branch numbers it 0003 because it is the next free number here; the parent renumbers it
-- to 0009 when the planner branch is merged after the other branches (update CURRENT_REVISION accordingly).
-- projects.start / end: project period; backfilled from the earliest/latest contract and time entry where known.
-- projects.extensions: JSON list of period extensions (manual or by offer). plan_scenarios: named what-if scenarios.

ALTER TABLE "projects" ADD COLUMN "start" TEXT;
ALTER TABLE "projects" ADD COLUMN "end" TEXT;
ALTER TABLE "projects" ADD COLUMN "extensions" JSONB;

UPDATE "projects" SET "start" = (
    SELECT MIN(t.x) FROM (
        SELECT "start" AS x FROM "contracts" WHERE "project_id" = "projects"."id"
        UNION ALL SELECT "date" AS x FROM "entries" WHERE "project_id" = "projects"."id"
    ) AS t
), "end" = (
    SELECT MAX(t.x) FROM (
        SELECT "end" AS x FROM "contracts" WHERE "project_id" = "projects"."id"
        UNION ALL SELECT "date" AS x FROM "entries" WHERE "project_id" = "projects"."id"
    ) AS t
);

CREATE TABLE "plan_scenarios" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "name" TEXT,
    "user_id" TEXT,
    "changes" JSONB,
    "extra" JSONB
);
CREATE INDEX "idx_plan_scenarios_user" ON "plan_scenarios" ("user_id");
