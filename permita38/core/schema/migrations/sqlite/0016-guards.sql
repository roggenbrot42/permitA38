-- migration: e4b70dba
-- requires: 2fe799c1
-- permitA38 migration: guards (sqlite)

-- non-unique lookup indexes for the number guards
CREATE INDEX "idx_invoices_number" ON "invoices" ("number");
CREATE INDEX "idx_offers_number" ON "offers" ("number");

-- guards: generated from core/repository/schema.py GUARDS by Dialect.guard_sql (a test keeps them equal)
CREATE TRIGGER "guard_invoices_number_unique_ins" BEFORE INSERT ON "invoices"
WHEN NEW."number" IS NOT NULL AND NEW."number" <> '' AND EXISTS (SELECT 1 FROM "invoices" WHERE "number" = NEW."number" AND "id" <> NEW."id")
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "number" IS NEW."number")
BEGIN SELECT RAISE(ABORT, 'permita38:numbering.duplicate'); END;
CREATE TRIGGER "guard_invoices_number_unique_upd" BEFORE UPDATE OF "number" ON "invoices"
WHEN NEW."number" IS NOT NULL AND NEW."number" <> '' AND EXISTS (SELECT 1 FROM "invoices" WHERE "number" = NEW."number" AND "id" <> NEW."id")
 AND (NEW."number" IS NOT OLD."number")
BEGIN SELECT RAISE(ABORT, 'permita38:numbering.duplicate'); END;
CREATE TRIGGER "guard_offers_number_unique_ins" BEFORE INSERT ON "offers"
WHEN NEW."number" IS NOT NULL AND NEW."number" <> '' AND EXISTS (SELECT 1 FROM "offers" WHERE "number" = NEW."number" AND "id" <> NEW."id")
 AND NOT EXISTS (SELECT 1 FROM "offers" WHERE "id" = NEW."id" AND "number" IS NEW."number")
BEGIN SELECT RAISE(ABORT, 'permita38:numbering.duplicate'); END;
CREATE TRIGGER "guard_offers_number_unique_upd" BEFORE UPDATE OF "number" ON "offers"
WHEN NEW."number" IS NOT NULL AND NEW."number" <> '' AND EXISTS (SELECT 1 FROM "offers" WHERE "number" = NEW."number" AND "id" <> NEW."id")
 AND (NEW."number" IS NOT OLD."number")
