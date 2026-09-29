import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))
from consumer import validate_event, TelemetryConsumer  # noqa: E402


def make_event(**overrides):
    evt = {
        "vin": "1HGCM82633A004352",
        "ts": "2026-09-25T10:15:02.120Z",
        "lat": 21.17, "lon": 72.83,
        "speed_kmh": 64.2, "seq": 1, "dtc": [],
    }
    evt.update(overrides)
    return evt


def test_valid_event_passes():
    ok, err = validate_event(make_event())
    assert ok and err is None


def test_invalid_vin_rejected():
    ok, err = validate_event(make_event(vin="INVALIDVIN12345OQ"))
    assert not ok


def test_bad_dtc_rejected():
    ok, err = validate_event(make_event(dtc=["NOTACODE"]))
    assert not ok


def test_lat_out_of_range_rejected():
    ok, err = validate_event(make_event(lat=999))
    assert not ok


def test_consumer_deduplicates_by_vin_seq():
    c = TelemetryConsumer(batch_size=10)
    c.handle(make_event(seq=1))
    c.handle(make_event(seq=1))  # duplicate
    c.handle(make_event(seq=2))
    assert c.duplicate_count == 1
    assert len(c._batch) == 2


def test_invalid_event_goes_to_dead_letter_not_batch():
    c = TelemetryConsumer(batch_size=10)
    c.handle(make_event(vin="bad"))
    assert len(c.dead_letters) == 1
    assert len(c._batch) == 0
