"""The dialect classes are pure string generation: tested without any database driver."""

import pytest

from permita38.core.repository.dialect import DIALECTS, MariaDBDialect, PostgresDialect, SQLiteDialect
from permita38.core.repository.schema import JSON, LOGICAL_TYPES, TABLES, Column

sqlite, pg, maria = SQLiteDialect(), PostgresDialect(), MariaDBDialect()
ENTRIES, USERS, SETTINGS = TABLES["entries"], TABLES["users"], TABLES["settings"]


def test_quoting_escapes_the_quote_char():
    assert sqlite.quote("end") == '"end"' and pg.quote('we"ird') == '"we""ird"'
    assert maria.quote("key") == "`key`" and maria.quote("a`b") == "`a``b`"


def test_placeholders():
    assert sqlite.placeholder == "?" and pg.placeholder == maria.placeholder == "%s"
    assert pg.value_placeholder(Column("lines", JSON)) == "%s::jsonb"
    assert maria.value_placeholder(Column("lines", JSON)) == "%s"
    assert sqlite.value_placeholder(Column("lines", JSON)) == "?"


@pytest.mark.parametrize("name", list(DIALECTS))
def test_every_logical_type_maps_in_every_dialect(name):
    assert set(DIALECTS[name].types) == set(LOGICAL_TYPES)


def test_create_table_sqlite_and_postgres():
    s = sqlite.create_table(ENTRIES)
    assert s.startswith('CREATE TABLE "entries" (') and '"id" TEXT PRIMARY KEY' in s
    assert '"minutes" INTEGER' in s and '"billable" INTEGER' in s and '"rate" REAL' in s
    assert '"end" TEXT' in s and s.rstrip().endswith('"extra" TEXT\n);'.strip()) or '"extra" TEXT' in s
    p = pg.create_table(ENTRIES)
    assert '"minutes" BIGINT' in p and '"billable" BOOLEAN' in p and '"rate" DOUBLE PRECISION' in p
    assert '"extra" JSONB' in p


def test_create_table_mariadb():
    m = maria.create_table(USERS)
    assert m.startswith("CREATE TABLE `users` (") and "`id` VARCHAR(32) PRIMARY KEY" in m
    assert "`username` VARCHAR(255)" in m and "`active` TINYINT(1)" in m and "`extra` LONGTEXT" in m
    assert m.endswith("ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")


def test_settings_table_uses_key_as_primary_key_without_extra():
    for d in (sqlite, pg, maria):
        s = d.create_table(SETTINGS)
        assert f"{d.quote('key')}" in s and "PRIMARY KEY" in s and "extra" not in s


def test_indexes():
    idx = next(i for i in USERS.indexes if i.unique)
    assert sqlite.create_index(USERS, idx) == 'CREATE UNIQUE INDEX "idx_users_username" ON "users" ("username");'
    assert maria.create_index(USERS, idx) == "CREATE UNIQUE INDEX `idx_users_username` ON `users` (`username`);"
    multi = next(i for i in ENTRIES.indexes if len(i.columns) == 2)
    assert pg.create_index(ENTRIES, multi) == 'CREATE INDEX "idx_entries_user_date" ON "entries" ("user_id", "date");'


def test_ddl_covers_all_tables_and_indexes():
    for d in DIALECTS.values():
        ddl = d.ddl(TABLES)
        assert ddl.count("CREATE TABLE") == len(TABLES)
        assert ddl.count("CREATE INDEX") + ddl.count("CREATE UNIQUE INDEX") == sum(
            len(t.indexes) for t in TABLES.values()
        )


def test_upserts():
    cols = ["id", "name", "rate", "extra"]
    t = TABLES["customers"]
    s = sqlite.upsert(t, cols)
    assert s == (
        'INSERT INTO "customers" ("id", "name", "rate", "extra") VALUES (?, ?, ?, ?) '
        'ON CONFLICT ("id") DO UPDATE SET "name" = excluded."name", "rate" = excluded."rate", "extra" = excluded."extra"'
    )
    p = pg.upsert(t, cols)
    assert p.endswith(
        'ON CONFLICT ("id") DO UPDATE SET "name" = EXCLUDED."name", "rate" = EXCLUDED."rate", "extra" = EXCLUDED."extra"'
    )
    assert "VALUES (%s, %s, %s, %s::jsonb)" in p  # json column cast
    m = maria.upsert(t, cols)
    assert "ON DUPLICATE KEY UPDATE `name` = VALUES(`name`)" in m and "ON CONFLICT" not in m
    assert "`id` = VALUES" not in m  # the primary key is never updated


def test_select_and_delete_statements():
    assert sqlite.select_one(SETTINGS) == 'SELECT "key", "value" FROM "settings" WHERE "key" = ?'
    assert pg.delete_one(ENTRIES) == 'DELETE FROM "entries" WHERE "id" = %s'
    assert maria.select_all(SETTINGS, "`key`") == "SELECT `key`, `value` FROM `settings` ORDER BY `key`"


def test_value_conversion():
    assert sqlite.decode_json('{"a": [1, 2]}') == {"a": [1, 2]}
    assert pg.decode_json({"a": 1}) == {"a": 1}  # drivers that already parse JSON
    assert sqlite.encode_json({"b": "ü"}) == '{"b": "ü"}'
    assert sqlite.encode_bool(True) == 1 and pg.encode_bool(True) is True
    assert sqlite.decode_bool(0) is False and maria.decode_bool(1) is True
