-- migration: c8af1eca
-- requires: 9897dd11
-- permitA38 migration: api_tokens (postgresql)

-- API tokens (hash only) and the append-only audit log of token requests
CREATE TABLE "api_tokens" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "user_id" TEXT,
    "label" TEXT,
    "token_hash" TEXT,
    "hint" TEXT,
    "read_only" BOOLEAN,
    "expires_at" BIGINT,
    "revoked_at" BIGINT,
    "last_used_at" BIGINT,
    "extra" JSONB
);
CREATE INDEX "idx_api_tokens_user" ON "api_tokens" ("user_id");
CREATE UNIQUE INDEX "idx_api_tokens_hash" ON "api_tokens" ("token_hash");

CREATE TABLE "audit_log" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "at" BIGINT,
    "token_id" TEXT,
    "user_id" TEXT,
    "method" TEXT,
    "route" TEXT,
    "status" BIGINT,
    "extra" JSONB
);
CREATE INDEX "idx_audit_log_token" ON "audit_log" ("token_id", "at");
CREATE INDEX "idx_audit_log_at" ON "audit_log" ("at");
