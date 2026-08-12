\set ON_ERROR_STOP on

\if :{?assistant_password}
\else
\echo 'assistant_password is required; no changes were made'
\quit 3
\endif

DO $assistant_role$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'assistant_core') THEN
        CREATE ROLE assistant_core LOGIN;
    END IF;
END
$assistant_role$;

ALTER ROLE assistant_core LOGIN;
ALTER ROLE assistant_core PASSWORD :'assistant_password';

CREATE SCHEMA IF NOT EXISTS assistant_core;
REVOKE ALL ON SCHEMA assistant_core FROM public;
GRANT CONNECT ON DATABASE postgres TO assistant_core;
GRANT USAGE, CREATE ON SCHEMA assistant_core TO assistant_core;
GRANT USAGE ON SCHEMA extensions TO assistant_core;
ALTER ROLE assistant_core IN DATABASE postgres
    SET search_path TO assistant_core, extensions, public;
