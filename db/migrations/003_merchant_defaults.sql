ALTER TABLE merchants ALTER COLUMN storefront_platform SET DEFAULT 'unknown';
ALTER TABLE merchants ALTER COLUMN platform_credential_verified SET DEFAULT false;
UPDATE merchants SET storefront_platform='unknown' WHERE storefront_platform IS NULL;
UPDATE merchants SET platform_credential_verified=false WHERE platform_credential_verified IS NULL;
