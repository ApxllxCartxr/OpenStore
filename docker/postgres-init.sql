-- Two databases, two roles, and no cross-grant (§16.1, SPEC §5).
--
-- The sidecar never shares a database with the Merchant, demo included. This
-- file is what makes that structural rather than a promise: `sc_app` is not
-- granted anything in `spoiledduckie`, and a test asserts it cannot SELECT.

CREATE ROLE sd_app WITH LOGIN PASSWORD 'sd_app_dev';
CREATE ROLE sc_app WITH LOGIN PASSWORD 'sc_app_dev';

CREATE DATABASE spoiledduckie OWNER sd_app;
CREATE DATABASE sidecar OWNER sc_app;

-- PUBLIC gets CONNECT on every new database by default, which is how "no
-- cross-grant" quietly becomes "can connect and read the public schema".
REVOKE CONNECT ON DATABASE spoiledduckie FROM PUBLIC;
REVOKE CONNECT ON DATABASE sidecar FROM PUBLIC;
GRANT CONNECT ON DATABASE spoiledduckie TO sd_app;
GRANT CONNECT ON DATABASE sidecar TO sc_app;
