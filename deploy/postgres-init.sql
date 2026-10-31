\getenv app_password ENVFORGE_APP_PASSWORD
CREATE ROLE envforge_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE envforge_request NOLOGIN;
GRANT envforge_request TO envforge_app;
GRANT USAGE, CREATE ON SCHEMA public TO envforge_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
