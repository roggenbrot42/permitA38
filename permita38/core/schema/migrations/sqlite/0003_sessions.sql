-- permitA38 migration: server-side sessions and API tokens (sqlite)
-- kind 'session': one row per sign-in (id = JWT `jti`); kind 'api': personal access token (token_hash = SHA-256 of the
-- secret, scopes = JSON list); kind 'marker': one row recording when server-side sessions were first enforced.
-- NOTE: numbered 0003 in the feat/sessions branch (migrations must be gap-free); renumber to 0007 when merging.

CREATE TABLE "sessions" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "user_id" TEXT,
    "kind" TEXT,
    "name" TEXT,
    "user_agent" TEXT,
    "ip_hint" TEXT,
    "expires" INTEGER,
    "last_seen" INTEGER,
    "revoked_at" INTEGER,
    "token_hash" TEXT,
    "scopes" TEXT,
    "extra" TEXT
);
CREATE INDEX "idx_sessions_user" ON "sessions" ("user_id");
CREATE INDEX "idx_sessions_expires" ON "sessions" ("expires");
