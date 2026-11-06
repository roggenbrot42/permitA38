-- migration: 7ca84bdc
-- requires: e4b70dba
-- permitA38 migration: project_planning (mariadb)

-- 1. schema
ALTER TABLE `projects` ADD COLUMN `kind` VARCHAR(255);

-- 2. data fixes (the new guards do not exist yet)
UPDATE `projects` SET `kind` = CASE WHEN (`customer_id` IS NOT NULL AND `customer_id` <> '')
  OR EXISTS (SELECT 1 FROM `offers` WHERE `project_id` = `projects`.`id`) OR EXISTS (SELECT 1 FROM `contracts` WHERE `project_id` = `projects`.`id`) OR EXISTS (SELECT 1 FROM `invoices` WHERE `project_id` = `projects`.`id`) THEN 'customer' ELSE 'internal' END;
UPDATE `contracts` SET `total_hours` = NULL WHERE `hours_per_week` IS NOT NULL AND `total_hours` IS NOT NULL;
UPDATE `allocations` SET `total_hours` = NULL WHERE `hours_per_week` IS NOT NULL AND `total_hours` IS NOT NULL;
UPDATE `projects` SET `status` = 'active' WHERE `status` IS NULL;
UPDATE `allocations` SET `status` = 'active' WHERE `status` IS NULL;
UPDATE `contracts` SET `status` = 'confirmed' WHERE `status` IS NULL;

ALTER TABLE `projects` DROP COLUMN `personal`;
ALTER TABLE `projects` DROP COLUMN `member_ids`;

