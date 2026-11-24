-- migration: 0aacb7a9
-- requires: 7ca84bdc
-- permitA38 migration: accounting-journal (mariadb)

-- accounting journal (docs/specs/accounting.md sections 7 and 8.1): internal tables, append-only journal

CREATE TABLE `fiscal_years` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `year` BIGINT,
    `chart` VARCHAR(255),
    `income_basis` VARCHAR(255),
    `vat_basis` VARCHAR(255),
    `vat_frequency` VARCHAR(255),
    `dauerfrist` TINYINT(1),
    `small_business` TINYINT(1),
    `vat_regular_from` VARCHAR(255),
    `rules_version` VARCHAR(255),
    `opened_at` BIGINT,
    `opened_by` VARCHAR(32),
    `closed_at` BIGINT,
    `closed_by` VARCHAR(32),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX `idx_fiscal_years_year` ON `fiscal_years` (`year`);

CREATE TABLE `journal_entries` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `fiscal_year` BIGINT,
    `entry_no` BIGINT,
    `document_date` VARCHAR(255),
    `booking_date` VARCHAR(255),
    `event` VARCHAR(255),
    `event_key` VARCHAR(255),
    `source_coll` VARCHAR(255),
    `source_id` VARCHAR(32),
    `source_number` VARCHAR(255),
    `reverses_id` VARCHAR(32),
    `counterparty_id` VARCHAR(32),
    `cp_vat_id` VARCHAR(255),
    `cp_country` VARCHAR(255),
    `text` LONGTEXT,
    `origin` VARCHAR(255),
    `created_at` BIGINT,
    `created_by` VARCHAR(32),
    `prev_hash` VARCHAR(255),
    `hash` VARCHAR(255),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX `idx_journal_entries_event_key` ON `journal_entries` (`event_key`);
CREATE UNIQUE INDEX `idx_journal_entries_year_no` ON `journal_entries` (`fiscal_year`, `entry_no`);
CREATE INDEX `idx_journal_entries_source` ON `journal_entries` (`source_id`);
CREATE INDEX `idx_journal_entries_booking` ON `journal_entries` (`booking_date`);

CREATE TABLE `journal_lines` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `entry_id` VARCHAR(32),
    `line_no` BIGINT,
    `role` VARCHAR(255),
    `account` VARCHAR(255),
    `amount_cents` BIGINT,
    `tax_key` VARCHAR(255),
    `tax_kind` VARCHAR(255),
    `tax_rate` VARCHAR(255),
    `tax_base_cents` BIGINT,
    `vat_date` VARCHAR(255),
    `income_date` VARCHAR(255),
    `open_item_id` VARCHAR(32),
    `treatment` VARCHAR(255),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX `idx_journal_lines_entry_line` ON `journal_lines` (`entry_id`, `line_no`);
CREATE INDEX `idx_journal_lines_vat` ON `journal_lines` (`vat_date`, `tax_key`);
CREATE INDEX `idx_journal_lines_income` ON `journal_lines` (`income_date`);
CREATE INDEX `idx_journal_lines_open_item` ON `journal_lines` (`open_item_id`);

