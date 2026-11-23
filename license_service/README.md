# Internal license service

Use a dedicated `aispace-license` service account, `/opt/aispace-license` for code/venv and `/var/lib/aispace-license` mode 0700 for persistent data. Install `requirements.txt` into that venv. Copy the included unit to `/etc/systemd/system/aispace-license.service`.

Initialize once (never overwrite signing.pem):
```sh
cd /opt/aispace-license
sudo -u aispace-license venv/bin/python -m license_service.cli init
sudo systemctl daemon-reload
sudo systemctl enable --now aispace-license
```
Copy **only public.pem** into the client's trusted public-key configuration. Never package signing.pem or licenses.sqlite3. Production must replace test keys and use HTTPS; HTTP internal testing does not provide confidentiality.

Local administration, from `/opt/aispace-license`:
```sh
sudo -u aispace-license venv/bin/python -m license_service.cli create --expires 2026-12-31T23:59:59Z
sudo -u aispace-license venv/bin/python -m license_service.cli status --id LICENSE_ID
sudo -u aispace-license venv/bin/python -m license_service.cli renew --id LICENSE_ID --expires 2027-01-31T23:59:59Z
sudo -u aispace-license venv/bin/python -m license_service.cli disable --id LICENSE_ID
sudo -u aispace-license venv/bin/python -m license_service.cli unbind --id LICENSE_ID
journalctl -u aispace-license -n 100 --no-pager
sudo systemctl stop aispace-license
```
Create prints the code once. Protect that terminal output. The public application exposes no admin HTTP routes; the separate local-only administrator service is documented below. Access logging is disabled by the unit; do not enable request-body logging in a proxy.

Unbind increments binding generation and removes the previous bearer credential. Same-device activation returns the existing credential. Disable immediately rejects subsequent checks. Existing signed runtime permission can last up to its original 30-minute/expiry deadline if offline. Renewal does not re-enable disabled licenses; issue a new code if needed.

Rollback: stop the unit, restore the previous code directory/version while retaining the protected persistent directory and restart. Existing license records are preserved by the compatible schema migration documented below. Keep 8088 bound to 127.0.0.1 during rollback. Confirm NTP and `https://license.aispacekit.com/health` from the client machine.

## First installation commands (after SSH/preflight succeeds)
Use these commands for a new installation only. Upload only the license_service source directory to a staging directory first.
```sh
cat /etc/os-release
sudo ss -lntp
sudo ufw status verbose
timedatectl status
sudo systemctl list-units --type=service --state=running --no-pager
# Stop if either target directory or service belongs to an existing installation.
sudo test ! -e /opt/aispace-license
sudo test ! -e /var/lib/aispace-license
sudo useradd --system --user-group --home-dir /var/lib/aispace-license --shell /usr/sbin/nologin aispace-license
sudo install -d -m 0755 /opt/aispace-license
sudo install -d -m 0700 -o aispace-license -g aispace-license /var/lib/aispace-license
sudo cp -R ~/aispace-license-stage/license_service /opt/aispace-license/
sudo python3 -m venv /opt/aispace-license/venv
sudo /opt/aispace-license/venv/bin/pip install -r /opt/aispace-license/license_service/requirements.txt
cd /opt/aispace-license
sudo -u aispace-license venv/bin/python -m license_service.cli init
sudo install -m 0644 license_service/aispace-license.service /etc/systemd/system/aispace-license.service
sudo systemctl daemon-reload
sudo systemctl enable --now aispace-license
curl --fail http://127.0.0.1:8088/health
sudo systemctl is-enabled aispace-license
sudo systemctl is-active aispace-license
```
If python3-venv is unavailable, install that OS package after inspecting the OS. Preserve SSH access. Expose the HTTPS reverse proxy on TCP443; keep 8088 and 8089 local-only. Do not overwrite pre-existing directories or keys.

## HTTPS reverse proxy

