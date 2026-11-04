-- migration: 2fe799c1
-- requires: f3fcc516
-- permitA38 migration: document_scans (mariadb)

-- scanned old offers and invoices (docs/specs/document-scan.md)
CREATE TABLE `document_scans` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `status` VARCHAR(255),
    `sha256` VARCHAR(255),
    `doc_key` VARCHAR(255),
    `attachment` VARCHAR(255),
    `filename` VARCHAR(255),
    `engine` VARCHAR(255),
    `kind` VARCHAR(255),
    `draft` LONGTEXT,
    `doc` LONGTEXT,
    `flags` LONGTEXT,
    `record_coll` VARCHAR(255),
    `record_id` VARCHAR(255),
    `batch_id` VARCHAR(255),
    `created_by` VARCHAR(32),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX `idx_document_scans_sha256` ON `document_scans` (`sha256`);
CREATE INDEX `idx_document_scans_status` ON `document_scans` (`status`);
CREATE INDEX `idx_document_scans_doc_key` ON `document_scans` (`doc_key`);
CREATE INDEX `idx_document_scans_record_id` ON `document_scans` (`record_id`);
