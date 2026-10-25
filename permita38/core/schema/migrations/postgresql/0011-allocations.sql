-- migration: 72306069
-- requires: c8af1eca
-- permitA38 migration: allocations (postgresql)

-- Allocations: hours per week of one resource (employee or, later, subcontractor) given to one project
CREATE TABLE "allocations" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "user_id" TEXT,
    "supplier_id" TEXT,
    "incoming_offer_id" TEXT,
    "project_id" TEXT,
    "contract_id" TEXT,
    "customer_id" TEXT,
    "start" TEXT,
    "end" TEXT,
    "hours_per_week" DOUBLE PRECISION,
    "total_hours" DOUBLE PRECISION,
    "rate" DOUBLE PRECISION,
    "status" TEXT,
    "cancelled_from" TEXT,
    "cancel_reason" TEXT,
    "extra" JSONB,
    CONSTRAINT "allocations_one_owner" CHECK (
        ("user_id" IS NOT NULL AND "supplier_id" IS NULL) OR ("user_id" IS NULL AND "supplier_id" IS NOT NULL))
);
CREATE INDEX "idx_allocations_user" ON "allocations" ("user_id");
CREATE INDEX "idx_allocations_project" ON "allocations" ("project_id");
