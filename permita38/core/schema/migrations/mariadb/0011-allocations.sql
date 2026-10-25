-- migration: 72306069
-- requires: c8af1eca
-- permitA38 migration: allocations (mariadb)

-- Allocations: hours per week of one resource (employee or, later, subcontractor) given to one project
CREATE TABLE `allocations` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `user_id` VARCHAR(32),
    `supplier_id` VARCHAR(32),
    `incoming_offer_id` VARCHAR(32),
    `project_id` VARCHAR(32),
    `contract_id` VARCHAR(32),
    `customer_id` VARCHAR(32),
    `start` VARCHAR(255),
    `end` VARCHAR(255),
    `hours_per_week` DOUBLE,
    `total_hours` DOUBLE,
    `rate` DOUBLE,
    `status` VARCHAR(255),
    `cancelled_from` VARCHAR(255),
    `cancel_reason` LONGTEXT,
    `extra` LONGTEXT,
    CONSTRAINT `allocations_one_owner` CHECK (
        (`user_id` IS NOT NULL AND `supplier_id` IS NULL) OR (`user_id` IS NULL AND `supplier_id` IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_allocations_user` ON `allocations` (`user_id`);
CREATE INDEX `idx_allocations_project` ON `allocations` (`project_id`);
