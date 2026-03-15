-- Applied on first DB initialization.
-- Keycloak --import-realm does not overwrite existing realm settings,
-- so sslRequired must be patched at the DB level after realm creation.
-- This function runs after Keycloak has written its initial schema.

-- We use a DO block with a loop so this is safe to run even before
-- the realm table exists (it will just be a no-op).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'realm') THEN
    UPDATE realm SET ssl_required = 'NONE' WHERE ssl_required != 'NONE';
  END IF;
END $$;
