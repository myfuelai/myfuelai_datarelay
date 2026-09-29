# MyFuel.AI Connector

A Windows service that runs on a customer's server and relays data between on-site systems and
MyFuel.AI. Today it syncs **QuickBooks Desktop** through the QuickBooks SDK (QBXMLRP2). This is the
Windows-service alternative to QuickBooks Web Connector.

- **The MyFuel database decides what runs.** The service asks MyFuel every tick. QuickBooks only
  syncs when the site's QB Web integration is active and has `SYNC_QB_Transport = Service`, and the
  sync window is open. Nothing is switched on or off locally.
- **The sync logic lives in the MyFuel API**, and it's the same code Web Connector uses. The service
  opens QuickBooks, passes qbXML back and forth, and always closes QuickBooks again.
- **QuickBooks is only open during a sync.** It's launched hidden (no window), and the integration
  user is logged out as soon as the run ends. See [Never left logged in](#never-left-logged-in).

PDI and SmartTank are also in this repo (`relay/integrations/pdi`, `relay/integrations/smarttank`),
but they're **commented out** in `relay/main.py`. They're believed unused and are kept so they can
come back.

## Layout

```
connector.py                 PyInstaller entry -> relay.main
relay/main.py                entry point: service / `run`; which integrations are registered
relay/service.py             native Windows service (pywin32) - replaces NSSM
relay/core/                  settings + secrets, logging, scheduler, MyFuel client, Sentry
relay/integrations/quickbooks/
    runner.py                every tick: ask MyFuel, run the worker, verify QuickBooks exited
    worker.py                one sync run in a child process (open -> relay -> always close)
    qbxmlrp2.py              QBXMLRP2 COM wrapper (ctLocalQBD, multi-user only)
    processes.py             finds *our* QuickBooks process - never another user's
    authorize.py             `authorize` command - one-time app authorization by a QuickBooks Admin
relay/integrations/pdi/      (commented out)
relay/integrations/smarttank/ (commented out)
tools/encrypt_secrets.py     builds the .env holding the encrypted secrets
MyfuelaiConnector.spec, build.ps1   build (onedir), signing, hashes
tests/test_quickbooks.py     worker/runner tests with a fake QuickBooks and a fake MyFuel
```

## How a QuickBooks sync runs

Every `interval_seconds` (60 by default), the service does the following:

1. It calls `POST /v1/qb-service/session/start/`. MyFuel returns `run:false` unless all of these are
   true:
   - the QB Web integration is active and primary;
   - `SYNC_QB_Transport` is `Service`;
   - `SYNC_QB_FullFilepath` is set;
   - the sync window is open (`SYNC_Sleep_Mins`, `SYNC_Next_RunDateTime`, `SYNC_Service_Window`).

   Most ticks stop here, and QuickBooks isn't touched.
2. When `run` is `true`, a **worker process** does the sync:
   - `OpenConnection2` (ctLocalQBD, so no QuickBooks window);
   - `BeginSession(file, qbFileOpenMultiUser)`, never single-user;
   - the **own-instance check** (below);
   - then a loop: `session/next` returns qbXML, `ProcessRequest` sends it to QuickBooks, and
     `session/response` returns the result to MyFuel. This repeats until MyFuel says `done`, or
     `SYNC_QB_MaxSessionRequests` or `SYNC_QB_MaxSessionMins` is reached.
   - Finally: `EndSession`, `CloseConnection`, release COM, then `session/end`.
3. The service waits for the worker to exit. Then it checks that the QuickBooks process the worker
   launched has exited within `SYNC_QB_CloseWaitSecs`.

### Never left logged in

These layers prevent the problem Web Connector has, where the user stays logged in and automatic
backups are blocked:

1. **One short session per run.** Nothing is kept open between runs, and QuickBooks is never
   launched when there's nothing to sync.
2. **Closing always runs.** `EndSession`, `CloseConnection` and COM release are in a `finally`, on
   every exit path: done, limit reached, COM error, MyFuel unreachable, service stopping, refused
   attach, or any exception.
3. **Each run is its own process.** A worker that hangs past `SYNC_QB_MaxSessionMins` + 2 minutes
   is terminated (`QBWorkerTimeout`). When a process dies, Windows drops its COM connection.
4. **QuickBooks must exit afterwards.** If our QuickBooks process is still running
   `SYNC_QB_CloseWaitSecs` after closing, the service logs `QBCloseVerifyFailed` and alerts Sentry.
   It never force-kills QuickBooks, because that can damage the company file.
5. **Stay clear of backups.** Set `SYNC_Service_Window` so it excludes the online-backup time.

### Terminal servers: never another user's QuickBooks

- The service runs in session 0 under its own Windows account. It only accepts a QuickBooks process
  owned by **that account in that session**.
- The worker compares the service account's own QuickBooks processes before and after
  `BeginSession`. If the connection isn't to one of them, it sends **no** qbXML, disconnects, and logs
  `QBAttachRefused`.
- Other users' QuickBooks processes are never checked, waited on, or touched.
- While a sync runs, the service's QuickBooks takes one QuickBooks user seat, logged in as
  `SYNC_QB_Username`. If no seat is free, or someone is logged in as that user, `BeginSession` fails.
  The service logs `QBSessionError`, closes cleanly, and tries again in the next window.

## Logs

| Where | What |
|---|---|
| `logs\service.log` | startup, settings, which integrations are registered |
| `logs\quickbooks.log` | every sync run, including the worker process (sent to the service process) |
| `logs\pdi.log`, `logs\smarttank.log` | only if those modules are re-enabled |
| MyFuel integration logs table | every qbXML request/response (written by the API, same as Web Connector), plus the service events: `SyncSessionStart` (with `transport=Service`), `QBSessionOpened`, `QBSessionClosed`, `QBSessionError`, `QBAttachRefused`, `QBWorkerTimeout`, `QBCloseVerifyFailed` |

Log files rotate at 10 MB and keep 10 copies. Service start and stop failures also go to the Windows
Event Viewer (Application log, source `MyfuelaiConnector`).

## Settings

**In MyFuel** (QB Web integration settings; seeded by
`myfuelai_api/dev_docs/sql/externalintegration/02_qb_windows_service_setting_types.sql`):

| Setting | Values |
|---|---|
| `SYNC_QB_Transport` | `WebConnector` or `Service` |
| `SYNC_QB_FullFilepath` | the company file |
| `SYNC_QB_MaxSessionMins` | default 20 |
| `SYNC_QB_MaxSessionRequests` | default 2000 |
| `SYNC_QB_CloseWaitSecs` | default 60 |
| `SYNC_QB_Username` | the QuickBooks "Login as" user |
| Scheduling | `SYNC_Sleep_Mins`, `SYNC_Next_RunDateTime`, `SYNC_Service_Window` (shared with Web Connector) |

`SYNC_QB_Password` isn't used by the service, because QBXMLRP2 has no login parameter. QuickBooks
logs in as the user chosen when the app was authorized.

A site runs Web Connector **or** the service, never both. Starting a session ends whichever session
is current, which is why `start` refuses to run while `SYNC_QB_Transport` isn't `Service`.

**Locally**, next to `MyfuelaiConnector.exe`. Both files are read at startup, so changing either one
needs a service restart, not a new build.
- **`config\relay.json`** (required): a plain JSON file; copy `config/relay.example.json`.
  - `myfuel_base_url` is **required**: the site's MyFuel API, e.g. `https://<site>-api.myfuel.ai`.
    There's no default, so the service won't start without it and can never fall back to another
    customer's API.
  - `quickbooks.app_name` is optional. It's the name QuickBooks shows when authorizing the app; keep
    it the same after authorizing.
  - `quickbooks.app_id` and `quickbooks.interval_seconds` (default 60) are optional.
- **`.env`** (required): `RELAY_ENCRYPTION_KEY` + `ENCRYPTED_BLOB`, created with
  `tools/encrypt_secrets.py`. The blob holds `REMOTE_AUTH_TOKEN`, and optionally `SENTRY_DSN` and
  `ENV`. The token belongs to the MyFuel environment in `myfuel_base_url`, so if you point a site
  at a different API, give it that environment's token too.

## Build

```powershell
.\build.ps1                                   # dist\MyfuelaiConnector\ + dist\MyfuelaiConnector-hashes.txt
.\build.ps1 -CertThumbprint <thumbprint>      # also Authenticode-signs every exe/dll/pyd not already signed
.\build.ps1 -Python 'py -3.12-32'             # 32-bit build, if QuickBooks' SDK is only registered 32-bit
```

The build is PyInstaller **onedir**, not onefile. A onefile exe unpacks its DLLs to a random
`%TEMP%\_MEI*` folder on every start, and ThreatLocker-style tools block those files. With onedir,
every file stays in the install folder.

**ThreatLocker.** Hashes change with every build. The better option is to sign with our
code-signing certificate, so the client allows the certificate once. Otherwise, send them
`MyfuelaiConnector-hashes.txt` with each release. Ask their ThreatLocker admin whether they allow by
certificate, hash or path, and whether ringfencing needs rules for the service to launch QuickBooks
and reach the MyFuel API. The worker process is the same exe, so there's no second program to
approve.

## Install on a site (QuickBooks)

Run all steps as Administrator on the QuickBooks machine (the terminal server).

1. **QuickBooks SDK.** Make sure the QuickBooks SDK request processor is installed. It comes with
   QuickBooks; otherwise install the QuickBooks SDK.
2. **Windows account.** Create a named Windows account for the service. Don't use LocalSystem.
3. **Authorize the app in QuickBooks (one time).** QuickBooks has to accept the app (named by
   `app_name`) with:
   - **"Yes, always; allow access even if QuickBooks is not running"**;
   - **Login as:** the user in `SYNC_QB_Username`.

   To trigger the prompt, log in to QuickBooks as Admin with the company file open, then run
   `MyfuelaiConnector.exe authorize` in that same Windows session. It connects to that QuickBooks
   (on purpose, unlike the service) and sends one read-only `HostQuery`. Confirm afterwards under
   Edit > Preferences > Integrated Applications (Company Preferences). **Confirm in the proof of
   concept** that an authorization made this way also applies when the service connects
   unattended as the service account.
4. **Copy the files.** Copy `dist\MyfuelaiConnector\` to e.g. `C:\Program Files\MyFuel\Connector\`,
   then add `config\relay.json` (with this site's `myfuel_base_url`) and the `.env`. Restrict the
   `.env` ACL to Administrators and the service account.
5. **Install and start the service:**
   ```powershell
   .\MyfuelaiConnector.exe --username .\svc_myfuel --password <pw> --startup auto install
   .\MyfuelaiConnector.exe start
   ```
   `install` also sets Windows recovery to restart the service 60 s after a crash. Other commands:
   `stop`, `restart`, `remove`, `update` (after replacing the files).
6. **Switch the site in MyFuel.** Set the site's `SYNC_QB_Transport` to `Service`, and turn off Web
   Connector's scheduled sync for that company file.

**Stopping the service** during a sync lets the current QuickBooks request finish, then closes
QuickBooks cleanly before the service reports stopped. That takes at most about 3 minutes.

**Upgrading a site that ran the old NSSM service:** run `nssm stop MyfuelaiConnector` and
`nssm remove MyfuelaiConnector confirm` first, then install as above.

## Develop and test

```powershell
py -3.12 -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m unittest discover -s tests -p "test_*.py" -v
.\.venv\Scripts\python -m relay run        # console mode, Ctrl+C to stop (needs .env)
```

The tests use a fake QuickBooks and a fake MyFuel, so they don't need the QuickBooks SDK. The rest of
`tests/` is older scratch material.
