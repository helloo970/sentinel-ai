"""Start: python3 -m sentinel_agent --config /etc/sentinel-agent/config.json

Set the phone view PIN (once, as root):
    sudo PYTHONPATH=/opt/sentinel-agent/src python3 -m sentinel_agent --set-mobile-pin
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import logging
import os
import sys
import threading
from pathlib import Path

from sentinel_agent import mobile
from sentinel_agent.server import AgentConfig, build_mobile_server, build_server


def set_mobile_pin(config_path: str) -> None:
    data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    target = Path(data.get("mobile_pin_file", "/etc/sentinel-agent/mobile-pin"))
    pin = getpass.getpass("New PIN for the phone view (at least 6 characters): ")
    if pin != getpass.getpass("Repeat PIN: "):
        print("PINs do not match.", file=sys.stderr)
        sys.exit(1)
    try:
        hashed = mobile.hash_pin(pin)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(hashed + "\n", encoding="utf-8")
    os.chmod(tmp, 0o640)
    if hasattr(os, "chown"):
        import grp

        with contextlib.suppress(KeyError):
            os.chown(tmp, 0, grp.getgrnam("sentinel-agent").gr_gid)
    tmp.replace(target)
    print(f"PIN saved ({target}). Now run: sudo systemctl restart sentinel-agent")


def main() -> None:
    parser = argparse.ArgumentParser(description="SENTINEL AI server agent")
    parser.add_argument("--config", default="/etc/sentinel-agent/config.json")
    parser.add_argument("--set-mobile-pin", action="store_true", help="set the PIN (as root)")
    args = parser.parse_args()
    if args.set_mobile_pin:
        set_mobile_pin(args.config)
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("refusing to run as root", file=sys.stderr)
        sys.exit(1)
    config = AgentConfig.load(args.config)
    relay = mobile.Relay()
    server = build_server(config, relay=relay)
    if config.mobile_port:
        if Path(config.mobile_pin_file).is_file():
            phone = build_mobile_server(config, relay)
            threading.Thread(target=phone.serve_forever, daemon=True).start()
            logging.info("phone view on https://%s:%d/m", config.bind, config.mobile_port)
        else:
            logging.warning("phone view disabled: no PIN set (--set-mobile-pin)")
    logging.info("listening on https://%s:%d", config.bind, config.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
