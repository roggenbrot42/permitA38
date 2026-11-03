-- migration: f3fcc516
-- requires: f12b52ff
-- permitA38 migration: receipt_scans (mariadb)

-- expenses: bill date vs paid date and the printed invoice number (docs/specs/receipt-scan.md)
ALTER TABLE `expenses` ADD COLUMN `paid_on` VARCHAR(255);
ALTER TABLE `expenses` ADD COLUMN `invoice_no` VARCHAR(255);

-- scanned receipts: flagged bills (not yet expenses), booked ones and hash-only rows
CREATE TABLE `receipt_scans` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `status` VARCHAR(255),
    `sha256` VARCHAR(255),
    `invoice_key` VARCHAR(255),
    `attachment` VARCHAR(255),
    `filename` VARCHAR(255),
    `engine` VARCHAR(255),
    `draft` LONGTEXT,
    `doc` LONGTEXT,
    `flags` LONGTEXT,
    `expense_id` VARCHAR(32),
    `created_by` VARCHAR(32),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX `idx_receipt_scans_sha256` ON `receipt_scans` (`sha256`);
CREATE INDEX `idx_receipt_scans_status` ON `receipt_scans` (`status`);
CREATE INDEX `idx_receipt_scans_invoice_key` ON `receipt_scans` (`invoice_key`);
CREATE INDEX `idx_receipt_scans_expense_id` ON `receipt_scans` (`expense_id`);