-- 3. guards: generated from core/repository/schema.py GUARDS by Dialect.guard_sql (a test keeps them equal)
ALTER TABLE `projects` ADD CONSTRAINT `guard_projects_kind_required` CHECK (NOT COALESCE((NOT (`kind` IS NOT NULL AND `kind` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `projects` ADD CONSTRAINT `guard_projects_kind_one_of` CHECK (NOT COALESCE(((`kind` IS NOT NULL AND `kind` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `kind` NOT IN ('customer', 'internal')), 0));
ALTER TABLE `projects` ADD CONSTRAINT `guard_projects_status_one_of` CHECK (NOT COALESCE(((`status` IS NOT NULL AND `status` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `status` NOT IN ('active', 'paused', 'done', 'archived')), 0));
ALTER TABLE `projects` ADD CONSTRAINT `guard_projects_name_required` CHECK (NOT COALESCE((NOT (`name` IS NOT NULL AND `name` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `projects` ADD CONSTRAINT `guard_projects_internal_customer_implies` CHECK (NOT COALESCE((COALESCE(BINARY `kind` = 'internal', 0) AND NOT COALESCE(NOT (`customer_id` IS NOT NULL AND `customer_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
ALTER TABLE `projects` ADD CONSTRAINT `guard_projects_budget_hours_range` CHECK (NOT COALESCE((`budget_hours` IS NOT NULL AND (`budget_hours` <= 0 OR `budget_hours` > 100000)), 0));
ALTER TABLE `allocations` ADD CONSTRAINT `guard_allocations_project_id_required` CHECK (NOT COALESCE((NOT (`project_id` IS NOT NULL AND `project_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `allocations` ADD CONSTRAINT `guard_allocations_start_required` CHECK (NOT COALESCE((NOT (`start` IS NOT NULL AND `start` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `allocations` ADD CONSTRAINT `guard_allocations_end_required` CHECK (NOT COALESCE((NOT (`end` IS NOT NULL AND `end` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `allocations` ADD CONSTRAINT `guard_allocations_status_one_of` CHECK (NOT COALESCE(((`status` IS NOT NULL AND `status` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `status` NOT IN ('active', 'cancelled')), 0));
ALTER TABLE `allocations` ADD CONSTRAINT `guard_allocations_hours_exactly_one` CHECK (NOT COALESCE((((`hours_per_week` IS NOT NULL AND `hours_per_week` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND (`total_hours` IS NOT NULL AND `total_hours` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')) OR (NOT (`hours_per_week` IS NOT NULL AND `hours_per_week` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT (`total_hours` IS NOT NULL AND `total_hours` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'))), 0));
ALTER TABLE `allocations` ADD CONSTRAINT `guard_allocations_hours_per_week_range` CHECK (NOT COALESCE((`hours_per_week` IS NOT NULL AND (`hours_per_week` <= 0 OR `hours_per_week` > 80)), 0));
ALTER TABLE `allocations` ADD CONSTRAINT `guard_allocations_total_hours_range` CHECK (NOT COALESCE((`total_hours` IS NOT NULL AND (`total_hours` <= 0 OR `total_hours` > 100000)), 0));
ALTER TABLE `allocations` ADD CONSTRAINT `guard_allocations_period_max_days` CHECK (NOT COALESCE((COALESCE((CASE WHEN `start` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$' AND `end` REGEXP '^[0-9]{4}-[01][0-9]-[0-3][0-9]$' THEN DATEDIFF(`end`, `start`) END) > 1830, 0)), 0));
ALTER TABLE `allocations` ADD CONSTRAINT `guard_allocations_rate_range` CHECK (NOT COALESCE((`rate` IS NOT NULL AND (`rate` < 0 OR `rate` > 10000)), 0));
ALTER TABLE `allocations` ADD CONSTRAINT `guard_allocations_cancelled_from_implies` CHECK (NOT COALESCE((COALESCE(BINARY `status` = 'cancelled', 0) AND NOT COALESCE((`cancelled_from` IS NOT NULL AND `cancelled_from` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
ALTER TABLE `allocations` ADD CONSTRAINT `guard_allocations_active_no_cancelled_from_implies` CHECK (NOT COALESCE((COALESCE(BINARY `status` = 'active', 0) AND NOT COALESCE(NOT (`cancelled_from` IS NOT NULL AND `cancelled_from` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
ALTER TABLE `allocations` ADD CONSTRAINT `guard_allocations_supplier_rate_implies` CHECK (NOT COALESCE((COALESCE((`supplier_id` IS NOT NULL AND `supplier_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0) AND NOT COALESCE((`rate` IS NOT NULL AND `rate` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
ALTER TABLE `allocations` ADD CONSTRAINT `guard_allocations_user_no_incoming_offer_implies` CHECK (NOT COALESCE((COALESCE((`user_id` IS NOT NULL AND `user_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0) AND NOT COALESCE(NOT (`incoming_offer_id` IS NOT NULL AND `incoming_offer_id` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'), 0)), 0));
ALTER TABLE `contracts` ADD CONSTRAINT `guard_contracts_title_required` CHECK (NOT COALESCE((NOT (`title` IS NOT NULL AND `title` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `contracts` ADD CONSTRAINT `guard_contracts_start_required` CHECK (NOT COALESCE((NOT (`start` IS NOT NULL AND `start` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `contracts` ADD CONSTRAINT `guard_contracts_end_required` CHECK (NOT COALESCE((NOT (`end` IS NOT NULL AND `end` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')), 0));
ALTER TABLE `contracts` ADD CONSTRAINT `guard_contracts_status_one_of` CHECK (NOT COALESCE(((`status` IS NOT NULL AND `status` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND BINARY `status` NOT IN ('confirmed', 'done')), 0));
ALTER TABLE `contracts` ADD CONSTRAINT `guard_contracts_hours_exactly_one` CHECK (NOT COALESCE((((`hours_per_week` IS NOT NULL AND `hours_per_week` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND (`total_hours` IS NOT NULL AND `total_hours` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')) OR (NOT (`hours_per_week` IS NOT NULL AND `hours_per_week` NOT REGEXP '^[\\x20\\x09-\\x0d]*$') AND NOT (`total_hours` IS NOT NULL AND `total_hours` NOT REGEXP '^[\\x20\\x09-\\x0d]*$'))), 0));
ALTER TABLE `contracts` ADD CONSTRAINT `guard_contracts_hours_per_week_range` CHECK (NOT COALESCE((`hours_per_week` IS NOT NULL AND (`hours_per_week` <= 0 OR `hours_per_week` > 10000)), 0));
ALTER TABLE `contracts` ADD CONSTRAINT `guard_contracts_total_hours_range` CHECK (NOT COALESCE((`total_hours` IS NOT NULL AND (`total_hours` <= 0 OR `total_hours` > 100000)), 0));
ALTER TABLE `contracts` ADD CONSTRAINT `guard_contracts_rate_range` CHECK (NOT COALESCE((`rate` IS NOT NULL AND (`rate` < 0 OR `rate` > 10000)), 0));
