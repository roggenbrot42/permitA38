-- migration: c6d8d0be
-- requires: 72306069
-- permitA38 migration: suppliers (mariadb)

-- Suppliers and subcontractors, offers received from suppliers, and links from expenses (docs/contracts/subcontractors.md)
ALTER TABLE `expenses` ADD COLUMN `supplier_id` VARCHAR(32);
ALTER TABLE `expenses` ADD COLUMN `allocation_id` VARCHAR(32);
ALTER TABLE `expenses` ADD COLUMN `reverse_charge` TINYINT(1);

CREATE TABLE `suppliers` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `name` VARCHAR(255),
    `supplier_no` VARCHAR(255),
    `kind` VARCHAR(255),
    `active` TINYINT(1),
    `street` VARCHAR(255),
    `zip` VARCHAR(255),
    `city` VARCHAR(255),
    `country` VARCHAR(255),
    `vat_id` VARCHAR(255),
    `tax_number` VARCHAR(255),
    `vat_mode` VARCHAR(255),
    `contact` VARCHAR(255),
    `email` VARCHAR(255),
    `phone` VARCHAR(255),
    `notes` LONGTEXT,
    `iban` VARCHAR(255),
    `bic` VARCHAR(255),
    `bank_name` VARCHAR(255),
    `payment_terms_days` BIGINT,
    `payment_terms_note` VARCHAR(255),
    `tags` LONGTEXT,
    `weekly_hours` DOUBLE,
    `workdays` LONGTEXT,
    `rate` DOUBLE,
    `unavailable` LONGTEXT,
    `login_user_id` VARCHAR(32),
    `external_id` VARCHAR(255),
    `import_batch` VARCHAR(255),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_suppliers_name` ON `suppliers` (`name`);
CREATE INDEX `idx_suppliers_external_id` ON `suppliers` (`external_id`);

CREATE TABLE `incoming_offers` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `supplier_id` VARCHAR(32),
    `reference` VARCHAR(255),
    `title` VARCHAR(255),
    `received_on` VARCHAR(255),
    `valid_until` VARCHAR(255),
    `lines` LONGTEXT,
    `net_total` DOUBLE,
    `vat` DOUBLE,
    `vat_rate` DOUBLE,
    `vat_mode` VARCHAR(255),
    `gross_total` DOUBLE,
    `plan` LONGTEXT,
    `project_id` VARCHAR(32),
    `contract_id` VARCHAR(32),
    `customer_id` VARCHAR(32),
    `attachment` VARCHAR(255),
    `status` VARCHAR(255),
    `status_changed_at` VARCHAR(255),
    `decided_by` VARCHAR(32),
    `decision_note` LONGTEXT,
    `allocation_id` VARCHAR(32),
    `notes` LONGTEXT,
    `external_id` VARCHAR(255),
    `import_batch` VARCHAR(255),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_incoming_offers_supplier` ON `incoming_offers` (`supplier_id`);
CREATE INDEX `idx_incoming_offers_status` ON `incoming_offers` (`status`);

CREATE INDEX `idx_allocations_supplier` ON `allocations` (`supplier_id`);
CREATE INDEX `idx_allocations_status` ON `allocations` (`status`);
CREATE INDEX `idx_expenses_supplier` ON `expenses` (`supplier_id`);
CREATE INDEX `idx_expenses_allocation` ON `expenses` (`allocation_id`);
