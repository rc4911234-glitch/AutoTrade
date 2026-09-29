"""Launcher for Trad-Auto with automatic public tunnel for Twilio WhatsApp integration."""

import logging
import re
import subprocess
import sys
import threading
import time

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from config.settings import get_settings
from trad_auto.engine import TradingEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("trad_auto.whatsapp_launcher")


def main() -> None:
    settings = get_settings()
    print("=" * 65)
    print("🚀 Initializing Trad-Auto Engine in PAPER mode...")
    print("=" * 65)

    engine = TradingEngine(settings=settings)
    engine.initialize(rehydrate=True)
    engine.start()

    port = settings.webhook_port
    print(f"\n✅ Local webhook server running on http://127.0.0.1:{port}/webhook/whatsapp")

    import os
    cloudflared_bin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflared.exe")
    use_cloudflared = os.path.exists(cloudflared_bin)

    if use_cloudflared:
        print("🌐 Creating high-speed stable public tunnel via Cloudflare (trycloudflare.com)...")
        tunnel_cmd = [
            cloudflared_bin,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{port}",
            "--no-autoupdate",
        ]
        tunnel_regex = re.compile(r"(https://[a-zA-Z0-9.-]+\.trycloudflare\.com)")
    else:
        print("🌐 Creating public secure tunnel via localhost.run...")
        tunnel_cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ServerAliveInterval=30",
            "-R",
            f"80:127.0.0.1:{port}",
            "nokey@localhost.run",
        ]
        tunnel_regex = re.compile(r"(https://[a-zA-Z0-9.-]+\.lhr\.life)")

    tunnel_proc = subprocess.Popen(
        tunnel_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    public_url = None

    # Read tunnel output to capture the public URL
    def monitor_tunnel() -> None:
        nonlocal public_url
        if tunnel_proc.stdout is None:
            return
        while True:
            line = tunnel_proc.stdout.readline()
            if not line:
                break
            line_str = line.strip()
            if line_str:
                print(f"[Tunnel] {line_str}", flush=True)
            match = tunnel_regex.search(line)
            if match and not public_url:
                public_url = match.group(1)
                webhook_url = f"{public_url}/webhook/whatsapp"
                print("\n" + "=" * 65, flush=True)
                print("🎉 WHATSAPP GATEWAY IS LIVE AND CONNECTED!", flush=True)
                print("=" * 65, flush=True)
                print(f"👉 Copy this Webhook URL:\n   {webhook_url}", flush=True)
                print("\n📌 Paste it in Twilio Console:", flush=True)
                print("   1. Open: https://console.twilio.com/us1/develop/sms/settings/whatsapp-sandbox", flush=True)
                print("   2. In 'WHEN A MESSAGE COMES IN', paste the URL above", flush=True)
                print("   3. Method: POST", flush=True)
                print("   4. Click 'Save'", flush=True)
                print(f"\n📱 WhatsApp se send karo to Twilio:", flush=True)
                print("   • Number: +1 415 523 8886 (code: join youth-pocket)", flush=True)
                print("   • OR Number: +1 737 232-4091 (code: join twilio-trial)", flush=True)
                print("   Commands: 'status', 'start trading 1000', 'stop trading', 'kill switch'", flush=True)
                print("=" * 65 + "\n", flush=True)

    t = threading.Thread(target=monitor_tunnel, daemon=True)
    t.start()

    # Wait up to 15 seconds for public URL
    for _ in range(30):
        if public_url:
            break
        time.sleep(0.5)

    if not public_url:
        print("Tunnel starting... waiting for connection.", flush=True)

    print("Trad-Auto WhatsApp Daemon is running. Keep this active.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping Trad-Auto...")
    finally:
        tunnel_proc.terminate()
        engine.stop(reason="Launcher terminated")
        print("Trad-Auto stopped cleanly.")


if __name__ == "__main__":
    main()
