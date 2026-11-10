"""waze/token.py on identity_pool.Pool: one mint per cold burst of threads,
the use budget charged when a call takes the token (not on success), a
403 count per mint exit that rotates it, retire_where by exit. No box, no
network: browser_client.token_any is faked.

    python -m pytest waze/test_token.py -q
"""
import threading
import time

import pytest

import browser_client
import config
from waze import fetch, token as tokens


@pytest.fixture
def box(monkeypatch):
    """A counting fake box: token_any answers on candidate 0 after a short
    hold; mint exits are distinct URLs so a rotation is visible."""
    ports = iter(range(20001, 20999))
    monkeypatch.setattr(config, "waze_mint_proxy", lambda: f"http://u:p@gw:{next(ports)}")
    monkeypatch.setattr(config, "WAZE_TOKEN_USES", 2)
    monkeypatch.setattr(config, "WAZE_MINT_ROTATE_AFTER", 2)
    calls = []
    lock = threading.Lock()

    def fake_token_any(site, candidates, input, variant="", timeout=None):
        assert site == "waze"
        with lock:
            calls.append(candidates[0][0])
        time.sleep(0.05)                                 # long enough for a burst to pile up
        return 0, f"tok-{len(calls)}"
    monkeypatch.setattr(browser_client, "token_any", fake_token_any)
    tokens._reset_for_tests()
    yield calls
    tokens._reset_for_tests()


def test_a_cold_burst_of_threads_mints_once(box):
    got, errors = [], []

    def worker():
        try:
            got.append(tokens.pool.get())
        except Exception as e:                           # pragma: no cover - the bug's symptom
            errors.append(e)
    threads = [threading.Thread(target=worker) for _ in range(2)]     # the budget is 2 uses
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert errors == [] and len(box) == 1
    assert len({id(i) for i in got}) == 1 and got[0].uses == {"use": 2}


def test_the_budget_is_charged_when_the_token_is_taken(box):
    a = tokens.pool.get()
    assert tokens.pool.get() is a and a.uses["use"] == 2 and len(box) == 1
    b = tokens.pool.get()                                # a's two uses are out before any call answered
    assert b is not a and a.retired and len(box) == 2


def test_an_old_token_is_not_handed_out(box, monkeypatch):
    monkeypatch.setattr(config, "WAZE_TOKEN_TTL", 100)
    a = tokens.pool.get()
    a.minted_at -= 101
    assert tokens.pool.get() is not a and a.retired


def test_a_403_wave_retires_once_and_rotates_the_exit_after_enough_refusals(box):
    a = tokens.pool.get()
    exit_a = a.exit
    assert tokens.pool.retire(a, "refused") and not tokens.pool.retire(a, "refused")   # one refusal wave, one retirement
    assert tokens._exit_pool().current()[0] == exit_a, "one refusal: the exit stays"
    b = tokens.pool.get()
    assert b.exit == exit_a and len(box) == 2
    tokens.pool.retire(b, "refused")                     # the second in a row on this exit
    assert tokens._exit_pool().current()[0] != exit_a
    assert tokens.pool.peek() is None and tokens._refusals == {}
    assert tokens.pool.get().exit != exit_a and len(box) == 3


def test_a_spent_token_resets_the_refusal_count(box):
    a = tokens.pool.get()
    tokens.pool.retire(a, "refused")
    b = tokens.pool.get()
    tokens.pool.get()                                    # b's budget is out
    assert tokens.pool.get() is not b and b.retired     # retired as spent
    assert tokens._refusals == {}


def test_retire_where_drops_every_token_of_an_exit(box):
    a = tokens.pool.get()
    assert tokens.pool.retire_where(lambda i: i.exit == a.exit, reason="replaced") == 1
    assert a.retired and tokens.pool.peek() is None
    assert tokens._refusals == {}, "a replaced exit's tokens are not refusals"


def test_fetch_retires_a_refused_token_and_retries_with_the_next(box, monkeypatch):
    seen = []

    class _Resp:
        def __init__(self, status, text):
            self.status_code, self.text = status, text

    class _Sess:
        def request(self, method, url, params=None, json=None, headers=None, timeout=None):
            seen.append(headers["x-recaptcha-token"])
            return _Resp(403, "") if len(seen) == 1 else _Resp(200, '{"ok": true}')
    monkeypatch.setattr(fetch, "_session", lambda: _Sess())
    assert fetch._call("GET", "/timezone", "timezone") == {"ok": True}
    assert seen == ["tok-1", "tok-2"] and len(box) == 2
    assert tokens._refusals == {box[0]: 1}


def test_a_box_outage_is_upstream_error(box, monkeypatch):
    def down(site, candidates, input, variant="", timeout=None):
        raise browser_client.BoxUnavailable("box unreachable")
    monkeypatch.setattr(browser_client, "token_any", down)
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    with pytest.raises(fetch.WazeUpstreamError, match="no reCAPTCHA token"):
        fetch._call("GET", "/timezone", "timezone")
    assert tokens.pool.stats()["minting"] == 0
