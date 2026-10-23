-- migration: 00000002
-- requires: 00000001
-- permitA38 migration 0002: TOTP two-factor authentication (mariadb)
-- totp_secret: base32 secret (pending until totp_enabled), totp_last_step: last accepted 30 s time step (replay
-- protection), totp_recovery: JSON list of salted recovery-code hashes.

ALTER TABLE `users` ADD COLUMN `totp_secret` VARCHAR(255);
ALTER TABLE `users` ADD COLUMN `totp_enabled` TINYINT(1);
ALTER TABLE `users` ADD COLUMN `totp_last_step` BIGINT;
ALTER TABLE `users` ADD COLUMN `totp_recovery` LONGTEXT;
