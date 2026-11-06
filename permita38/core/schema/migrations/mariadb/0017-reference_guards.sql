-- migration: dea6e12b
-- requires: e4b70dba
-- permitA38 migration: reference_guards (mariadb)

-- lookup indexes for the delete-side reference checks
CREATE INDEX `idx_contracts_project` ON `contracts` (`project_id`);
CREATE INDEX `idx_offers_project` ON `offers` (`project_id`);
CREATE INDEX `idx_invoices_project` ON `invoices` (`project_id`);
CREATE INDEX `idx_incoming_offers_project` ON `incoming_offers` (`project_id`);
CREATE INDEX `idx_trips_project` ON `trips` (`project_id`);
CREATE INDEX `idx_timers_project` ON `timers` (`project_id`);

-- reference guards: generated from core/repository/schema.py GUARDS by Dialect.guard_sql (a test keeps them equal)
CREATE TRIGGER `guard_allocations_project_id_ref_ins` BEFORE INSERT ON `allocations` FOR EACH ROW
BEGIN
  IF (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) AND NOT EXISTS (SELECT 1 FROM `allocations` WHERE `id` = NEW.`id` AND `project_id` <=> NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_allocations_project_id_ref_upd` BEFORE UPDATE ON `allocations` FOR EACH ROW
BEGIN
  IF NOT (OLD.`project_id` <=> NEW.`project_id`) AND (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_allocations_project_id_ref_del` BEFORE DELETE ON `projects` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `allocations` WHERE `project_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_allocations_user_id_ref_ins` BEFORE INSERT ON `allocations` FOR EACH ROW
BEGIN
  IF (NEW.`user_id` IS NOT NULL AND NEW.`user_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `users` WHERE `id` = NEW.`user_id`) AND NOT EXISTS (SELECT 1 FROM `allocations` WHERE `id` = NEW.`id` AND `user_id` <=> NEW.`user_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_allocations_user_id_ref_upd` BEFORE UPDATE ON `allocations` FOR EACH ROW
BEGIN
  IF NOT (OLD.`user_id` <=> NEW.`user_id`) AND (NEW.`user_id` IS NOT NULL AND NEW.`user_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `users` WHERE `id` = NEW.`user_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_allocations_user_id_ref_del` BEFORE DELETE ON `users` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `allocations` WHERE `user_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_allocations_supplier_id_ref_ins` BEFORE INSERT ON `allocations` FOR EACH ROW
BEGIN
  IF (NEW.`supplier_id` IS NOT NULL AND NEW.`supplier_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `suppliers` WHERE `id` = NEW.`supplier_id`) AND NOT EXISTS (SELECT 1 FROM `allocations` WHERE `id` = NEW.`id` AND `supplier_id` <=> NEW.`supplier_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_allocations_supplier_id_ref_upd` BEFORE UPDATE ON `allocations` FOR EACH ROW
BEGIN
  IF NOT (OLD.`supplier_id` <=> NEW.`supplier_id`) AND (NEW.`supplier_id` IS NOT NULL AND NEW.`supplier_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `suppliers` WHERE `id` = NEW.`supplier_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_allocations_supplier_id_ref_del` BEFORE DELETE ON `suppliers` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `allocations` WHERE `supplier_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_contracts_project_id_ref_ins` BEFORE INSERT ON `contracts` FOR EACH ROW
BEGIN
  IF (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) AND NOT EXISTS (SELECT 1 FROM `contracts` WHERE `id` = NEW.`id` AND `project_id` <=> NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_contracts_project_id_ref_upd` BEFORE UPDATE ON `contracts` FOR EACH ROW
BEGIN
  IF NOT (OLD.`project_id` <=> NEW.`project_id`) AND (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_contracts_project_id_ref_del` BEFORE DELETE ON `projects` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `contracts` WHERE `project_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_entries_project_id_ref_ins` BEFORE INSERT ON `entries` FOR EACH ROW
BEGIN
  IF (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) AND NOT EXISTS (SELECT 1 FROM `entries` WHERE `id` = NEW.`id` AND `project_id` <=> NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_entries_project_id_ref_upd` BEFORE UPDATE ON `entries` FOR EACH ROW
BEGIN
  IF NOT (OLD.`project_id` <=> NEW.`project_id`) AND (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_entries_project_id_ref_del` BEFORE DELETE ON `projects` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `entries` WHERE `project_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_offers_project_id_ref_ins` BEFORE INSERT ON `offers` FOR EACH ROW
BEGIN
  IF (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) AND NOT EXISTS (SELECT 1 FROM `offers` WHERE `id` = NEW.`id` AND `project_id` <=> NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_offers_project_id_ref_upd` BEFORE UPDATE ON `offers` FOR EACH ROW
BEGIN
  IF NOT (OLD.`project_id` <=> NEW.`project_id`) AND (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_offers_project_id_ref_del` BEFORE DELETE ON `projects` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `offers` WHERE `project_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_invoices_project_id_ref_ins` BEFORE INSERT ON `invoices` FOR EACH ROW
BEGIN
  IF (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) AND NOT EXISTS (SELECT 1 FROM `invoices` WHERE `id` = NEW.`id` AND `project_id` <=> NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_invoices_project_id_ref_upd` BEFORE UPDATE ON `invoices` FOR EACH ROW
BEGIN
  IF NOT (OLD.`project_id` <=> NEW.`project_id`) AND (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_invoices_project_id_ref_del` BEFORE DELETE ON `projects` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `invoices` WHERE `project_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_tasks_project_id_ref_ins` BEFORE INSERT ON `tasks` FOR EACH ROW
BEGIN
  IF (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) AND NOT EXISTS (SELECT 1 FROM `tasks` WHERE `id` = NEW.`id` AND `project_id` <=> NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_tasks_project_id_ref_upd` BEFORE UPDATE ON `tasks` FOR EACH ROW
BEGIN
  IF NOT (OLD.`project_id` <=> NEW.`project_id`) AND (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_tasks_project_id_ref_del` BEFORE DELETE ON `projects` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `tasks` WHERE `project_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_expenses_project_id_ref_ins` BEFORE INSERT ON `expenses` FOR EACH ROW
BEGIN
  IF (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) AND NOT EXISTS (SELECT 1 FROM `expenses` WHERE `id` = NEW.`id` AND `project_id` <=> NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_expenses_project_id_ref_upd` BEFORE UPDATE ON `expenses` FOR EACH ROW
BEGIN
  IF NOT (OLD.`project_id` <=> NEW.`project_id`) AND (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_expenses_project_id_ref_del` BEFORE DELETE ON `projects` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `expenses` WHERE `project_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_incoming_offers_project_id_ref_ins` BEFORE INSERT ON `incoming_offers` FOR EACH ROW
BEGIN
  IF (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) AND NOT EXISTS (SELECT 1 FROM `incoming_offers` WHERE `id` = NEW.`id` AND `project_id` <=> NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_incoming_offers_project_id_ref_upd` BEFORE UPDATE ON `incoming_offers` FOR EACH ROW
BEGIN
  IF NOT (OLD.`project_id` <=> NEW.`project_id`) AND (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_incoming_offers_project_id_ref_del` BEFORE DELETE ON `projects` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `incoming_offers` WHERE `project_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_trips_project_id_ref_ins` BEFORE INSERT ON `trips` FOR EACH ROW
BEGIN
  IF (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) AND NOT EXISTS (SELECT 1 FROM `trips` WHERE `id` = NEW.`id` AND `project_id` <=> NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_trips_project_id_ref_upd` BEFORE UPDATE ON `trips` FOR EACH ROW
BEGIN
  IF NOT (OLD.`project_id` <=> NEW.`project_id`) AND (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_trips_project_id_ref_del` BEFORE DELETE ON `projects` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `trips` WHERE `project_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
CREATE TRIGGER `guard_timers_project_id_ref_ins` BEFORE INSERT ON `timers` FOR EACH ROW
BEGIN
  IF (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) AND NOT EXISTS (SELECT 1 FROM `timers` WHERE `id` = NEW.`id` AND `project_id` <=> NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_timers_project_id_ref_upd` BEFORE UPDATE ON `timers` FOR EACH ROW
BEGIN
  IF NOT (OLD.`project_id` <=> NEW.`project_id`) AND (NEW.`project_id` IS NOT NULL AND NEW.`project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT EXISTS (SELECT 1 FROM `projects` WHERE `id` = NEW.`project_id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'; END IF;
END;
CREATE TRIGGER `guard_timers_project_id_ref_del` BEFORE DELETE ON `projects` FOR EACH ROW
BEGIN
  IF EXISTS (SELECT 1 FROM `timers` WHERE `project_id` = OLD.`id`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'; END IF;
END;
