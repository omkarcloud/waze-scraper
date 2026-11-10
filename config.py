"""Configuration for the Waze Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT        port the API listens on (default 8000)
    WAZE_PROXY  proxy URL for the API calls to Waze, e.g.
                http://user:pass@host:port (default: none — direct). In our
                tests Waze limited each reCAPTCHA token, never the IP, so
                you very likely don't need this.

How a call works: every request to Waze's live-map API carries a reCAPTCHA
token, and Waze only accepts tokens that a real Chrome earned. So the first
call opens a Google Chrome window on this machine (browser_client.py), lets
it load google.com and the Waze live map, and asks the page for tokens. The
window stays open between calls and closes after 10 idle minutes. It always
uses your own connection: a home or office connection scores best.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

WAZE_PROXY = os.environ.get("WAZE_PROXY") or None

# reCAPTCHA tokens (minted in the Chrome window, see browser_client.py).
WAZE_TOKEN_USES = 5            # calls one token serves; Waze answers 429 to the 6th
WAZE_TOKEN_TTL = 14 * 60       # seconds a token is used (Waze accepts it for ~15 min)
WAZE_TOKEN_ATTEMPTS = 4        # tokens tried per call before it gives up
WAZE_MINT_ROTATE_AFTER = 3     # refused tokens in a row before Chrome is relaunched
WAZE_MINT_TIMEOUT = 150        # seconds for one token (a cold window loads two pages first)
INPAGE_BROWSER_SLOTS = 1       # Chrome windows minting tokens

# A box with 200 alerts or 200 jams (Waze's cap per request) is split in
# four this many levels deep and the pieces merged.
WAZE_TILE_DEPTH = 1


def waze_proxy():
    """Proxy for the API calls to Waze (None = direct)."""
    return os.environ.get("WAZE_PROXY") or None


def waze_mint_proxy():
    """The Chrome window always runs on your own connection."""
    return None
