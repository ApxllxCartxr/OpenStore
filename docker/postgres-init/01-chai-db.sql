-- Second merchant database on the same Postgres server.
-- Single-tenant per sidecar process (DECISION-015): gelateria and chai never
-- share tables, only the server.
SELECT 'CREATE DATABASE openstore_chai'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'openstore_chai')\gexec
