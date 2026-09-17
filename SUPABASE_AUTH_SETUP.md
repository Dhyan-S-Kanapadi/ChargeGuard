# Supabase merchant identity — stage 3

Implemented locally; **not deployed or live-verified**. Existing public-demo
sessions and non-production operator keys are unchanged. Supabase manages user
credentials and MFA. ChargeGuard's own PostgreSQL database owns memberships,
authorization, local session expiry/revocation and access auditing. No application
database move to Supabase is required. No custom JWT signing or password storage.

## External setup (operator action, never automatic)

1. Use separate Supabase projects for staging and production. Choose the region,
   paid plan, data processing terms and recovery/support requirements with your
   operator; this implementation provisions nothing.
2. Use asymmetric signing keys (ES256 or RS256). Configure access-token expiry
   to **600 seconds**. Legacy shared-secret HS256 tokens and longer-lived tokens
   are deliberately rejected. The backend gets public keys only from the configured
   project's JWKS endpoint; arbitrary issuers and custom domains are not supported
   by this initial configuration.
3. Enable email confirmation, disable anonymous login and public signup for this
   invite-only pilot. Configure a production SMTP sender with authenticated domain,
   abuse/rate-limit controls and the exact HTTPS dashboard URL. Never use wildcard
   redirects. Disable unneeded login methods. No secret/service-role key is needed
   by the ChargeGuard browser or API.
4. Enable authenticator-app (TOTP) MFA. Owners, reviewers and platform admins must
   have `aal2` for protected work. Read-only users can use `aal1`. The login screen
   guides initial TOTP enrollment and challenges an existing verified factor.
5. The dashboard recovery form uses **email OTP**, not an implicit callback token.
   Configure the Supabase **Reset Password** email template to show `{{ .Token }}`
   so the user can enter the code in the form. Test expiry, reuse, wrong codes,
   SMTP delivery and account-enumeration resistance. Recovery does not bypass MFA.
   Lost-factor recovery requires your documented identity-verification process at
   Supabase; never restore access solely because someone knows a merchant ID.
6. Populate `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` (only `sb_publishable_...`)
   in the **separate staging/production service**, with
   `CHARGEGUARD_AUTH_MODE=supabase`, `CHARGEGUARD_STORE_BACKEND=postgres` and that
   environment's `DATABASE_URL`. Do not change the working reviewer demo.
   Public configuration is served by `/auth/config`. Publishable is not a privileged
   key; the endpoint rejects secret/service-role keys. Keep existing runtime-required
   operator-key settings during this transition, but those keys grant **no access**
   to protected routes in Supabase mode.
7. Apply reviewed DB migrations explicitly. For the first administrator, create
   and verify a real Supabase user through the provider's admin workflow, then run:

   ```text
   python -m db.bootstrap_identity <verified-user-uuid>          # dry run
   python -m db.bootstrap_identity <verified-user-uuid> --apply  # explicit first admin
   ```

   This never contacts Supabase. Verify the UUID belongs to the intended account
   before applying it. It refuses if an administrator already exists. Later
   administrator changes require an independently reviewed offline database change;
   there is no HTTP route that promotes a user to platform admin.
8. A platform admin with MFA can provision a known Supabase user into ChargeGuard
   with `POST /auth/users/{uuid}` (`{"active":true}`), register the merchant using
   the existing merchant API, and assign the initial owner using
   `POST /auth/merchants/{merchant_id}/members/{uuid}` (`{"role":"owner"}`).
   Subsequent owners can assign/remove their own merchant's memberships. Platform
   administrators cannot use merchant data/chat routes or receive a membership;
   use a separate non-admin account when legitimately working on merchant cases.

## Permission boundaries

