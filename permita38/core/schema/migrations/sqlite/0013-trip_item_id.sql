-- migration: f12b52ff
-- requires: c6d8d0be
-- permitA38 migration: trip-item-id (sqlite)


-- expenses.trip_item_id: the planned item of the trip a bill is booked against (docs/specs/business-trips-wizard.md)
ALTER TABLE "expenses" ADD COLUMN "trip_item_id" TEXT;
