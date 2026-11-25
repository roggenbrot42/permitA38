-- migration: 522011c0
-- requires: 10bcb698
-- permitA38 migration: year-closing-hash (mariadb)
-- year closing (docs/specs/accounting.md 7.6, 13): the closed year's last journal hash and entry count, set once.
ALTER TABLE `fiscal_years` ADD COLUMN `closing_hash` VARCHAR(255);
ALTER TABLE `fiscal_years` ADD COLUMN `closing_entry_no` BIGINT;
ALTER TABLE `fiscal_years` ADD CONSTRAINT `guard_fiscal_years_closing_entry_no_range` CHECK (NOT COALESCE((`closing_entry_no` IS NOT NULL AND (`closing_entry_no` < 0 OR `closing_entry_no` > 2000000000)), 0));
CREATE TRIGGER `guard_fiscal_years_closing_hash_once_upd` BEFORE UPDATE ON `fiscal_years` FOR EACH ROW
BEGIN
  IF (OLD.`closing_hash` IS NOT NULL AND OLD.`closing_hash` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT (BINARY NEW.`closing_hash` <=> BINARY OLD.`closing_hash`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.set_once'; END IF;
END;
CREATE TRIGGER `guard_fiscal_years_closing_entry_no_once_upd` BEFORE UPDATE ON `fiscal_years` FOR EACH ROW
BEGIN
  IF (OLD.`closing_entry_no` IS NOT NULL AND OLD.`closing_entry_no` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT (NEW.`closing_entry_no` <=> OLD.`closing_entry_no`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.set_once'; END IF;
END;
