# Process and results

[Home](Home.md) · [Configuration](Configuration.md) · [Notifications](Notifications.md) · [Development](Development.md)

This page explains the work sequence and two concrete Python corrections. It is not a live release-status page. The [complete source-linked guide](https://github.com/victron-venus/inverter-dashboard/blob/main/docs/process-and-results.md) contains editable diagrams and implementation references.

## Workflow, not completion status

![Source changes pass through tests and review, official UI adoption if needed, backend release verification and separate installation checks.](https://raw.githubusercontent.com/victron-venus/inverter-dashboard/main/docs/assets/update-process.svg)

Start with a reproducible behavior and identify its owner. Test and review Python changes with isolated clients; shared UI changes belong in Vue. For a UI change, verify and adopt the official Vue artifact before building the backend release. Preserve exact bytes, inventory and source provenance.

A merged source change does not establish that a full release was published. A verified package does not establish that a particular installation runs it. At deployment, check the selected package/image, served UI, startup, fresh telemetry and preserved settings separately. Preserve Web Push identity and subscriptions as well as configured Home devices. Read-only acceptance must not operate equipment or acknowledge alarms.

## Results grounded in the implementation

**MQTT CLI precedence:** explicit host and port arguments now survive the saved-settings stage of startup, including an intentionally empty host. The lifecycle regression follows real argument parsing and startup with mocked source initialization. Omitted arguments still permit saved/default values. This proves startup precedence, not real broker connectivity; IGW and HA require separate isolation for an offline preview.

**Home dictionary documentation:** `HA_SWITCH_ENTITIES` is a dictionary keyed by stable Home button names. Entity strings, pairs and configuration objects are values within that dictionary, not alternative top-level containers. Empty `{}` means no Home buttons. Integer `order` controls sorting, with declaration order retained for equal ranks. The correction documents existing parser behavior rather than adding support for a list.

## Why a reconnect does not make an old event new

![Synthetic event at 11:00 and receipt at 12:15: source age is 75 minutes; receipt-based age zero is misleading.](https://raw.githubusercontent.com/victron-venus/inverter-dashboard/main/docs/assets/event-time-comparison.svg)

This is a synthetic same-day, same-time-zone example: **12:15 minus 11:00 is 75 minutes**. Using the 12:15 receipt time instead gives zero and incorrectly implies “just now”. Source occurrence time stays authoritative; unknown event time stays unknown.

A banner may include an inactive, unacknowledged warning. Push delivery has a separate 300-second event lifetime and silent initial/reconnect priming, so this 75-minute-old example is not a new push event. Local dismissal, device acknowledgement, provider acceptance and actual OS display remain distinct outcomes.

## Evidence limits

Local regressions and diagram/link checks do not establish hardware operation, browser acceptance or OS notification display. No benchmark or completed deployment is claimed here. Use the exact source and artifact identities, and state which acceptance stages were observed. See [Troubleshooting](Troubleshooting.md) before interpreting an absent banner or unavailable control.
