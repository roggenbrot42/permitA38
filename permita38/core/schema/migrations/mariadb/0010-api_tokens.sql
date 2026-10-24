-- migration: c8af1eca
-- requires: 9897dd11
-- permitA38 migration: api_tokens (mariadb)

-- API tokens (hash only) and the append-only audit log of token requests
CREATE TABLE `api_tokens` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `user_id` VARCHAR(32),
    `label` VARCHAR(255),
    `token_hash` VARCHAR(255),
    `hint` VARCHAR(255),
    `read_only` TINYINT(1),
    `expires_at` BIGINT,
    `revoked_at` BIGINT,
    `last_used_at` BIGINT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_api_tokens_user` ON `api_tokens` (`user_id`);
CREATE UNIQUE INDEX `idx_api_tokens_hash` ON `api_tokens` (`token_hash`);

CREATE TABLE `audit_log` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `at` BIGINT,
    `token_id` VARCHAR(32),
    `user_id` VARCHAR(32),
    `method` VARCHAR(255),
    `route` VARCHAR(255),
    `status` BIGINT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_audit_log_token` ON `audit_log` (`token_id`, `at`);
CREATE INDEX `idx_audit_log_at` ON `audit_log` (`at`);