CREATE TABLE `payments` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `document_id` VARCHAR(32),
    `date` VARCHAR(255),
    `amount_cents` BIGINT,
    `method` VARCHAR(255),
    `kind` VARCHAR(255),
    `note` LONGTEXT,
    `reverses_id` VARCHAR(32),
    `created_at` BIGINT,
    `created_by` VARCHAR(32),
    `extra` LONGTEXT,
    CONSTRAINT `payments_amount_nonzero` CHECK (`amount_cents` <> 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_payments_document` ON `payments` (`document_id`);

CREATE TABLE `period_locks` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `year` BIGINT,
    `month` BIGINT,
    `locked_at` BIGINT,
    `locked_by` VARCHAR(32),
    `reason` VARCHAR(255),
    `extra` LONGTEXT,
    CONSTRAINT `period_locks_reason_vocab` CHECK (BINARY `reason` IN ('manual', 'year_close') OR BINARY `reason` LIKE 'vat_return:%')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX `idx_period_locks_year_month` ON `period_locks` (`year`, `month`);

-- guards: generated from core/repository/schema.py GUARDS by Dialect.guard_sql (a test keeps them equal)
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_year_required` CHECK (NOT COALESCE((NOT (`year` IS NOT NULL AND `year` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_chart_required` CHECK (NOT COALESCE((NOT (`chart` IS NOT NULL AND `chart` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_income_basis_required` CHECK (NOT COALESCE((NOT (`income_basis` IS NOT NULL AND `income_basis` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_vat_basis_required` CHECK (NOT COALESCE((NOT (`vat_basis` IS NOT NULL AND `vat_basis` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_vat_frequency_required` CHECK (NOT COALESCE((NOT (`vat_frequency` IS NOT NULL AND `vat_frequency` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_dauerfrist_required` CHECK (NOT COALESCE((NOT (`dauerfrist` IS NOT NULL AND `dauerfrist` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_small_business_required` CHECK (NOT COALESCE((NOT (`small_business` IS NOT NULL AND `small_business` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_rules_version_required` CHECK (NOT COALESCE((NOT (`rules_version` IS NOT NULL AND `rules_version` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_opened_at_required` CHECK (NOT COALESCE((NOT (`opened_at` IS NOT NULL AND `opened_at` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_opened_by_required` CHECK (NOT COALESCE((NOT (`opened_by` IS NOT NULL AND `opened_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_vat_regular_from_date` CHECK (`vat_regular_from` IS NULL OR `vat_regular_from` = '' OR `vat_regular_from` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_chart_one_of` CHECK (NOT COALESCE(((`chart` IS NOT NULL AND `chart` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `chart` NOT IN ('skr03', 'skr04')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_income_basis_one_of` CHECK (NOT COALESCE(((`income_basis` IS NOT NULL AND `income_basis` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `income_basis` NOT IN ('eur', 'accrual')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_vat_basis_one_of` CHECK (NOT COALESCE(((`vat_basis` IS NOT NULL AND `vat_basis` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `vat_basis` NOT IN ('soll', 'ist')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_vat_frequency_one_of` CHECK (NOT COALESCE(((`vat_frequency` IS NOT NULL AND `vat_frequency` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `vat_frequency` NOT IN ('month', 'quarter', 'none')), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_closed_by_implies` CHECK (NOT COALESCE((COALESCE((`closed_at` IS NOT NULL AND `closed_at` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0) AND NOT COALESCE((`closed_by` IS NOT NULL AND `closed_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_closed_at_implies` CHECK (NOT COALESCE((COALESCE((`closed_by` IS NOT NULL AND `closed_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0) AND NOT COALESCE((`closed_at` IS NOT NULL AND `closed_at` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_year_range` CHECK (NOT COALESCE((`year` IS NOT NULL AND (`year` < 2000 OR `year` > 2100)), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_fiscal_year_required` CHECK (NOT COALESCE((NOT (`fiscal_year` IS NOT NULL AND `fiscal_year` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_entry_no_required` CHECK (NOT COALESCE((NOT (`entry_no` IS NOT NULL AND `entry_no` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_document_date_required` CHECK (NOT COALESCE((NOT (`document_date` IS NOT NULL AND `document_date` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_booking_date_required` CHECK (NOT COALESCE((NOT (`booking_date` IS NOT NULL AND `booking_date` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_event_required` CHECK (NOT COALESCE((NOT (`event` IS NOT NULL AND `event` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_event_key_required` CHECK (NOT COALESCE((NOT (`event_key` IS NOT NULL AND `event_key` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_text_required` CHECK (NOT COALESCE((NOT (`text` IS NOT NULL AND `text` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_origin_required` CHECK (NOT COALESCE((NOT (`origin` IS NOT NULL AND `origin` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_created_at_required` CHECK (NOT COALESCE((NOT (`created_at` IS NOT NULL AND `created_at` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_created_by_required` CHECK (NOT COALESCE((NOT (`created_by` IS NOT NULL AND `created_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_prev_hash_required` CHECK (NOT COALESCE((NOT (`prev_hash` IS NOT NULL AND `prev_hash` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_hash_required` CHECK (NOT COALESCE((NOT (`hash` IS NOT NULL AND `hash` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_document_date_date` CHECK (`document_date` IS NULL OR `document_date` = '' OR `document_date` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_booking_date_date` CHECK (`booking_date` IS NULL OR `booking_date` = '' OR `booking_date` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_origin_one_of` CHECK (NOT COALESCE(((`origin` IS NOT NULL AND `origin` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `origin` NOT IN ('auto', 'manual', 'opening')), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_fiscal_year_range` CHECK (NOT COALESCE((`fiscal_year` IS NOT NULL AND (`fiscal_year` < 2000 OR `fiscal_year` > 2100)), 0));
ALTER TABLE `journal_entries` ADD CONSTRAINT `guard_journal_entries_entry_no_range` CHECK (NOT COALESCE((`entry_no` IS NOT NULL AND (`entry_no` < 1)), 0));
CREATE TRIGGER `guard_journal_entries_reverses_id_ref_ins` BEFORE INSERT ON `journal_entries` FOR EACH ROW
BEGIN
  IF (NEW.`reverses_id` IS NOT NULL AND NEW.`reverses_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `journal_entries` WHERE `id` = NEW.`reverses_id`) AND NOT EXISTS (SELECT 1 FROM `journal_entries` WHERE `id` = NEW.`id` AND `reverses_id` <=> NEW.`reverses_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_journal_entries_reverses_id_ref_upd` BEFORE UPDATE ON `journal_entries` FOR EACH ROW
BEGIN
  IF NOT (OLD.`reverses_id` <=> NEW.`reverses_id`) AND (NEW.`reverses_id` IS NOT NULL AND NEW.`reverses_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `journal_entries` WHERE `id` = NEW.`reverses_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_journal_entries_reverses_id_ref_del` BEFORE DELETE ON `journal_entries` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `journal_entries` WHERE `reverses_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_journal_entries_append_only_upd` BEFORE UPDATE ON `journal_entries` FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.append_only';
END;
CREATE TRIGGER `guard_journal_entries_append_only_del` BEFORE DELETE ON `journal_entries` FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.append_only';
END;
ALTER TABLE `journal_lines` ADD CONSTRAINT `guard_journal_lines_entry_id_required` CHECK (NOT COALESCE((NOT (`entry_id` IS NOT NULL AND `entry_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_lines` ADD CONSTRAINT `guard_journal_lines_line_no_required` CHECK (NOT COALESCE((NOT (`line_no` IS NOT NULL AND `line_no` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_lines` ADD CONSTRAINT `guard_journal_lines_account_required` CHECK (NOT COALESCE((NOT (`account` IS NOT NULL AND `account` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_lines` ADD CONSTRAINT `guard_journal_lines_amount_cents_required` CHECK (NOT COALESCE((NOT (`amount_cents` IS NOT NULL AND `amount_cents` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `journal_lines` ADD CONSTRAINT `guard_journal_lines_vat_date_date` CHECK (`vat_date` IS NULL OR `vat_date` = '' OR `vat_date` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE `journal_lines` ADD CONSTRAINT `guard_journal_lines_income_date_date` CHECK (`income_date` IS NULL OR `income_date` = '' OR `income_date` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE `journal_lines` ADD CONSTRAINT `guard_journal_lines_tax_kind_one_of` CHECK (NOT COALESCE(((`tax_kind` IS NOT NULL AND `tax_kind` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `tax_kind` NOT IN ('tax', 'input', 'base_only')), 0));
ALTER TABLE `journal_lines` ADD CONSTRAINT `guard_journal_lines_tax_key_one_of` CHECK (NOT COALESCE(((`tax_key` IS NOT NULL AND `tax_key` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `tax_key` NOT IN ('EU_SVC', 'KU', 'NONE', 'RC_3C_SVC', 'RC_EU_SVC', 'S19', 'S7', 'SX', 'VST')), 0));
ALTER TABLE `journal_lines` ADD CONSTRAINT `guard_journal_lines_line_no_range` CHECK (NOT COALESCE((`line_no` IS NOT NULL AND (`line_no` < 1)), 0));
CREATE TRIGGER `guard_journal_lines_entry_id_ref_ins` BEFORE INSERT ON `journal_lines` FOR EACH ROW
BEGIN
  IF (NEW.`entry_id` IS NOT NULL AND NEW.`entry_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `journal_entries` WHERE `id` = NEW.`entry_id`) AND NOT EXISTS (SELECT 1 FROM `journal_lines` WHERE `id` = NEW.`id` AND `entry_id` <=> NEW.`entry_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_journal_lines_entry_id_ref_upd` BEFORE UPDATE ON `journal_lines` FOR EACH ROW
BEGIN
  IF NOT (OLD.`entry_id` <=> NEW.`entry_id`) AND (NEW.`entry_id` IS NOT NULL AND NEW.`entry_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `journal_entries` WHERE `id` = NEW.`entry_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_journal_lines_entry_id_ref_del` BEFORE DELETE ON `journal_entries` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `journal_lines` WHERE `entry_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
ALTER TABLE `journal_lines` ADD CONSTRAINT `guard_journal_lines_tax_kind_key_implies` CHECK (NOT COALESCE((COALESCE((`tax_kind` IS NOT NULL AND `tax_kind` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0) AND NOT COALESCE((`tax_key` IS NOT NULL AND `tax_key` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
ALTER TABLE `journal_lines` ADD CONSTRAINT `guard_journal_lines_tax_key_vat_date_implies` CHECK (NOT COALESCE((COALESCE(BINARY `tax_key` IN ('EU_SVC', 'KU', 'RC_3C_SVC', 'RC_EU_SVC', 'S19', 'S7', 'SX', 'VST'), 0) AND NOT COALESCE((`vat_date` IS NOT NULL AND `vat_date` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
CREATE TRIGGER `guard_journal_lines_append_only_upd` BEFORE UPDATE ON `journal_lines` FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.append_only';
END;
CREATE TRIGGER `guard_journal_lines_append_only_del` BEFORE DELETE ON `journal_lines` FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.append_only';
END;
ALTER TABLE `payments` ADD CONSTRAINT `guard_payments_document_id_required` CHECK (NOT COALESCE((NOT (`document_id` IS NOT NULL AND `document_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `payments` ADD CONSTRAINT `guard_payments_date_required` CHECK (NOT COALESCE((NOT (`date` IS NOT NULL AND `date` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `payments` ADD CONSTRAINT `guard_payments_amount_cents_required` CHECK (NOT COALESCE((NOT (`amount_cents` IS NOT NULL AND `amount_cents` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `payments` ADD CONSTRAINT `guard_payments_method_required` CHECK (NOT COALESCE((NOT (`method` IS NOT NULL AND `method` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `payments` ADD CONSTRAINT `guard_payments_kind_required` CHECK (NOT COALESCE((NOT (`kind` IS NOT NULL AND `kind` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `payments` ADD CONSTRAINT `guard_payments_created_at_required` CHECK (NOT COALESCE((NOT (`created_at` IS NOT NULL AND `created_at` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `payments` ADD CONSTRAINT `guard_payments_created_by_required` CHECK (NOT COALESCE((NOT (`created_by` IS NOT NULL AND `created_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `payments` ADD CONSTRAINT `guard_payments_date_date` CHECK (`date` IS NULL OR `date` = '' OR `date` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE `payments` ADD CONSTRAINT `guard_payments_method_one_of` CHECK (NOT COALESCE(((`method` IS NOT NULL AND `method` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `method` NOT IN ('bank', 'cash')), 0));
ALTER TABLE `payments` ADD CONSTRAINT `guard_payments_kind_one_of` CHECK (NOT COALESCE(((`kind` IS NOT NULL AND `kind` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `kind` NOT IN ('payment', 'refund', 'reversal')), 0));
CREATE TRIGGER `guard_payments_document_id_ref_ins` BEFORE INSERT ON `payments` FOR EACH ROW
BEGIN
  IF (NEW.`document_id` IS NOT NULL AND NEW.`document_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `invoices` WHERE `id` = NEW.`document_id`) AND NOT EXISTS (SELECT 1 FROM `payments` WHERE `id` = NEW.`id` AND `document_id` <=> NEW.`document_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_payments_document_id_ref_upd` BEFORE UPDATE ON `payments` FOR EACH ROW
BEGIN
  IF NOT (OLD.`document_id` <=> NEW.`document_id`) AND (NEW.`document_id` IS NOT NULL AND NEW.`document_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `invoices` WHERE `id` = NEW.`document_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_payments_document_id_ref_del` BEFORE DELETE ON `invoices` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `payments` WHERE `document_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_payments_reverses_id_ref_ins` BEFORE INSERT ON `payments` FOR EACH ROW
BEGIN
  IF (NEW.`reverses_id` IS NOT NULL AND NEW.`reverses_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `payments` WHERE `id` = NEW.`reverses_id`) AND NOT EXISTS (SELECT 1 FROM `payments` WHERE `id` = NEW.`id` AND `reverses_id` <=> NEW.`reverses_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_payments_reverses_id_ref_upd` BEFORE UPDATE ON `payments` FOR EACH ROW
BEGIN
  IF NOT (OLD.`reverses_id` <=> NEW.`reverses_id`) AND (NEW.`reverses_id` IS NOT NULL AND NEW.`reverses_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `payments` WHERE `id` = NEW.`reverses_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_payments_reverses_id_ref_del` BEFORE DELETE ON `payments` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `payments` WHERE `reverses_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
ALTER TABLE `payments` ADD CONSTRAINT `guard_payments_reversal_reverses_implies` CHECK (NOT COALESCE((COALESCE(BINARY `kind` = 'reversal', 0) AND NOT COALESCE((`reverses_id` IS NOT NULL AND `reverses_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
ALTER TABLE `payments` ADD CONSTRAINT `guard_payments_reverses_reversal_implies` CHECK (NOT COALESCE((COALESCE((`reverses_id` IS NOT NULL AND `reverses_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0) AND NOT COALESCE(BINARY `kind` = 'reversal', 0)), 0));
CREATE TRIGGER `guard_payments_append_only_upd` BEFORE UPDATE ON `payments` FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.append_only';
END;
CREATE TRIGGER `guard_payments_append_only_del` BEFORE DELETE ON `payments` FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.append_only';
END;
ALTER TABLE `period_locks` ADD CONSTRAINT `guard_period_locks_year_required` CHECK (NOT COALESCE((NOT (`year` IS NOT NULL AND `year` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `period_locks` ADD CONSTRAINT `guard_period_locks_month_required` CHECK (NOT COALESCE((NOT (`month` IS NOT NULL AND `month` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `period_locks` ADD CONSTRAINT `guard_period_locks_locked_at_required` CHECK (NOT COALESCE((NOT (`locked_at` IS NOT NULL AND `locked_at` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `period_locks` ADD CONSTRAINT `guard_period_locks_locked_by_required` CHECK (NOT COALESCE((NOT (`locked_by` IS NOT NULL AND `locked_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `period_locks` ADD CONSTRAINT `guard_period_locks_reason_required` CHECK (NOT COALESCE((NOT (`reason` IS NOT NULL AND `reason` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `period_locks` ADD CONSTRAINT `guard_period_locks_month_range` CHECK (NOT COALESCE((`month` IS NOT NULL AND (`month` < 1 OR `month` > 12)), 0));
CREATE TRIGGER `guard_period_locks_append_only_upd` BEFORE UPDATE ON `period_locks` FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.append_only';
END;
CREATE TRIGGER `guard_period_locks_append_only_del` BEFORE DELETE ON `period_locks` FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.append_only';
END;
