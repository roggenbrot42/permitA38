-- permitA38 migration 0003 (renumbered to 0011 at merge): calendars (ICS import / subscription) and per-user working hours (sqlite)
-- calendar_sources.url is a SECRET (ICS share links are bearer URLs): it is never returned by the API.
-- calendar_events holds the expanded busy time of the horizon; timed events are UTC ISO strings
-- (YYYY-MM-DDTHH:MM:SSZ), all-day events are dates (end exclusive).

ALTER TABLE "users" ADD COLUMN "work_start" TEXT;
ALTER TABLE "users" ADD COLUMN "work_end" TEXT;
ALTER TABLE "users" ADD COLUMN "timezone" TEXT;

CREATE TABLE "calendar_sources" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "user_id" TEXT,
    "name" TEXT,
    "kind" TEXT,
    "url" TEXT,
    "refresh_minutes" INTEGER,
    "title_policy" TEXT,
    "all_day_blocks" INTEGER,
    "include_keywords" TEXT,
    "exclude_keywords" TEXT,
    "busy_default" INTEGER,
    "last_fetched" INTEGER,
    "last_status" TEXT,
    "last_error" TEXT,
    "etag" TEXT,
    "last_modified" TEXT,
    "event_count" INTEGER,
    "extra" TEXT
);
CREATE INDEX "idx_calendar_sources_user" ON "calendar_sources" ("user_id");

CREATE TABLE "calendar_events" (
    "id" TEXT PRIMARY KEY,
    "created" INTEGER,
    "updated" INTEGER,
    "source_id" TEXT,
    "uid" TEXT,
    "recurrence_id" TEXT,
    "start_utc" TEXT,
    "end_utc" TEXT,
    "all_day" INTEGER,
    "busy" INTEGER,
    "ooo" INTEGER,
    "title" TEXT,
    "categories" TEXT,
    "extra" TEXT
);
CREATE INDEX "idx_calendar_events_source" ON "calendar_events" ("source_id");
CREATE INDEX "idx_calendar_events_start" ON "calendar_events" ("start_utc");
