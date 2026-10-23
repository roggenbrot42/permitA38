-- permitA38 migration 0004: double-entry accounting (mariadb)
-- accounts (chart of accounts), journal_entries + journal_lines (immutable once posted, integer cents; the tax_*
-- columns carry the VAT base/amount of a line for the VAT return), period_locks (closed months/years).
-- Generated from permita38/core/repository/schema.py.

CREATE TABLE `accounts` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `number` VARCHAR(255),
    `name` VARCHAR(255),
    `type` VARCHAR(255),
    `side` VARCHAR(255),
    `chart` VARCHAR(255),
    `role` VARCHAR(255),
    `tax_code` VARCHAR(255),
    `active` TINYINT(1),
    `system` TINYINT(1),
    `description` LONGTEXT,
    `valid_for_year` BIGINT,
    `reviewed_on` VARCHAR(255),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE UNIQUE INDEX `idx_accounts_number` ON `accounts` (`number`);

CREATE INDEX `idx_accounts_role` ON `accounts` (`role`);

CREATE TABLE `journal_entries` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `fiscal_year` BIGINT,
    `booking_no` BIGINT,
    `date` VARCHAR(255),
    `posting_date` VARCHAR(255),
    `description` LONGTEXT,
    `source_type` VARCHAR(255),
    `source_id` VARCHAR(32),
    `source_part` VARCHAR(255),
    `reversal_of` VARCHAR(32),
    `created_by` VARCHAR(32),
    `prev_hash` VARCHAR(255),
    `hash` VARCHAR(255),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE UNIQUE INDEX `idx_journal_entries_booking` ON `journal_entries` (`fiscal_year`, `booking_no`);

CREATE INDEX `idx_journal_entries_source` ON `journal_entries` (`source_type`, `source_id`);

CREATE INDEX `idx_journal_entries_date` ON `journal_entries` (`date`);

CREATE INDEX `idx_journal_entries_reversal` ON `journal_entries` (`reversal_of`);

CREATE TABLE `journal_lines` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `entry_id` VARCHAR(32),
    `line_no` BIGINT,
    `account` VARCHAR(255),
    `debit_cents` BIGINT,
    `credit_cents` BIGINT,
    `tax_code` VARCHAR(255),
    `tax_base_cents` BIGINT,
    `tax_amount_cents` BIGINT,
    `memo` LONGTEXT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX `idx_journal_lines_entry` ON `journal_lines` (`entry_id`);

CREATE INDEX `idx_journal_lines_account` ON `journal_lines` (`account`);

CREATE TABLE `period_locks` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `year` BIGINT,
    `month` BIGINT,
    `locked_by` VARCHAR(32),
    `note` LONGTEXT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE UNIQUE INDEX `idx_period_locks_period` ON `period_locks` (`year`, `month`);
