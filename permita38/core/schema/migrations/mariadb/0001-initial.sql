-- migration: 00000001
-- requires: none
-- permitA38 migration 0001: initial schema (mariadb)
-- generated from core/repository/schema.py

CREATE TABLE `customers` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `name` VARCHAR(255),
    `customer_no` VARCHAR(255),
    `street` VARCHAR(255),
    `zip` VARCHAR(255),
    `city` VARCHAR(255),
    `country` VARCHAR(255),
    `tax_number` VARCHAR(255),
    `vat_id` VARCHAR(255),
    `contact` VARCHAR(255),
    `email` VARCHAR(255),
    `phone` VARCHAR(255),
    `notes` LONGTEXT,
    `rate` DOUBLE,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `projects` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `name` VARCHAR(255),
    `customer_id` VARCHAR(32),
    `personal` TINYINT(1),
    `rate` DOUBLE,
    `budget_hours` DOUBLE,
    `status` VARCHAR(255),
    `color` VARCHAR(255),
    `notes` LONGTEXT,
    `member_ids` LONGTEXT,
    `offer_id` VARCHAR(32),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_projects_customer` ON `projects` (`customer_id`);

CREATE TABLE `tasks` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `project_id` VARCHAR(32),
    `name` VARCHAR(255),
    `rate` DOUBLE,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_tasks_project` ON `tasks` (`project_id`);

CREATE TABLE `entries` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `user_id` VARCHAR(32),
    `date` VARCHAR(255),
    `minutes` BIGINT,
    `project_id` VARCHAR(32),
    `task_id` VARCHAR(32),
    `note` LONGTEXT,
    `start` VARCHAR(255),
    `end` VARCHAR(255),
    `billable` TINYINT(1),
    `invoice_id` VARCHAR(32),
    `rate` DOUBLE,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_entries_user_date` ON `entries` (`user_id`, `date`);
CREATE INDEX `idx_entries_project` ON `entries` (`project_id`);
CREATE INDEX `idx_entries_invoice` ON `entries` (`invoice_id`);

CREATE TABLE `contracts` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `title` VARCHAR(255),
    `customer_id` VARCHAR(32),
    `project_id` VARCHAR(32),
    `start` VARCHAR(255),
    `end` VARCHAR(255),
    `hours_per_week` DOUBLE,
    `total_hours` DOUBLE,
    `rate` DOUBLE,
    `status` VARCHAR(255),
    `user_ids` LONGTEXT,
    `offer_id` VARCHAR(32),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `absences` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `user_id` VARCHAR(32),
    `kind` VARCHAR(255),
    `start` VARCHAR(255),
    `end` VARCHAR(255),
    `note` LONGTEXT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_absences_user` ON `absences` (`user_id`);

CREATE TABLE `offers` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `number` VARCHAR(255),
    `customer_id` VARCHAR(32),
    `project_id` VARCHAR(32),
    `status` VARCHAR(255),
    `date` VARCHAR(255),
    `due_date` VARCHAR(255),
    `valid_until` VARCHAR(255),
    `paid_date` VARCHAR(255),
    `service_from` VARCHAR(255),
    `service_to` VARCHAR(255),
    `title` VARCHAR(255),
    `reference` VARCHAR(255),
    `template_id` VARCHAR(255),
    `language` VARCHAR(255),
    `intro` LONGTEXT,
    `outro` LONGTEXT,
    `notes` LONGTEXT,
    `vat_rate` DOUBLE,
    `lines` LONGTEXT,
    `plan` LONGTEXT,
    `offer_id` VARCHAR(32),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_offers_status` ON `offers` (`status`);
CREATE INDEX `idx_offers_customer` ON `offers` (`customer_id`);

CREATE TABLE `invoices` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `number` VARCHAR(255),
    `customer_id` VARCHAR(32),
    `project_id` VARCHAR(32),
    `status` VARCHAR(255),
    `date` VARCHAR(255),
    `due_date` VARCHAR(255),
    `valid_until` VARCHAR(255),
    `paid_date` VARCHAR(255),
    `service_from` VARCHAR(255),
    `service_to` VARCHAR(255),
    `title` VARCHAR(255),
    `reference` VARCHAR(255),
    `template_id` VARCHAR(255),
    `language` VARCHAR(255),
    `intro` LONGTEXT,
    `outro` LONGTEXT,
    `notes` LONGTEXT,
    `vat_rate` DOUBLE,
    `lines` LONGTEXT,
    `plan` LONGTEXT,
    `offer_id` VARCHAR(32),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_invoices_status` ON `invoices` (`status`);
CREATE INDEX `idx_invoices_customer` ON `invoices` (`customer_id`);

CREATE TABLE `expenses` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `date` VARCHAR(255),
    `net` DOUBLE,
    `vat` DOUBLE,
    `vat_rate` DOUBLE,
    `vendor` VARCHAR(255),
    `description` LONGTEXT,
    `category` VARCHAR(255),
    `project_id` VARCHAR(32),
    `deductible` DOUBLE,
    `attachment` VARCHAR(255),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_expenses_date` ON `expenses` (`date`);
CREATE INDEX `idx_expenses_project` ON `expenses` (`project_id`);

CREATE TABLE `users` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `username` VARCHAR(255),
    `name` VARCHAR(255),
    `role` VARCHAR(255),
    `weekly_hours` DOUBLE,
    `workdays` LONGTEXT,
    `default_rate` DOUBLE,
    `cost_rate` DOUBLE,
    `color` VARCHAR(255),
    `tracking_start` VARCHAR(255),
    `active` TINYINT(1),
    `password_hash` VARCHAR(255),
    `pw_changed` BIGINT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX `idx_users_username` ON `users` (`username`);

CREATE TABLE `templates` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `name` VARCHAR(255),
    `layout` VARCHAR(255),
    `accent` VARCHAR(255),
    `language` VARCHAR(255),
    `show_logo` TINYINT(1),
    `intro_offer` LONGTEXT,
    `outro_offer` LONGTEXT,
    `intro_invoice` LONGTEXT,
    `outro_invoice` LONGTEXT,
    `closing` LONGTEXT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `timers` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `running` TINYINT(1),
    `started` BIGINT,
    `project_id` VARCHAR(32),
    `task_id` VARCHAR(32),
    `note` LONGTEXT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `settings` (
    `key` VARCHAR(255) PRIMARY KEY,
    `value` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
