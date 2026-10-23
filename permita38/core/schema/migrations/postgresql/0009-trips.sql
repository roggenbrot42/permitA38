-- migration: 9897dd11
-- requires: d8a69cde
-- permitA38 migration: trips (postgresql)

-- trips: one row per business trip (docs/specs/business-trips.md); figures are stored when computed
ALTER TABLE "expenses" ADD COLUMN "trip_id" TEXT;

CREATE TABLE "trips" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "user_id" TEXT,
    "title" TEXT,
    "purpose" TEXT,
    "customer_id" TEXT,
    "project_id" TEXT,
    "start" TEXT,
    "end" TEXT,
    "places" JSONB,
    "meals_provided" JSONB,
    "mileage" JSONB,
    "not_needed" JSONB,
    "status" TEXT,
    "allowance_total" DOUBLE PRECISION,
    "mileage_total" DOUBLE PRECISION,
    "rules_version" TEXT,
    "days" JSONB,
    "extra" JSONB
);
CREATE INDEX "idx_trips_user" ON "trips" ("user_id");
