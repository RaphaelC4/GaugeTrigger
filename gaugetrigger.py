# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
import json
import re
import hashlib
import datetime
from decimal import Decimal, InvalidOperation

# GaugeTrigger - did a measured threshold really hold, according to several
# independent public sensors, with a receipt anyone can recompute?
#
# THE PROBLEM
#   Flood, drought, heat and air-quality agreements (farm cover, relief
#   funds, event insurance, community alerts) are meant to act on a MEASURED
#   condition: "river above 4.5 m for 6 readings in a row", "no reading above
#   2 mm of rain for 20 days". Today that decision is a person reading a
#   chart, or one sensor feed trusted blindly. One sensor can be down, faulty
#   or edited, and a payout decided by one feed is only as honest as that
#   feed.
#
# THE PRIMITIVE
#   A trigger is registered with up to 5 public data URLs (CSV or JSON-like
#   time series), a comparison (above or below), a threshold, a required run
#   of consecutive readings, a maximum gap between readings, a fixed time
#   window, and a quorum K. Anyone can request the evaluation. The contract
#   fetches every series itself, reads the numbers in plain code, and rules
#   "triggered" only if at least K independent stations each hold the
#   condition for the required run inside the window. Each station's result
#   carries a digest of the exact readings used, so anyone can download the
#   same file and recompute the same answer.
#
# WHY THIS ONE IS NOT AN LLM CALL
#   The evidence is numbers. Counting readings above a line is a job for
#   code, not for a model, and a model would only add disagreement. The
#   intelligence of the contract is the rule set around the numbers: what
#   counts as a run, what counts as covered, what happens when a station is
#   missing, and how several stations combine.
#
# CONSENSUS DESIGN - exact recomputation of a deterministic result
#   Validators do not "approximate agree". Every validator fetches every
#   series itself, runs the same pure function, and the result must be
#   IDENTICAL, field by field: verdict, per-station coverage, longest run,
#   run start and end, extreme reading, and the digest of the readings used
#   (gl.eq_principle.strict_eq). The whole stored result is exactly what
#   every validator computed. Any difference, such as a station serving
#   different numbers to different fetches, means no ruling is written.
#
# EVIDENCE RULES (what makes a station count)
#   - Decimal arithmetic only. No floating point, so no NaN or infinity.
#   - A station is COVERED only if its series reaches the window start and
#     the window end: at least one reading at or before the start and at
#     least one at or after the end. So a trigger can never be ruled on
#     before its window is complete, and an unfinished feed cannot rule
#     "not triggered".
#   - The same minute with two different values makes the station unusable
#     ("conflict"). Silent choosing is not allowed.
#   - A run breaks if consecutive readings are further apart than
#     max_gap_minutes, so a missing stretch of data cannot be bridged.
#   - An unreachable station is "unreachable", and does not count as a
#     reading either way.
#   - At least K stations must be covered, otherwise the evaluation is
#     refused (INSUFFICIENT_DATA) and nothing is written, so it can be
#     retried when more data is available.
#
# REPLAY RULE
#   One ruling per trigger, and it is final. The window is in the past and
#   the evidence is recomputable, so a second run could only repeat the
#   answer or be used to re-roll an unlucky fetch.
#
# KNOWN LIMITATIONS (stated, not hidden)
#   - Accepted formats are two-column data: lines like 2026-05-01T06:00,3.42
#     or JSON-like pairs such as ["2026-05-01T06:00", 3.42]. Timestamps are
#     UTC, minute resolution (seconds are ignored).
#   - Stations can revise past readings. If they differ between validators'
#     fetches, no ruling is written; the result is a snapshot of the data at
#     the time of the ruling.
#   - Independence is by URL only. Two URLs can come from one operator.
#   - Up to 20,000 readings per station and about 1.5 million characters of
#     data are read.
#   - A faulty sensor reports faulty numbers; the contract checks the data,
#     not the sensor.

