# CitrusPi performance investigation — October 28, 2026

Investigated the running Raspberry Pi over `ssh -A citruspi`, its SQLite records,
service/network/kernel logs, and the matching local application source. Both
checkouts were at `b66b9d1`. Diagnostics did not change the running application,
configuration, database records, or device settings. Times below are Eastern
Daylight Time unless a timestamp explicitly ends in `Z`.

## Findings

There are several separate causes: expensive concurrent history processing,
confirmed intermittent network loss, genuine failed device reads, exaggerated
chart gaps from downsampling, and a clock correction after boot. The observed
slow range changes were primarily a CPU/application issue. The measurements do
not establish an out-of-memory failure or a memory leak.

### 1. Range changes reproduce a 14–15 second delay without Wi-Fi

Requests sent to `127.0.0.1` on the Pi, one at a time:

| Selected range | Sensor history | Equipment summary / cost | Equipment history / activity |
| --- | ---: | ---: | ---: |
| 24 hours | 0.252 s | 0.267 s | 0.505 s |
| 7 days | 0.820 s | 1.730 s | 3.256 s |
| 30 days | 1.330 s | 2.362 s | 4.510 s |

The dashboard actually launches these together and fetches sensor history twice:
`dashboard.html:385–388` and `static/plugs.js:286–287`. Reproducing those four
requests concurrently took **15.495 seconds** for equipment history, with the
two sensor-history requests each taking about **13.8 seconds**. A second run
took **13.816 seconds** for equipment history and **12.6 seconds** for each
sensor-history request. These tests did not depend on the Wi-Fi connection.

During the second run, the service used roughly 160–206% CPU for much of the
request (about two CPU cores). Available RAM remained around 126 MiB. There was
a small initial swap-in and little continuing swap activity; no swap-out was
observed during that run. A lightweight `/api/current` request completed in
0.032 seconds during the first run, so the server was still responsive to small
requests.

The database was only 4.2 MiB, with approximately 19,081 sensor records and
27,023 equipment records at inspection. `PRAGMA quick_check` returned `ok`.
History responses were roughly 70–98 KB, so a huge response payload does not
explain the localhost delay.

Relevant work in `store.py`:

- History responses scan raw records and downsample them on every request.
- Each equipment-history request independently scans activity and power history
  for both plugs.
- Each equipment-summary request scans raw samples again for energy, runtime,
  and time-of-use cost. The browser repeats this request every 30 seconds,
  including for a 30-day selection.
- For one plug over 30 days, measured work was about 0.96 s for power history,
  1.26 s for activity, and 1.15 s for summary with electricity pricing. Summary
  without pricing was 0.51 s.
- Profiling power history showed much of its time in per-row decoding and gap
  processing. The timestamp indexes exist and are used. However, the history
  `COUNT(*) OVER ()` query also creates a temporary B-tree for ordering;
  the equivalent plain range query uses the timestamp index without that sort.

Requests have identifiers to discard obsolete results, but old requests are not
aborted when the user changes ranges. There is no application-level fetch
timeout. Rapid range changes can therefore continue doing obsolete work and
make recovery from a broken connection confusing.

### 2. Wi-Fi did drop, despite being connected during inspection

NetworkManager recorded loss of connectivity on **October 27 at 6:22:46 PM**,
followed by `ssid-not-found` and a disconnected state. It reported successful
activation again at **6:29:58 PM**, about seven minutes later. Further association
failures occurred around 6:31 PM.

At the same time, equipment polling logged `Network is unreachable` for both
plugs. This is direct evidence of a Pi-side network outage, rather than merely
a slow browser. At inspection the Pi reported **−71 dBm** signal and power saving
was reported enabled in the boot kernel log. Those observations alone do not
prove why the connection dropped. An additional diagnostic SSH connection also
closed during the investigation; its cause was not established.

### 3. Some gaps are real, and the charts exaggerate isolated failures

The database contains records with no canopy or root values from October 15
through much of October 21. The retained logs cannot establish the original
cause of all those older missing measurements.

After October 21, there were just two isolated missing canopy readings through
the inspected data, with no missing root readings during October 22–28:

- October 22, `2026-10-22T19:37:07Z` (3:37:07 PM).
- October 26, `2026-10-26T19:24:36Z` (3:24:36 PM).

The October 26 service log explicitly reports a BME280 reset/skipped measurement
error. The driver recovered on a later poll. This is a device-read failure;
the BME280 is read over local I²C, independently of Wi-Fi.

However, the 30-day history sampling retains a null measurement without
necessarily retaining the good samples immediately around it. In the sampled
response, the October 26 null is surrounded by good points at **12:43:17 PM**
and **3:42:51 PM**. With gaps between null chart points, that isolated failure
can therefore produce a visual gap spanning hours. The October 22 null similarly
had its next sampled good point at 6:15:24 PM.

