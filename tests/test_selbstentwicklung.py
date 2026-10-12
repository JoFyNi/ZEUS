"""Tests für den Kern am echten Agenten und für die Selbstentwicklung.

Der zweite Teil ist der wichtigere. ENTWICKLUNG.md hält fest:

    "Ein Agent, der sich selbst umschreibt, während er läuft, ist keine
     Funktion, sondern eine Fehlerquelle ohne Rückweg."

Der Satz bleibt richtig. Was hier geprüft wird, ist deshalb nicht, DASS Zeus
seinen Code ändern kann, sondern dass jeder einzelne Riegel hält:

  * Der Quellbaum wird beim Vorschlagen nicht angefasst.
  * Ein Vorschlag mit Syntaxfehler kommt nicht bis zum Testlauf.
  * Ein Vorschlag, der die Tests rot macht, wird abgelehnt - auf der KOPIE,
    nicht im Original.
  * Ein Vorschlag auf eine unantastbare Datei wird abgelehnt.
  * Zeus kann nichts selbst übernehmen; das geht nur über die Freigabe.
  * Beim Übernehmen wird der vorherige Stand gesichert.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_selbst_"))
VAULT = TMP / "Zeus"
PROJEKT = TMP / "projekt"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["CODE_ROOTS"] = str(PROJEKT)
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"
os.environ["KERN_AKTIV"] = "true"

from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.kern.rechte import AUTONOM, SELBSTAENDERUNG  # noqa: E402
from jarvis.kern.selbst import Quarantaene  # noqa: E402

FAILURES = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


# Ein winziges Projekt mit eigener Testreihe - die Quarantäne braucht eine,
# sonst verweigert sie zu Recht die Arbeit.
RECHNER = '''"""Ein kleines Modul zum Prüfen."""


def verdoppeln(x):
    return x * 2


def dreifach(x):
    return x * 3
'''

PROBE = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rechner import verdoppeln, dreifach

fehler = []
if verdoppeln(3) != 6:
    fehler.append("verdoppeln")
if dreifach(3) != 9:
    fehler.append("dreifach")
print("Alle Tests bestanden." if not fehler else f"FEHLER: {fehler}")
sys.exit(1 if fehler else 0)
'''


def projekt_bauen() -> None:
    PROJEKT.mkdir(parents=True)
    (PROJEKT / "rechner.py").write_text(RECHNER, encoding="utf-8")
    (PROJEKT / "tests").mkdir()
    (PROJEKT / "tests" / "test_rechner.py").write_text(PROBE, encoding="utf-8")


def quarantaene_pruefen() -> None:
    print("\n--- Quarantäne ---")
    quarantaene = Quarantaene(PROJEKT, TMP / "quarantaene")
    original = (PROJEKT / "rechner.py").read_text(encoding="utf-8")

    # ------------------------------------------------- 1. Ein guter Vorschlag
    gut = RECHNER.replace("return x * 2", "return x + x")
    vorschlag = quarantaene.einreichen("rechner.py", gut,
                                       "x + x ist dasselbe und liest sich klarer")
    check("Ein sauberer Vorschlag wird angenommen", vorschlag.zustand == "neu",
          vorschlag.abgelehnt_weil)
    check("Der Quellbaum bleibt dabei unberührt",
          (PROJEKT / "rechner.py").read_text(encoding="utf-8") == original)
    check("Der Diff wird mitgeführt", "return x + x" in vorschlag.diff)
    check("Die geänderten Zeilen sind gezählt", vorschlag.geaenderte_zeilen == 2,
          str(vorschlag.geaenderte_zeilen))

    geprueft = quarantaene.pruefen(vorschlag)
    check("Nach grünem Testlauf gilt er als geprüft", geprueft.zustand == "geprueft",
          geprueft.abgelehnt_weil)
    check("Die Testausgabe ist festgehalten",
          "test_rechner.py" in geprueft.testausgabe, geprueft.testausgabe)
    check("Auch nach dem Testlauf ist der Quellbaum unberührt",
          (PROJEKT / "rechner.py").read_text(encoding="utf-8") == original)

    # -------------------------------------- 2. Ein Vorschlag, der etwas kaputt macht
    kaputt = RECHNER.replace("return x * 2", "return x * 3")
    schlecht = quarantaene.einreichen("rechner.py", kaputt, "so ist es symmetrischer")
    schlecht = quarantaene.pruefen(schlecht)
    check("Ein Vorschlag, der die Tests rot macht, fällt durch",
          schlecht.zustand == "durchgefallen", schlecht.zustand)
    check("Der Grund nennt die rote Reihe",
          "test_rechner.py" in schlecht.abgelehnt_weil, schlecht.abgelehnt_weil)
    check("Der Quellbaum hat davon nichts abbekommen",
          (PROJEKT / "rechner.py").read_text(encoding="utf-8") == original)

    # ------------------------------------------------- 3. Syntaxfehler
    syntax = quarantaene.einreichen("rechner.py", "def kaputt(:\n    pass\n", "ups")
    check("Ein Syntaxfehler kommt gar nicht erst zum Testlauf",
          syntax.zustand == "durchgefallen" and "Syntaxfehler" in syntax.abgelehnt_weil,
          syntax.abgelehnt_weil)

    # ------------------------------------------------- 4. Schranken
    raus = quarantaene.einreichen("../draussen.py", "x = 1\n", "ausbrechen")
    check("Ein Pfad aus dem Projektbaum heraus wird abgelehnt",
          raus.zustand == "durchgefallen", raus.abgelehnt_weil)
    neu = quarantaene.einreichen("gibtsnicht.py", "x = 1\n", "neue Datei")
    check("Neue Dateien legt kein Vorschlag an", neu.zustand == "durchgefallen",
          neu.abgelehnt_weil)
    gleich = quarantaene.einreichen("rechner.py", RECHNER, "keine Änderung")
    check("Ein Vorschlag, der nichts ändert, wird abgelehnt",
          gleich.zustand == "durchgefallen", gleich.abgelehnt_weil)

    riesig = RECHNER + "\n".join(f"def f{i}(): return {i}" for i in range(200))
    gross = quarantaene.einreichen("rechner.py", riesig, "alles neu")
    check("Ein Vorschlag über der Zeilenschranke wird abgelehnt",
          gross.zustand == "durchgefallen" and "Neubau" in gross.abgelehnt_weil,
          gross.abgelehnt_weil)

    # Die unantastbare Liste - hier über eine Datei, die es im Testprojekt gibt
    from jarvis.kern import selbst as selbstmodul
    selbstmodul.UNANTASTBAR.add("rechner.py")
    gesperrt = quarantaene.einreichen("rechner.py", gut, "nochmal")
    check("Eine unantastbare Datei wird abgelehnt",
          gesperrt.zustand == "durchgefallen" and "ausgenommen" in gesperrt.abgelehnt_weil,
          gesperrt.abgelehnt_weil)
    selbstmodul.UNANTASTBAR.discard("rechner.py")

    # ------------------------------------------------- 5. Übernehmen
    misslungen, meldung = quarantaene.uebernehmen(schlecht.kennung)
    check("Ein durchgefallener Vorschlag lässt sich nicht übernehmen",
          not misslungen and "durchgefallen" in meldung, meldung)

    geglueckt, meldung = quarantaene.uebernehmen(geprueft.kennung)
    check("Ein geprüfter Vorschlag lässt sich übernehmen", geglueckt, meldung)
    check("Die Änderung steht jetzt wirklich im Quellbaum",
          "return x + x" in (PROJEKT / "rechner.py").read_text(encoding="utf-8"))
    sicherungen = list((TMP / "quarantaene" / "vorher").glob("*rechner.py"))
    check("Der vorherige Stand ist gesichert", len(sicherungen) == 1,
          str([p.name for p in sicherungen]))
    check("Die Sicherung enthält den ALTEN Stand",
          sicherungen and sicherungen[0].read_text(encoding="utf-8") == original)
    check("Die Meldung sagt, dass ein Neustart nötig ist", "Neustart" in meldung)

    nochmal, meldung = quarantaene.uebernehmen(geprueft.kennung)
    check("Zweimal übernehmen geht nicht", not nochmal, meldung)

    bericht = quarantaene.bericht()
    check("Der Bericht führt beide Ausgänge",
          "▲" in bericht and "✖" in bericht, bericht[:120])


def kern_am_agenten_pruefen() -> None:
    print("\n--- Der Kern am echten Agenten ---")
    (VAULT / "Wissen").mkdir(parents=True)
    (VAULT / "Wissen" / "Regelung.md").write_text(
        "# Regelung\n\nRegler, Strecke, Rückführung.\n", encoding="utf-8")

    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.llm.chat = (lambda self, m, **k: "Text.").__get__(agent.llm)
    kern = agent.kern_starten()

    try:
        namen = set(kern.module)
        check("Alle sieben Module sind angemeldet",
              namen == {"gedaechtnis", "verarbeitung", "fertigkeiten", "chat",
                        "aufgaben", "autonomie", "selbstentwicklung"},
              ", ".join(sorted(namen)))
        check("Der Aufgabenstapel startet nach dem Chat",
              kern._reihenfolge.index("chat") < kern._reihenfolge.index("aufgaben"),
              str(kern._reihenfolge))
        check("Das Gespräch benutzt dasselbe Werkzeugtor wie die Aufgaben",
              getattr(agent, "_werkzeugtor", None) is kern.tor
              and kern.tor is not None)
        check("Das Gedächtnis läuft und hat den Bestand",
              kern.modul("gedaechtnis").gesundheit().kennzahlen.get("notizen", 0) >= 1)
        check("Das Gedächtnis startet vor dem Chat",
              kern._reihenfolge.index("gedaechtnis") < kern._reihenfolge.index("chat"),
              str(kern._reihenfolge))

        # Der entscheidende Punkt für Joshs Vorgabe: Ollama ist NICHT das
        # Grundsystem. Ohne Ollama muss Zeus laufen.
        verarbeitung = kern.modul("verarbeitung")
        check("Ollama ist ausdrücklich nicht wesentlich", not verarbeitung.wesentlich)
        check("Ohne Ollama läuft der Kern trotzdem", kern.laeuft)
        check("Das Chatmodul läuft auch ohne erreichbares Modell",
              kern.modul("chat").gesundheit().ok)
        check("Die Statuszeile sagt, dass Ollama fehlt",
              not verarbeitung.gesundheit().ok
              and "nicht erreichbar" in verarbeitung.gesundheit().meldung,
              verarbeitung.gesundheit().meldung[:70])

        # Arbeit über den Planer, mit Vault-Sperre
        auftrag = kern.beauftragen(
            "notiz", "Notiz über den Planer anlegen",
            lambda a: agent.tools.ausfuehren(
                "notiz_erstellen", {"titel": "Aus dem Planer", "inhalt": "Inhalt."}),
            braucht_vault=True)
        auftrag.warten(30)
        check("Ein Auftrag über den Planer schreibt wirklich in die Vault",
              bool(list(VAULT.glob("**/Aus_dem_Planer*.md"))
                   or list(VAULT.glob("**/Aus dem Planer*.md"))),
              str(auftrag.fehler or auftrag.zustand))

        # Rechte greifen auch über den Kern
        verweigert = False
        try:
            kern.beauftragen("wartung", "nächtlich", lambda a: None, wiederholen_alle=3600)
        except PermissionError:
            verweigert = True
        check("Unbeaufsichtigte Arbeit braucht Stufe 2", verweigert)

        # Selbstentwicklung: ohne Stufe 3 kein Patch
        selbst = kern.modul("selbstentwicklung")
        check("Die Selbstentwicklung ist bereit",
              selbst.quarantaene is not None,
              selbst.abgelehnt)
        abgelehnt = False
        try:
            selbst.vorschlagen("rechner.py", RECHNER + "\n# x\n", "kleine Ergänzung")
        except PermissionError:
            abgelehnt = True
        check("Ohne Rechtestufe 3 wird kein Patch gebaut", abgelehnt)

        kern.rechte.setzen(SELBSTAENDERUNG, durch="test")
        vorschlag = selbst.vorschlagen("rechner.py", RECHNER + "\n# geprüft\n",
                                       "Kommentar ergänzt", sofort_pruefen=True)
        check("Mit Stufe 3 wird der Vorschlag angenommen",
              vorschlag.zustand != "durchgefallen", vorschlag.abgelehnt_weil)
        kern.planer.ruhig(120)
        nachher = selbst.quarantaene.holen(vorschlag.kennung)
        check("Der Testlauf lief im Hintergrund und ist abgeschlossen",
              nachher and nachher.zustand in ("geprueft", "durchgefallen"),
              nachher.zustand if nachher else "weg")
        check("Zeus hat den Vorschlag NICHT selbst übernommen",
              nachher and nachher.zustand != "uebernommen")

        bericht = kern.bericht()
        check("Der Bericht nennt alle Module", all(n in bericht for n in namen))
        check("Der Bericht markiert das nicht erreichbare Ollama",
              "!" in bericht, bericht)
    finally:
        agent.kern_anhalten()

    check("Nach dem Anhalten steht der Kern", not kern.laeuft)
    check("Der Agent weiß, dass kein Kern mehr da ist", agent.kern is None)


def main() -> int:
    projekt_bauen()
    quarantaene_pruefen()
    kern_am_agenten_pruefen()
    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
