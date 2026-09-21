-- A database per shop and a database per sidecar, with no cross-grant
-- (§16.1, SPEC §5).
--
-- The sidecar never shares a database with the Merchant, demo included. This
-- file is what makes that structural rather than a promise: `sc_app` is not
-- granted anything in `spoiledduckie`, and a test asserts it cannot SELECT.
--
-- The second shop repeats the pattern rather than joining the first: two
-- Merchants in one database would make a tenant column the only thing
-- standing between their catalogues, and one deploy serves one Merchant
-- domain (ADR-0007).

CREATE ROLE sd_app WITH LOGIN PASSWORD 'sd_app_dev';
CREATE ROLE sc_app WITH LOGIN PASSWORD 'sc_app_dev';
CREATE ROLE de_app WITH LOGIN PASSWORD 'de_app_dev';
CREATE ROLE scb_app WITH LOGIN PASSWORD 'scb_app_dev';

CREATE DATABASE spoiledduckie OWNER sd_app;
CREATE DATABASE sidecar OWNER sc_app;
CREATE DATABASE dogeared OWNER de_app;
CREATE DATABASE sidecar_books OWNER scb_app;

-- PUBLIC gets CONNECT on every new database by default, which is how "no
-- cross-grant" quietly becomes "can connect and read the public schema".
REVOKE CONNECT ON DATABASE spoiledduckie FROM PUBLIC;
REVOKE CONNECT ON DATABASE sidecar FROM PUBLIC;
REVOKE CONNECT ON DATABASE dogeared FROM PUBLIC;
REVOKE CONNECT ON DATABASE sidecar_books FROM PUBLIC;
GRANT CONNECT ON DATABASE spoiledduckie TO sd_app;
GRANT CONNECT ON DATABASE sidecar TO sc_app;
GRANT CONNECT ON DATABASE dogeared TO de_app;
GRANT CONNECT ON DATABASE sidecar_books TO scb_app;
