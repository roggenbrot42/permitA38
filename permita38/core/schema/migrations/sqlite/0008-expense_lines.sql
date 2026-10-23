-- migration: d8a69cde
-- requires: c41e7d90
-- permitA38 migration: expense-lines (sqlite)

ALTER TABLE "expenses" ADD COLUMN "lines" TEXT;