| Capability | Read-only | Reviewer | Owner | Platform admin |
| --- | --- | --- | --- | --- |
| Own merchant cases, metadata, stats, grounded chat | Yes | Yes, MFA | Yes, MFA | No |
| Case classification and existing manual outcome API | No | Yes, MFA | Yes, MFA | No |
| Orders, normalized ingress, merchant changes and connectors | No | No | Yes, MFA | No |
| Raw evidence | No | No | MFA + separate internal token | No |
| Assign/remove merchant members | No | No | Own merchant, MFA | MFA |
| Provision/disable accounts, create merchants, recovery/reconcile admin | No | No | No | MFA |

Permissions are checked server-side using stored ownership, not browser claims,
email domains, `user_metadata`, an `actor_id`, or a supplied `merchant_id` alone.
Classification audit actors come from verified identity. Unknown route templates
are denied. New exports/artifact downloads must be explicitly permissioned in stage
5; there is no unauthenticated file-download route added here. Stats and assistant
context are filtered before aggregation/model input. Model instructions cannot
expand that context. Merchant monitoring separately filters to the owning merchant.

Membership/account changes are transactional and audited. Concurrent removal of
the last owner is rejected. Administrative disable can intentionally suspend the
last owner during incident response. Only a platform admin may reactivate accounts.
Authorization audits record actor/session, route template and authorization result;
membership/account audits also record target user and action. An `authorized` row
does **not** claim the subsequent business operation completed. Existing business
audits remain separate. Audit retention/exports/alerting are stage 12.

## Sessions and revocation

- Bearer tokens only; no ambient authentication cookie, so cookie-CSRF is not
  introduced. No CORS relaxation was added. Browser merchant requests are same-origin
  only and refuse redirects. Tokens/refresh credentials stay in SDK memory; browser
  refresh requires a new sign-in. Protect the page from XSS with deployment CSP and
  dependency controls before launch (operations stage).
- Supabase SDK handles login, token refresh, TOTP and password recovery. ChargeGuard
  verifies signature, issuer, audience, expiry, issued-at, subject, session UUID,
  authenticated/non-anonymous status and MFA assurance with PyJWT.
- ChargeGuard sessions have an 8-hour absolute limit from first API use and a
  30-minute inactivity limit. Every request reloads account/membership status.
  Expired/revoked session IDs cannot be revived by refreshing a JWT.
- Sign out calls `/auth/session/revoke` **before** SDK logout. Revocation is durable
  across workers/restarts and applies to that session ID. Disabling an app account
  blocks all sessions and revokes all locally observed session IDs. Membership
  removal takes effect at the next authorization check; already-authorized in-flight
  operations are not retroactively cancelled.
- **Provider-only logout, password reset or provider account disable does not
  instantly invalidate an already issued JWT.** The residual window here is at most
  600 seconds, and signing-key caches can delay key-revocation observation. For an
  immediate incident response, disable the account in ChargeGuard **and** revoke
  provider sessions. Do not promise immediate external revocation or global logout
  based on JWT signature verification alone. Require a new login after app expiry.

## Verification and outstanding work

Automated tests use genuine locally signed JWTs, mocked JWKS transport and real
disposable PostgreSQL. They do not use real Supabase users or consume quota.
Run `tests/test_identity.py` with the isolated test DB URL along with the full
backend and frontend suites. Check both tenant-ID tampering and aggregate/AI paths.

Before approving a live pilot: test real email delivery/confirmation, MFA enrollment
and recovery, key rotation, logout/revocation, account suspension, token refresh,
reverse-proxy HTTPS behavior and audit review with two separate merchant accounts.
The admin provisioning API is implemented; a dedicated admin console and membership
management UI are not. Existing merchant UI can show actions a role cannot perform;
the API rejects them. Durable job fencing, managed secrets, immutable evidence,
reviewed submission and the remaining stages still block production launch.

Official contracts consulted:
[Supabase JWT verification](https://supabase.com/docs/guides/auth/jwts),
[sessions](https://supabase.com/docs/guides/auth/sessions),
[MFA](https://supabase.com/docs/guides/auth/auth-mfa),
[PyJWT](https://pyjwt.readthedocs.io/en/stable/usage.html).
