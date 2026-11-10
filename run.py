"""Start the Waze Scraper API.

    python run.py            # http://localhost:8000
    PORT=9000 python run.py  # another port

Then:  curl "http://localhost:8000/jams/search?location=40.758,-73.9855"

The first call opens a Google Chrome window that earns Waze's reCAPTCHA
token (see browser_client.py); leave it open, it closes by itself after 10
idle minutes.
"""
import bottle
from cheroot import wsgi

import config
import routes  # noqa: F401  (mounts the routes on bottle's default app)


def main():
    app = bottle.default_app()
    print(f"Waze Scraper listening on http://localhost:{config.PORT}/")
    print(f"Try:  curl \"http://localhost:{config.PORT}/jams/search?location=40.758,-73.9855\"")
    server = wsgi.Server(("0.0.0.0", config.PORT), app, server_name="waze-scraper", numthreads=16)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
