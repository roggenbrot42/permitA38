-- migration: ae32358b
-- requires: 0aacb7a9
-- permitA38 migration: accounting-documents (mariadb)
-- accounting documents (docs/specs/accounting.md 8.1 and 8.2): nullable columns on invoices, expenses and trips
-- (no backfill: NULL reads as the default of the spec); vat_returns and zm_reports (internal collections)

ALTER TABLE `invoices` ADD COLUMN `kind` VARCHAR(255);
ALTER TABLE `invoices` ADD COLUMN `credit_kind` VARCHAR(255);
ALTER TABLE `invoices` ADD COLUMN `corrects_id` VARCHAR(32);
ALTER TABLE `invoices` ADD COLUMN `tax_treatment` VARCHAR(255);
ALTER TABLE `invoices` ADD COLUMN `issued` LONGTEXT;
ALTER TABLE `expenses` ADD COLUMN `tax_treatment` VARCHAR(255);
ALTER TABLE `expenses` ADD COLUMN `service_date` VARCHAR(255);
ALTER TABLE `expenses` ADD COLUMN `bill_rev` BIGINT;
ALTER TABLE `expenses` ADD COLUMN `pay_rev` BIGINT;
ALTER TABLE `trips` ADD COLUMN `post_rev` BIGINT;

