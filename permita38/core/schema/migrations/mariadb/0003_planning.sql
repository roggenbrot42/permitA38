-- permitA38 migration 0003: planning (mariadb)
-- RENUMBERING NOTE: this branch numbers it 0003 because it is the next free number here; the parent renumbers it
-- to 0009 when the planner branch is merged after the other branches (update CURRENT_REVISION accordingly).
-- projects.start / end: project period; backfilled from the earliest/latest contract and time entry where known.
-- projects.extensions: JSON list of period extensions (manual or by offer). plan_scenarios: named what-if scenarios.

ALTER TABLE `projects` ADD COLUMN `start` VARCHAR(255);
ALTER TABLE `projects` ADD COLUMN `end` VARCHAR(255);
ALTER TABLE `projects` ADD COLUMN `extensions` LONGTEXT;

UPDATE `projects` SET `start` = (
    SELECT MIN(t.x) FROM (
        SELECT `start` AS x FROM `contracts` WHERE `project_id` = `projects`.`id`
        UNION ALL SELECT `date` AS x FROM `entries` WHERE `project_id` = `projects`.`id`
    ) AS t
), `end` = (
    SELECT MAX(t.x) FROM (
        SELECT `end` AS x FROM `contracts` WHERE `project_id` = `projects`.`id`
        UNION ALL SELECT `date` AS x FROM `entries` WHERE `project_id` = `projects`.`id`
    ) AS t
);

CREATE TABLE `plan_scenarios` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `name` VARCHAR(255),
    `user_id` VARCHAR(32),
    `changes` LONGTEXT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_plan_scenarios_user` ON `plan_scenarios` (`user_id`);
