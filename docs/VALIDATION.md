# Validation and hardware acceptance

## Checks completed while creating this repository

The delivered code was tested with Python 3.12 on the available Linux execution host. The physical SparkFun board was not connected, and Fedora itself was not available for a local execution run. GitHub Actions contains a Fedora container job and multiple Python versions for validation after you push; those remote jobs have not yet run.

On 11 October 2026, all **65 automated tests passed**. The standard Python wheel build also completed successfully with no runtime dependencies. The shell installer passed bash syntax validation, and the user installer was exercised in an isolated temporary home directory with its generated launcher verified.

Automated checks cover:

- Independent known UBX MON-VER poll bytes and a reference NMEA GGA checksum/coordinate vector.
- Every split boundary of a mixed UBX/NMEA stream and one-byte incremental delivery.
- Checksum corruption/recovery, noise, partial sync, frame length limits and incomplete-frame timeout.
- Navigation/UTC/fix/invalid-LLH flags, units, constellation/RF offsets and truncated/unsupported payloads.
- Stale/frozen epochs, no-fix and RF alarms, absent diagnostics, unexpected model and receiver errors.
- Read-only poll construction, passive mode, timeout/recovery and rejected-poll handling.
- Actual Linux PTY transport and CLI subprocess: healthy/no-fix/RF/silent/disconnected/NMEA-only streams, JSON reports, raw evidence and exit codes.
- Replay, output path conflicts, terminal text escaping, numeric validation and auto-selection ambiguity.

The fixtures are synthetic and do not establish the behavior of your physical device. A PASS means all implemented checks had the expected evidence during the observation window; it is not a full electrical/RF manufacturing test.

## Physical-board acceptance on Fedora

| Step | Command/action | Expected evidence |
| --- | --- | --- |
| 1 | Connect native USB with a data cable | u-blox appears in USB inventory and a tty is created |
| 2 | `m9n doctor` | Selected tty accessible; inspect any port-owning services |
| 3 | `m9n test --duration 120 --report reports/test.json` outdoors | MON-VER identifies NEO-M9N, navigation epochs advance, fix acquired and diagnostics present |
| 4 | `m9n watch --duration 30 --json` | Multiple current coordinate/fix/epoch observations, clean stream counters |
| 5 | Move antenna indoors or shield sky view without damaging it | Communication continues; fix availability/signal quality may degrade |
| 6 | Unplug during an active timed test | Explicit USB transport FAIL with final report |
| 7 | Start another CLI while first owns tty | Actionable device-busy failure |
| 8 | Capture raw/JSON/debug and replay | Raw frames decode, source clearly labelled as recorded data |

Create `reports/` before writing there. Stop only services actually using this receiver. Record software, hardware and PROTVER/MOD extensions with the acceptance result. If a firmware does not return an optional diagnostic, expect WARN and inspect the message-specific evidence rather than forcing a PASS.
