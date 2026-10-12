"""Tests für Serverwahl, Ausweichbetrieb und Tempomessung.

Anlass: Josh betreibt Ollama jetzt auf einem eigenen Rechner im Netz. Daraus
folgen drei Anforderungen, die vorher niemand hatte:

  * Der Server ist nicht immer an. Ist er aus, muss Zeus auf das lokale Ollama
    ausweichen - und zwar VOR dem ersten Aufruf, nicht erst mittendrin.
  * Auf dem Ausweichrechner liegt ein kleineres Modell. Das große dort
    anzufordern, scheitert bei jedem einzelnen Aufruf.
  * `/tempo` maß bisher aus Zeichenzahl und Wanduhr - inklusive der Ladezeit
    des Modells. Bei einem 27b-Modell dauert das Laden über eine Minute, und
    heraus kam "0,5 Token/s" für einen Server, der ein Vielfaches schafft.

Zwei kleine HTTP-Server stehen hier für die beiden Ollama-Instanzen. Der eine
lässt sich abschalten - das ist der eigentliche Prüffall.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_server_"))
VAULT = TMP / "Zeus"
VAULT.mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.config import Settings, url_normieren  # noqa: E402
from jarvis.llm import OllamaClient  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


# ------------------------------------------------------------------- Fake-Ollama
class FakeOllama(BaseHTTPRequestHandler):
    """Ein Ollama, das man konfigurieren kann. MODELLE steuert /api/tags."""

    modelle: list = []
    an: bool = True
    # Was der letzte Chunk melden soll. Genau daraus wird das Tempo berechnet.
    eval_count = 40
    eval_ns = 2_000_000_000        # 2 s → 20 Token/s
    zaehlwerte = True

    def log_message(self, *_a):
        pass

    def _senden(self, daten, status=200):
        koerper = json.dumps(daten).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(koerper)))
        self.end_headers()
        self.wfile.write(koerper)

    def do_GET(self):
        if not type(self).an:
            self.send_response(503)
            self.end_headers()
            return
        if self.path.startswith("/api/tags"):
            self._senden({"models": [{"name": m} for m in type(self).modelle]})
        elif self.path.startswith("/api/version"):
            self._senden({"version": "0.5.0"})
        elif self.path.startswith("/api/ps"):
            self._senden({"models": [{"name": type(self).modelle[0] if type(self).modelle else "x",
                                      "size": 18 * 1024 ** 3,
                                      "size_vram": 18 * 1024 ** 3}]})
        else:
            self._senden({}, 404)

    def do_POST(self):
        if not type(self).an:
            self.send_response(503)
            self.end_headers()
            return
        laenge = int(self.headers.get("Content-Length", 0) or 0)
        self.rfile.read(laenge)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for wort in ("eins ", "zwei ", "drei "):
            self.wfile.write(
                json.dumps({"message": {"content": wort}}).encode("utf-8") + b"\n")
        letzte = {"message": {"content": ""}, "done": True}
        if type(self).zaehlwerte:
            letzte.update({
                "eval_count": type(self).eval_count,
                "eval_duration": type(self).eval_ns,
                "prompt_eval_count": 100,
                "prompt_eval_duration": 500_000_000,
                "total_duration": type(self).eval_ns + 500_000_000,
            })
        self.wfile.write(json.dumps(letzte).encode("utf-8") + b"\n")
        try:
            self.wfile.flush()
        except OSError:
            # Abgebrochene Verbindung ist im Test ohne Bedeutung.
            pass


def eigene_adresse() -> str:
    """Die LAN-Adresse dieses Rechners - nicht 127.0.0.1.

    Gebraucht wird sie, um den Fall "Ollama läuft woanders" überhaupt
    nachstellen zu können: Über 127.0.0.1 gilt ein Server per Definition als
    lokal, und genau diese Unterscheidung soll geprüft werden.
    """
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            adresse = s.getsockname()[0]
        if adresse and not adresse.startswith("127."):
            return adresse
    except OSError:
        pass
    return ""


def starten(modelle, adresse: str = "127.0.0.1"):
    klasse = type("Ollama", (FakeOllama,), {"modelle": list(modelle), "an": True})
    server = HTTPServer(("" if adresse != "127.0.0.1" else "127.0.0.1", 0), klasse)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, klasse, f"http://{adresse}:{server.server_address[1]}"


def main() -> int:
    # ================================================== 1. Adressen geradebiegen
    # Genau der Tippfehler aus Joshs .env: 'tp://localhost:11434'
    check("Tippfehler 'tp://' wird geheilt",
          url_normieren("tp://localhost:11434") == "http://localhost:11434",
          url_normieren("tp://localhost:11434"))
    check("Fehlendes Schema wird ergänzt",
          url_normieren("192.168.173.176:11434") == "http://192.168.173.176:11434")
    check("Abschließender Schrägstrich fliegt weg",
          url_normieren("http://x:11434/") == "http://x:11434")
    check("Anführungszeichen aus der .env fliegen weg",
          url_normieren('"http://x:11434"') == "http://x:11434")
    check("Leer bleibt leer", url_normieren("  ") == "")
    check("Eine gültige Adresse bleibt unverändert",
          url_normieren("https://ki.local:11434") == "https://ki.local:11434")

    haupt_srv, haupt, haupt_url = starten(["qwen3:27b", "nomic-embed-text"])
    ersatz_srv, ersatz, ersatz_url = starten(["qwen2.5:14b", "nomic-embed-text"])

    def frisch(**kw):
        cfg = Settings()
        cfg.host = haupt_url
        cfg.host_alternative = ersatz_url
        cfg.llm_model = "qwen3:27b"
        cfg.llm_model_alternative = "qwen2.5:14b"
        cfg.embedding_model = "nomic-embed-text"
        cfg.warmup = False
        for k, v in kw.items():
            setattr(cfg, k, v)
        return cfg, OllamaClient(cfg)

    # =============================================== 2. Hauptserver läuft
    cfg, client = frisch()
    meldungen = []
    check("Bei laufendem Hauptserver wird nicht umgeschaltet",
          client.verbindung_pruefen(melden=meldungen.append)
          and not client.auf_ausweich and cfg.host == haupt_url,
          f"{cfg.host} · {cfg.llm_model}")
    check("Und das große Modell bleibt eingestellt", cfg.llm_model == "qwen3:27b")

    # ==================================== 3. Hauptserver aus → Ausweichbetrieb
    haupt.an = False
    cfg, client = frisch()
    meldungen = []
    ok = client.verbindung_pruefen(melden=meldungen.append)
    check("Ist der Hauptserver aus, wird ausgewichen",
          ok and client.auf_ausweich and cfg.host == ersatz_url, f"{cfg.host}")
    check("Und das dort vorhandene Modell wird genommen",
          cfg.llm_model == "qwen2.5:14b", cfg.llm_model)
    text = " ".join(meldungen)
    check("Der Wechsel wird gemeldet, nicht stillschweigend vollzogen",
          "umgeschaltet" in text.lower(), text[:120])
    check("Und die Meldung nennt beide Modelle",
          "qwen3:27b" in text and "qwen2.5:14b" in text, text[:160])

    # -------- FAST_MODEL, das es auf dem Ausweichserver nicht gibt
    haupt.an = False
    cfg, client = frisch(fast_model="qwen2.5:3b")
    meldungen = []
    client.verbindung_pruefen(melden=meldungen.append)
    check("Ein dort fehlendes FAST_MODEL wird abgeschaltet statt zu scheitern",
          cfg.fast_model == "", f"'{cfg.fast_model}'")
    check("Und auch das wird gesagt",
          "FAST_MODEL" in " ".join(meldungen), " ".join(meldungen)[:160])

    # -------- Einbettungsmodell fehlt: warnen, aber NICHT ersetzen
    ersatz.modelle = ["qwen2.5:14b"]
    cfg, client = frisch()
    meldungen = []
    client.verbindung_pruefen(melden=meldungen.append)
    check("Fehlendes Einbettungsmodell wird laut gemeldet",
          "Einbettungsmodell" in " ".join(meldungen), " ".join(meldungen)[:200])
    check("Es wird aber NICHT ersetzt – sonst wäre der Index wertlos",
          cfg.embedding_model == "nomic-embed-text", cfg.embedding_model)
    ersatz.modelle = ["qwen2.5:14b", "nomic-embed-text"]

    # ============================== 4. Beide aus → ehrliche Fehlermeldung
    ersatz.an = False
    cfg, client = frisch()
    meldungen = []
    check("Sind beide aus, wird das gemeldet statt Erfolg vorzutäuschen",
          client.verbindung_pruefen(melden=meldungen.append) is False)
    check("Und die Meldung nennt beide Server",
          haupt_url in " ".join(meldungen) and ersatz_url in " ".join(meldungen),
          " ".join(meldungen)[:200])

    # ====================== 5. Kein Ausweichserver eingetragen
    ersatz.an = True
    cfg, client = frisch(host_alternative="")
    meldungen = []
    check("Ohne Eintrag wird auf OLLAMA_HOST_ALTERNATIVE hingewiesen",
          not client.verbindung_pruefen(melden=meldungen.append)
          and "OLLAMA_HOST_ALTERNATIVE" in " ".join(meldungen),
          " ".join(meldungen)[:160])

    # ====================== 6. Rückkehr auf den Hauptserver
    haupt.an = True
    cfg, client = frisch()
    client.verbindung_pruefen()
    check("Läuft der Hauptserver wieder, bleibt man dort",
          not client.auf_ausweich and cfg.host == haupt_url)

    client.ausweichen("Von Hand")
    check("Von Hand umschalten geht", client.auf_ausweich and cfg.host == ersatz_url)
    client._zurueck_auf_haupt()
    check("Und zurück ebenso",
          not client.auf_ausweich and cfg.host == haupt_url
          and cfg.llm_model == "qwen3:27b", f"{cfg.host} · {cfg.llm_model}")

    bericht = "\n".join(client.serverbericht())
    check("Der Serverbericht nennt Haupt- und Ausweichserver",
          haupt_url in bericht and ersatz_url in bericht, bericht[:120])

    # ====================== 7. Umschalten MITTEN im Betrieb
    # Der Fall, um den es Josh geht: Der KI-Rechner wird ausgeschaltet,
    # während Zeus läuft. Ein abgeschalteter Rechner meldet sich NICHT als
    # Verbindungsfehler - er antwortet einfach nicht mehr, und das sieht von
    # außen aus wie ein langsam ladendes Modell. Deshalb muss nachgesehen
    # werden, ob der Server überhaupt noch da ist.
    cfg, client = frisch(idle_timeout=3)
    client.verbindung_pruefen()
    haupt_srv.shutdown()                  # Server wirklich stilllegen
    haupt_srv.server_close()
    client.session.close()                # offene Verbindung nicht wiederverwenden
    try:
        antwort = client.chat([{"role": "user", "content": "test"}])
    except Exception as exc:
        antwort = ""
        print(f"   (Aufruf endete mit {type(exc).__name__})")
    check("Fällt der Server im Betrieb aus, wird umgeschaltet und der Aufruf "
          "wiederholt",
          bool(antwort) and client.auf_ausweich, f"'{antwort[:40]}' / {cfg.host}")
    check("Der Grund wird festgehalten",
          "erreichbar" in client.umschaltgrund or "verloren" in client.umschaltgrund,
          client.umschaltgrund)

    # ====================== 8. Tempomessung
    lan = eigene_adresse()
    haupt2_srv, haupt2, haupt2_url = starten(["qwen3:27b", "nomic-embed-text"],
                                             adresse=lan or "127.0.0.1")
    cfg, client = frisch(host=haupt2_url, host_alternative="")
    cfg.host = haupt2_url
    client.haupt_host = haupt2_url

    messung = client.tempo_messen(tokens=40)
    check("Das Tempo kommt aus Ollamas eigener Zählung",
          messung.get("quelle") == "Ollama-Zählung", str(messung))
    # 40 Token in 2 s = 20 Token/s. Aus der Zeichenzahl ("eins zwei drei",
    # 15 Zeichen) käme etwas völlig anderes heraus - das ist der Punkt.
    check("Und ist exakt, nicht aus der Zeichenzahl geschätzt",
          messung.get("token_pro_s") == 20.0, str(messung.get("token_pro_s")))
    check("Die Ladezeit wird getrennt ausgewiesen",
          messung.get("aufwaermzeit") is not None, str(messung.get("aufwaermzeit")))
    check("Die Prompt-Verarbeitung wird getrennt ausgewiesen",
          messung.get("prompt_pro_s") == 200.0, str(messung.get("prompt_pro_s")))
    check("Der befragte Server steht dabei", messung.get("host") == haupt2_url)

    # Ein langsamer Server: 40 Token in 80 s = 0,5 Token/s. Das ist der Wert,
    # den Josh gesehen hat - hier muss er tatsächlich herauskommen.
    haupt2.eval_ns = 80_000_000_000
    messung = client.tempo_messen(tokens=40)
    check("Ein wirklich langsamer Server wird auch als langsam gemessen",
          messung.get("token_pro_s") == 0.5, str(messung.get("token_pro_s")))

    # Alte Ollama-Fassung ohne Zählwerte: schätzen, aber es dazusagen
    haupt2.zaehlwerte = False
    messung = client.tempo_messen(tokens=40)
    check("Ohne Zählwerte wird geschätzt",
          messung.get("token_pro_s", 0) > 0, str(messung))
    check("Und die Schätzung wird als solche gekennzeichnet",
          "geschätzt" in str(messung.get("quelle")), str(messung.get("quelle")))
    haupt2.zaehlwerte = True

    # ====================== 9. Entfernter Server: lokale Hardware zählt nicht
    from jarvis.hardware import analysieren, ist_entfernt

    check("localhost gilt als eigener Rechner",
          not ist_entfernt(Settings(host="http://localhost:11434")))
    check("127.0.0.1 ebenso",
          not ist_entfernt(Settings(host="http://127.0.0.1:11434")))
    check("Eine Adresse im Netz gilt als entfernt",
          ist_entfernt(Settings(host="http://192.168.173.176:11434")))

    if not lan:
        print("   (keine LAN-Adresse verfügbar – Abschnitt 9 übersprungen)")
    else:
        befund = analysieren(cfg, tempo=20.0)
        text = befund.bericht()
        check("Bei entferntem Server wird die lokale GPU nicht behauptet",
              befund.entfernt and "GPU: keine erkannt" not in text, text[:200])
        check("Der Bericht sagt, wo gerechnet wird", haupt2_url in text, text[:120])
        check("Und weist die Netz-Laufzeit als Einflussgröße aus",
              "über das Netz" in text, text[-400:])
        check("Der Ladezustand auf dem SERVER wird ausgewiesen",
              "auf dem Server" in text, text[:300])

        # Server ohne GPU: der Fall, der ein niedriges Tempo vollständig erklärt
        class OhneGpu(type(haupt2)):
            pass
        haupt2_srv.RequestHandlerClass = haupt2
        original_get = haupt2.do_GET

        def kein_vram(self):
            if self.path.startswith("/api/ps"):
                self._senden({"models": [{"name": "qwen3:27b",
                                          "size": 18 * 1024 ** 3, "size_vram": 0}]})
                return
            original_get(self)

        haupt2.do_GET = kein_vram
        befund = analysieren(cfg, tempo=0.5)
        text = befund.bericht()
        check("Liegt auf dem Server nichts im VRAM, wird das als Ursache benannt",
              "vollständig auf der CPU" in text, text[:400])
        check("Und die Formel dazu steht dabei",
              "Speicherbandbreite" in text, text[:600])
        check("Mit einem konkreten nächsten Schritt auf dem Server",
              "nvidia-smi" in text or "ollama ps" in text)
        haupt2.do_GET = original_get

        # Server nicht erreichbar: brauchbare Hinweise statt Ratlosigkeit
        haupt2.an = False
        befund = analysieren(cfg)
        text = befund.bericht()
        check("Antwortet der Server nicht, nennt der Bericht die übliche Ursache",
              "0.0.0.0:11434" in text, text[:300])
        check("Und den Ausweichserver als Gegenmaßnahme",
              "OLLAMA_HOST_ALTERNATIVE" in text)

    # ============ 10. "no user query found in messages" (Ollama 0.3x + qwen3)
    # Der Fehler aus Joshs Protokoll. Nach Werkzeugaufrufen endet die
    # Nachrichtenliste mit 'tool'-Einträgen; die Vorlage mancher Modelle findet
    # dann keine Frage mehr und der Server lehnt mit 500 ab. Für den Benutzer
    # sah das aus wie ein "Verbindungsproblem" - es war die Nachrichtenliste.
    class Waehlerisch(FakeOllama):
        """Antwortet nur, wenn die LETZTE Nachricht von der Rolle 'user' ist."""

        modelle = ["qwen3:27b", "nomic-embed-text"]
        an = True
        gesehen: list = []

        def do_POST(self):
            laenge = int(self.headers.get("Content-Length", 0) or 0)
            roh = self.rfile.read(laenge)
            try:
                nachrichten = json.loads(roh).get("messages") or []
            except ValueError:
                nachrichten = []
            type(self).gesehen.append([m.get("role") for m in nachrichten])
            if not nachrichten or nachrichten[-1].get("role") != "user":
                koerper = json.dumps(
                    {"error": "no user query found in messages"}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(koerper)))
                self.end_headers()
                self.wfile.write(koerper)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"message": {"content": "Antwort aus den Ergebnissen."}}
            ).encode("utf-8") + b"\n")
            self.wfile.write(json.dumps(
                {"message": {"content": ""}, "done": True,
                 "eval_count": 10, "eval_duration": 1_000_000_000}
            ).encode("utf-8") + b"\n")

    waehl_srv = HTTPServer(("127.0.0.1", 0), Waehlerisch)
    threading.Thread(target=waehl_srv.serve_forever, daemon=True).start()
    waehl_url = f"http://127.0.0.1:{waehl_srv.server_address[1]}"

    cfg, client = frisch(host=waehl_url, host_alternative="")
    cfg.host = waehl_url
    client.haupt_host = waehl_url

    # Die Nachrichtenliste, wie sie nach einem Werkzeugaufruf aussieht:
    nach_werkzeug = [
        {"role": "system", "content": "Du bist JARVIS."},
        {"role": "user", "content": "Nutzt /messen die Prüfaufgaben?"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "notizen_suchen", "arguments": {}}}]},
        {"role": "tool", "name": "notizen_suchen", "content": "8 Treffer."},
    ]
    Waehlerisch.gesehen = []
    antwort = client.chat(nach_werkzeug)
    check("Nach Werkzeugergebnissen kommt trotzdem eine Antwort",
          "Antwort aus den Ergebnissen" in antwort, repr(antwort)[:80])
    check("Dafür wurde genau EINMAL nachgebessert",
          len(Waehlerisch.gesehen) == 2, str(len(Waehlerisch.gesehen)))
    check("Und die Nachbesserung war eine abschließende Nutzerzeile",
          Waehlerisch.gesehen[-1][-1] == "user", str(Waehlerisch.gesehen[-1]))

    # Die ursprüngliche Frage muss in der Nachbesserung auftauchen, sonst
    # antwortet das Modell auf etwas anderes.
    from jarvis.llm import _nutzerfrage_anhaengen

    ergaenzt = _nutzerfrage_anhaengen(nach_werkzeug)
    check("Die ergänzte Zeile nennt die ursprüngliche Frage",
          "Nutzt /messen die Prüfaufgaben?" in ergaenzt[-1]["content"],
          ergaenzt[-1]["content"][:100])
    check("Und untersagt weitere Werkzeugaufrufe",
          "Keine weiteren Werkzeugaufrufe" in ergaenzt[-1]["content"])
    check("Die vorhandenen Nachrichten bleiben unangetastet",
          ergaenzt[:len(nach_werkzeug)] == nach_werkzeug)

    # Ein Server, der IMMER ablehnt, darf nicht endlos wiederholen.
    class ImmerNein(Waehlerisch):
        gesehen: list = []

        def do_POST(self):
            koerper = json.dumps(
                {"error": "no user query found in messages"}).encode("utf-8")
            type(self).gesehen.append(1)
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(koerper)))
            self.end_headers()
            self.wfile.write(koerper)

    nein_srv = HTTPServer(("127.0.0.1", 0), ImmerNein)
    threading.Thread(target=nein_srv.serve_forever, daemon=True).start()
    cfg.host = f"http://127.0.0.1:{nein_srv.server_address[1]}"
    ImmerNein.gesehen = []
    try:
        client.chat(nach_werkzeug)
        geplatzt = False
    except Exception:
        geplatzt = True
    check("Ein Server, der immer ablehnt, führt nicht in eine Endlosschleife",
          geplatzt and len(ImmerNein.gesehen) == 2, str(len(ImmerNein.gesehen)))

    # ====================== 11. Modellnamen: Marke zählt
    check("'qwen2.5:3b' ist NICHT dasselbe wie 'qwen2.5:14b'",
          not OllamaClient._modell_vorhanden("qwen2.5:3b", ["qwen2.5:14b"]))
    check("'qwen2.5' ohne Marke passt auf 'qwen2.5:14b'",
          OllamaClient._modell_vorhanden("qwen2.5", ["qwen2.5:14b"]))
    check("Exakte Übereinstimmung passt",
          OllamaClient._modell_vorhanden("qwen2.5:14b", ["qwen2.5:14b"]))
    check("':latest' passt auf den nackten Namen",
          OllamaClient._modell_vorhanden("llama3:latest", ["llama3"]))
    check("Ein gar nicht vorhandenes Modell passt nicht",
          not OllamaClient._modell_vorhanden("mistral", ["qwen2.5:14b"]))

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
