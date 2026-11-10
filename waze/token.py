"""reCAPTCHA Enterprise tokens for the Waze live-map API, minted on the
browser box and spent over curl on GKE.

Every /live-map/api/* call (georss, user-drive, autocomplete, places,
reverse-geocoding, timezone) carries `X-Recaptcha-Token`, a v3 action
token the page mints with grecaptcha.enterprise.execute(SITE_KEY,
{action: "api"}) (livemap-*.js axios interceptor, 2026-11-07). Waze scores
it server-side: a low score, a forged or an expired token is 403. Measured
2026-11-07 (research in the memory file waze-scraper-research):

  * a token is NOT bound to IP, TLS or cookies: minted in one browser, it
    passed from curl_cffi chrome / safari17_0 / edge101 on the Mac's own
    IP, on datacenter us / de and on residential us proxy exits;
  * each token serves ~5 calls, the next one is 429 (per token: a sibling
    token minted in the same session keeps answering 200);
  * an unused token was still accepted at 15 min and refused at 30 min;
  * minting is ~0.2 s once the page is up; the SCORE is the problem: tokens
    from CapSolver (proxyless and proxy-bound), from headless Chrome and
    from a fresh automated headed Chrome were all 403, while a fresh
    persistent profile that had just browsed google.com passed about half
    of its sessions on a home IP (and all tokens of a session agree).

So the recipe (recipes/waze.py, a TokenMint) parks a headed Chrome on the
live map after a short google.com visit and mints on demand; this module
keeps the minted token in an identity_pool.Pool (shared mode: one token
serves every thread; a use is charged when a call TAKES it, so concurrent
holders cannot overrun the WAZE_TOKEN_USES budget - until 2026-11-07 uses
were counted on success only, and the mint ran outside the lock, so a
cold burst minted per thread), retires it after WAZE_TOKEN_USES calls or
WAZE_TOKEN_TTL seconds or on its first 403 / 429, and after
WAZE_MINT_ROTATE_AFTER refused (403) tokens from one exit rotates the exit
the box's page is parked on (a fresh browser session = a fresh score), the
semrush/captcha.py shape. The exits come from browser_client.ExitPool on
config.waze_mint_proxy() (the box's own IP, or a residential country).

GKE-only: browser_client is imported lazily so the module reads on the
boxes if it ever travels there (it is not in the worker pack list).
"""
import threading
import time

import config
import identity_pool

SITE = "waze"                 # recipes/waze.py + recipes/registry.py


class TokenUnavailable(Exception):
    """No token: the browser box is down / busy, its recipe failed, or the
    re-mint budget is spent. waze/fetch.py maps it (and the pool's own
    MintBudgetExceeded) to WazeUpstreamError."""


_lock = threading.Lock()
_exits = None                 # browser_client.ExitPool (built on first use)
_refusals = {}                # mint exit -> refused (403) tokens since its last good token


def _browser_client():
    import browser_client
    return browser_client


def _exit_pool():
    global _exits
    with _lock:
        if _exits is None:
            _exits = _browser_client().ExitPool(SITE, config.waze_mint_proxy,
                                                size=lambda: config.INPAGE_BROWSER_SLOTS, burst=True)
        return _exits


def _mint(key, hint):
    """The pool's mint function: one fresh token from the box (whichever
    parked page is free: the box picks among this module's own exits, so
    `hint` is unused). Box errors map to TokenUnavailable; the Identity
    carries the exit the token came from."""
    browser_client = _browser_client()
    exits = _exit_pool()
    try:
        try:
            proxy, value = exits.token_any(SITE, input=None, timeout=config.WAZE_MINT_TIMEOUT)
        except browser_client.BoxRefused as e:
            # the parked page on that exit is dead (grecaptcha gone, page
            # replaced): rotate it, one retry over the pool, then give up
            exits.rotate(e.proxy)
            proxy, value = exits.token_any(SITE, input=None, timeout=config.WAZE_MINT_TIMEOUT)
    except (browser_client.BoxUnavailable, browser_client.BoxRefused,
            browser_client.RemintBudgetExceeded) as e:
        raise TokenUnavailable(f"{type(e).__name__}: {e}") from e
    return identity_pool.Identity(value, exit=proxy)


def _on_retire(ident, reason):
    """The pool is done with a token. A refusal (403: scored too low, or
    expired) counts towards rotating the exit its page is parked on; a
    token that was spent or aged out served its calls, so the count for
    its exit starts over (the "in a row" of WAZE_MINT_ROTATE_AFTER)."""
    rotate = False
    with _lock:
        if reason == "refused":
            n = _refusals[ident.exit] = _refusals.get(ident.exit, 0) + 1
            rotate = n >= config.WAZE_MINT_ROTATE_AFTER
            if rotate:
                _refusals.pop(ident.exit, None)
        elif reason in ("spent", "expired"):
            _refusals.pop(ident.exit, None)
    if rotate:
        print(f"waze: {config.WAZE_MINT_ROTATE_AFTER} rejected tokens in a row; rotating the mint exit", flush=True)
        _rotate_mint_exit(ident.exit)


# One shared token at a time; the pool charges a use at get() and retires on
# budget / age / refusal. No exit_pool= here: the box picks among this
# module's own exits (token_any) and the 403 count decides the rotation.
pool = identity_pool.Pool(SITE, _mint, budget=lambda: config.WAZE_TOKEN_USES, max_age=lambda: config.WAZE_TOKEN_TTL,
                          mints_per_minute=5, on_retire=_on_retire)


def _rotate_mint_exit(burned):
    """Replace the exit the rejected tokens came from with a fresh one; the
    box parks a new page (a new reCAPTCHA session) on it at the next mint.
    Every token still pooled from that exit goes with it."""
    exits = _exit_pool()
    victim = burned
    if victim is None:
        cur = exits.current()
        victim = cur[0] if cur else None
    if victim is not None:
        exits.rotate(victim)
    pool.retire_where(lambda i: i.exit == victim, reason="replaced")


def _reset_for_tests():
    global _exits
    pool.clear()
    pool._mint_times.clear()
    with _lock:
        _exits = None
        _refusals.clear()


if __name__ == "__main__":
    # Smoke test (needs BROWSER_BOX_URLS): python waze/token.py
    t0 = time.time()
    tok = pool.get()
    print(f"token {tok.value[:24]}… ({len(tok.value)} chars) in {time.time() - t0:.1f}s via {tok.exit or 'the box IP'}")
