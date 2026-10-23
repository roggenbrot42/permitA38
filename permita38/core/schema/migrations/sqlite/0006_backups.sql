-- permitA38 migration 0006: legal-retention backups (sqlite)
-- issued_documents: immutable copies (PDF files under <data>/issued/) of finalized offers/invoices.
-- backup_runs: one row per backup archive written to a target (status, hashes, retention).

CREATE TABLE "issued_documents" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "kind" TEXT,
    "document_id" TEXT,
    "number" TEXT,
    "sha256" TEXT,
    "size" INTEGER,
    "path" TEXT,
    "issued_on" TEXT,
    "retention_class" TEXT,
    "retention_until" TEXT,
    "extra" TEXT
);

CREATE UNIQUE INDEX "idx_issued_document" ON "issued_documents" ("kind", "document_id");
CREATE INDEX "idx_issued_number" ON "issued_documents" ("number");

CREATE TABLE "backup_runs" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "started" INTEGER,
    "finished" INTEGER,
    "kind" TEXT,
    "status" TEXT,
    "target" TEXT,
    "path" TEXT,
    "filename" TEXT,
    "size" INTEGER,
    "sha256" TEXT,
    "manifest_sha256" TEXT,
    "prev_manifest_sha256" TEXT,
    "verified" INTEGER,
    "encrypted" INTEGER,
    "protected" INTEGER,
    "retention_until" TEXT,
    "error" TEXT,
    "extra" TEXT
);

CREATE INDEX "idx_backup_runs_started" ON "backup_runs" ("started");
CREATE INDEX "idx_backup_runs_kind" ON "backup_runs" ("kind", "status");
