-- permitA38 migration 0005: VAT return (Umsatzsteuer-Voranmeldung) (mariadb)
-- vat_returns: frozen snapshots of prepared / submitted VAT returns (data = the complete computed return).
-- invoices.vat_treatment / offers.vat_treatment: how a document is reported (reverse charge, EU service, export ...).
-- expenses.vat_treatment / supplier_country: how a bill is reported (reverse charge, EU acquisition, import ...).

CREATE TABLE `vat_returns` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `period` VARCHAR(255),
    `year` BIGINT,
    `status` VARCHAR(255),
    `submitted_on` VARCHAR(255),
    `reference` VARCHAR(255),
    `note` LONGTEXT,
    `total` DOUBLE,
    `data` LONGTEXT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_vat_returns_period` ON `vat_returns` (`period`);
CREATE INDEX `idx_vat_returns_year` ON `vat_returns` (`year`);

ALTER TABLE `invoices` ADD COLUMN `vat_treatment` VARCHAR(255);
ALTER TABLE `offers` ADD COLUMN `vat_treatment` VARCHAR(255);
ALTER TABLE `expenses` ADD COLUMN `vat_treatment` VARCHAR(255);
ALTER TABLE `expenses` ADD COLUMN `supplier_country` VARCHAR(255);
