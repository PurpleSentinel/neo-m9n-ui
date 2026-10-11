# CLI reference

Run `m9n COMMAND --help` for the authoritative options.

| Command | Purpose | Key options |
| --- | --- | --- |
| `list` | Enumerate ttyACM/ttyUSB devices and USB identities | `--json` |
| `doctor` | Read-only host diagnostics; does not open/test GNSS protocol | `--port`, `--json` |
| `watch` | Live display/JSON until Ctrl-C or duration | Common options below |
| `test` | Timed diagnostics and final PASS/WARN/FAIL report | `--duration` (default 60), `--report`, common options |
| `demo` | Synthetic live view | `--duration` (default 10), `--fault`, display/log options |
| `replay FILE` | Decode raw RX capture as a batch, emit final snapshot | Display/log options |

Options belong after the command, for example `m9n watch --debug`.

## watch/test options

| Option | Default | Meaning |
| --- | --- | --- |
| `--port PATH` | Auto-select single VID 1546 tty | Use tty path or persistent by-id symlink |
| `--baud N` | 9600 | termios line coding, primarily for UART bridges; no UART configuration command |
| `--passive` | Off | Read without writing polls |
| `--duration SECONDS` | watch: 0; test: 60 | 0 is indefinite for watch; test must be positive |
| `--interval SECONDS` | 1 | Snapshot/display interval; separate from GNSS update rate |
| `--stale-after SECONDS` | 5 | Navigation receive/epoch timeout and satellite/status freshness |
| `--json` | Off | JSONL snapshots on watch; one final JSON report on test |
| `--plain` | Auto based on TTY | Avoid screen redraw |
| `--debug` | Off | RX/TX hex and parsed messages; stderr on plain view or `--log-file` |
| `--log-file FILE` | None | Append application logs; combine with `--debug` for full traces |
| `--json-log FILE` | None | Append snapshots and the final test report as JSONL |
| `--raw-log FILE` | None | Exclusive new file of exact RX bytes; no TX bytes or timing header |
| `--report FILE` | None; test only | Final structured JSON report, overwrites existing report |

For interactive redraw, routine console logs are suppressed to avoid damaging the view; warnings still appear in the view, and a log file retains the requested verbosity. Output parent directories must exist. File errors are explicit. Output paths must differ from each other and from a replay input or explicitly selected device.

`replay` decodes the capture as a batch and emits a final snapshot. Raw capture has no per-read timestamps, so replay cannot reproduce original timing, original TTFF or original stale-data intervals. Its sample ages describe this decode pass and its `source` is always labelled REPLAY. It sends no commands to hardware. A capture ending mid-frame records a truncation error in the resulting snapshot.

`demo --fault` accepts `no-fix`, `rf`, `corrupt`, `frozen` and `silent`. The standalone PTY simulator additionally supports `unplug`. Demo can use `--json`, `--plain`, `--interval`, `--stale-after`, `--debug`, `--log-file` and `--json-log`.

`doctor` exits 0 when its selected tty is accessible to the current user, otherwise 1. This is an access/inventory check, not proof that the device communicates or has a fix. `watch` exits 1 on transport failure or a completed silent/invalid-only session; its normal completion does not perform the `test` health assessment. `replay` exits 1 for empty captures or captures without navigation; protocol error counters should also be inspected.
