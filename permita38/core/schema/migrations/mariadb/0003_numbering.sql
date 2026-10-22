-- permitA38 migration 0003 (renumber to 0010 when merging the branches, see the fork notes)
-- permitA38 migration: document numbering sequences and document import (mariadb)
-- number_sequences: atomic counters per document type and period; numbering_log: audit trail of number changes;
-- import_batches: one row per import run;
-- import columns on offers/invoices/customers/expenses (external_id for idempotent re-import, import_batch for undo).

ALTER TABLE `offers` ADD COLUMN `imported` TINYINT(1);
ALTER TABLE `offers` ADD COLUMN `original_number` VARCHAR(255);
ALTER TABLE `offers` ADD COLUMN `external_id` VARCHAR(255);
ALTER TABLE `offers` ADD COLUMN `import_batch` VARCHAR(255);
ALTER TABLE `offers` ADD COLUMN `totals_unverified` TINYINT(1);
ALTER TABLE `offers` ADD COLUMN `original_pdf` VARCHAR(255);
ALTER TABLE `invoices` ADD COLUMN `imported` TINYINT(1);
ALTER TABLE `invoices` ADD COLUMN `original_number` VARCHAR(255);
ALTER TABLE `invoices` ADD COLUMN `external_id` VARCHAR(255);
ALTER TABLE `invoices` ADD COLUMN `import_batch` VARCHAR(255);
ALTER TABLE `invoices` ADD COLUMN `totals_unverified` TINYINT(1);
ALTER TABLE `invoices` ADD COLUMN `original_pdf` VARCHAR(255);
ALTER TABLE `customers` ADD COLUMN `external_id` VARCHAR(255);
ALTER TABLE `customers` ADD COLUMN `import_batch` VARCHAR(255);
ALTER TABLE `expenses` ADD COLUMN `external_id` VARCHAR(255);
ALTER TABLE `expenses` ADD COLUMN `import_batch` VARCHAR(255);

CREATE TABLE `number_sequences` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `doc_type` VARCHAR(255),
    `period` VARCHAR(255),
    `last_value` BIGINT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX `idx_number_sequences_key` ON `number_sequences` (`doc_type`, `period`);

CREATE TABLE `import_batches` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `kind` VARCHAR(255),
    `filename` VARCHAR(255),
    `status` VARCHAR(255),
    `counts` LONGTEXT,
    `mapping` LONGTEXT,
    `finished` BIGINT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `numbering_log` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `doc_type` VARCHAR(255),
    `action` VARCHAR(255),
    `before` LONGTEXT,
    `after` LONGTEXT,
    `warnings` LONGTEXT,
    `reason` LONGTEXT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