The client endpoint is `https://license.aispacekit.com`, proxied to `127.0.0.1:8088`.
The administrator endpoint is `https://admin.aispacekit.com`, proxied to `127.0.0.1:8089`.
Install Nginx and provision a certificate covering both names at `/etc/nginx/ssl/aispace/certificate.pem` and its root-readable private key at `/etc/nginx/ssl/aispace/private.key` (mode 0600). Certificate contents, private keys, passwords and databases must remain outside Git.
For Cloudflare Origin CA, enable proxying for both DNS records, use Full (strict), and enable Always Use HTTPS. Back up any existing virtual-host configurations before installing these templates. From `/opt/aispace-license`:

```sh
sudo install -m 0644 license_service/license-https.conf /etc/nginx/conf.d/license-https.conf
sudo install -m 0644 license_service/admin-https.conf /etc/nginx/conf.d/admin-https.conf
sudo nginx -t && sudo systemctl reload nginx
curl --fail https://license.aispacekit.com/health
```

The administrator service must be installed and configured as described below. Verify that its HTTPS login page loads and unauthenticated `/api/licenses` requests return 401. If proxy validation fails, restore the backed-up configuration, run `nginx -t` and reload; do not restore an old database or reopen 8088.

## Client platform identity and runtime checks
Windows keeps the existing MachineGuid digest unchanged. macOS uses IOPlatformUUID; Linux uses /etc/machine-id with /var/lib/dbus/machine-id as fallback. Only application-specific hashes are sent; failed reads never produce a shared default. Successful identity reads are cached for the process.
Elapsed clocks compare suspend-inclusive and awake time: Windows GetTickCount64/QueryUnbiasedInterruptTime, Linux CLOCK_BOOTTIME/CLOCK_MONOTONIC, macOS mach_continuous_time/mach_absolute_time. Calendar adjustments do not extend authorization.
Network validation is serialized separately from cached-state access. Async business checks after resume reject new operations and schedule revalidation without blocking the event loop; old pre-suspend responses cannot restore permission.
Validation: Windows/Linux native identity and clock smoke passed; macOS covered through injected API tests only, pending native hardware verification. Focused P1/P2 plus authorization/DM regressions: 61 passed.

## Administrator web over HTTPS

The separate `aispace-license-admin.service` serves only `127.0.0.1:8089`.
Nginx serves `https://admin.aispacekit.com` and proxies to this local port. The public license application has no administrator routes. Both use the same Store and database.
Set or reset the single administrator password locally (hidden interactive prompts):

```sh
cd /opt/aispace-license
sudo -u aispace-license venv/bin/python -m license_service.cli admin-password
sudo systemctl restart aispace-license-admin
```

No default password exists. Passwords require at least 8 characters and use salted scrypt; password changes invalidate existing sessions. The single administrator signs in with only a password, without a username.
Sessions last eight hours, are memory-only, and expire on service restart. Cookies are host-only with Secure, HttpOnly and SameSite=strict. Mutation requests require a session CSRF token.
Creation request IDs persist without retaining plaintext codes. A repeated request is rejected with instructions to check the list; it never creates another license.

Open `https://admin.aispacekit.com` and use the existing administrator password.
Deploy `license_service/admin-https.conf` to `/etc/nginx/conf.d/admin-https.conf` after installing the matching certificate and private key at the paths specified in that file. Run `sudo nginx -t` before reloading Nginx. Keep port 8089 bound to loopback; no public 8089 rule is needed. Cloudflare must proxy the `admin` DNS record and use Full (strict) TLS.
The `license.aispacekit.com` client endpoint remains separate. Back up the administrator code and any existing Nginx virtual-host configuration before deployment; rollback restores those files and restarts only the administrator service, without restoring or replacing the live database or password file.

```sh
sudo systemctl start aispace-license-admin
sudo systemctl stop aispace-license-admin
sudo journalctl -u aispace-license-admin -n 100 --no-pager
```

Before deployment, back up code and use SQLite's backup API for a consistent database backup.
Migration adds a non-null empty-default `note` column and an `admin_requests` table, retaining old records and credentials.
Rollback: stop the administrator unit and restore prior public code. The original public activation/check routes remain compatible with the added column. The old CLI create uses positional INSERT and is not compatible: retain the new explicit-column INSERT in core.py if creation via old CLI is required, or restore the database backup only if no post-backup writes need preservation. Do not overwrite signing.pem. Avoid database restore that discards subsequent licensing activity.
