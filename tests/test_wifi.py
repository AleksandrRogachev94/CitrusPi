"""Tests for Wi-Fi signal reading."""

from citruspi.wifi import read_signal

_HEADER = (
    "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
    " face | tstatus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
)


def _proc(tmp_path, body):
    path = tmp_path / "wireless"
    path.write_text(_HEADER + body)
    return path


def test_reads_dbm_and_bars(tmp_path):
    path = _proc(tmp_path, " wlan0: 0000   40.  -71.  -256.  0 0 0 0 0 0\n")
    assert read_signal(path) == {"interface": "wlan0", "dbm": -71, "bars": 2, "label": "Fair"}


def test_bar_thresholds(tmp_path):
    for dbm, bars in ((-50, 4), (-60, 3), (-70, 2), (-80, 1), (-90, 0)):
        path = _proc(tmp_path, f" wlan0: 0000 40. {dbm}. -256. 0 0 0 0 0 0\n")
        assert read_signal(path)["bars"] == bars


def test_unavailable_returns_none(tmp_path):
    assert read_signal(tmp_path / "missing") is None
    assert read_signal(_proc(tmp_path, "")) is None
    assert read_signal(_proc(tmp_path, " wlan0: 0000 0. 0. 0. 0 0 0 0 0 0\n")) is None

