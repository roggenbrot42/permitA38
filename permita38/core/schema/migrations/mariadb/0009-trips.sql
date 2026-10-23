-- migration: 9897dd11
-- requires: d8a69cde
-- permitA38 migration: trips (mariadb)

-- trips: one row per business trip (docs/specs/business-trips.md); figures are stored when computed
ALTER TABLE `expenses` ADD COLUMN `trip_id` VARCHAR(32);

CREATE TABLE `trips` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `user_id` VARCHAR(32),
    `title` VARCHAR(255),
    `purpose` LONGTEXT,
    `customer_id` VARCHAR(32),
    `project_id` VARCHAR(32),
    `start` VARCHAR(255),
    `end` VARCHAR(255),
    `places` LONGTEXT,
    `meals_provided` LONGTEXT,
    `mileage` LONGTEXT,
    `not_needed` LONGTEXT,
    `cost_centre` VARCHAR(255),
    `status` VARCHAR(255),
    `allowance_total` DOUBLE,
    `mileage_km` DOUBLE,
    `mileage_total` DOUBLE,
    `rules_version` VARCHAR(255),
    `days` LONGTEXT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_trips_user` ON `trips` (`user_id`);
