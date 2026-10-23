-- permitA38 migration 0006: legal-retention backups (mariadb)
-- issued_documents: immutable copies (PDF files under <data>/issued/) of finalized offers/invoices.
-- backup_runs: one row per backup archive written to a target (status, hashes, retention).

CREATE TABLE `issued_documents` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `kind` VARCHAR(255),
    `document_id` VARCHAR(32),
    `number` VARCHAR(255),
    `sha256` VARCHAR(255),
    `size` BIGINT,
    `path` VARCHAR(255),
    `issued_on` VARCHAR(255),
    `retention_class` VARCHAR(255),
    `retention_until` VARCHAR(255),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE UNIQUE INDEX `idx_issued_document` ON `issued_documents` (`kind`, `document_id`);
CREATE INDEX `idx_issued_number` ON `issued_documents` (`number`);

CREATE TABLE `backup_runs` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `started` BIGINT,
    `finished` BIGINT,
    `kind` VARCHAR(255),
    `status` VARCHAR(255),
    `target` VARCHAR(255),
    `path` VARCHAR(255),
    `filename` VARCHAR(255),
    `size` BIGINT,
    `sha256` VARCHAR(255),
    `manifest_sha256` VARCHAR(255),
    `prev_manifest_sha256` VARCHAR(255),
    `verified` TINYINT(1),
    `encrypted` TINYINT(1),
    `protected` TINYINT(1),
    `retention_until` VARCHAR(255),
    `error` LONGTEXT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX `idx_backup_runs_started` ON `backup_runs` (`started`);
CREATE INDEX `idx_backup_runs_kind` ON `backup_runs` (`kind`, `status`);
