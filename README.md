# GaugeTrigger: Did the Measured Threshold Really Hold? Several Sensors, One Receipt

**Category:** Intelligent Contracts (standalone primitive)
**Field:** climate and hydrology (flood, drought, heat, air quality)
**Files:** `gaugetrigger.py` (contract), `test_flow.py` (call-sequence walkthrough), three sample CSV files for Studio testing

## The problem

Farm cover, relief funds, event insurance and community alerts are meant to
act on a **measured** condition: "river above 4.5 m for 6 readings in a row",
"rain below 2 mm for 20 days". Today that decision is a person reading a
chart, or one sensor feed trusted blindly. A single sensor can be down,
faulty or edited, and a decision resting on it is only as honest as it is.

## The primitive

A trigger is registered with up to 5 public data URLs, a comparison (above or
below), a threshold, a required run of consecutive readings, a maximum gap
between readings, a fixed UTC window and a quorum **K**. Anyone can request
the evaluation. The contract fetches every series itself, reads the numbers in
code, and rules **triggered** only if at least K stations each hold the
condition for the required run inside the window.

Each station's result carries a SHA-256 digest of the exact readings used, so
anyone can download the same file and recompute the same answer.
`get_status(trigger_id)` returns `unevaluated`, `triggered` or
`not_triggered`.

## Why there is no LLM call

The evidence is numbers. Counting readings above a line is a job for code, and
a model would only add disagreement. The intelligence is in the rules around
the numbers: what counts as a run, what counts as covered, what a missing
station means, and how several stations combine.

## Consensus design: exact recomputation

Every validator fetches every series itself, runs the same pure function, and
the whole result must be **identical** field by field (`strict_eq`): verdict,
per-station coverage, longest run, run start and end, extreme reading and
window digest. The stored ruling is exactly what every validator computed. If a
station serves different numbers to different fetches, no ruling is written.

## Evidence rules

- **Decimal arithmetic only.** No floating point, so no NaN or infinity.
  `nan` and `inf` text is ignored.
- **Coverage.** A station counts only if it has a reading at or before the
  window start and at or after the window end. A trigger can never be ruled
  before its window is complete, and an unfinished feed cannot rule
  "not triggered".
- **No silent choosing.** One minute carrying two different values makes the
  station unusable (`conflict`).
- **Gaps break runs.** If consecutive readings are further apart than
  `max_gap_minutes`, the run restarts. Missing data cannot be bridged.
- **Dead stations are neutral.** An unreachable station is `unreachable`. It
  is not a "no" and not a "yes". If fewer than K stations cover the window the
  evaluation is refused (`INSUFFICIENT_DATA`), nothing is written, and it can be
  retried later. Taking a station offline can never create a final
  "not_triggered".
- Readings outside the window do not affect the result or the digest.

## Replay rule

One ruling per trigger, and it is final. The window is in the past and the
evidence is recomputable, so a second run could only repeat the answer or
re-roll an unlucky fetch.

## State

| Map | Key | Value |
|---|---|---|
| `triggers` | `trigger_id` | rules, window, quorum, station URLs |
| `rulings` | `trigger_id` | verdict, counts, per-station result with digest |

`total_triggers` and `total_rulings` are `u256` counters. No raw data is
stored, only the per-station summary and digest.

## Data format

Two-column time series, UTC, minute resolution (seconds ignored):

```
2026-05-01T06:00,3.42
```

or JSON-like pairs such as `["2026-05-01T06:00", 3.42]`. Files with extra
columns or non-UTC offsets are not read.

## Known limitations

- Stations can revise past readings. If validators fetch different numbers
  inside the window there is no ruling, and a ruling is a snapshot.
- Independence is by URL only. Two URLs can come from one operator.
- Up to 20,000 readings and about 1.5 million characters per station.
- A faulty sensor reports faulty numbers. The contract checks the data, not the
  sensor.

## Extending

- Weighted stations (an official gauge counts double).
- Two-sided triggers (above A and below B).
- A payout contract that only releases funds when `get_status` is `triggered`.
