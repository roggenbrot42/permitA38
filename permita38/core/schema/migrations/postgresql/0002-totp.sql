-- migration: 00000002
-- requires: 00000001
-- permitA38 migration 0002: TOTP two-factor authentication (postgresql)
-- totp_secret: base32 secret (pending until totp_enabled), totp_last_step: last accepted 30 s time step (replay
-- protection), totp_recovery: JSON list of salted recovery-code hashes.

ALTER TABLE "users" ADD COLUMN "totp_secret" TEXT;
ALTER TABLE "users" ADD COLUMN "totp_enabled" BOOLEAN;
ALTER TABLE "users" ADD COLUMN "totp_last_step" BIGINT;
ALTER TABLE "users" ADD COLUMN "totp_recovery" JSONB;
