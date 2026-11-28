-- migration: 10bcb698
-- requires: ae32358b
-- permitA38 migration: accounting-row-state (mariadb)
-- row-state guards (docs/specs/accounting.md 8.1, docs/specs/db-constraints.md): a final vat_returns / zm_reports row
-- only takes filed_on, reference, note and replaced_by (reopen to draft only while filed_on is unset; external returns
-- are exempt until replaced_by is set); filed_on and fiscal_years.closed_at / closed_by are set once. No new columns.

CREATE TRIGGER `guard_vat_returns_frozen_upd` BEFORE UPDATE ON `vat_returns` FOR EACH ROW
BEGIN
  IF BINARY OLD.`status` = 'final' AND NOT COALESCE((OLD.`external` = 1 AND NOT (OLD.`replaced_by` IS NOT NULL AND OLD.`replaced_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0) AND (NOT (BINARY NEW.`id` <=> BINARY OLD.`id`) OR NOT (NEW.`created` <=> OLD.`created`) OR NOT (NEW.`year` <=> OLD.`year`) OR NOT (BINARY NEW.`period_kind` <=> BINARY OLD.`period_kind`) OR NOT (NEW.`period_no` <=> OLD.`period_no`) OR NOT (BINARY NEW.`from_date` <=> BINARY OLD.`from_date`) OR NOT (BINARY NEW.`to_date` <=> BINARY OLD.`to_date`) OR NOT (BINARY NEW.`due_date` <=> BINARY OLD.`due_date`) OR NOT (BINARY NEW.`basis` <=> BINARY OLD.`basis`) OR NOT (BINARY NEW.`frequency` <=> BINARY OLD.`frequency`) OR NOT (NEW.`dauerfrist` <=> OLD.`dauerfrist`) OR NOT (NEW.`small_business` <=> OLD.`small_business`) OR NOT (BINARY NEW.`rules_version` <=> BINARY OLD.`rules_version`) OR NOT (BINARY NEW.`amends_id` <=> BINARY OLD.`amends_id`) OR NOT (BINARY NEW.`amends_key` <=> BINARY OLD.`amends_key`) OR NOT (NEW.`external` <=> OLD.`external`) OR NOT (BINARY NEW.`extra` <=> BINARY OLD.`extra`) OR (NOT NOT (BINARY NEW.`status` <=> BINARY OLD.`status`) AND (NOT (NEW.`finalised_at` <=> OLD.`finalised_at`) OR NOT (BINARY NEW.`finalised_by` <=> BINARY OLD.`finalised_by`) OR NOT (BINARY NEW.`boxes` <=> BINARY OLD.`boxes`) OR NOT (BINARY NEW.`source_lines` <=> BINARY OLD.`source_lines`) OR NOT (NEW.`result_cents` <=> OLD.`result_cents`) OR NOT (NEW.`special_prepayment_credit_cents` <=> OLD.`special_prepayment_credit_cents`) OR NOT (NEW.`rounding_difference_cents` <=> OLD.`rounding_difference_cents`))) OR (NOT (BINARY NEW.`status` <=> BINARY OLD.`status`) AND ((OLD.`filed_on` IS NOT NULL AND OLD.`filed_on` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') OR NOT (BINARY NEW.`filed_on` <=> BINARY OLD.`filed_on`)))) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.frozen'; END IF;
END;
CREATE TRIGGER `guard_vat_returns_frozen_del` BEFORE DELETE ON `vat_returns` FOR EACH ROW
BEGIN
  IF BINARY OLD.`status` = 'final' AND NOT COALESCE((OLD.`external` = 1 AND NOT (OLD.`replaced_by` IS NOT NULL AND OLD.`replaced_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.frozen'; END IF;
END;
CREATE TRIGGER `guard_zm_reports_frozen_upd` BEFORE UPDATE ON `zm_reports` FOR EACH ROW
BEGIN
  IF BINARY OLD.`status` = 'final' AND (NOT (BINARY NEW.`id` <=> BINARY OLD.`id`) OR NOT (NEW.`created` <=> OLD.`created`) OR NOT (NEW.`year` <=> OLD.`year`) OR NOT (NEW.`quarter` <=> OLD.`quarter`) OR NOT (BINARY NEW.`due_date` <=> BINARY OLD.`due_date`) OR NOT (BINARY NEW.`rules_version` <=> BINARY OLD.`rules_version`) OR NOT (BINARY NEW.`amends_id` <=> BINARY OLD.`amends_id`) OR NOT (BINARY NEW.`amends_key` <=> BINARY OLD.`amends_key`) OR NOT (BINARY NEW.`extra` <=> BINARY OLD.`extra`) OR (NOT NOT (BINARY NEW.`status` <=> BINARY OLD.`status`) AND (NOT (NEW.`finalised_at` <=> OLD.`finalised_at`) OR NOT (BINARY NEW.`finalised_by` <=> BINARY OLD.`finalised_by`) OR NOT (BINARY NEW.`lines` <=> BINARY OLD.`lines`) OR NOT (BINARY NEW.`source_lines` <=> BINARY OLD.`source_lines`))) OR (NOT (BINARY NEW.`status` <=> BINARY OLD.`status`) AND ((OLD.`filed_on` IS NOT NULL AND OLD.`filed_on` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') OR NOT (BINARY NEW.`filed_on` <=> BINARY OLD.`filed_on`)))) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.frozen'; END IF;
END;
CREATE TRIGGER `guard_zm_reports_frozen_del` BEFORE DELETE ON `zm_reports` FOR EACH ROW
BEGIN
  IF BINARY OLD.`status` = 'final' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.frozen'; END IF;
END;
CREATE TRIGGER `guard_vat_returns_filed_on_once_upd` BEFORE UPDATE ON `vat_returns` FOR EACH ROW
BEGIN
  IF (OLD.`filed_on` IS NOT NULL AND OLD.`filed_on` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT (BINARY NEW.`filed_on` <=> BINARY OLD.`filed_on`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.set_once'; END IF;
END;
CREATE TRIGGER `guard_zm_reports_filed_on_once_upd` BEFORE UPDATE ON `zm_reports` FOR EACH ROW
BEGIN
  IF (OLD.`filed_on` IS NOT NULL AND OLD.`filed_on` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT (BINARY NEW.`filed_on` <=> BINARY OLD.`filed_on`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.set_once'; END IF;
END;
CREATE TRIGGER `guard_fiscal_years_closed_at_once_upd` BEFORE UPDATE ON `fiscal_years` FOR EACH ROW
BEGIN
  IF (OLD.`closed_at` IS NOT NULL AND OLD.`closed_at` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT (NEW.`closed_at` <=> OLD.`closed_at`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.set_once'; END IF;
END;
CREATE TRIGGER `guard_fiscal_years_closed_by_once_upd` BEFORE UPDATE ON `fiscal_years` FOR EACH ROW
BEGIN
  IF (OLD.`closed_by` IS NOT NULL AND OLD.`closed_by` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT (BINARY NEW.`closed_by` <=> BINARY OLD.`closed_by`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.set_once'; END IF;
END;
