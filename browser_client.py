"""Mints Waze's reCAPTCHA token with a Google Chrome (patchright) on this
machine.

Every call to Waze's live-map API (traffic feed, routing, place search,
places, reverse geocoding) carries an `X-Recaptcha-Token`, a reCAPTCHA
Enterprise token the live map's own page mints. Waze scores it: tokens from
captcha-solving services and from headless Chrome are refused, a real
headed Chrome that has just visited google.com gets good ones. So the first
call opens ONE Chrome window, loads google.com and then
https://www.waze.com/live-map/, waits for reCAPTCHA, and asks the page for a
token. The window stays parked on the live map and mints again whenever
waze/token.py needs a fresh token (one token serves ~5 calls); it closes
after BROWSER_IDLE seconds without a mint, and is relaunched (a new
reCAPTCHA session) when Waze refuses several tokens in a row.

Needs Google Chrome installed (patchright drives it). Waze refuses headless
Chrome, so a window opens; on a Linux server without a display the driver
starts a virtual one (Xvfb, included in the Docker image). The token is not
tied to the IP that minted it, but its score is: a home or office
connection scores best, a cloud server's IP may be refused.

Same interface as the hosted service's client for its browser servers:
ExitPool, token_any() and the exception classes, so waze/ runs unchanged."""
import threading
import time

SITE_KEY = "6Lf4WdUqAAAAAEUYUvzyLYIkO3PoFAqi8ZHGiDLW"   # the live map's reCAPTCHA Enterprise key
LIVE_MAP = "https://www.waze.com/live-map/"
WARM_UP = "https://www.google.com/"
PAGE_SETTLE = 4            # seconds after the live map loads before the first token
READY_POLL = 1.0
READY_TIMEOUT = 45         # seconds for grecaptcha.enterprise to appear on the page
MINT_TIMEOUT_MS = 30_000
BLOCK_RESOURCES = ("media", "font")   # map tiles stay: the map is what a visitor loads
BROWSER_IDLE = 600         # close the window after this many seconds without a mint

# grecaptcha lives in the page's MAIN world; patchright evaluates in an
# isolated world by default, where it is invisible (every evaluate below
# passes main_world=True).
_READY_JS = "() => !!(window.grecaptcha && window.grecaptcha.enterprise && window.grecaptcha.enterprise.execute)"
_EXECUTE_JS = """([key, timeoutMs]) => new Promise((res) => {
  const t = setTimeout(() => res({error: 'timeout'}), timeoutMs);
  try {
    grecaptcha.enterprise.execute(key, {action: 'api'}).then(
      tok => { clearTimeout(t); res({token: tok}); },
      err => { clearTimeout(t); res({error: String(err)}); });
  } catch (e) { clearTimeout(t); res({error: 'throw ' + String(e)}); }
})"""


class BoxUnavailable(Exception):
    """Chrome could not start on this machine (not installed, no display)."""

    def __init__(self, message="", candidate=None):
        super().__init__(message)
        self.candidate = candidate
        self.proxy = None


class BoxRefused(Exception):
    """The live map did not hand out a token in this Chrome (reCAPTCHA did
    not load, or execute() failed); the window is closed."""

    def __init__(self, message="", candidate=None):
        super().__init__(message)
        self.candidate = candidate
        self.proxy = None


class RemintBudgetExceeded(Exception):
    """Kept for interface parity with the hosted client; never raised here."""


# ---- one Chrome per exit -------------------------------------------------------------

def _launch(proxy):
    try:
        from patchright_driver import PatchrightDriver
        return PatchrightDriver(proxy, headless=False, block_resources=list(BLOCK_RESOURCES),
                                geoip_timezone=False)
    except Exception as e:
        raise BoxUnavailable(f"could not start Chrome: {type(e).__name__}: {e}")


class _Browser:
    def __init__(self, proxy):
        self.proxy = proxy
        self.lock = threading.Lock()
        self.driver = None
        self.last_use = time.monotonic()

    def warm(self):
        """Open the window: google.com first (what a real visitor's history
        looks like to reCAPTCHA), then the live map, then wait for reCAPTCHA."""
        self.driver = _launch(self.proxy)
        try:
            try:
                self.driver.call(lambda page: page.goto(WARM_UP, wait_until="domcontentloaded", timeout=30_000),
                                 timeout=45)
                time.sleep(2)
            except Exception as e:                   # a slow google.com is not a failed mint
                print(f"waze: google.com warm-up skipped: {type(e).__name__}: {e}", flush=True)
            self.driver.call(lambda page: page.goto(LIVE_MAP, referer=WARM_UP, wait_until="domcontentloaded",
                                                    timeout=60_000), timeout=90)
            time.sleep(PAGE_SETTLE)
            deadline = time.monotonic() + READY_TIMEOUT
            while not self.driver.evaluate(_READY_JS, main_world=True):
                if time.monotonic() > deadline:
                    raise BoxRefused("waze: reCAPTCHA did not load on the live map")
                time.sleep(READY_POLL)
        except Exception:
            self.close()
            raise

    def mint(self):
        res = self.driver.evaluate(_EXECUTE_JS, [SITE_KEY, MINT_TIMEOUT_MS], main_world=True) or {}
        if res.get("error") or not res.get("token"):
            raise BoxRefused(f"waze: no token from the live map ({res.get('error') or 'empty'})")
        return res["token"]

    def close(self):
        driver, self.driver = self.driver, None
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass


