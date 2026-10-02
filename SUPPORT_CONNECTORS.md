# Gmail and Freshdesk connectors

Connectors use the existing authenticated merchant routes, `CredentialSecretStore`,
metadata store and audit trail. In Supabase mode, only a merchant owner may
create, verify or disconnect; members can list their own merchant's metadata.
Local operator API keys retain their existing operator-wide access.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/merchants/{merchant_id}/support-connectors/gmail` | Verify and attach/rotate an issued access token |
| POST | `/merchants/{merchant_id}/support-connectors/freshdesk` | Verify and attach/rotate a Freshdesk API key |
| GET | `/merchants/{merchant_id}/support-connectors` | List safe metadata, including inactive attempts |
| POST | `/merchants/{merchant_id}/support-connectors/{connector_id}/verify` | Reverify stored credentials |
| DELETE | `/merchants/{merchant_id}/support-connectors/{connector_id}` | Disconnect and delete encrypted credentials |

Gmail accepts `{"access_token":"<issued-token>"}` and always uses mailbox `me`.
Freshdesk accepts `{"api_key":"<key>","domain":"your-tenant.freshdesk.com"}`.
Only canonical lowercase Freshdesk subdomains are accepted; arbitrary URLs,
custom domains, ports and redirects are not supported. Neither endpoint accepts
OAuth authorization codes, refresh tokens or caller-supplied secret references.
Credentials are omitted entirely from metadata, responses and audit records.

Creation returns 201 with `status=verified` or `status=invalid` and a fixed
`last_error_code`. Verification checks a bounded messages/tickets listing through
the existing clients, including successful empty results. A failed rotation leaves
the current verified connector active. Successful rotation disconnects its
predecessor and removes its secret. Reverification preserves verified status on
transient errors; 401/403 invalidates it. Reverification of a disconnected
connector returns 409. An initial failed verification stores no secret: submit a
new connection to retry. DELETE is retryable when secret deletion fails (503);
metadata is already disconnected, so the secret cannot be used for evidence.

Live communications collection resolves the current verified connector by
merchant and provider on every collection. It does not use legacy
`support_connector_ref`, mailbox/domain overrides or global environment credentials.
Existing synthetic evidence mode remains available. Gmail and Freshdesk collection
fail independently and preserve successful evidence from the other provider.
Default dispute responses expose only the communications assessment booleans;
message/ticket content, including nested structures, requires the existing raw
access authorization and internal token. Provider credential echoes are removed
before evidence enters workflow state.

Configure the existing `CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY` and
`CHARGEGUARD_CREDENTIAL_STORE_PATH` for durable credentials. PostgreSQL deployments
must explicitly apply migration `006_support_connectors.sql` with the existing
migration command before startup. The existing encrypted file adapter remains
single-process infrastructure; this change does not implement a managed vault.
Gmail tokens must be manually replaced when they expire. Provider stubs do not
bypass connector verification in the normal application.

## Local HTTP walkthrough (no real credentials)

From the repository root, with project dependencies installed:

```sh
python -m scripts.support_connector_demo
```

This separate development runner serves the real FastAPI application at
`http://127.0.0.1:8028/docs`. It uses existing provider clients with HTTP mocks,
accepts only the synthetic credentials below, and keeps metadata and encrypted
secrets in an isolated temporary directory removed on exit. It refuses to run
outside development/test and does not read `.env`. Stop with Ctrl-C.

```sh
curl -s http://127.0.0.1:8028/merchants \
  -H 'X-API-Key: support-demo-local-key' -H 'Content-Type: application/json' \
  -d '{"merchant_id":"support-demo","name":"Support demo","vertical":"ecommerce"}'

curl -s http://127.0.0.1:8028/merchants/support-demo/support-connectors/gmail \
  -H 'X-API-Key: support-demo-local-key' -H 'Content-Type: application/json' \
  -d '{"access_token":"demo-gmail-token"}'

curl -s http://127.0.0.1:8028/merchants/support-demo/support-connectors/freshdesk \
  -H 'X-API-Key: support-demo-local-key' -H 'Content-Type: application/json' \
  -d '{"api_key":"demo-freshdesk-key","domain":"demo.freshdesk.com"}'

curl -s http://127.0.0.1:8028/merchants/support-demo/support-connectors \
  -H 'X-API-Key: support-demo-local-key'
```

Copy a returned connector ID, then use POST on its `/verify` URL and DELETE on
its connector URL. Repeat a successful create to test rotation. Use
`demo-denied-token` for an authentication failure, or `demo-unavailable-token`
for a simulated 503, in either credential field. An unsuccessful rotation must
leave the earlier verified connector usable. Register a second merchant and
try the first merchant's connector ID under the second merchant's path: both
verify and delete return 404. The mock providers return empty successful
listings; automated tests cover non-empty evidence, independent failures and
nested redaction.
