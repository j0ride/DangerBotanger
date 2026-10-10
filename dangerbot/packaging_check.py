"""Offline packaged-build check; no browser, account or external service access."""
import json
from pathlib import Path
import ssl
import sys
import tkinter as tk

from .gui import Desktop
from .queue import RequestQueue
from .settings import ensure_certificate, load_settings, save_settings


def self_test(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / "report.json"
    try:
        values = load_settings(directory / ".env")
        save_settings(directory / ".env", values)
        cert, key = directory / "certificate.pem", directory / "key.pem"
        ensure_certificate(cert, key)
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key)
        queue = RequestQueue(directory / "queue.sqlite3")
        try:
            assert queue.count() == 0
        finally:
            queue.close()
        root = tk.Tk()
        root.withdraw()
        desktop = Desktop(root, directory)
        try:
            root.update()
            assert len(desktop.variables) == len(values)
        finally:
            desktop.dispose()
        report = {"ok": True, "frozen": bool(getattr(sys, "frozen", False)),
                  "checks": ["Tkinter", "configuration", "cryptography", "TLS", "SQLite", "desktop"]}
    except Exception as error:
        # Only the exception class is included; local paths and configuration may be private.
        report = {"ok": False, "error": type(error).__name__}
        report_path.write_text(json.dumps(report), encoding="utf-8")
        raise SystemExit(1) from None
    report_path.write_text(json.dumps(report), encoding="utf-8")