_browsers = {}
_browsers_lock = threading.Lock()


def _reap_idle(keep=None):
    """Close every window nobody minted with for BROWSER_IDLE seconds."""
    now = time.monotonic()
    with _browsers_lock:
        idle = [b for key, b in _browsers.items()
                if key != keep and b.driver is not None and now - b.last_use > BROWSER_IDLE and not b.lock.locked()]
    for b in idle:
        b.close()


def _browser(proxy):
    _reap_idle(keep=proxy)
    with _browsers_lock:
        b = _browsers.get(proxy)
        if b is None:
            b = _browsers[proxy] = _Browser(proxy)
        return b


def close(proxy=None):
    """Close the window for `proxy` (the next mint opens a fresh one)."""
    with _browsers_lock:
        b = _browsers.get(proxy)
    if b is not None:
        with b.lock:
            b.close()


def _idle_reaper():
    while True:
        time.sleep(60)
        try:
            _reap_idle()
        except Exception:
            pass


threading.Thread(target=_idle_reaper, daemon=True, name="waze-chrome-reaper").start()


# ---- actions ---------------------------------------------------------------------------

def token(site, input=None, proxy=None, variant="", timeout=None):
    """One token from the window for `proxy` (opened on first use)."""
    b = _browser(proxy)
    with b.lock:
        b.last_use = time.monotonic()
        if b.driver is None:
            b.warm()
        try:
            tok = b.mint()
        except BoxRefused:
            b.close()
            raise
        except Exception as e:
            b.close()
            raise BoxRefused(f"waze: Chrome failed: {type(e).__name__}: {e}")
        b.last_use = time.monotonic()
        return tok


def token_any(site, candidates, input=None, variant="", timeout=None):
    """token() on the first candidate [(proxy, session), ...] -> (index, token);
    a failure carries the index so the caller rotates that exit."""
    proxy = candidates[0][0] if candidates else None
    try:
        return 0, token(site, input, proxy=proxy, variant=variant, timeout=timeout)
    except (BoxRefused, BoxUnavailable) as e:
        e.candidate = 0
        e.proxy = proxy
        raise


# ---- sticky exits per site (the hosted client's holder, unchanged) ------------------------

class ExitPool:
    """Sticky exits for one site's browsers: `mint` returns a proxy URL (or
    None for your own connection), `size` how many are kept per key. Here
    the Chrome window always runs on your own connection, so every slot is
    the same exit and rotate() relaunches the window instead (a fresh
    reCAPTCHA session)."""

    def __init__(self, site, mint, size=1, burst=False, uses_per_exit=None):
        self.site = site
        self._mint = mint
        self._size = size
        self.burst = burst
        self._lock = threading.Lock()
        self._slots = {}
        self._next = {}
        self._filled = {}

    def share(self):
        n = self._size() if callable(self._size) else self._size
        return max(1, int(n or 1))

    def _n(self):
        return self.share()

    def _mint_for(self, key):
        return self._mint()

    def _resize_locked(self, key, n):
        slots = self._slots.setdefault(key, [])
        filled = self._filled.setdefault(key, set())
        while len(slots) < n:
            slots.append(None)
        if len(slots) > n:
            del slots[n:]
            filled.difference_update(i for i in list(filled) if i >= n)
        return slots, filled

    def get(self, key=""):
        with self._lock:
            n = self._n()
            slots, filled = self._resize_locked(key, n)
            i = self._next.get(key, 0) % n
            self._next[key] = (i + 1) % n
            if i not in filled:
                slots[i] = self._mint_for(key)
                filled.add(i)
            return slots[i]

    def all(self, key=""):
        with self._lock:
            n = self._n()
            slots, filled = self._resize_locked(key, n)
            for i in range(n):
                if i not in filled:
                    slots[i] = self._mint_for(key)
                    filled.add(i)
            return list(slots)

    def token_any(self, site, key="", sessions=None, **kw):
        exits = self.all(key)
        candidates = [(p, sessions(p) if sessions else None) for p in exits]
        try:
            index, tok = token_any(site, candidates, **kw)
        except (BoxRefused, BoxUnavailable) as e:
            if e.candidate is not None and 0 <= e.candidate < len(exits):
                e.proxy = exits[e.candidate]
            raise
        return exits[index], tok

    def rotate(self, proxy, key=""):
        """Replace `proxy` with a fresh exit and close its window, so the next
        mint starts a new reCAPTCHA session. False when another caller
        already replaced it."""
        with self._lock:
            slots = self._slots.get(key) or []
            filled = self._filled.setdefault(key, set())
            replaced = False
            for i, cur in enumerate(slots):
                if cur == proxy and i in filled:
                    slots[i] = self._mint_for(key)
                    replaced = True
                    break
        if replaced:
            close(proxy)
        return replaced

    def current(self, key=""):
        with self._lock:
            return list(self._slots.get(key) or [])

    def reset(self, key=None):
        with self._lock:
            if key is None:
                self._slots.clear(); self._next.clear(); self._filled.clear()
            else:
                self._slots.pop(key, None); self._next.pop(key, None); self._filled.pop(key, None)
