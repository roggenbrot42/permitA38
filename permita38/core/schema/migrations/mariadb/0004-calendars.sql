-- migration: c41e7d90
-- requires: 00000003
-- permitA38 migration 0004: calendars (ICS import / subscription) and per-user working hours
-- calendar_sources.url is a SECRET (ICS share links are bearer URLs): it is never returned by the API.
-- calendar_events holds the expanded busy time of the horizon; timed events are UTC ISO strings
-- (YYYY-MM-DDTHH:MM:SSZ), all-day events are dates (end exclusive).

ALTER TABLE `users` ADD COLUMN `work_start` VARCHAR(255);
ALTER TABLE `users` ADD COLUMN `work_end` VARCHAR(255);
ALTER TABLE `users` ADD COLUMN `timezone` VARCHAR(255);

CREATE TABLE `calendar_sources` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `user_id` VARCHAR(32),
    `name` VARCHAR(255),
    `kind` VARCHAR(255),
    `url` LONGTEXT,
    `refresh_minutes` BIGINT,
    `title_policy` VARCHAR(255),
    `all_day_blocks` TINYINT(1),
    `include_keywords` LONGTEXT,
    `exclude_keywords` LONGTEXT,
    `busy_default` TINYINT(1),
    `last_fetched` BIGINT,
    `last_status` VARCHAR(255),
    `last_error` LONGTEXT,
    `etag` VARCHAR(255),
    `last_modified` VARCHAR(255),
    `event_count` BIGINT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_calendar_sources_user` ON `calendar_sources` (`user_id`);

CREATE TABLE `calendar_events` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `source_id` VARCHAR(32),
    `uid` VARCHAR(255),
    `recurrence_id` VARCHAR(255),
    `start_utc` VARCHAR(255),
    `end_utc` VARCHAR(255),
    `all_day` TINYINT(1),
    `busy` TINYINT(1),
    `ooo` TINYINT(1),
    `title` VARCHAR(255),
    `categories` LONGTEXT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_calendar_events_source` ON `calendar_events` (`source_id`);
CREATE INDEX `idx_calendar_events_start` ON `calendar_events` (`start_utc`);
