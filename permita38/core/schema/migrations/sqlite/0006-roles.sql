-- migration: 4d5e0131
-- requires: 6c7ec897
-- permitA38 migration 0006: roles (data only, sqlite)
-- Role "member" is renamed "employee" (behaviour unchanged); "accountant" is new; "boss" stays. No column changes:
-- the permissions per role live in permita38/core/rights.py.
UPDATE "users" SET "role" = 'employee' WHERE "role" = 'member';