CREATE TABLE `vat_returns` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `year` BIGINT,
    `period_kind` VARCHAR(255),
    `period_no` BIGINT,
    `from_date` VARCHAR(255),
    `to_date` VARCHAR(255),
    `due_date` VARCHAR(255),
    `status` VARCHAR(255),
    `basis` VARCHAR(255),
    `frequency` VARCHAR(255),
    `dauerfrist` TINYINT(1),
    `small_business` TINYINT(1),
    `rules_version` VARCHAR(255),
    `boxes` LONGTEXT,
    `source_lines` LONGTEXT,
    `result_cents` BIGINT,
    `special_prepayment_credit_cents` BIGINT,
    `rounding_difference_cents` BIGINT,
    `filed_on` VARCHAR(255),
    `reference` VARCHAR(255),
    `note` LONGTEXT,
    `amends_id` VARCHAR(32),
    `amends_key` VARCHAR(255),
    `replaced_by` VARCHAR(32),
    `external` TINYINT(1),
    `finalised_at` BIGINT,
    `finalised_by` VARCHAR(32),
    `extra` LONGTEXT,
    CONSTRAINT `vat_returns_amends_key` CHECK (`amends_key` IS NOT NULL AND `amends_key` = COALESCE(`amends_id`, '')),
    CONSTRAINT `vat_returns_quarter_no` CHECK (`period_kind` <> 'quarter' OR `period_no` BETWEEN 1 AND 4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX `idx_vat_returns_period` ON `vat_returns` (`year`, `period_kind`, `period_no`, `amends_key`);

CREATE TABLE `zm_reports` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `year` BIGINT,
    `quarter` BIGINT,
    `status` VARCHAR(255),
    `due_date` VARCHAR(255),
    `lines` LONGTEXT,
    `source_lines` LONGTEXT,
    `rules_version` VARCHAR(255),
    `filed_on` VARCHAR(255),
    `reference` VARCHAR(255),
    `note` LONGTEXT,
    `amends_id` VARCHAR(32),
    `amends_key` VARCHAR(255),
    `replaced_by` VARCHAR(32),
    `finalised_at` BIGINT,
    `finalised_by` VARCHAR(32),
    `extra` LONGTEXT,
    CONSTRAINT `zm_reports_amends_key` CHECK (`amends_key` IS NOT NULL AND `amends_key` = COALESCE(`amends_id`, ''))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX `idx_zm_reports_quarter` ON `zm_reports` (`year`, `quarter`, `amends_key`);

-- guards: generated from core/repository/schema.py GUARDS by Dialect.guard_sql (a test keeps them equal)
ALTER TABLE `expenses` ADD CONSTRAINT `guard_expenses_service_date_date` CHECK (`service_date` IS NULL OR `service_date` = '' OR `service_date` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE `invoices` ADD CONSTRAINT `guard_invoices_kind_one_of` CHECK (NOT COALESCE(((`kind` IS NOT NULL AND `kind` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `kind` NOT IN ('invoice', 'credit_note')), 0));
ALTER TABLE `invoices` ADD CONSTRAINT `guard_invoices_credit_kind_one_of` CHECK (NOT COALESCE(((`credit_kind` IS NOT NULL AND `credit_kind` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `credit_kind` NOT IN ('storno', 'partial', 'discount', 'write_off', 'write_off_reversal')), 0));
ALTER TABLE `invoices` ADD CONSTRAINT `guard_invoices_credit_kind_credit_note_implies` CHECK (NOT COALESCE((COALESCE((`credit_kind` IS NOT NULL AND `credit_kind` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0) AND NOT COALESCE(BINARY `kind` = 'credit_note', 0)), 0));
ALTER TABLE `invoices` ADD CONSTRAINT `guard_invoices_tax_treatment_one_of` CHECK (NOT COALESCE(((`tax_treatment` IS NOT NULL AND `tax_treatment` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `tax_treatment` NOT IN ('standard', 'eu_b2b_service', 'small_business')), 0));
CREATE TRIGGER `guard_invoices_corrects_id_ref_ins` BEFORE INSERT ON `invoices` FOR EACH ROW
BEGIN
  IF (NEW.`corrects_id` IS NOT NULL AND NEW.`corrects_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `invoices` WHERE `id` = NEW.`corrects_id`) AND NOT EXISTS (SELECT 1 FROM `invoices` WHERE `id` = NEW.`id` AND `corrects_id` <=> NEW.`corrects_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_invoices_corrects_id_ref_upd` BEFORE UPDATE ON `invoices` FOR EACH ROW
BEGIN
  IF NOT (OLD.`corrects_id` <=> NEW.`corrects_id`) AND (NEW.`corrects_id` IS NOT NULL AND NEW.`corrects_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `invoices` WHERE `id` = NEW.`corrects_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_invoices_corrects_id_ref_del` BEFORE DELETE ON `invoices` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `invoices` WHERE `corrects_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
ALTER TABLE `expenses` ADD CONSTRAINT `guard_expenses_tax_treatment_one_of` CHECK (NOT COALESCE(((`tax_treatment` IS NOT NULL AND `tax_treatment` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `tax_treatment` NOT IN ('standard', 'no_input_vat', 'reverse_charge_eu_service', 'reverse_charge_third_country')), 0));
ALTER TABLE `expenses` ADD CONSTRAINT `guard_expenses_bill_rev_range` CHECK (NOT COALESCE((`bill_rev` IS NOT NULL AND (`bill_rev` < 0)), 0));
ALTER TABLE `expenses` ADD CONSTRAINT `guard_expenses_pay_rev_range` CHECK (NOT COALESCE((`pay_rev` IS NOT NULL AND (`pay_rev` < 0)), 0));
ALTER TABLE `trips` ADD CONSTRAINT `guard_trips_post_rev_range` CHECK (NOT COALESCE((`post_rev` IS NOT NULL AND (`post_rev` < 0)), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_year_required` CHECK (NOT COALESCE((NOT (`year` IS NOT NULL AND `year` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_period_kind_required` CHECK (NOT COALESCE((NOT (`period_kind` IS NOT NULL AND `period_kind` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_period_no_required` CHECK (NOT COALESCE((NOT (`period_no` IS NOT NULL AND `period_no` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_from_date_required` CHECK (NOT COALESCE((NOT (`from_date` IS NOT NULL AND `from_date` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_to_date_required` CHECK (NOT COALESCE((NOT (`to_date` IS NOT NULL AND `to_date` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_due_date_required` CHECK (NOT COALESCE((NOT (`due_date` IS NOT NULL AND `due_date` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_status_required` CHECK (NOT COALESCE((NOT (`status` IS NOT NULL AND `status` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_from_date_date` CHECK (`from_date` IS NULL OR `from_date` = '' OR `from_date` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_to_date_date` CHECK (`to_date` IS NULL OR `to_date` = '' OR `to_date` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_due_date_date` CHECK (`due_date` IS NULL OR `due_date` = '' OR `due_date` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_filed_on_date` CHECK (`filed_on` IS NULL OR `filed_on` = '' OR `filed_on` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_period_kind_one_of` CHECK (NOT COALESCE(((`period_kind` IS NOT NULL AND `period_kind` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `period_kind` NOT IN ('month', 'quarter')), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_status_one_of` CHECK (NOT COALESCE(((`status` IS NOT NULL AND `status` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `status` NOT IN ('draft', 'final')), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_basis_one_of` CHECK (NOT COALESCE(((`basis` IS NOT NULL AND `basis` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `basis` NOT IN ('soll', 'ist')), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_frequency_one_of` CHECK (NOT COALESCE(((`frequency` IS NOT NULL AND `frequency` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `frequency` NOT IN ('month', 'quarter', 'none')), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_year_range` CHECK (NOT COALESCE((`year` IS NOT NULL AND (`year` < 2000 OR `year` > 2100)), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_period_no_range` CHECK (NOT COALESCE((`period_no` IS NOT NULL AND (`period_no` < 1 OR `period_no` > 12)), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_period_order` CHECK (`from_date` IS NULL OR `from_date` = '' OR `to_date` IS NULL OR `to_date` = '' OR `to_date` >= `from_date`);
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_final_finalised_at_implies` CHECK (NOT COALESCE((COALESCE(BINARY `status` = 'final', 0) AND NOT COALESCE((`finalised_at` IS NOT NULL AND `finalised_at` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
ALTER TABLE `vat_returns` ADD CONSTRAINT `guard_vat_returns_final_finalised_by_implies` CHECK (NOT COALESCE((COALESCE(BINARY `status` = 'final', 0) AND NOT COALESCE((`finalised_by` IS NOT NULL AND `finalised_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
CREATE TRIGGER `guard_vat_returns_amends_id_ref_ins` BEFORE INSERT ON `vat_returns` FOR EACH ROW
BEGIN
  IF (NEW.`amends_id` IS NOT NULL AND NEW.`amends_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `vat_returns` WHERE `id` = NEW.`amends_id`) AND NOT EXISTS (SELECT 1 FROM `vat_returns` WHERE `id` = NEW.`id` AND `amends_id` <=> NEW.`amends_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_vat_returns_amends_id_ref_upd` BEFORE UPDATE ON `vat_returns` FOR EACH ROW
BEGIN
  IF NOT (OLD.`amends_id` <=> NEW.`amends_id`) AND (NEW.`amends_id` IS NOT NULL AND NEW.`amends_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `vat_returns` WHERE `id` = NEW.`amends_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_vat_returns_amends_id_ref_del` BEFORE DELETE ON `vat_returns` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `vat_returns` WHERE `amends_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_vat_returns_replaced_by_ref_ins` BEFORE INSERT ON `vat_returns` FOR EACH ROW
BEGIN
  IF (NEW.`replaced_by` IS NOT NULL AND NEW.`replaced_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `vat_returns` WHERE `id` = NEW.`replaced_by`) AND NOT EXISTS (SELECT 1 FROM `vat_returns` WHERE `id` = NEW.`id` AND `replaced_by` <=> NEW.`replaced_by`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_vat_returns_replaced_by_ref_upd` BEFORE UPDATE ON `vat_returns` FOR EACH ROW
BEGIN
  IF NOT (OLD.`replaced_by` <=> NEW.`replaced_by`) AND (NEW.`replaced_by` IS NOT NULL AND NEW.`replaced_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `vat_returns` WHERE `id` = NEW.`replaced_by`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_vat_returns_replaced_by_ref_del` BEFORE DELETE ON `vat_returns` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `vat_returns` WHERE `replaced_by` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
ALTER TABLE `zm_reports` ADD CONSTRAINT `guard_zm_reports_year_required` CHECK (NOT COALESCE((NOT (`year` IS NOT NULL AND `year` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `zm_reports` ADD CONSTRAINT `guard_zm_reports_quarter_required` CHECK (NOT COALESCE((NOT (`quarter` IS NOT NULL AND `quarter` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `zm_reports` ADD CONSTRAINT `guard_zm_reports_status_required` CHECK (NOT COALESCE((NOT (`status` IS NOT NULL AND `status` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `zm_reports` ADD CONSTRAINT `guard_zm_reports_due_date_required` CHECK (NOT COALESCE((NOT (`due_date` IS NOT NULL AND `due_date` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `zm_reports` ADD CONSTRAINT `guard_zm_reports_due_date_date` CHECK (`due_date` IS NULL OR `due_date` = '' OR `due_date` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE `zm_reports` ADD CONSTRAINT `guard_zm_reports_filed_on_date` CHECK (`filed_on` IS NULL OR `filed_on` = '' OR `filed_on` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$');
ALTER TABLE `zm_reports` ADD CONSTRAINT `guard_zm_reports_status_one_of` CHECK (NOT COALESCE(((`status` IS NOT NULL AND `status` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `status` NOT IN ('draft', 'final')), 0));
ALTER TABLE `zm_reports` ADD CONSTRAINT `guard_zm_reports_year_range` CHECK (NOT COALESCE((`year` IS NOT NULL AND (`year` < 2000 OR `year` > 2100)), 0));
ALTER TABLE `zm_reports` ADD CONSTRAINT `guard_zm_reports_quarter_range` CHECK (NOT COALESCE((`quarter` IS NOT NULL AND (`quarter` < 1 OR `quarter` > 4)), 0));
ALTER TABLE `zm_reports` ADD CONSTRAINT `guard_zm_reports_final_finalised_at_implies` CHECK (NOT COALESCE((COALESCE(BINARY `status` = 'final', 0) AND NOT COALESCE((`finalised_at` IS NOT NULL AND `finalised_at` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
ALTER TABLE `zm_reports` ADD CONSTRAINT `guard_zm_reports_final_finalised_by_implies` CHECK (NOT COALESCE((COALESCE(BINARY `status` = 'final', 0) AND NOT COALESCE((`finalised_by` IS NOT NULL AND `finalised_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
CREATE TRIGGER `guard_zm_reports_amends_id_ref_ins` BEFORE INSERT ON `zm_reports` FOR EACH ROW
BEGIN
  IF (NEW.`amends_id` IS NOT NULL AND NEW.`amends_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `zm_reports` WHERE `id` = NEW.`amends_id`) AND NOT EXISTS (SELECT 1 FROM `zm_reports` WHERE `id` = NEW.`id` AND `amends_id` <=> NEW.`amends_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_zm_reports_amends_id_ref_upd` BEFORE UPDATE ON `zm_reports` FOR EACH ROW
BEGIN
  IF NOT (OLD.`amends_id` <=> NEW.`amends_id`) AND (NEW.`amends_id` IS NOT NULL AND NEW.`amends_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `zm_reports` WHERE `id` = NEW.`amends_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_zm_reports_amends_id_ref_del` BEFORE DELETE ON `zm_reports` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `zm_reports` WHERE `amends_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_zm_reports_replaced_by_ref_ins` BEFORE INSERT ON `zm_reports` FOR EACH ROW
BEGIN
  IF (NEW.`replaced_by` IS NOT NULL AND NEW.`replaced_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `zm_reports` WHERE `id` = NEW.`replaced_by`) AND NOT EXISTS (SELECT 1 FROM `zm_reports` WHERE `id` = NEW.`id` AND `replaced_by` <=> NEW.`replaced_by`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_zm_reports_replaced_by_ref_upd` BEFORE UPDATE ON `zm_reports` FOR EACH ROW
BEGIN
  IF NOT (OLD.`replaced_by` <=> NEW.`replaced_by`) AND (NEW.`replaced_by` IS NOT NULL AND NEW.`replaced_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `zm_reports` WHERE `id` = NEW.`replaced_by`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_zm_reports_replaced_by_ref_del` BEFORE DELETE ON `zm_reports` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `zm_reports` WHERE `replaced_by` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