Equipment records included **373 `unreachable` observations out of 27,023**
(about 1.4%), spread between heater and light. These are stored as unknown
activity/power, and create intentional gaps. Not every timeout can be attributed
to the Pi's Wi-Fi; a plug or its protocol can also fail independently.

Separately, sensor history does not insert explicit gap markers for entirely
absent timestamp intervals. Such collection pauses can be connected by chart
lines. Both null-boundary preservation and timestamp-outage handling need care.

### 4. The apparent October 25 one-hour gap aligns with clock synchronization

Sensor timestamps jump from `2026-10-25T17:51:26Z` to
`2026-10-25T18:54:26Z`. Both plugs show a similar apparent gap.

The service started with the Pi's pre-synchronization clock at about 1:50 PM.
`systemd-timesyncd` then logged its **initial clock synchronization to 2:53:49 PM**.
Thus this apparent gap aligns with a wall-clock correction after boot and should
not be interpreted as evidence that collection stopped for a full hour.
The service starts after `network-online.target`, but does not require successful
time synchronization before recording timestamps.

### 5. Memory is constrained, but was not the main measured bottleneck

At the initial idle inspection:

- Total RAM: 415 MiB; available RAM: approximately 119 MiB.
- Swap: approximately 125 MiB used, backed by compressed `/dev/zram0`.
- Service resident memory: approximately 143 MiB; recorded resident peak:
  approximately 219 MiB; swapped service pages: approximately 109 MiB.
- CPU was nearly idle with no active swapping in the sampled idle interval.
- Disk: 4.0 GiB used of 29 GiB; 24 GiB available.
- Temperature: 34.9 °C; `get_throttled` returned `0x0`.
- No out-of-memory kills or SD-card I/O errors found in retained kernel logs;
  systemd reported zero automatic service restarts since the current service
  start. Only the current boot, beginning October 25, was available in the
  journal.

Memory usage and compressed swap deserve attention, especially with live video,
but swap usage alone does not demonstrate current memory thrashing. This
investigation did not establish long-term memory growth, and did not benchmark
range changes while opening an additional live camera stream.

## Recommended implementation order

1. Share one sensor-history response across all charts. Coalesce equivalent
   in-flight requests and abort obsolete range requests. Add bounded fetch
   timeouts and a clear loading state.
2. Add a small bounded cache of history/activity/summary results, invalidated as
   new readings arrive. Separate inexpensive live watts/status refreshes from
   the expensive historical totals. Avoid recalculating a month of cost every
   30 seconds when its source records have not changed.
3. Remove the window-count ordering penalty and reduce repeated raw-row scans.
   For longer retention, consider maintained aggregates, with raw observations
   retained for accurate transitions, gaps, energy, and pricing boundaries.
4. Preserve the valid points immediately before and after a missing sensor
   value when downsampling. Mark genuine timestamp outages without treating
   sparse chart samples as outages. Keep output sizes bounded.
5. Require successful clock synchronization before persisting boot-time
   telemetry, with an explicit policy for offline startup. Do not rewrite old
   timestamps based on an assumed correction.
6. Investigate the confirmed network interruption with the access-point logs
   and Pi association events. Evaluate placement and power-saving behavior
   before changing network settings. The current evidence does not identify
   whether the router, radio conditions, or Pi power saving caused the outage.

Re-measure the same concurrent dashboard requests after changes, alongside CPU,
available memory, swap activity, response sizes, and verification that chart
gaps still match the underlying records. No application fixes or deployments
were performed as part of this diagnostic investigation.

## Follow-up optimization — October 28, 2026

After the user requested a focused optimization, the dashboard was changed to
share its sensor-history promise with equipment charts, including the deferred
script's initial load. A range refresh now makes three history-related requests
instead of four. A 12-hour option was added to the range selector and remembered
browser preference, along with a pending-history loading message.

Validation passed: 45 JavaScript dashboard/equipment checks and 19 Python web
checks. The two dashboard files were deployed directly to the Pi after verifying
their original contents. No service restart was needed. Original files were
saved under `/tmp/citruspi-dashboard-backup-20261028-nk6u989n` on the Pi.

Live HTTP checks confirmed the new page, versioned JavaScript, and 12-hour API
response. The revised three-request 30-day refresh measured 6.964 s for equipment
summary, 8.975 s for sensor history, and 10.311 s for equipment history, versus
the previous four-request refresh completing in 13.816–15.495 s. This is a small
number of measurements, rather than a latency guarantee. The most recent sensor
reading was 57 seconds old after the benchmark, consistent with normal polling.
The remaining network, gap-rendering, and raw-history processing findings above
still apply.
