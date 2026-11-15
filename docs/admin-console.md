# PowerAble — Admin Console

## Purpose

An administrator is the installer role. It has authority over **pairing labels,
outlets, and accounts** — not over measurements. The console can generate a QR
label, rotate or revoke a code, rename or delete an outlet, change roles and
account status, reset a password, and review an audit trail.

It never marks a device online, invents a reading, or simulates a relay change.

## Creating an administrator

Admins are created only from the local installer CLI, never by the public
registration form:

```powershell
.\.venv\Scripts\python.exe -m flask --app run.py create-admin --username installer
```

The command prompts for the password without echo. Running it again for an
existing username promotes that account to `admin`, reactivates it, and replaces
its password. Each run writes `admin.created` or `admin.promoted` to the audit
log. Passwords are hashed before storage and never written to the log.

The first admin gets no special UI to create more admins; use the same CLI, then
manage roles from `/admin/users`.

## Access rules

| Request | Result |
|---------|--------|
| Anonymous `GET /admin/...` | Redirect to `/auth/login`, then back after sign-in |
| Signed-in `role="user"` | `403 Not allowed` |
| Signed-in `role="admin"` | Allowed |
| Disabled account with an old session | Signed out on the next request |

Every route is protected twice:

1. `app/permissions.py` applies `login_required` + the `admin` role check.
2. `app/services/admin_service.py` repeats the role check before every mutation.

Templates hide links for non-admins, but hiding a button is never treated as
security.

## Capabilities

| Area | Actions |
|------|---------|
| Overview | Account, outlet, label, audit, and device-event totals |
| QR labels | Generate a label, rotate a code, print a label |
| Outlets | Rename, revoke/release, permanently delete |
| Accounts | Change role, change status, reset password, delete account |
| Activity | Admin actions and device lifecycle events |

### QR label rules

The label is the same format used by the CLI:

```
POWERABLE:PAIR
VERSION:1
DEVICE_ID:PA-ESP32-0001
PAIRING_CODE:ABCDEFGHIJKLMNOPQRSTUVWX
```

- The pairing code has 120 bits of entropy and is single-use.
- Only a scrypt hash is stored in `smart_outlets.pairing_token_hash`.
- The plaintext code is rendered **once**, directly into the response to the
  request that generated it. It is never put in the session cookie, audit log,
  or device event.
- That response sends `Cache-Control: no-store` and `Pragma: no-cache`.
- Reusing an unregistered device ID rotates the previous code immediately.
- A registered outlet must be revoked before a new label can be issued.
- The QR never contains a Wi-Fi password, account data, or a permanent device
  secret. Wi-Fi provisioning remains a separate future workflow.

If the one-time page is lost, generate a new label. The old code stops working.

### Confirmation and destructive actions

- Revoke and delete forms ask for browser confirmation.
- Permanent outlet deletion requires typing the exact device ID.
- Permanent account deletion requires typing the exact username.
- Deleting an outlet also deletes its `DeviceEvent` rows.
- Deleting an account first revokes every outlet it owned, then deletes the
  account. Audit records keep the former username as a snapshot.

## Guardrails

- An admin cannot change their **own** role.
- An admin cannot disable their **own** account.
- An admin cannot delete **themselves**.
- At least one active administrator must remain, even if a role/status/delete
  request is crafted directly against the service layer.
- A disabled account is signed out on its next request. JSON API calls receive
  `403` with `error="account_disabled"`.
- Login is refused for a disabled account even with a correct password.

These checks live in the service layer so they apply to both the web console and
any future client.

## Audit trail

`admin_actions` stores:

| Column | Meaning |
|--------|---------|
| `actor_id` | FK to `users.id`, set to `NULL` if the actor is deleted |
| `actor_name` | Username snapshot, so history stays readable |
| `action` | Short verb such as `outlet.provisioned` |
| `target` | Device ID or username |
| `description` | Short human-readable note |
| `created_at` | UTC timestamp |

Recorded actions:

`outlet.provisioned`, `outlet.renamed`, `outlet.revoked`, `outlet.deleted`,
`user.role_changed`, `user.status_changed`, `user.password_reset`,
`user.deleted`, `admin.created`, `admin.promoted`.

No password, pairing code, hash, or session token is written to this table or to
`device_events`.

## Routes

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/admin/` | Overview |
| GET | `/admin/outlets` | Search/filter outlets |
| GET, POST | `/admin/outlets/new` | Create a label; POST renders it once |
| POST | `/admin/outlets/<id>/rotate` | Replace an unregistered label |
| POST | `/admin/outlets/<id>/rename` | Rename an outlet |
| POST | `/admin/outlets/<id>/revoke` | Revoke and release ownership |
| POST | `/admin/outlets/<id>/delete` | Permanently delete |
| GET | `/admin/users` | Search accounts |
| POST | `/admin/users/<id>/role` | Change role |
| POST | `/admin/users/<id>/status` | Activate/disable |
| POST | `/admin/users/<id>/password` | Reset password |
| POST | `/admin/users/<id>/delete` | Delete account and revoke outlets |
| GET | `/admin/activity` | Audit and device-event history |

All POST routes require the session CSRF token. The mobile dock keeps five items:
for an admin, the **Schedules** slot is replaced by **Admin**, and the same
Schedules pages remain available from the main navigation.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_admin.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

The admin suite covers anonymous/non-admin denial, CSRF, label generation with
hash-only storage, rotate/rename/revoke/delete, exact confirmation fields,
registered-outlet protection, self and last-admin guards, password reset,
disabled-session enforcement, account deletion, and secret-free audit records.
