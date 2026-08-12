# kemo app bridge expand

`kemo_app` is the global expand module that runs the Android App bridge for
kemo-agent. It exposes the App-facing HTTP, SSE and WebSocket API on a separate
port while forwarding authorized operations to the framework Web API.

Current bridge version: **1.1.2**.

## Android device actions

The control entry exposes one structured `device_action` command instead of separate
server endpoints for every phone feature. The bridge validates, queues and routes only
App-declared actions to one authenticated `user + device_id` WebSocket connection.
The current Android client implements `alarm.create`, `timer.start`,
`calendar.event.create` and `todo.create`; arbitrary intents and shell commands are never
accepted. Commands carry an idempotency id and expiry time, and device acknowledgements
are persisted in ignored local runtime state.

## Default lifecycle

The source tree is deliberately published in an **uninitialized and inactive**
state. `open_input` is false, no successful update timestamp is committed, and
the status document contains no host, upstream, user, device or connection
information. Merely cloning or updating kemo-agent never starts this service.

After an installation has been explicitly activated, ordinary framework
updates preserve that local activation choice even if credential readiness
cannot be validated while update files are being copied. A fresh installation
remains inactive, and an explicit `deactivate` choice remains inactive. A
plain `stop` only ends the current process while preserving the operator's
activation intent.
Local configuration and credential files are never replaced by the updater.

An explicit successful `start`/`activate` also records deployment-local
activation intent in `_activated.json`. Once that marker exists, the periodic
collector may restore a configured bridge that is offline, including after a
framework or computer restart and after an unexpected bridge-process crash.
`deactivate` removes the marker, so an explicitly deactivated bridge stays
inactive. A plain `stop` only stops the current daemon and deliberately keeps
the marker; this is useful for maintenance and for verifying the next automatic
recovery cycle. Automatic launch attempts are spaced by at least 60 seconds and stop
after three consecutive failures until a manual `start` succeeds. These
failures remain isolated from the framework process and are reported through
the normal Expand status document.

`open_control` remains available only so an administrator can inspect the
initialization state and explicitly activate the bridge. The `start`/`activate`
commands refuse to launch a process until local configuration, a device-token
hash, a generated session secret and at least one enabled App user are present.

## Version 1.1.2 lifecycle contract

- A successful explicit `start`/`activate` stores an ignored local activation
  marker. The periodic collector may restore an unexpectedly stopped bridge
  after framework or host restart without putting startup in the core request
  path.
- `stop` ends the current daemon but preserves activation intent; `deactivate`
  ends the daemon and removes that intent. Automatic recovery is rate-limited,
  stops after three consecutive failures, and never blocks the main runtime.
- The published source tree remains uninitialized and inactive, contains no
  credentials or host status, and does not start a listener after clone/update.

## Version 1.1.1 contract

- Android App chat and conversation operations always use the dedicated
  `source=app` history partition; the device cannot select or impersonate
  another source.
- App runs, client leases, history windows, close/compress/delete operations,
  and memory processing are isolated from `source=web` even when a session ID
  is reused.
- The Web history drawer labels App archives as `APP版` and opens them read-only.
- Core updates refresh the tracked bridge code while retaining deployment-only
  configuration, credential records, activation state, logs and runtime data.

## Version 1.1.0 transport contract

- Streaming chat uses an SSE-specific transport with no response-body read
  deadline and emits a heartbeat every 15 seconds. Ordinary REST requests keep
  their configured bounded timeout.
- A running response can receive guidance or be cancelled without tying its
  lifetime to an individual Android screen.
- Conversation history supports listing, loading, deletion, closing,
  compression and undoing the last round.
- App uploads and framework-generated image, audio, video and ordinary file
  artifacts can be transferred through the bridge. A single App upload is
  limited to 80 MiB.
- WebSocket events expose online connection/device counts without exposing
  device tokens, session tokens or upstream credentials.
- Model discovery remains limited to the Kemo protocol; Chat-compatible model
  names are configured manually.

## Source and runtime boundary

The Python modules, expand manifest and control documentation are tracked here.
The following files are deliberately local runtime data and must not be copied
between installations or committed:

- `config.json` (device-token hash and optional upstream credentials)
- `users.json` (salted App-user password verifiers)
- `credential_registry.json` (generated credential audit snapshot)
- PID, lock, connection, runtime and log files

The deployed instance under `D:\kemo-agent\global_expand\kemo_app` remains a
runtime copy. Changes should be developed here and deployed explicitly; copying
the runtime credential files back into source is forbidden.

## Initial setup

```powershell
python initialize_config.py
python manage_device_token.py
python manage_user.py <username>
python credential_registry.py --check
python start_expand.py configuration_status
python start_expand.py start
```

`initialize_config.py` creates ignored local `config.json` and `users.json`
files and generates a random session secret. It does not activate or start the
bridge and does not create a device Token or user password on the operator's
behalf. Activation is therefore always explicit.

Verify the service with:

```powershell
Invoke-RestMethod http://127.0.0.1:8742/v1/health
python start_expand.py status
```

The App-facing protocol and operational commands are documented in
`expand_control.md`.

The authenticated App API includes `/v1/models/capabilities?model=...`, which
forwards the current user's Kemo model capability declaration without exposing
upstream credentials. The App uses its declared reasoning efforts instead of
guessing a fixed Kemo effort list.
