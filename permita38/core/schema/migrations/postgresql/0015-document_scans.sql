-- migration: 2fe799c1
-- requires: f3fcc516
-- permitA38 migration: document_scans (postgresql)

-- scanned old offers and invoices (docs/specs/document-scan.md)
CREATE TABLE "document_scans" (
    "id" TEXT PRIMARY KEY,
    "created" BIGINT,
    "updated" BIGINT,
    "status" TEXT,
    "sha256" TEXT,
    "doc_key" TEXT,
    "attachment" TEXT,
    "filename" TEXT,
    "engine" TEXT,
    "kind" TEXT,
    "draft" JSONB,
    "doc" JSONB,
    "flags" JSONB,
    "record_coll" TEXT,
    "record_id" TEXT,
    "batch_id" TEXT,
    "created_by" TEXT,
    "extra" JSONB
);
CREATE UNIQUE INDEX "idx_document_scans_sha256" ON "document_scans" ("sha256");
CREATE INDEX "idx_document_scans_status" ON "document_scans" ("status");
CREATE INDEX "idx_document_scans_doc_key" ON "document_scans" ("doc_key");
CREATE INDEX "idx_document_scans_record_id" ON "document_scans" ("record_id");
