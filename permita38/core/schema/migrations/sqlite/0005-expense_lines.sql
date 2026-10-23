-- migration: d8a69cde
-- requires: ab46b196
-- permitA38 migration: expense-lines (sqlite)

ALTER TABLE "expenses" ADD COLUMN "lines" TEXT;
