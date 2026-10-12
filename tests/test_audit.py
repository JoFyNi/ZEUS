"""Tests für das Betriebsprotokoll (Audit-Log).

Heißt test_audit.py und nicht test_protokoll.py: Der Name war schon vergeben.
`tests/test_protokoll.py` prüft die Befunde aus Joshs echten Vault-Protokollen
vom 20.-24.08. - eine andere Sache, die erhalten bleibt.



Das Protokoll beantwortet drei Fragen, die vorher unbeantwortet waren:
was wurde geändert, was ist gescheitert, und **was verlässt den Rechner**.

Die letzte ist der Grund, warum diese Reihe streng ist. "Zeus ist lokal" ist
eine Behauptung, solange niemand nachsehen kann. Wenn das Protokoll eine
Anfrage übersieht, ist es schlimmer als keins - dann behauptet es Vollzähligkeit,
die es nicht hat.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_log_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.protokoll import (  # noqa: E402
    EBENEN, Eintrag, Protokoll, ProtokollSession, protokoll,
)

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def build_vault() -> None:
    (VAULT / "Wissen").mkdir(parents=True)
    (VAULT / "Wissen" / "Anfang.md").write_text("# Anfang\n\nText.\n", encoding="utf-8")


# ================================================== 1. Der Kern
def test_kern() -> None:
    datei = TMP / "probe.jsonl"
    log = Protokoll(cfg=None, pfad=datei)

    log.info("Start", "test")
    log.erfolg("geklappt", "werkzeug", dauer=1.2)
    log.fehler("kaputt", "werkzeug")
    log.aenderung("angelegt: x.md", "notiz_erstellen", datei="x.md")

    check("Alle Einträge sind da", len(log.lesen()) == 4)
    check("Laufende Nummer zählt hoch",
          [e["nummer"] for e in log.lesen()] == [1, 2, 3, 4])
    check("Filter nach Ebene", len(log.lesen(ebene="fehler")) == 1)
    check("ab_nummer liefert nur Neueres",
          [e["nummer"] for e in log.lesen(ab_nummer=2)] == [3, 4])
    zahlen = log.zahlen()
    check("Zahlen stimmen",
          zahlen["gesamt"] == 4 and zahlen["fehler"] == 1 and zahlen["aenderung"] == 1,
          str(zahlen))
    check("Unbekannte Ebene wird zu info",
          log.melden("quatsch", "x").ebene == "info")

    check("Die Datei wird geschrieben", datei.is_file())
    zeilen = [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines()]
    check("Jede Zeile ist gültiges JSON", len(zeilen) == 5, str(len(zeilen)))
    check("Die Daten stehen mit drin",
          any(z.get("daten", {}).get("dauer") == 1.2 for z in zeilen))

    vorher = len(log.lesen())
    geleert = log.leeren()
    check("Leeren räumt die Anzeige", geleert == vorher and not log.lesen())
    check("Leeren löscht die Datei NICHT – sie ist der Nachweis", datei.is_file())

    check("Zeile lässt sich formatieren",
          "✔" in Eintrag(ebene="erfolg", text="ok").zeile())
    check("Alle Ebenen haben ein Zeichen",
          all(Eintrag(ebene=e, text="x").zeile() for e in EBENEN))


def test_zuhoerer() -> None:
    log = Protokoll(cfg=None, pfad=TMP / "hoerer.jsonl")
    gehoert = []
    abmelden = log.zuhoeren(gehoert.append)
    log.info("eins")
    log.info("zwei")
    abmelden()
    log.info("drei")
    check("Zuhörer bekommt Einträge live", [e.text for e in gehoert] == ["eins", "zwei"],
          str([e.text for e in gehoert]))
    check("Abmelden wirkt", len(gehoert) == 2)

    def tauber(_):
        raise RuntimeError("ich bin kaputt")

    log.zuhoeren(tauber)
    log.info("vier")
    check("Ein kaputter Zuhörer reißt nichts mit",
          log.lesen()[-1]["text"] == "vier")


def test_schreibfehler() -> None:
    """Ein nicht beschreibbarer Pfad darf nichts zum Absturz bringen.

    Der Blockierer ist eine DATEI, wo ein Ordner sein müsste. Ein einfach
    nicht existierender Pfad taugt nicht: Mit genug Rechten legt mkdir ihn
    klaglos an - der erste Entwurf dieses Tests ist genau darüber gestolpert
    und hat dabei nebenbei /gibt/es/nicht/ erzeugt.
    """
    sperre = TMP / "sperre"
    sperre.write_text("hier ist kein Ordner", encoding="utf-8")
    log = Protokoll(cfg=None, pfad=sperre / "protokoll.jsonl")
    log.info("trotzdem")
    check("Ohne Schreibrecht läuft es weiter", len(log.lesen()) == 1)
    check("Der Schreibfehler wird gezählt, nicht verschwiegen",
          log.schreibfehler == 1, str(log.schreibfehler))


# ======================================= 2. Netzverkehr wird mitgeschrieben
class _Antwort:
    status_code = 200
    content = b"x" * 42


class _FalscheSession:
    def __init__(self):
        self.aufrufe = []

    def get(self, url, **kw):
        self.aufrufe.append(("GET", url, kw))
        return _Antwort()

    def post(self, url, **kw):
        self.aufrufe.append(("POST", url, kw))
        if "kaputt" in url:
            raise ConnectionError("Verbindung abgelehnt")
        return _Antwort()


def test_netz() -> None:
    log = Protokoll(cfg=None, pfad=TMP / "netz.jsonl")
    unten = _FalscheSession()
    sitzung = ProtokollSession(session=unten, log=log, quelle="ollama")

    sitzung.get("http://localhost:11434/api/tags", timeout=4)
    sitzung.post("http://192.168.1.5:11434/api/chat",
                 json={"model": "qwen3:32b", "messages": []}, stream=True)

    check("Die Anfrage geht wirklich raus", len(unten.aufrufe) == 2, str(unten.aufrufe))
    eintraege = log.lesen()
    raus = [e for e in eintraege if e["ebene"] == "netz_raus"]
    rein = [e for e in eintraege if e["ebene"] == "netz_rein"]
    check("Jede Anfrage steht im Protokoll", len(raus) == 2, str(len(raus)))
    check("Jede Antwort steht im Protokoll", len(rein) == 2, str(len(rein)))
    check("Das Ziel steht dabei",
          "192.168.1.5:11434" in raus[1]["text"], raus[1]["text"])
    check("Das Modell steht dabei",
          raus[1]["daten"].get("modell") == "qwen3:32b", str(raus[1]["daten"]))
    check("Die gesendete Größe steht dabei",
          raus[1]["daten"].get("bytes", 0) > 0, str(raus[1]["daten"]))
    check("Die empfangene Größe steht dabei",
          rein[0]["daten"].get("bytes") == 42, str(rein[0]["daten"]))
    check("Die Dauer steht dabei", "dauer" in rein[0]["daten"])

    # Ein Fehlschlag muss auch drinstehen - gerade der.
    try:
        sitzung.post("http://kaputt:11434/api/chat", json={})
    except ConnectionError:
        pass
    else:
        check("Der Fehler wird durchgereicht", False, "keine Ausnahme")
    fehler = [e for e in log.lesen() if e["ebene"] == "fehler"]
    check("Eine gescheiterte Anfrage steht im Protokoll", len(fehler) == 1,
          str(len(fehler)))
    check("Der Grund steht dabei", "abgelehnt" in fehler[0]["text"], fehler[0]["text"])


def test_alle_netzwege_gehen_durchs_protokoll() -> None:
    """Kein Modul darf an der protokollierenden Session vorbei ins Netz.

    Das ist die eigentliche Zusage dieses Protokolls. Ein `requests.get(` ohne
    Umweg wäre ein blinder Fleck - und ein Protokoll mit blindem Fleck ist
    schlimmer als keins, weil es Vollzähligkeit behauptet.
    """
    import re

    wurzel = Path(__file__).resolve().parents[1] / "jarvis"
    blind = []
    for datei in sorted(wurzel.rglob("*.py")):
        if datei.name == "protokoll.py":
            continue
        text = datei.read_text(encoding="utf-8", errors="replace")
        for treffer in re.finditer(r"\brequests\.(get|post|put|delete|request)\s*\(",
                                   text):
            zeile = text[:treffer.start()].count("\n") + 1
            blind.append(f"{datei.name}:{zeile}")
    check("Kein direkter requests-Aufruf am Protokoll vorbei", not blind, str(blind))


# ============================= 3. Werkzeuge und Änderungen landen im Protokoll
def test_werkzeuge(agent) -> None:
    log = protokoll()
    log.leeren()

    agent.tools.ausfuehren("notiz_erstellen",
                           {"titel": "Protokollprobe", "inhalt": "Text.",
                            "ordner": "Wissen"})
    agent.tools.ausfuehren("notiz_lesen", {"name": "GibtEsNichtWirklich"})
    agent.tools.ausfuehren("gibt_es_nicht", {})

    eintraege = log.lesen()
    ebenen = [e["ebene"] for e in eintraege]
    check("Ein Erfolg steht drin", "erfolg" in ebenen, str(ebenen))
    check("Ein Fehler steht drin", "fehler" in ebenen, str(ebenen))
    check("Eine Dateiänderung steht drin", "aenderung" in ebenen, str(ebenen))

    aenderung = next(e for e in eintraege if e["ebene"] == "aenderung")
    check("Die geänderte Datei wird genannt",
          "Protokollprobe" in str(aenderung["daten"].get("datei", "")),
          str(aenderung["daten"]))
    check("Neu angelegt wird als solches vermerkt",
          aenderung["daten"].get("neu") is True, str(aenderung["daten"]))

    fehler = [e for e in eintraege if e["ebene"] == "fehler"]
    check("Der fehlgeschlagene Aufruf nennt sein Werkzeug",
          any(e["quelle"] == "notiz_lesen" for e in fehler), str(fehler))
    check("Ein unbekanntes Werkzeug wird als Fehler vermerkt",
          any("Unbekanntes Werkzeug" in e["text"] for e in fehler), str(fehler))

    check("Parameter werden mitgeschrieben",
          any(e["daten"].get("parameter") for e in eintraege if e["ebene"] == "erfolg"))
    # Ein 60-kB-Inhalt hat im Protokoll nichts verloren.
    log.leeren()
    agent.tools.ausfuehren("notiz_erstellen",
                           {"titel": "Lang", "inhalt": "x" * 5000, "ordner": "Wissen"})
    erfolg = next(e for e in log.lesen() if e["ebene"] == "erfolg")
    check("Lange Parameter werden gekürzt",
          len(str(erfolg["daten"].get("parameter", {}).get("inhalt", ""))) < 200,
          str(len(str(erfolg["daten"].get("parameter", {}).get("inhalt", "")))))


def main() -> int:
    build_vault()
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.llm.chat = (lambda self, m, **k: "Text.").__get__(agent.llm)
    agent.sync()

    test_kern()
    test_zuhoerer()
    test_schreibfehler()
    test_netz()
    test_alle_netzwege_gehen_durchs_protokoll()
    test_werkzeuge(agent)

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
