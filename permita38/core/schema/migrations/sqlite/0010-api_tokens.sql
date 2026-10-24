-- migration: c8af1eca
-- requires: 9897dd11
-- permitA38 migration: api_tokens (sqlite)

-- API tokens (hash only) and the append-only audit log of token requests
CREATE TABLE "api_tokens" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "user_id" TEXT,
    "label" TEXT,
    "token_hash" TEXT,
    "hint" TEXT,
    "read_only" INTEGER,
    "expires_at" INTEGER,
    "revoked_at" INTEGER,
    "last_used_at" INTEGER,
    "extra" TEXT
);
CREATE INDEX "idx_api_tokens_user" ON "api_tokens" ("user_id");
CREATE UNIQUE INDEX "idx_api_tokens_hash" ON "api_tokens" ("token_hash");

CREATE TABLE "audit_log" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "at" INTEGER,
    "token_id" TEXT,
    "user_id" TEXT,
    "method" TEXT,
    "route" TEXT,
    "status" INTEGER,
    "extra" TEXT
);
CREATE INDEX "idx_audit_log_token" ON "audit_log" ("token_id", "at");
CREATE INDEX "idx_audit_log_at" ON "audit_log" ("at");