MAX_STATIONS = 5
MAX_READINGS = 20000
MAX_TEXT = 1500000
MAX_WINDOW_MINUTES = 44640

READING_PATTERN = re.compile(
    r"(?<!\d)(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})"
    r"(?::\d{2}(?:\.\d+)?)?(?:Z|\+00:00|\+0000)?"
    r"[,;\t \"'\]\[|]+(-?\d+(?:\.\d+)?)(?![\d.])"
)
WINDOW_PATTERN = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$")
EPOCH = datetime.datetime(1970, 1, 1)


def minutes_of(year, month, day, hour, minute):
    moment = datetime.datetime(year, month, day, hour, minute)
    delta = moment - EPOCH
    return delta.days * 1440 + delta.seconds // 60


def label_of(minutes):
    moment = EPOCH + datetime.timedelta(minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M")


def parse_window_time(text):
    match = WINDOW_PATTERN.match(text)
    if not match:
        return None
    try:
        return minutes_of(*[int(g) for g in match.groups()])
    except ValueError:
        return None


def parse_readings(text):
    """Returns (readings, conflict). readings is a list of
    (minute, value_text) sorted by minute with exact duplicates removed.
    conflict is True if one minute carries two different values."""
    seen = {}
    conflict = False
    count = 0
    for match in READING_PATTERN.finditer(text):
        count += 1
        if count > MAX_READINGS:
            break
        try:
            minute = minutes_of(*[int(g) for g in match.groups()[:5]])
            value_text = match.group(6)
            value = Decimal(value_text)
        except (ValueError, InvalidOperation):
            continue
        if not value.is_finite():
            continue
        if minute in seen:
            if Decimal(seen[minute]) != value:
                conflict = True
            continue
        seen[minute] = value_text
    readings = sorted(seen.items(), key=lambda item: item[0])
    return readings, conflict


def analyze_station(text, comparison, threshold, min_run, max_gap, start, end):
    """Pure function. Same input text always gives the same result."""
    empty = {
        "covered": False,
        "reason": "",
        "readings_in_window": 0,
        "longest_run": 0,
        "run_start": "",
        "run_end": "",
        "extreme": "",
        "triggered": False,
        "window_digest": "",
    }
    readings, conflict = parse_readings(text)
    if conflict:
        result = dict(empty)
        result["reason"] = "conflict"
        return result
    if not readings:
        result = dict(empty)
        result["reason"] = "no_readings"
        return result
    if readings[0][0] > start or readings[-1][0] < end:
        result = dict(empty)
        result["reason"] = "window_not_covered"
        return result

    window = [r for r in readings if start <= r[0] <= end]
    limit = Decimal(threshold)

    longest = 0
    longest_start = 0
    longest_end = 0
    run = 0
    run_start = 0
    previous_minute = None
    extreme_value = None
    extreme_text = ""
    for minute, value_text in window:
        value = Decimal(value_text)
        if extreme_value is None:
            extreme_value = value
            extreme_text = value_text
        elif comparison == "above" and value > extreme_value:
            extreme_value = value
            extreme_text = value_text
        elif comparison == "below" and value < extreme_value:
            extreme_value = value
            extreme_text = value_text

        holds = value > limit if comparison == "above" else value < limit
        broken_by_gap = previous_minute is not None and (minute - previous_minute) > max_gap
        if holds:
            if run > 0 and not broken_by_gap:
                run += 1
            else:
                run = 1
                run_start = minute
            if run > longest:
                longest = run
                longest_start = run_start
                longest_end = minute
        else:
            run = 0
        previous_minute = minute

    canonical = "\n".join(label_of(m) + "," + v for m, v in window)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return {
        "covered": True,
        "reason": "ok",
        "readings_in_window": len(window),
        "longest_run": longest,
        "run_start": label_of(longest_start) if longest else "",
        "run_end": label_of(longest_end) if longest else "",
        "extreme": extreme_text,
        "triggered": longest >= min_run,
        "window_digest": digest,
    }


def fetch_text(url):
    try:
        response = gl.nondet.web.get(url)
    except Exception:
        return None
    if response.status != 200 or response.body is None:
        return None
    return bytes(response.body).decode("utf-8", errors="replace")[:MAX_TEXT]


class Contract(gl.Contract):
    # trigger_id -> JSON: {registrant, label, comparison, threshold, min_run,
    #   max_gap_minutes, window_start, window_end, quorum, stations: [urls]}
    triggers: TreeMap[str, str]

    # trigger_id -> JSON ruling: {trigger_id, verdict, quorum,
    #   covered_stations, triggered_stations, stations: [per-station result]}
    rulings: TreeMap[str, str]

    total_triggers: u256
    total_rulings: u256

    def __init__(self):
        self.total_triggers = u256(0)
        self.total_rulings = u256(0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_id(self, value: str) -> None:
        if not value or len(value) > 32:
            raise gl.vm.UserError("trigger_id must be 1 to 32 characters")
        for ch in value:
            if not (ch.isascii() and (ch.isalnum() or ch in "-_")):
                raise gl.vm.UserError("trigger_id may only use letters, numbers, - and _")

    def _check_url(self, url: str) -> None:
        if len(url) > 300:
            raise gl.vm.UserError("URL too long (max 300 characters)")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise gl.vm.UserError("URL must start with http:// or https://")
        if " " in url:
            raise gl.vm.UserError("URL must not contain spaces")
        rest = url.split("://", 1)[1]
        host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if "." not in host:
            raise gl.vm.UserError("URL has no valid host")

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    @gl.public.write
    def register_trigger(
        self,
        trigger_id: str,
        label: str,
        comparison: str,
        threshold: str,
        min_run: int,
        max_gap_minutes: int,
        window_start: str,
        window_end: str,
        quorum: int,
        station_urls: str,
    ) -> None:
        """Open to anyone. comparison is "above" or "below". threshold is a
        plain decimal like 4.5. window_start and window_end look like
        2026-05-01T00:00 (UTC). station_urls is comma-separated, 1 to 5
        distinct URLs. quorum is how many stations must hold the condition."""
        self._check_id(trigger_id)
        if trigger_id in self.triggers:
            raise gl.vm.UserError("trigger_id already used")

        label = label.strip()
        if not label or len(label) > 120:
            raise gl.vm.UserError("label must be 1 to 120 characters")

        comparison = comparison.strip().lower()
        if comparison not in ("above", "below"):
            raise gl.vm.UserError('comparison must be "above" or "below"')

        threshold = threshold.strip()
        if not re.match(r"^-?\d+(\.\d+)?$", threshold) or len(threshold) > 30:
            raise gl.vm.UserError("threshold must be a plain decimal number such as 4.5")

        run_value = int(min_run)
        if run_value < 1 or run_value > 1000:
            raise gl.vm.UserError("min_run must be between 1 and 1000")
        gap_value = int(max_gap_minutes)
        if gap_value < 1 or gap_value > 1440:
            raise gl.vm.UserError("max_gap_minutes must be between 1 and 1440")

        start = parse_window_time(window_start.strip())
        end = parse_window_time(window_end.strip())
        if start is None or end is None:
            raise gl.vm.UserError("window times must look like 2026-05-01T00:00")
        if end <= start:
            raise gl.vm.UserError("window_end must be after window_start")
        if end - start > MAX_WINDOW_MINUTES:
            raise gl.vm.UserError("window can be at most 31 days")

        urls = [u.strip() for u in station_urls.split(",") if u.strip()]
        if len(urls) < 1 or len(urls) > MAX_STATIONS:
            raise gl.vm.UserError("provide 1 to 5 station URLs, comma-separated")
        normalized = []
        for url in urls:
            self._check_url(url)
            key = url.lower().rstrip("/")
            if key in normalized:
                raise gl.vm.UserError("station URLs must be distinct")
            normalized.append(key)

        quorum_value = int(quorum)
        if quorum_value < 1 or quorum_value > len(urls):
            raise gl.vm.UserError("quorum must be between 1 and the number of stations")

        record = {
            "registrant": gl.message.sender_address.as_hex,
            "label": label,
            "comparison": comparison,
            "threshold": threshold,
            "min_run": run_value,
            "max_gap_minutes": gap_value,
            "window_start": label_of(start),
            "window_end": label_of(end),
            "quorum": quorum_value,
            "stations": urls,
        }
        self.triggers[trigger_id] = json.dumps(record)
        self.total_triggers = u256(int(self.total_triggers) + 1)

    # ------------------------------------------------------------------
    # Evaluation - the consensus-backed primitive
    # ------------------------------------------------------------------

    @gl.public.write
    def evaluate(self, trigger_id: str) -> str:
        """Permissionless. Returns "triggered" or "not_triggered". Refused
        with INSUFFICIENT_DATA when fewer than `quorum` stations cover the
        whole window."""
        if trigger_id not in self.triggers:
            raise gl.vm.UserError("unknown trigger_id")
        if trigger_id in self.rulings:
            raise gl.vm.UserError("trigger already ruled; the ruling is final")

        trigger = json.loads(self.triggers[trigger_id])
        comparison = trigger["comparison"]
        threshold = trigger["threshold"]
        min_run = trigger["min_run"]
        max_gap = trigger["max_gap_minutes"]
        start = parse_window_time(trigger["window_start"])
        end = parse_window_time(trigger["window_end"])
        quorum = trigger["quorum"]
        urls = list(trigger["stations"])

        def compute():
            results = []
            for url in urls:
                text = fetch_text(url)
                if text is None:
                    station = analyze_station("", comparison, threshold, min_run, max_gap, start, end)
                    station["reason"] = "unreachable"
                else:
                    station = analyze_station(text, comparison, threshold, min_run, max_gap, start, end)
                station["url"] = url
                results.append(station)
            covered = sum(1 for s in results if s["covered"])
            triggered = sum(1 for s in results if s["triggered"])
            if covered < quorum:
                verdict = "insufficient_data"
            elif triggered >= quorum:
                verdict = "triggered"
            else:
                verdict = "not_triggered"
            return {
                "verdict": verdict,
                "covered_stations": covered,
                "triggered_stations": triggered,
                "stations": results,
            }

        # Every validator fetches every series itself and must compute the
        # identical result, field by field.
        result = gl.eq_principle.strict_eq(compute)

        if result["verdict"] == "insufficient_data":
            raise gl.vm.UserError("INSUFFICIENT_DATA")

        ruling = {
            "trigger_id": trigger_id,
            "verdict": result["verdict"],
            "quorum": quorum,
            "covered_stations": result["covered_stations"],
            "triggered_stations": result["triggered_stations"],
            "stations": result["stations"],
        }
        self.rulings[trigger_id] = json.dumps(ruling)
        self.total_rulings = u256(int(self.total_rulings) + 1)
        return result["verdict"]

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    @gl.public.view
    def get_trigger(self, trigger_id: str) -> str:
        if trigger_id not in self.triggers:
            raise gl.vm.UserError("unknown trigger_id")
        return self.triggers[trigger_id]

    @gl.public.view
    def get_ruling(self, trigger_id: str) -> str:
        if trigger_id not in self.rulings:
            raise gl.vm.UserError("no ruling for this trigger_id")
        return self.rulings[trigger_id]

    @gl.public.view
    def get_status(self, trigger_id: str) -> str:
        """unevaluated, triggered or not_triggered. Easy for other
        contracts to use as a payout or alert condition."""
        if trigger_id not in self.triggers:
            raise gl.vm.UserError("unknown trigger_id")
        if trigger_id not in self.rulings:
            return "unevaluated"
        return json.loads(self.rulings[trigger_id])["verdict"]
