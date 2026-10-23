-- migration: 00000001
-- requires: none
-- permitA38 migration 0001: initial schema (sqlite)
-- generated from core/repository/schema.py

CREATE TABLE "customers" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "name" TEXT,
    "customer_no" TEXT,
    "street" TEXT,
    "zip" TEXT,
    "city" TEXT,
    "country" TEXT,
    "tax_number" TEXT,
    "vat_id" TEXT,
    "contact" TEXT,
    "email" TEXT,
    "phone" TEXT,
    "notes" TEXT,
    "rate" REAL,
    "extra" TEXT
);

CREATE TABLE "projects" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "name" TEXT,
    "customer_id" TEXT,
    "personal" INTEGER,
    "rate" REAL,
    "budget_hours" REAL,
    "status" TEXT,
    "color" TEXT,
    "notes" TEXT,
    "member_ids" TEXT,
    "offer_id" TEXT,
    "extra" TEXT
);
CREATE INDEX "idx_projects_customer" ON "projects" ("customer_id");

CREATE TABLE "tasks" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "project_id" TEXT,
    "name" TEXT,
    "rate" REAL,
    "extra" TEXT
);
CREATE INDEX "idx_tasks_project" ON "tasks" ("project_id");

CREATE TABLE "entries" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "user_id" TEXT,
    "date" TEXT,
    "minutes" INTEGER,
    "project_id" TEXT,
    "task_id" TEXT,
    "note" TEXT,
    "start" TEXT,
    "end" TEXT,
    "billable" INTEGER,
    "invoice_id" TEXT,
    "rate" REAL,
    "extra" TEXT
);
CREATE INDEX "idx_entries_user_date" ON "entries" ("user_id", "date");
CREATE INDEX "idx_entries_project" ON "entries" ("project_id");
CREATE INDEX "idx_entries_invoice" ON "entries" ("invoice_id");

CREATE TABLE "contracts" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "title" TEXT,
    "customer_id" TEXT,
    "project_id" TEXT,
    "start" TEXT,
    "end" TEXT,
    "hours_per_week" REAL,
    "total_hours" REAL,
    "rate" REAL,
    "status" TEXT,
    "user_ids" TEXT,
    "offer_id" TEXT,
    "extra" TEXT
);

CREATE TABLE "absences" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "user_id" TEXT,
    "kind" TEXT,
    "start" TEXT,
    "end" TEXT,
    "note" TEXT,
    "extra" TEXT
);
CREATE INDEX "idx_absences_user" ON "absences" ("user_id");

CREATE TABLE "offers" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "number" TEXT,
    "customer_id" TEXT,
    "project_id" TEXT,
    "status" TEXT,
    "date" TEXT,
    "due_date" TEXT,
    "valid_until" TEXT,
    "paid_date" TEXT,
    "service_from" TEXT,
    "service_to" TEXT,
    "title" TEXT,
    "reference" TEXT,
    "template_id" TEXT,
    "language" TEXT,
    "intro" TEXT,
    "outro" TEXT,
    "notes" TEXT,
    "vat_rate" REAL,
    "lines" TEXT,
    "plan" TEXT,
    "offer_id" TEXT,
    "extra" TEXT
);
CREATE INDEX "idx_offers_status" ON "offers" ("status");
CREATE INDEX "idx_offers_customer" ON "offers" ("customer_id");

CREATE TABLE "invoices" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "number" TEXT,
    "customer_id" TEXT,
    "project_id" TEXT,
    "status" TEXT,
    "date" TEXT,
    "due_date" TEXT,
    "valid_until" TEXT,
    "paid_date" TEXT,
    "service_from" TEXT,
    "service_to" TEXT,
    "title" TEXT,
    "reference" TEXT,
    "template_id" TEXT,
    "language" TEXT,
    "intro" TEXT,
    "outro" TEXT,
    "notes" TEXT,
    "vat_rate" REAL,
    "lines" TEXT,
    "plan" TEXT,
    "offer_id" TEXT,
    "extra" TEXT
);
CREATE INDEX "idx_invoices_status" ON "invoices" ("status");
CREATE INDEX "idx_invoices_customer" ON "invoices" ("customer_id");

CREATE TABLE "expenses" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "date" TEXT,
    "net" REAL,
    "vat" REAL,
    "vat_rate" REAL,
    "vendor" TEXT,
    "description" TEXT,
    "category" TEXT,
    "project_id" TEXT,
    "deductible" REAL,
    "attachment" TEXT,
    "extra" TEXT
);
CREATE INDEX "idx_expenses_date" ON "expenses" ("date");
CREATE INDEX "idx_expenses_project" ON "expenses" ("project_id");

CREATE TABLE "users" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "username" TEXT,
    "name" TEXT,
    "role" TEXT,
    "weekly_hours" REAL,
    "workdays" TEXT,
    "default_rate" REAL,
    "cost_rate" REAL,
    "color" TEXT,
    "tracking_start" TEXT,
    "active" INTEGER,
    "password_hash" TEXT,
    "pw_changed" INTEGER,
    "extra" TEXT
);
CREATE UNIQUE INDEX "idx_users_username" ON "users" ("username");

CREATE TABLE "templates" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "name" TEXT,
    "layout" TEXT,
    "accent" TEXT,
    "language" TEXT,
    "show_logo" INTEGER,
    "intro_offer" TEXT,
    "outro_offer" TEXT,
    "intro_invoice" TEXT,
    "outro_invoice" TEXT,
    "closing" TEXT,
    "extra" TEXT
);

CREATE TABLE "timers" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "running" INTEGER,
    "started" INTEGER,
    "project_id" TEXT,
    "task_id" TEXT,
    "note" TEXT,
    "extra" TEXT
);

CREATE TABLE "settings" (
    "key" TEXT PRIMARY KEY,
    "value" TEXT
);
