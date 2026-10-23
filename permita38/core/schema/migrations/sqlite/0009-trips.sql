-- migration: 9897dd11
-- requires: d8a69cde
-- permitA38 migration: trips (sqlite)

-- trips: one row per business trip (docs/specs/business-trips.md); figures are stored when computed
ALTER TABLE "expenses" ADD COLUMN "trip_id" TEXT;

CREATE TABLE "trips" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "user_id" TEXT,
    "title" TEXT,
    "purpose" TEXT,
    "customer_id" TEXT,
    "project_id" TEXT,
    "start" TEXT,
    "end" TEXT,
    "places" TEXT,
    "meals_provided" TEXT,
    "mileage" TEXT,
    "not_needed" TEXT,
    "status" TEXT,
    "allowance_total" REAL,
    "mileage_total" REAL,
    "rules_version" TEXT,
    "days" TEXT,
    "extra" TEXT
);
CREATE INDEX "idx_trips_user" ON "trips" ("user_id");
