"""Tests für Robustheit (Timeouts) und die Lernschleife (Systemlernen)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_learn_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"
os.environ["IDLE_TIMEOUT"] = "3"
os.environ["REQUEST_TIMEOUT"] = "20"

from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.config import settings  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.llm import OllamaClient, OllamaError  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


# ----------------------------------------------------- Ollama-Attrappenserver
MODUS = {"art": "normal"}


class FakeOllama(BaseHTTPRequestHandler):
    # Wie echtes Ollama: HTTP/1.1 mit chunked Transfer-Encoding
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        laenge = int(self.headers.get("Content-Length", 0))
        self.rfile.read(laenge)
        if self.path.endswith("/api/show"):
            self._json({"capabilities": ["completion", "tools"]})
            return

        art = MODUS["art"]
        if art == "tot":                       # antwortet nie
            time.sleep(30)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            if art == "langsam_aber_stetig":
                # 12 Sekunden Arbeit, aber alle 0,4 s ein Token -> darf NICHT
                # in den Leerlauf-Timeout laufen
                for i in range(30):
                    self._zeile({"message": {"content": f"Wort{i} "}, "done": False})
                    time.sleep(0.4)
                self._zeile({"message": {"content": ""}, "done": True})
                self.wfile.write(b"0\r\n\r\n")
            elif art == "verstummt":
                for i in range(3):
                    self._zeile({"message": {"content": f"Anfang{i} "}, "done": False})
                time.sleep(30)                 # danach Funkstille
            else:
                self._zeile({"message": {"content": "Kurze Antwort."}, "done": True})
                self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _zeile(self, daten):
        koerper = (json.dumps(daten) + "\n").encode()
        self.wfile.write(f"{len(koerper):X}\r\n".encode() + koerper + b"\r\n")

    def _json(self, daten):
        körper = json.dumps(daten).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(körper)))
        self.end_headers()
        self.wfile.write(körper)


def main() -> int:
    (VAULT / "Deep Learning").mkdir(parents=True)
    (VAULT / "Deep Learning" / "Skript.md").write_text(
        "# Skript\n\n## Neuron\nGewichtete Summe und Aktivierung.\n", encoding="utf-8")

    server = HTTPServer(("127.0.0.1", 0), FakeOllama)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    settings.host = f"http://127.0.0.1:{port}"

    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.sync()
    client = agent.llm

    # ------------------------------------------- 1. Langsam ist kein Timeout
    MODUS["art"] = "langsam_aber_stetig"
    start = time.time()
    try:
        text = client.chat([{"role": "user", "content": "test"}])
        dauer = time.time() - start
        check("Langsame Generierung läuft durch (kein Timeout bei 3s Leerlaufgrenze)",
              "Wort29" in text and dauer > 10, f"{dauer:.1f}s, {len(text)} Zeichen")
    except OllamaError as exc:
        check("Langsame Generierung läuft durch (kein Timeout bei 3s Leerlaufgrenze)",
              False, str(exc))

    # ------------------------------------ 2. Echter Hänger wird schnell erkannt
    MODUS["art"] = "verstummt"
    start = time.time()
    text = client.chat([{"role": "user", "content": "test"}])
    dauer = time.time() - start
    check("Verstummen wird nach IDLE_TIMEOUT abgebrochen", dauer < 10, f"{dauer:.1f}s")
    check("Teilantwort bleibt erhalten", "Anfang0" in text, text[:40])
    check("Abbruch wird gemeldet", "nichts mehr" in client.last_warning, client.last_warning)

    # --------------------------------- 3. Keine Antwort = klare Fehlermeldung
    MODUS["art"] = "tot"
    start = time.time()
    try:
        client.chat([{"role": "user", "content": "test"}])
        check("Toter Server erzeugt Fehler", False, "kein Fehler geworfen")
    except OllamaError as exc:
        dauer = time.time() - start
        check("Toter Server erzeugt schnellen, verständlichen Fehler",
              dauer < 10 and "Modell" in str(exc), f"{dauer:.1f}s: {str(exc)[:70]}")

    # ------------------------------------------ 4. Hinweistext für den Nutzer
    hinweis = agent._verbindungshinweis(OllamaError("Keine Antwort innerhalb von 90s."))
    check("Timeout-Hinweis nennt konkrete Stellschrauben",
          "KEEP_ALIVE" in hinweis and "NUM_PREDICT" in hinweis and "NUM_CTX" in hinweis)

    # ------------------------------------------------- 5. Kontextbudget wirkt
    settings.num_ctx = 4096
    budget = settings.prompt_char_budget
    check("Prompt-Budget folgt num_ctx", 5000 < budget < 10000, str(budget))
    settings.num_ctx = 8192
    riesig = "Blabla. " * 20000
    prompt = agent.system_prompt(context=riesig, frage="test")
    check("Zu großer Kontext wird gekürzt, nicht durchgereicht",
          len(prompt) <= settings.prompt_char_budget + 200, f"{len(prompt)} Zeichen")
    check("Anweisungen bleiben nach dem Kürzen erhalten",
          "SPRACHE" in prompt and "REGELN" in prompt)

    # ---------------------------------------------------- 6. Lernschleife
    MODUS["art"] = "normal"
    lernwerk = agent.lernwerk

    class Antwort:
        text = "Irgendeine Antwort"
        kind = "rag"
        sources = ["Deep Learning/Skript.md"]
        tool_calls = []

    lernwerk.beobachten("Was ist ein Neuron?", Antwort(), 2.0)
    check("Beobachtung wird erfasst", len(lernwerk.beobachtungen) == 1)

    check("Korrektur wird erkannt",
          lernwerk.rueckmeldung_erkennen("das ist falsch, so war das nicht gemeint") == "negativ")
    check("Lob wird erkannt", lernwerk.rueckmeldung_erkennen("perfekt, danke") == "positiv")
    check("Neutrale Frage ist keine Rückmeldung",
          lernwerk.rueckmeldung_erkennen("Was ist Backpropagation?") is None)

    agent.llm.chat = lambda *a, **k: "Prüfe vor dem Anlegen einer Notiz, ob sie schon existiert."
    lektion = lernwerk.rueckmeldung_verarbeiten("das ist falsch", "negativ")
    check("Aus der Korrektur entsteht eine Lektion", bool(lektion), str(lektion))
    check("Lektion liegt als Notiz in der Vault",
          (VAULT / settings.learning_folder / "Lektionen.md").exists())
    check("Lektion wird wieder eingelesen", lektion in lernwerk.lektionen())

    lernwerk.lektion_speichern("Prüfe vor dem Anlegen einer Notiz, ob sie schon existiert!")
    check("Nahezu identische Lektion wird nicht doppelt gespeichert",
          len(lernwerk.lektionen()) == 1, str(lernwerk.lektionen()))

    # Paraphrasen bleiben bewusst stehen - lexikalisch zu verschmelzen wäre
    # riskant ("vor dem Anlegen" vs. "vor dem Löschen"). Dafür gibt es die
    # ausdrückliche Konsolidierung mit Modellurteil.
    lernwerk.lektion_speichern("Sieh vorher nach, ob zum Thema schon eine Notiz da ist.")
    check("Paraphrase wird zunächst behalten", len(lernwerk.lektionen()) == 2,
          str(lernwerk.lektionen()))
    agent.llm.chat = lambda *a, **k: json.dumps(
        {"regeln": ["Prüfe vor dem Anlegen einer Notiz, ob sie schon existiert."]})
    meldung = lernwerk.lektionen_konsolidieren()
    check("Konsolidierung fasst Paraphrasen zusammen",
          len(lernwerk.lektionen()) == 1, meldung)

    prompt = agent.system_prompt(frage="Notiz anlegen")
    check("Lektion landet im Systemprompt (Systemlernen)",
          "GELERNTE REGELN" in prompt and lektion in prompt)

    # ------------------------------------------------- 7. Wissenslücken
    class Leer:
        text = "weiß ich nicht"
        kind = "rag"
        sources = []
        tool_calls = []

    lernwerk.beobachten("Was ist ein Kalman-Filter?", Leer(), 1.0)
    check("Unbeantwortbare Frage wird als Lücke vermerkt",
          any("Kalman" in l for l in lernwerk.offene_luecken()), str(lernwerk.offene_luecken()))

    kennzahlen = lernwerk.kennzahlen()
    check("Kennzahlen werden berechnet",
          kennzahlen["zuege"] == 2 and kennzahlen["lektionen"] == 1, str(kennzahlen))

    # ------------------------------------------------- 8. Fertigkeiten
    fert = agent.fertigkeiten
    ergebnis = fert.definieren(
        name="thema aufarbeiten",
        beschreibung="Sucht ein Thema und legt eine Übersicht an",
        parameter=["thema"],
        schritte=[
            'notizen_suchen(frage="{thema}", anzahl=5)',
            'notiz_erstellen(titel="Übersicht {thema}", inhalt="Sammlung zu {thema}: {ergebnis1}")',
        ],
    )
    check("Fertigkeit wird angelegt", ergebnis.ok, ergebnis.meldung)
    check("Fertigkeit liegt als Notiz vor",
          (VAULT / settings.skills_folder / "thema_aufarbeiten.md").exists())
    check("Fertigkeit ist geladen", "thema_aufarbeiten" in fert.alle)

    schlecht = fert.definieren("boese", "Test", [], ['rm_rf(pfad="/")'])
    check("Unbekanntes Werkzeug wird abgelehnt", not schlecht.ok, schlecht.meldung)
    schlecht2 = fert.definieren("boese2", "Test", [], ['notiz_erstellen(titel="{unbekannt}")'])
    check("Unbekannter Platzhalter wird abgelehnt", not schlecht2.ok, schlecht2.meldung)
    schlecht3 = fert.definieren("boese3", "Test", [], ['notizen_suchen(gibtsnicht="x")'])
    check("Unbekannter Parameter wird abgelehnt", not schlecht3.ok, schlecht3.meldung)

    lauf = fert.ausfuehren("thema_aufarbeiten", {"thema": "Neuron"})
    check("Fertigkeit läuft durch", lauf.ok, lauf.meldung)
    erzeugt = list(VAULT.rglob("Übersicht_Neuron*.md"))
    check("Fertigkeit hat wirklich geschrieben", bool(erzeugt),
          str([p.name for p in erzeugt]))
    fehlend = fert.ausfuehren("thema_aufarbeiten", {})
    check("Fehlende Parameter werden gemeldet", not fehlend.ok, fehlend.meldung)

    check("Fertigkeit taucht in der Werkzeugliste auf",
          any(w["function"]["name"] == "thema_aufarbeiten" for w in agent._werkzeugliste()))
    check("Definier-Werkzeug ist verfügbar",
          any(w["function"]["name"] == "fertigkeit_definieren" for w in agent._werkzeugliste()))

    # --------------------------------------------------------- 9. Reflexion
    agent.llm.chat = lambda *a, **k: "## Was gut lief\nAlles.\n## Wo es hakte\nNichts."
    bericht = lernwerk.reflektieren(schreiben=True)
    check("Reflexionsbericht entsteht", "Was gut lief" in bericht)
    check("Reflexion wird als Notiz abgelegt",
          bool(list((VAULT / settings.learning_folder).glob("Reflexion_*.md"))))

    server.shutdown()
    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
