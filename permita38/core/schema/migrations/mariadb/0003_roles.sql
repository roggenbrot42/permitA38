-- permitA38 migration 0003: roles and rights (mariadb)
-- New table `roles` (name, description, rights, system flag) and `users.role_ids`. Seeds the two locked built-in
-- roles `boss` / `member` and two editable examples; existing users are mapped from the old `role` column
-- (boss -> sysboss, everything else -> sysmember). The rights listed here are a snapshot of the catalog at this
-- revision (permita38/core/rights.py); the built-in roles are always resolved from the code at runtime.

CREATE TABLE `roles` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `name` VARCHAR(255),
    `description` LONGTEXT,
    `rights` LONGTEXT,
    `system` TINYINT(1),
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX `idx_roles_name` ON `roles` (`name`);

ALTER TABLE `users` ADD COLUMN `role_ids` LONGTEXT;

INSERT INTO `roles` (`id`, `created`, `updated`, `name`, `description`, `rights`, `system`) VALUES ('sysboss', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), 'Boss', 'Everything, including roles and settings. Locked: cannot be edited or deleted.', '["absences.manage_all", "absences.manage_own", "absences.view_all", "accounting.manage", "accounting.view", "attachments.view", "audit.view", "backup.manage", "backup.view", "customers.manage", "customers.view", "expenses.manage", "expenses.view", "export.run", "finance.view", "gdpr.manage", "invoices.manage", "invoices.view", "offers.manage", "offers.view", "planning.manage", "planning.view", "projects.manage", "projects.view_all", "projects.view_assigned", "roles.manage", "settings.manage", "tasks.manage", "team.manage", "team.view", "templates.manage", "time.edit_all", "time.track_own", "time.view_all", "vat.manage", "vat.view"]', 1);
INSERT INTO `roles` (`id`, `created`, `updated`, `name`, `description`, `rights`, `system`) VALUES ('sysmember', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), 'Member', 'Tracks their own time and time off. Locked: cannot be edited or deleted.', '["absences.manage_own", "projects.view_assigned", "time.track_own"]', 1);
INSERT INTO `roles` (`id`, `created`, `updated`, `name`, `description`, `rights`, `system`) VALUES ('exaccountant', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), 'Accountant', 'Reads invoices, offers, customers, finance and the planning; manages expenses and bills.', '["attachments.view", "customers.view", "expenses.manage", "finance.view", "invoices.view", "offers.view", "planning.view", "projects.view_all"]', 0);
INSERT INTO `roles` (`id`, `created`, `updated`, `name`, `description`, `rights`, `system`) VALUES ('exprojectmanager', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), 'Project manager', 'Plans and runs projects and the team''s time, without access to money, invoices or settings.', '["absences.manage_all", "absences.manage_own", "customers.view", "planning.manage", "projects.manage", "team.view", "time.edit_all", "time.track_own", "time.view_all"]', 0);

UPDATE `users` SET `role_ids` = '["sysboss"]' WHERE `role` = 'boss';
UPDATE `users` SET `role_ids` = '["sysmember"]' WHERE `role_ids` IS NULL;
