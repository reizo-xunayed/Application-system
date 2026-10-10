"""Start the dashboard: python3 -m app"""
from __future__ import annotations

import os
import threading
import webbrowser

from . import config, create_app


def main() -> None:
    app = create_app()
    url = f"http://localhost:{config.PORT}/"
    print(f"\n  Ausbildung outreach is running at {url}\n  Press Ctrl+C to stop.\n")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
