"""Umschalten auf den lokalen Ausweichserver, wenn der KI-Server hängt.

Anlass ist der 03.09.: Der KI-Server antwortete auf `/api/tags` und `/api/ps`
in Millisekunden - `qwen3.8:27b` lag mit 17,3 GB im VRAM - und lieferte auf
`/api/chat` in 90 Sekunden **null Bytes**, auch auf "Antworte in einem Satz".

Zeus schaltete trotzdem nicht um, denn die Prüfung lautete:

    if not umgeschaltet and not self.host_erreichbar(self.cfg.host) ...

`host_erreichbar()` fragt `/api/tags`. Die Antwort war "ja". Also blieb Zeus
auf dem stummen Server und meldete "Keine Antwort innerhalb von 120s",
obwohl lokal ein funktionierendes Ollama mit `qwen2.5:14b` lief.

Die Prüfung fragte das Falsche: nicht "antwortet er?", sondern "erzeugt er?".

Hier wird der Defekt nachgebaut - ein Server, der Auskunft gibt und beim
Erzeugen schweigt - und geprüft, dass Zeus jetzt umschaltet.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODELL = "qwen3.8:27b"
ERSATZMODELL = "qwen2.5:14b"

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def _liste(modelle):
    return json.dumps({"models": [{"name": m, "model": m} for m in modelle]}).encode()


class _Basis(BaseHTTPRequestHandler):
    modelle = [MODELL]
    stumm = False                    # True = /api/chat schweigt

    def log_message(self, *_a):      # kein Rauschen im Testlauf
        pass

    def _senden(self, rohdaten: bytes, typ: str = "application/json"):
        self.send_response(200)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(rohdaten)))
        self.end_headers()
        self.wfile.write(rohdaten)

    def do_GET(self):
        if self.path.startswith("/api/tags") or self.path.startswith("/api/ps"):
            self._senden(_liste(self.modelle))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        laenge = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(laenge)
        if self.stumm:
            time.sleep(60)           # genau der Defekt: nimmt an, liefert nichts
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for stueck in ("Hallo", " von", " lokal"):
            self.wfile.write(json.dumps(
                {"message": {"role": "assistant", "content": stueck}}).encode() + b"\n")
            self.wfile.flush()
        self.wfile.write(json.dumps({"done": True, "done_reason": "stop"}).encode() + b"\n")
        self.wfile.flush()


class StummerServer(_Basis):
    modelle = [MODELL]
    stumm = True


class GesunderServer(_Basis):
    modelle = [ERSATZMODELL, "llama3:latest"]
    stumm = False


def starten(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def main() -> int:
    stumm_srv, stumm_url = starten(StummerServer)
    gesund_srv, gesund_url = starten(GesunderServer)

    os.environ["OLLAMA_HOST"] = stumm_url
    os.environ["OLLAMA_HOST_ALTERNATIVE"] = gesund_url
    os.environ["LLM_MODEL"] = MODELL
    os.environ["LLM_MODEL_ALTERNATIVE"] = ERSATZMODELL
    os.environ["IDLE_TIMEOUT"] = "5"          # Test soll nicht 120s dauern
    os.environ["OBSIDIAN_VAULT"] = str(Path(__file__).resolve().parents[1])

    from jarvis.config import reload_settings
    reload_settings()
    from jarvis.config import settings as cfg
    from jarvis.llm import OllamaClient

    klient = OllamaClient(cfg)
    meldungen = []
    klient.melden = meldungen.append

    # ---------------- 1. Die alte Frage sagt fälschlich "alles gut"
    check("Der stumme Server gilt als 'erreichbar' (das war die Falle)",
          klient.host_erreichbar(stumm_url))

    # ---------------- 2. Die neue Frage erkennt ihn
    check("generierung_pruefen entlarvt den stummen Server",
          not klient.generierung_pruefen(stumm_url, MODELL, zeitlimit=8))
    check("generierung_pruefen bestätigt den gesunden Server",
          klient.generierung_pruefen(gesund_url, ERSATZMODELL, zeitlimit=8))
    check("arbeitsfaehig() fasst beides richtig zusammen",
          not klient.arbeitsfaehig(stumm_url, MODELL)
          and klient.arbeitsfaehig(gesund_url, ERSATZMODELL))

    # ---------------- 3. Beim Start wird umgeschaltet
    ok = klient.verbindung_pruefen()
    check("verbindung_pruefen meldet Erfolg", ok, str(ok))
    check("Es wurde auf den Ausweichserver umgeschaltet",
          klient.auf_ausweich and cfg.host == gesund_url,
          f"host={cfg.host}, auf_ausweich={klient.auf_ausweich}")
    check("Das Modell wurde auf ein dort vorhandenes gewechselt",
          cfg.llm_model == ERSATZMODELL, cfg.llm_model)
    check("Die Umschaltung wird dem Nutzer gemeldet",
          any("umgeschaltet" in m.lower() for m in meldungen), str(meldungen))
    check("Die Meldung nennt den Grund (erzeugt nichts)",
          any("erzeugt" in m.lower() for m in meldungen), str(meldungen))

    # ---------------- 4. Und danach wird wirklich lokal geantwortet
    text = klient.chat([{"role": "user", "content": "Hallo?"}])
    check("Nach dem Umschalten kommt eine echte Antwort",
          "lokal" in (text or ""), repr(text))

    stumm_srv.shutdown()
    gesund_srv.shutdown()
    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
