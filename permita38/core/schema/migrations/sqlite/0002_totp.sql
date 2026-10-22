-- permitA38 migration 0002: TOTP two-factor authentication (sqlite)
-- totp_secret: base32 secret (pending until totp_enabled), totp_last_step: last accepted 30 s time step (replay
-- protection), totp_recovery: JSON list of salted recovery-code hashes.

ALTER TABLE "users" ADD COLUMN "totp_secret" TEXT;
ALTER TABLE "users" ADD COLUMN "totp_enabled" INTEGER;
ALTER TABLE "users" ADD COLUMN "totp_last_step" INTEGER;
ALTER TABLE "users" ADD COLUMN "totp_recovery" TEXT;
