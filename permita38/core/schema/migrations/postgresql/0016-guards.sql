-- migration: e4b70dba
-- requires: 2fe799c1
-- permitA38 migration: guards (postgresql)

-- non-unique lookup indexes for the number guards
CREATE INDEX "idx_invoices_number" ON "invoices" ("number");
CREATE INDEX "idx_offers_number" ON "offers" ("number");

-- guards as native constraints (not wired to a driver yet; they validate existing rows too)
CREATE UNIQUE INDEX "guard_invoices_number_unique" ON "invoices" ("number") WHERE "number" IS NOT NULL AND "number" <> '';
CREATE UNIQUE INDEX "guard_offers_number_unique" ON "offers" ("number") WHERE "number" IS NOT NULL AND "number" <> '';
ALTER TABLE "entries" ADD CONSTRAINT "guard_entries_date_date" CHECK ("date" IS NULL OR "date" = '' OR "date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "invoices" ADD CONSTRAINT "guard_invoices_date_date" CHECK ("date" IS NULL OR "date" = '' OR "date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "invoices" ADD CONSTRAINT "guard_invoices_due_date_date" CHECK ("due_date" IS NULL OR "due_date" = '' OR "due_date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "invoices" ADD CONSTRAINT "guard_invoices_valid_until_date" CHECK ("valid_until" IS NULL OR "valid_until" = '' OR "valid_until" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "invoices" ADD CONSTRAINT "guard_invoices_paid_date_date" CHECK ("paid_date" IS NULL OR "paid_date" = '' OR "paid_date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "invoices" ADD CONSTRAINT "guard_invoices_service_from_date" CHECK ("service_from" IS NULL OR "service_from" = '' OR "service_from" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "invoices" ADD CONSTRAINT "guard_invoices_service_to_date" CHECK ("service_to" IS NULL OR "service_to" = '' OR "service_to" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "offers" ADD CONSTRAINT "guard_offers_date_date" CHECK ("date" IS NULL OR "date" = '' OR "date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "offers" ADD CONSTRAINT "guard_offers_due_date_date" CHECK ("due_date" IS NULL OR "due_date" = '' OR "due_date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "offers" ADD CONSTRAINT "guard_offers_valid_until_date" CHECK ("valid_until" IS NULL OR "valid_until" = '' OR "valid_until" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "offers" ADD CONSTRAINT "guard_offers_paid_date_date" CHECK ("paid_date" IS NULL OR "paid_date" = '' OR "paid_date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "offers" ADD CONSTRAINT "guard_offers_service_from_date" CHECK ("service_from" IS NULL OR "service_from" = '' OR "service_from" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "offers" ADD CONSTRAINT "guard_offers_service_to_date" CHECK ("service_to" IS NULL OR "service_to" = '' OR "service_to" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "expenses" ADD CONSTRAINT "guard_expenses_date_date" CHECK ("date" IS NULL OR "date" = '' OR "date" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "expenses" ADD CONSTRAINT "guard_expenses_paid_on_date" CHECK ("paid_on" IS NULL OR "paid_on" = '' OR "paid_on" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "absences" ADD CONSTRAINT "guard_absences_start_date" CHECK ("start" IS NULL OR "start" = '' OR "start" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "absences" ADD CONSTRAINT "guard_absences_end_date" CHECK ("end" IS NULL OR "end" = '' OR "end" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_start_date" CHECK ("start" IS NULL OR "start" = '' OR "start" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_end_date" CHECK ("end" IS NULL OR "end" = '' OR "end" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "projects" ADD CONSTRAINT "guard_projects_start_date" CHECK ("start" IS NULL OR "start" = '' OR "start" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "projects" ADD CONSTRAINT "guard_projects_end_date" CHECK ("end" IS NULL OR "end" = '' OR "end" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_start_date" CHECK ("start" IS NULL OR "start" = '' OR "start" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_end_date" CHECK ("end" IS NULL OR "end" = '' OR "end" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_cancelled_from_date" CHECK ("cancelled_from" IS NULL OR "cancelled_from" = '' OR "cancelled_from" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "incoming_offers" ADD CONSTRAINT "guard_incoming_offers_received_on_date" CHECK ("received_on" IS NULL OR "received_on" = '' OR "received_on" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "incoming_offers" ADD CONSTRAINT "guard_incoming_offers_valid_until_date" CHECK ("valid_until" IS NULL OR "valid_until" = '' OR "valid_until" ~ '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE "entries" ADD CONSTRAINT "guard_entries_minutes_nonneg" CHECK ("minutes" IS NULL OR "minutes" >= 0);
ALTER TABLE "entries" ADD CONSTRAINT "guard_entries_rate_nonneg" CHECK ("rate" IS NULL OR "rate" >= 0);
ALTER TABLE "customers" ADD CONSTRAINT "guard_customers_rate_nonneg" CHECK ("rate" IS NULL OR "rate" >= 0);
ALTER TABLE "projects" ADD CONSTRAINT "guard_projects_rate_nonneg" CHECK ("rate" IS NULL OR "rate" >= 0);
ALTER TABLE "projects" ADD CONSTRAINT "guard_projects_budget_hours_nonneg" CHECK ("budget_hours" IS NULL OR "budget_hours" >= 0);
ALTER TABLE "tasks" ADD CONSTRAINT "guard_tasks_rate_nonneg" CHECK ("rate" IS NULL OR "rate" >= 0);
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_rate_nonneg" CHECK ("rate" IS NULL OR "rate" >= 0);
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_hours_per_week_nonneg" CHECK ("hours_per_week" IS NULL OR "hours_per_week" >= 0);
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_total_hours_nonneg" CHECK ("total_hours" IS NULL OR "total_hours" >= 0);
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_rate_nonneg" CHECK ("rate" IS NULL OR "rate" >= 0);
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_hours_per_week_nonneg" CHECK ("hours_per_week" IS NULL OR "hours_per_week" >= 0);
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_total_hours_nonneg" CHECK ("total_hours" IS NULL OR "total_hours" >= 0);
ALTER TABLE "suppliers" ADD CONSTRAINT "guard_suppliers_rate_nonneg" CHECK ("rate" IS NULL OR "rate" >= 0);
ALTER TABLE "expenses" ADD CONSTRAINT "guard_expenses_net_nonneg" CHECK ("net" IS NULL OR "net" >= 0);
ALTER TABLE "expenses" ADD CONSTRAINT "guard_expenses_vat_nonneg" CHECK ("vat" IS NULL OR "vat" >= 0);
ALTER TABLE "absences" ADD CONSTRAINT "guard_absences_period_order" CHECK ("start" IS NULL OR "start" = '' OR "end" IS NULL OR "end" = '' OR "end" >= "start");
ALTER TABLE "contracts" ADD CONSTRAINT "guard_contracts_period_order" CHECK ("start" IS NULL OR "start" = '' OR "end" IS NULL OR "end" = '' OR "end" >= "start");
ALTER TABLE "projects" ADD CONSTRAINT "guard_projects_period_order" CHECK ("start" IS NULL OR "start" = '' OR "end" IS NULL OR "end" = '' OR "end" >= "start");
ALTER TABLE "allocations" ADD CONSTRAINT "guard_allocations_period_order" CHECK ("start" IS NULL OR "start" = '' OR "end" IS NULL OR "end" = '' OR "end" >= "start");
ALTER TABLE "trips" ADD CONSTRAINT "guard_trips_period_order" CHECK ("start" IS NULL OR "start" = '' OR "end" IS NULL OR "end" = '' OR "end" >= "start");
ALTER TABLE "invoices" ADD CONSTRAINT "guard_invoices_service_order" CHECK ("service_from" IS NULL OR "service_from" = '' OR "service_to" IS NULL OR "service_to" = '' OR "service_to" >= "service_from");
ALTER TABLE "offers" ADD CONSTRAINT "guard_offers_service_order" CHECK ("service_from" IS NULL OR "service_from" = '' OR "service_to" IS NULL OR "service_to" = '' OR "service_to" >= "service_from");
