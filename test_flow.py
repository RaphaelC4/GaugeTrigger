# test_flow.py - call sequence for GenLayer Studio (type params WITHOUT quote
# marks). The three sample_station_*.csv files are the test data.
#
# SETUP: put the three CSV files in a public GitHub repo, open each one, press
# "Raw", and copy that URL (https://raw.githubusercontent.com/USER/REPO/main/...).
# Use those raw URLs below (comma-separated, no spaces).
#
# What the data says (hourly readings, 2026-05-01):
#   station A: 4.6 4.8 5.1 4.9 4.7 4.6 4.55 from 02:00 to 08:00 (7 readings above 4.5)
#   station B: 4.6 4.8 5.0 4.9 4.7 4.6 4.9  from 02:00 to 08:00 (7 readings above 4.5)
#   station C: 1.0 all day (never above 4.5)

def run(contract, a, b, c):
    urls = f"{a},{b},{c}"

    # 1. Quorum 2 of 3, 6 readings in a row above 4.5, window 02:00 to 10:00.
    contract.register_trigger(
        trigger_id="flood-1", label="River level",
        comparison="above", threshold="4.5", min_run=6, max_gap_minutes=90,
        window_start="2026-05-01T02:00", window_end="2026-05-01T10:00",
        quorum=2, station_urls=urls,
    ).transact()

    print(contract.get_status(trigger_id="flood-1").call())  # unevaluated
    print(contract.evaluate(trigger_id="flood-1").transact())  # triggered
    print(contract.get_ruling(trigger_id="flood-1").call())
    # A and B: covered, triggered, longest_run 7. C: covered, not triggered.
    # window_digest = SHA-256 of the in-window "timestamp,value" lines.

    # 2. Same data, quorum 3: station C does not trigger, so "not_triggered".
    contract.register_trigger(
        trigger_id="flood-2", label="River level strict",
        comparison="above", threshold="4.5", min_run=6, max_gap_minutes=90,
        window_start="2026-05-01T02:00", window_end="2026-05-01T10:00",
        quorum=3, station_urls=urls,
    ).transact()
    print(contract.evaluate(trigger_id="flood-2").transact())  # not_triggered

    # 3. Window not finished: the data ends at 11:00 on 2026-05-01, so a window
    #    ending 2026-05-02T00:00 must be REFUSED with INSUFFICIENT_DATA.

    # Must be rejected: evaluating flood-1 again (final), comparison "over",
    # threshold "nan", window_end before window_start, a window over 31 days,
    # duplicate station URLs, quorum above the number of stations.

if __name__ == "__main__":
    print(__doc__)
