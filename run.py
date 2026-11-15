"""Entry point for local development.

Run the app with:
    python run.py

By default it listens on http://0.0.0.0:5000/ so phones and ESP32 boards on
the same Wi-Fi can reach the PowerAble server through this computer's LAN
address. The dashboard shows the address to use.

For camera-based QR scanning on phones, use the local HTTPS helper:

    python scripts/serve_https.py

Optional environment overrides:
    HOST=127.0.0.1    restrict plain HTTP to this computer only
    PORT=5000         listen port
    FLASK_DEBUG=0     disable debug mode and the auto-reloader
"""

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    debug_default = "1" if app.config.get("DEBUG") else "0"
    debug = os.environ.get("FLASK_DEBUG", debug_default) == "1"

    app.run(host=host, port=port, debug=debug, use_reloader=debug)
