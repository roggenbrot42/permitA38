-- permitA38 migration: server-side sessions and API tokens (mariadb)
-- kind 'session': one row per sign-in (id = JWT `jti`); kind 'api': personal access token (token_hash = SHA-256 of the
-- secret, scopes = JSON list); kind 'marker': one row recording when server-side sessions were first enforced.
-- NOTE: numbered 0003 in the feat/sessions branch (migrations must be gap-free); renumber to 0007 when merging.

CREATE TABLE `sessions` (
    `id` VARCHAR(32) PRIMARY KEY,
    `created` BIGINT,
    `updated` BIGINT,
    `user_id` VARCHAR(32),
    `kind` VARCHAR(255),
    `name` VARCHAR(255),
    `user_agent` VARCHAR(255),
    `ip_hint` VARCHAR(255),
    `expires` BIGINT,
    `last_seen` BIGINT,
    `revoked_at` BIGINT,
    `token_hash` VARCHAR(255),
    `scopes` LONGTEXT,
    `extra` LONGTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX `idx_sessions_user` ON `sessions` (`user_id`);
CREATE INDEX `idx_sessions_expires` ON `sessions` (`expires`);