BEGIN SELECT RAISE(ABORT, 'permita38:numbering.duplicate'); END;
CREATE TRIGGER "guard_entries_date_date_ins" BEFORE INSERT ON "entries"
WHEN NEW."date" IS NOT NULL AND NEW."date" <> '' AND NOT (NEW."date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "entries" WHERE "id" = NEW."id" AND "date" IS NEW."date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_entries_date_date_upd" BEFORE UPDATE OF "date" ON "entries"
WHEN NEW."date" IS NOT NULL AND NEW."date" <> '' AND NOT (NEW."date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."date" IS NOT OLD."date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_invoices_date_date_ins" BEFORE INSERT ON "invoices"
WHEN NEW."date" IS NOT NULL AND NEW."date" <> '' AND NOT (NEW."date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "date" IS NEW."date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_invoices_date_date_upd" BEFORE UPDATE OF "date" ON "invoices"
WHEN NEW."date" IS NOT NULL AND NEW."date" <> '' AND NOT (NEW."date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."date" IS NOT OLD."date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_invoices_due_date_date_ins" BEFORE INSERT ON "invoices"
WHEN NEW."due_date" IS NOT NULL AND NEW."due_date" <> '' AND NOT (NEW."due_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "due_date" IS NEW."due_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_invoices_due_date_date_upd" BEFORE UPDATE OF "due_date" ON "invoices"
WHEN NEW."due_date" IS NOT NULL AND NEW."due_date" <> '' AND NOT (NEW."due_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."due_date" IS NOT OLD."due_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_invoices_valid_until_date_ins" BEFORE INSERT ON "invoices"
WHEN NEW."valid_until" IS NOT NULL AND NEW."valid_until" <> '' AND NOT (NEW."valid_until" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "valid_until" IS NEW."valid_until")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_invoices_valid_until_date_upd" BEFORE UPDATE OF "valid_until" ON "invoices"
WHEN NEW."valid_until" IS NOT NULL AND NEW."valid_until" <> '' AND NOT (NEW."valid_until" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."valid_until" IS NOT OLD."valid_until")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_invoices_paid_date_date_ins" BEFORE INSERT ON "invoices"
WHEN NEW."paid_date" IS NOT NULL AND NEW."paid_date" <> '' AND NOT (NEW."paid_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "paid_date" IS NEW."paid_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_invoices_paid_date_date_upd" BEFORE UPDATE OF "paid_date" ON "invoices"
WHEN NEW."paid_date" IS NOT NULL AND NEW."paid_date" <> '' AND NOT (NEW."paid_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."paid_date" IS NOT OLD."paid_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_invoices_service_from_date_ins" BEFORE INSERT ON "invoices"
WHEN NEW."service_from" IS NOT NULL AND NEW."service_from" <> '' AND NOT (NEW."service_from" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "service_from" IS NEW."service_from")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_invoices_service_from_date_upd" BEFORE UPDATE OF "service_from" ON "invoices"
WHEN NEW."service_from" IS NOT NULL AND NEW."service_from" <> '' AND NOT (NEW."service_from" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."service_from" IS NOT OLD."service_from")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_invoices_service_to_date_ins" BEFORE INSERT ON "invoices"
WHEN NEW."service_to" IS NOT NULL AND NEW."service_to" <> '' AND NOT (NEW."service_to" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "service_to" IS NEW."service_to")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_invoices_service_to_date_upd" BEFORE UPDATE OF "service_to" ON "invoices"
WHEN NEW."service_to" IS NOT NULL AND NEW."service_to" <> '' AND NOT (NEW."service_to" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."service_to" IS NOT OLD."service_to")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_offers_date_date_ins" BEFORE INSERT ON "offers"
WHEN NEW."date" IS NOT NULL AND NEW."date" <> '' AND NOT (NEW."date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "offers" WHERE "id" = NEW."id" AND "date" IS NEW."date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_offers_date_date_upd" BEFORE UPDATE OF "date" ON "offers"
WHEN NEW."date" IS NOT NULL AND NEW."date" <> '' AND NOT (NEW."date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."date" IS NOT OLD."date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_offers_due_date_date_ins" BEFORE INSERT ON "offers"
WHEN NEW."due_date" IS NOT NULL AND NEW."due_date" <> '' AND NOT (NEW."due_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "offers" WHERE "id" = NEW."id" AND "due_date" IS NEW."due_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_offers_due_date_date_upd" BEFORE UPDATE OF "due_date" ON "offers"
WHEN NEW."due_date" IS NOT NULL AND NEW."due_date" <> '' AND NOT (NEW."due_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."due_date" IS NOT OLD."due_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_offers_valid_until_date_ins" BEFORE INSERT ON "offers"
WHEN NEW."valid_until" IS NOT NULL AND NEW."valid_until" <> '' AND NOT (NEW."valid_until" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "offers" WHERE "id" = NEW."id" AND "valid_until" IS NEW."valid_until")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_offers_valid_until_date_upd" BEFORE UPDATE OF "valid_until" ON "offers"
WHEN NEW."valid_until" IS NOT NULL AND NEW."valid_until" <> '' AND NOT (NEW."valid_until" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."valid_until" IS NOT OLD."valid_until")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_offers_paid_date_date_ins" BEFORE INSERT ON "offers"
WHEN NEW."paid_date" IS NOT NULL AND NEW."paid_date" <> '' AND NOT (NEW."paid_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "offers" WHERE "id" = NEW."id" AND "paid_date" IS NEW."paid_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_offers_paid_date_date_upd" BEFORE UPDATE OF "paid_date" ON "offers"
WHEN NEW."paid_date" IS NOT NULL AND NEW."paid_date" <> '' AND NOT (NEW."paid_date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."paid_date" IS NOT OLD."paid_date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_offers_service_from_date_ins" BEFORE INSERT ON "offers"
WHEN NEW."service_from" IS NOT NULL AND NEW."service_from" <> '' AND NOT (NEW."service_from" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "offers" WHERE "id" = NEW."id" AND "service_from" IS NEW."service_from")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_offers_service_from_date_upd" BEFORE UPDATE OF "service_from" ON "offers"
WHEN NEW."service_from" IS NOT NULL AND NEW."service_from" <> '' AND NOT (NEW."service_from" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."service_from" IS NOT OLD."service_from")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_offers_service_to_date_ins" BEFORE INSERT ON "offers"
WHEN NEW."service_to" IS NOT NULL AND NEW."service_to" <> '' AND NOT (NEW."service_to" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "offers" WHERE "id" = NEW."id" AND "service_to" IS NEW."service_to")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_offers_service_to_date_upd" BEFORE UPDATE OF "service_to" ON "offers"
WHEN NEW."service_to" IS NOT NULL AND NEW."service_to" <> '' AND NOT (NEW."service_to" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."service_to" IS NOT OLD."service_to")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_expenses_date_date_ins" BEFORE INSERT ON "expenses"
WHEN NEW."date" IS NOT NULL AND NEW."date" <> '' AND NOT (NEW."date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "expenses" WHERE "id" = NEW."id" AND "date" IS NEW."date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_expenses_date_date_upd" BEFORE UPDATE OF "date" ON "expenses"
WHEN NEW."date" IS NOT NULL AND NEW."date" <> '' AND NOT (NEW."date" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."date" IS NOT OLD."date")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_expenses_paid_on_date_ins" BEFORE INSERT ON "expenses"
WHEN NEW."paid_on" IS NOT NULL AND NEW."paid_on" <> '' AND NOT (NEW."paid_on" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "expenses" WHERE "id" = NEW."id" AND "paid_on" IS NEW."paid_on")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_expenses_paid_on_date_upd" BEFORE UPDATE OF "paid_on" ON "expenses"
WHEN NEW."paid_on" IS NOT NULL AND NEW."paid_on" <> '' AND NOT (NEW."paid_on" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."paid_on" IS NOT OLD."paid_on")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_absences_start_date_ins" BEFORE INSERT ON "absences"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NOT (NEW."start" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "absences" WHERE "id" = NEW."id" AND "start" IS NEW."start")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_absences_start_date_upd" BEFORE UPDATE OF "start" ON "absences"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NOT (NEW."start" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."start" IS NOT OLD."start")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_absences_end_date_ins" BEFORE INSERT ON "absences"
WHEN NEW."end" IS NOT NULL AND NEW."end" <> '' AND NOT (NEW."end" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "absences" WHERE "id" = NEW."id" AND "end" IS NEW."end")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_absences_end_date_upd" BEFORE UPDATE OF "end" ON "absences"
WHEN NEW."end" IS NOT NULL AND NEW."end" <> '' AND NOT (NEW."end" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."end" IS NOT OLD."end")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_contracts_start_date_ins" BEFORE INSERT ON "contracts"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NOT (NEW."start" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "start" IS NEW."start")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_contracts_start_date_upd" BEFORE UPDATE OF "start" ON "contracts"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NOT (NEW."start" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."start" IS NOT OLD."start")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_contracts_end_date_ins" BEFORE INSERT ON "contracts"
WHEN NEW."end" IS NOT NULL AND NEW."end" <> '' AND NOT (NEW."end" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "end" IS NEW."end")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_contracts_end_date_upd" BEFORE UPDATE OF "end" ON "contracts"
WHEN NEW."end" IS NOT NULL AND NEW."end" <> '' AND NOT (NEW."end" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."end" IS NOT OLD."end")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_projects_start_date_ins" BEFORE INSERT ON "projects"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NOT (NEW."start" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."id" AND "start" IS NEW."start")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_projects_start_date_upd" BEFORE UPDATE OF "start" ON "projects"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NOT (NEW."start" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."start" IS NOT OLD."start")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_projects_end_date_ins" BEFORE INSERT ON "projects"
WHEN NEW."end" IS NOT NULL AND NEW."end" <> '' AND NOT (NEW."end" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."id" AND "end" IS NEW."end")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_projects_end_date_upd" BEFORE UPDATE OF "end" ON "projects"
WHEN NEW."end" IS NOT NULL AND NEW."end" <> '' AND NOT (NEW."end" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."end" IS NOT OLD."end")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_allocations_start_date_ins" BEFORE INSERT ON "allocations"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NOT (NEW."start" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "start" IS NEW."start")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_allocations_start_date_upd" BEFORE UPDATE OF "start" ON "allocations"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NOT (NEW."start" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."start" IS NOT OLD."start")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_allocations_end_date_ins" BEFORE INSERT ON "allocations"
WHEN NEW."end" IS NOT NULL AND NEW."end" <> '' AND NOT (NEW."end" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "end" IS NEW."end")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_allocations_end_date_upd" BEFORE UPDATE OF "end" ON "allocations"
WHEN NEW."end" IS NOT NULL AND NEW."end" <> '' AND NOT (NEW."end" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."end" IS NOT OLD."end")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_allocations_cancelled_from_date_ins" BEFORE INSERT ON "allocations"
WHEN NEW."cancelled_from" IS NOT NULL AND NEW."cancelled_from" <> '' AND NOT (NEW."cancelled_from" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "cancelled_from" IS NEW."cancelled_from")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_allocations_cancelled_from_date_upd" BEFORE UPDATE OF "cancelled_from" ON "allocations"
WHEN NEW."cancelled_from" IS NOT NULL AND NEW."cancelled_from" <> '' AND NOT (NEW."cancelled_from" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."cancelled_from" IS NOT OLD."cancelled_from")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_incoming_offers_received_on_date_ins" BEFORE INSERT ON "incoming_offers"
WHEN NEW."received_on" IS NOT NULL AND NEW."received_on" <> '' AND NOT (NEW."received_on" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "incoming_offers" WHERE "id" = NEW."id" AND "received_on" IS NEW."received_on")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_incoming_offers_received_on_date_upd" BEFORE UPDATE OF "received_on" ON "incoming_offers"
WHEN NEW."received_on" IS NOT NULL AND NEW."received_on" <> '' AND NOT (NEW."received_on" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."received_on" IS NOT OLD."received_on")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_incoming_offers_valid_until_date_ins" BEFORE INSERT ON "incoming_offers"
WHEN NEW."valid_until" IS NOT NULL AND NEW."valid_until" <> '' AND NOT (NEW."valid_until" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND NOT EXISTS (SELECT 1 FROM "incoming_offers" WHERE "id" = NEW."id" AND "valid_until" IS NEW."valid_until")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_incoming_offers_valid_until_date_upd" BEFORE UPDATE OF "valid_until" ON "incoming_offers"
WHEN NEW."valid_until" IS NOT NULL AND NEW."valid_until" <> '' AND NOT (NEW."valid_until" GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')
 AND (NEW."valid_until" IS NOT OLD."valid_until")
BEGIN SELECT RAISE(ABORT, 'permita38:format.date'); END;
CREATE TRIGGER "guard_entries_minutes_nonneg_ins" BEFORE INSERT ON "entries"
WHEN NEW."minutes" IS NOT NULL AND NEW."minutes" < 0
 AND NOT EXISTS (SELECT 1 FROM "entries" WHERE "id" = NEW."id" AND "minutes" IS NEW."minutes")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_entries_minutes_nonneg_upd" BEFORE UPDATE OF "minutes" ON "entries"
WHEN NEW."minutes" IS NOT NULL AND NEW."minutes" < 0
 AND (NEW."minutes" IS NOT OLD."minutes")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_entries_rate_nonneg_ins" BEFORE INSERT ON "entries"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND NOT EXISTS (SELECT 1 FROM "entries" WHERE "id" = NEW."id" AND "rate" IS NEW."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_entries_rate_nonneg_upd" BEFORE UPDATE OF "rate" ON "entries"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND (NEW."rate" IS NOT OLD."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_customers_rate_nonneg_ins" BEFORE INSERT ON "customers"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND NOT EXISTS (SELECT 1 FROM "customers" WHERE "id" = NEW."id" AND "rate" IS NEW."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_customers_rate_nonneg_upd" BEFORE UPDATE OF "rate" ON "customers"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND (NEW."rate" IS NOT OLD."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_projects_rate_nonneg_ins" BEFORE INSERT ON "projects"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."id" AND "rate" IS NEW."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_projects_rate_nonneg_upd" BEFORE UPDATE OF "rate" ON "projects"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND (NEW."rate" IS NOT OLD."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_projects_budget_hours_nonneg_ins" BEFORE INSERT ON "projects"
WHEN NEW."budget_hours" IS NOT NULL AND NEW."budget_hours" < 0
 AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."id" AND "budget_hours" IS NEW."budget_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_projects_budget_hours_nonneg_upd" BEFORE UPDATE OF "budget_hours" ON "projects"
WHEN NEW."budget_hours" IS NOT NULL AND NEW."budget_hours" < 0
 AND (NEW."budget_hours" IS NOT OLD."budget_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_tasks_rate_nonneg_ins" BEFORE INSERT ON "tasks"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND NOT EXISTS (SELECT 1 FROM "tasks" WHERE "id" = NEW."id" AND "rate" IS NEW."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_tasks_rate_nonneg_upd" BEFORE UPDATE OF "rate" ON "tasks"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND (NEW."rate" IS NOT OLD."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_contracts_rate_nonneg_ins" BEFORE INSERT ON "contracts"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "rate" IS NEW."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_contracts_rate_nonneg_upd" BEFORE UPDATE OF "rate" ON "contracts"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND (NEW."rate" IS NOT OLD."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_contracts_hours_per_week_nonneg_ins" BEFORE INSERT ON "contracts"
WHEN NEW."hours_per_week" IS NOT NULL AND NEW."hours_per_week" < 0
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "hours_per_week" IS NEW."hours_per_week")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_contracts_hours_per_week_nonneg_upd" BEFORE UPDATE OF "hours_per_week" ON "contracts"
WHEN NEW."hours_per_week" IS NOT NULL AND NEW."hours_per_week" < 0
 AND (NEW."hours_per_week" IS NOT OLD."hours_per_week")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_contracts_total_hours_nonneg_ins" BEFORE INSERT ON "contracts"
WHEN NEW."total_hours" IS NOT NULL AND NEW."total_hours" < 0
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "total_hours" IS NEW."total_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_contracts_total_hours_nonneg_upd" BEFORE UPDATE OF "total_hours" ON "contracts"
WHEN NEW."total_hours" IS NOT NULL AND NEW."total_hours" < 0
 AND (NEW."total_hours" IS NOT OLD."total_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_allocations_rate_nonneg_ins" BEFORE INSERT ON "allocations"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "rate" IS NEW."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_allocations_rate_nonneg_upd" BEFORE UPDATE OF "rate" ON "allocations"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND (NEW."rate" IS NOT OLD."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_allocations_hours_per_week_nonneg_ins" BEFORE INSERT ON "allocations"
WHEN NEW."hours_per_week" IS NOT NULL AND NEW."hours_per_week" < 0
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "hours_per_week" IS NEW."hours_per_week")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_allocations_hours_per_week_nonneg_upd" BEFORE UPDATE OF "hours_per_week" ON "allocations"
WHEN NEW."hours_per_week" IS NOT NULL AND NEW."hours_per_week" < 0
 AND (NEW."hours_per_week" IS NOT OLD."hours_per_week")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_allocations_total_hours_nonneg_ins" BEFORE INSERT ON "allocations"
WHEN NEW."total_hours" IS NOT NULL AND NEW."total_hours" < 0
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "total_hours" IS NEW."total_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_allocations_total_hours_nonneg_upd" BEFORE UPDATE OF "total_hours" ON "allocations"
WHEN NEW."total_hours" IS NOT NULL AND NEW."total_hours" < 0
 AND (NEW."total_hours" IS NOT OLD."total_hours")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_suppliers_rate_nonneg_ins" BEFORE INSERT ON "suppliers"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND NOT EXISTS (SELECT 1 FROM "suppliers" WHERE "id" = NEW."id" AND "rate" IS NEW."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_suppliers_rate_nonneg_upd" BEFORE UPDATE OF "rate" ON "suppliers"
WHEN NEW."rate" IS NOT NULL AND NEW."rate" < 0
 AND (NEW."rate" IS NOT OLD."rate")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_expenses_net_nonneg_ins" BEFORE INSERT ON "expenses"
WHEN NEW."net" IS NOT NULL AND NEW."net" < 0
 AND NOT EXISTS (SELECT 1 FROM "expenses" WHERE "id" = NEW."id" AND "net" IS NEW."net")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_expenses_net_nonneg_upd" BEFORE UPDATE OF "net" ON "expenses"
WHEN NEW."net" IS NOT NULL AND NEW."net" < 0
 AND (NEW."net" IS NOT OLD."net")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_expenses_vat_nonneg_ins" BEFORE INSERT ON "expenses"
WHEN NEW."vat" IS NOT NULL AND NEW."vat" < 0
 AND NOT EXISTS (SELECT 1 FROM "expenses" WHERE "id" = NEW."id" AND "vat" IS NEW."vat")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_expenses_vat_nonneg_upd" BEFORE UPDATE OF "vat" ON "expenses"
WHEN NEW."vat" IS NOT NULL AND NEW."vat" < 0
 AND (NEW."vat" IS NOT OLD."vat")
BEGIN SELECT RAISE(ABORT, 'permita38:range.negative'); END;
CREATE TRIGGER "guard_absences_period_order_ins" BEFORE INSERT ON "absences"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NEW."end" IS NOT NULL AND NEW."end" <> '' AND NEW."end" < NEW."start"
 AND NOT EXISTS (SELECT 1 FROM "absences" WHERE "id" = NEW."id" AND "start" IS NEW."start" AND "end" IS NEW."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
CREATE TRIGGER "guard_absences_period_order_upd" BEFORE UPDATE OF "start", "end" ON "absences"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NEW."end" IS NOT NULL AND NEW."end" <> '' AND NEW."end" < NEW."start"
 AND (NEW."start" IS NOT OLD."start" OR NEW."end" IS NOT OLD."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
CREATE TRIGGER "guard_contracts_period_order_ins" BEFORE INSERT ON "contracts"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NEW."end" IS NOT NULL AND NEW."end" <> '' AND NEW."end" < NEW."start"
 AND NOT EXISTS (SELECT 1 FROM "contracts" WHERE "id" = NEW."id" AND "start" IS NEW."start" AND "end" IS NEW."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
CREATE TRIGGER "guard_contracts_period_order_upd" BEFORE UPDATE OF "start", "end" ON "contracts"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NEW."end" IS NOT NULL AND NEW."end" <> '' AND NEW."end" < NEW."start"
 AND (NEW."start" IS NOT OLD."start" OR NEW."end" IS NOT OLD."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
CREATE TRIGGER "guard_projects_period_order_ins" BEFORE INSERT ON "projects"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NEW."end" IS NOT NULL AND NEW."end" <> '' AND NEW."end" < NEW."start"
 AND NOT EXISTS (SELECT 1 FROM "projects" WHERE "id" = NEW."id" AND "start" IS NEW."start" AND "end" IS NEW."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
CREATE TRIGGER "guard_projects_period_order_upd" BEFORE UPDATE OF "start", "end" ON "projects"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NEW."end" IS NOT NULL AND NEW."end" <> '' AND NEW."end" < NEW."start"
 AND (NEW."start" IS NOT OLD."start" OR NEW."end" IS NOT OLD."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
CREATE TRIGGER "guard_allocations_period_order_ins" BEFORE INSERT ON "allocations"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NEW."end" IS NOT NULL AND NEW."end" <> '' AND NEW."end" < NEW."start"
 AND NOT EXISTS (SELECT 1 FROM "allocations" WHERE "id" = NEW."id" AND "start" IS NEW."start" AND "end" IS NEW."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
CREATE TRIGGER "guard_allocations_period_order_upd" BEFORE UPDATE OF "start", "end" ON "allocations"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NEW."end" IS NOT NULL AND NEW."end" <> '' AND NEW."end" < NEW."start"
 AND (NEW."start" IS NOT OLD."start" OR NEW."end" IS NOT OLD."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
CREATE TRIGGER "guard_trips_period_order_ins" BEFORE INSERT ON "trips"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NEW."end" IS NOT NULL AND NEW."end" <> '' AND NEW."end" < NEW."start"
 AND NOT EXISTS (SELECT 1 FROM "trips" WHERE "id" = NEW."id" AND "start" IS NEW."start" AND "end" IS NEW."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
CREATE TRIGGER "guard_trips_period_order_upd" BEFORE UPDATE OF "start", "end" ON "trips"
WHEN NEW."start" IS NOT NULL AND NEW."start" <> '' AND NEW."end" IS NOT NULL AND NEW."end" <> '' AND NEW."end" < NEW."start"
 AND (NEW."start" IS NOT OLD."start" OR NEW."end" IS NOT OLD."end")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
CREATE TRIGGER "guard_invoices_service_order_ins" BEFORE INSERT ON "invoices"
WHEN NEW."service_from" IS NOT NULL AND NEW."service_from" <> '' AND NEW."service_to" IS NOT NULL AND NEW."service_to" <> '' AND NEW."service_to" < NEW."service_from"
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "service_from" IS NEW."service_from" AND "service_to" IS NEW."service_to")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
CREATE TRIGGER "guard_invoices_service_order_upd" BEFORE UPDATE OF "service_from", "service_to" ON "invoices"
WHEN NEW."service_from" IS NOT NULL AND NEW."service_from" <> '' AND NEW."service_to" IS NOT NULL AND NEW."service_to" <> '' AND NEW."service_to" < NEW."service_from"
 AND (NEW."service_from" IS NOT OLD."service_from" OR NEW."service_to" IS NOT OLD."service_to")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
CREATE TRIGGER "guard_offers_service_order_ins" BEFORE INSERT ON "offers"
WHEN NEW."service_from" IS NOT NULL AND NEW."service_from" <> '' AND NEW."service_to" IS NOT NULL AND NEW."service_to" <> '' AND NEW."service_to" < NEW."service_from"
 AND NOT EXISTS (SELECT 1 FROM "offers" WHERE "id" = NEW."id" AND "service_from" IS NEW."service_from" AND "service_to" IS NEW."service_to")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
CREATE TRIGGER "guard_offers_service_order_upd" BEFORE UPDATE OF "service_from", "service_to" ON "offers"
WHEN NEW."service_from" IS NOT NULL AND NEW."service_from" <> '' AND NEW."service_to" IS NOT NULL AND NEW."service_to" <> '' AND NEW."service_to" < NEW."service_from"
 AND (NEW."service_from" IS NOT OLD."service_from" OR NEW."service_to" IS NOT OLD."service_to")
BEGIN SELECT RAISE(ABORT, 'permita38:state.period_reversed'); END;
