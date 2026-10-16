# Architecture and ownership

[README](../README.md) · [Configuration](configuration.md) · [Notifications](notifications.md)

![Component boundaries and current-source command path](assets/architecture.svg)

## One selected native source

FastAPI owns an asynchronous native connection and broadcasts a normalized state over `/ws`; authorized `/api/state` uses the same filtered payload for HTTP fallback. The MQTT path and IGW snapshot path feed the same native reducer. The selected portal and connection generation bind data and commands to one source. A failover retires the prior source, clears live authority and cancels source-owned pending work. Late callbacks cannot retarget a replacement client, even if the transport changes from MQTT to IGW and back.

The source modules are [server.py](../src/inverter_dashboard/server.py), [gateway.py](../src/inverter_dashboard/gateway.py), [cerbo.py](../src/inverter_dashboard/cerbo.py) and [websocket_handler.py](../src/inverter_dashboard/websocket_handler.py). Their regression tests cover teardown, retained data, malformed leaves, source switches during HTTP entry and already-completed asynchronous work.

## Each field has an owner

Native Cerbo services own grid/consumption, batteries, solar, Water, EV and AC-load readings. Controller messages own policy flags, history, forecasts, daily statistics and supported command metadata. Optional direct HA polling owns only configured HA devices and appliances. HA cannot overwrite controller or native authority fields.

MQTT receives controller data through `inverter/state` and dedicated status topics. Current IGW snapshots can include the controller object as `inverter`; an explicit null clears it, while omission from older producers has separate compatibility behavior. Do not infer capability from the presence of a similarly named HA entity.

Native nulls, device disconnection and service removal invalidate affected readings. Measured zero remains zero. Main battery percentage is currently derived from the selected native voltage using the Desktop-aligned voltage curve; actual BMS/shunt SoC remains on device tiles and supplies low-SoC push detection. These are intentionally different quantities. Loads retain device-instance identity independently of labels. Daily grid energy retains source coverage and partial-day reasons; the web service does not fill missing hours with invented samples.

## Freshness is not one timestamp

`telemetry.observed_at` is a local receipt time in epoch milliseconds with `timestamp_source=local_receipt`. Overall native quality becomes stale after 120 seconds or disconnect. It does not establish the sampling time of every sensor. Controller display state also has a separate expiry.

Control authority is tighter: current nonretained controller observations and relevant native/HA observations must be fresh within 30 seconds. Unsupported, future, malformed or retained authority cannot enable a write. Invalid current pump Mode revokes its previous write authority immediately; a later regular state broadcast updates the visual control. Original alert occurrence time is retained separately from all connection and receipt times.

## Commands and acknowledgements

The browser sends a request ID for correlated feedback. Backend authorization, target allowlists, current source/client identity, capability and freshness are checked at dispatch. MQTT physical writes are nonretained with QoS 0; IGW credentials attach only to a validated verified HTTPS origin without redirects. No automatic resend or fallback to another transport follows an uncertain command.

ESS exposes six explicit modes when supported. Native Water Mode uses the configured pump instance, not an HA mirror. Generic `command_result` with `status=accepted` means the backend accepted/dispatched the request; it is not confirmation of physical completion. Displayed state follows telemetry.

Persistent Setpoint Override is executed by inverter-control, not by a repeating dashboard timer. Start/change/stop share one five-second deadline over gateway preflight, write and acknowledgement reads. Stop sends `null`. Confirmation requires a subsequent valid current-source observation with matching request ID, exact value and no error; an old cached matching ID is insufficient. Override intentionally remains independent of DRY mode.

Tariff editing uses a controller revision for compare-and-set behavior and authoritative request correlation. A competing revision must be reloaded rather than overwritten silently. Generic local settings are different: they are private JSON preferences stored by this web service.

Local notification dismissal affects the dashboard view; explicit native acknowledgement/silence is a separately guarded device command. Receiving or clicking Web Push never acknowledges an alarm or controls a device.

## HTTP surface

- `GET /health/live`: lightweight process liveness; no device access.
- `GET /`: bundled Vue application, authenticated if a secret is configured.
- `GET /api/state`: connectivity/version health, plus authorized live state; no-store.
- `GET/POST /api/settings`: masked preferences and validated JSON changes.
- `POST /api/check-update`, `POST /api/update`: existing update interface; self-update is disabled by default.
- `/api/notifications/*`: opt-in subscription/status/test interface, described in [Notifications](notifications.md).
- `/ws`: current state and controlled commands; browser origin and token checks apply.

Only the existing asset routes and three narrow notification resources are public static content. They must never serve configuration or private state. Reverse proxies must preserve Host and support WebSocket upgrades. No source-generation check can compensate for exposing an unauthenticated deployment.
