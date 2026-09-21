-- A database per shop and a database per sidecar, with no cross-grant
-- (§16.1, SPEC §5).
--
-- The sidecar never shares a database with the Merchant, demo included. This
-- file is what makes that structural rather than a promise: `sc_app` is not
-- granted anything in `spoiledduckie`, and a test asserts it cannot SELECT.
--
-- Every later shop repeats the pattern rather than joining an earlier one:
-- two Merchants in one database would make a tenant column the only thing
-- standing between their catalogues, and one deploy serves one Merchant
-- domain (ADR-0007).

CREATE ROLE sd_app WITH LOGIN PASSWORD 'sd_app_dev';
CREATE ROLE sc_app WITH LOGIN PASSWORD 'sc_app_dev';
CREATE ROLE de_app WITH LOGIN PASSWORD 'de_app_dev';
CREATE ROLE scb_app WITH LOGIN PASSWORD 'scb_app_dev';
CREATE ROLE cy_app WITH LOGIN PASSWORD 'cy_app_dev';
CREATE ROLE sccy_app WITH LOGIN PASSWORD 'sccy_app_dev';
CREATE ROLE il_app WITH LOGIN PASSWORD 'il_app_dev';
CREATE ROLE scil_app WITH LOGIN PASSWORD 'scil_app_dev';
CREATE ROLE pl_app WITH LOGIN PASSWORD 'pl_app_dev';
CREATE ROLE scpl_app WITH LOGIN PASSWORD 'scpl_app_dev';
CREATE ROLE kg_app WITH LOGIN PASSWORD 'kg_app_dev';
CREATE ROLE sckg_app WITH LOGIN PASSWORD 'sckg_app_dev';
CREATE ROLE df_app WITH LOGIN PASSWORD 'df_app_dev';
CREATE ROLE scdf_app WITH LOGIN PASSWORD 'scdf_app_dev';
CREATE ROLE rl_app WITH LOGIN PASSWORD 'rl_app_dev';
CREATE ROLE scrl_app WITH LOGIN PASSWORD 'scrl_app_dev';
CREATE ROLE ps_app WITH LOGIN PASSWORD 'ps_app_dev';
CREATE ROLE scps_app WITH LOGIN PASSWORD 'scps_app_dev';
CREATE ROLE fw_app WITH LOGIN PASSWORD 'fw_app_dev';
CREATE ROLE scfw_app WITH LOGIN PASSWORD 'scfw_app_dev';

CREATE DATABASE spoiledduckie OWNER sd_app;
CREATE DATABASE sidecar OWNER sc_app;
CREATE DATABASE dogeared OWNER de_app;
CREATE DATABASE sidecar_books OWNER scb_app;
CREATE DATABASE circuityard OWNER cy_app;
CREATE DATABASE sidecar_cy OWNER sccy_app;
CREATE DATABASE ironlist OWNER il_app;
CREATE DATABASE sidecar_il OWNER scil_app;
CREATE DATABASE pantryline OWNER pl_app;
CREATE DATABASE sidecar_pl OWNER scpl_app;
CREATE DATABASE kettleandgrain OWNER kg_app;
CREATE DATABASE sidecar_kg OWNER sckg_app;
CREATE DATABASE deskfield OWNER df_app;
CREATE DATABASE sidecar_df OWNER scdf_app;
CREATE DATABASE rootandleaf OWNER rl_app;
CREATE DATABASE sidecar_rl OWNER scrl_app;
CREATE DATABASE playspool OWNER ps_app;
CREATE DATABASE sidecar_ps OWNER scps_app;
CREATE DATABASE furrow OWNER fw_app;
CREATE DATABASE sidecar_fw OWNER scfw_app;

-- PUBLIC gets CONNECT on every new database by default, which is how "no
-- cross-grant" quietly becomes "can connect and read the public schema".
REVOKE CONNECT ON DATABASE spoiledduckie FROM PUBLIC;
REVOKE CONNECT ON DATABASE sidecar FROM PUBLIC;
REVOKE CONNECT ON DATABASE dogeared FROM PUBLIC;
REVOKE CONNECT ON DATABASE sidecar_books FROM PUBLIC;
REVOKE CONNECT ON DATABASE circuityard FROM PUBLIC;
REVOKE CONNECT ON DATABASE sidecar_cy FROM PUBLIC;
REVOKE CONNECT ON DATABASE ironlist FROM PUBLIC;
REVOKE CONNECT ON DATABASE sidecar_il FROM PUBLIC;
REVOKE CONNECT ON DATABASE pantryline FROM PUBLIC;
REVOKE CONNECT ON DATABASE sidecar_pl FROM PUBLIC;
REVOKE CONNECT ON DATABASE kettleandgrain FROM PUBLIC;
REVOKE CONNECT ON DATABASE sidecar_kg FROM PUBLIC;
REVOKE CONNECT ON DATABASE deskfield FROM PUBLIC;
REVOKE CONNECT ON DATABASE sidecar_df FROM PUBLIC;
REVOKE CONNECT ON DATABASE rootandleaf FROM PUBLIC;
REVOKE CONNECT ON DATABASE sidecar_rl FROM PUBLIC;
REVOKE CONNECT ON DATABASE playspool FROM PUBLIC;
REVOKE CONNECT ON DATABASE sidecar_ps FROM PUBLIC;
REVOKE CONNECT ON DATABASE furrow FROM PUBLIC;
REVOKE CONNECT ON DATABASE sidecar_fw FROM PUBLIC;
GRANT CONNECT ON DATABASE spoiledduckie TO sd_app;
GRANT CONNECT ON DATABASE sidecar TO sc_app;
GRANT CONNECT ON DATABASE dogeared TO de_app;
GRANT CONNECT ON DATABASE sidecar_books TO scb_app;
GRANT CONNECT ON DATABASE circuityard TO cy_app;
GRANT CONNECT ON DATABASE sidecar_cy TO sccy_app;
GRANT CONNECT ON DATABASE ironlist TO il_app;
GRANT CONNECT ON DATABASE sidecar_il TO scil_app;
GRANT CONNECT ON DATABASE pantryline TO pl_app;
GRANT CONNECT ON DATABASE sidecar_pl TO scpl_app;
GRANT CONNECT ON DATABASE kettleandgrain TO kg_app;
GRANT CONNECT ON DATABASE sidecar_kg TO sckg_app;
GRANT CONNECT ON DATABASE deskfield TO df_app;
GRANT CONNECT ON DATABASE sidecar_df TO scdf_app;
GRANT CONNECT ON DATABASE rootandleaf TO rl_app;
GRANT CONNECT ON DATABASE sidecar_rl TO scrl_app;
GRANT CONNECT ON DATABASE playspool TO ps_app;
GRANT CONNECT ON DATABASE sidecar_ps TO scps_app;
GRANT CONNECT ON DATABASE furrow TO fw_app;
GRANT CONNECT ON DATABASE sidecar_fw TO scfw_app;
