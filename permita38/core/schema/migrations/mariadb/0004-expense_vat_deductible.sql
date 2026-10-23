-- migration: ab46b196
-- requires: 00000003
-- permitA38 migration: expense-vat-deductible (mariadb)

ALTER TABLE `expenses` ADD COLUMN `vat_deductible` DOUBLE;
