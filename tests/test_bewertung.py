"""Der TaskAssessor, gemessen an einem festen Satz Aufgaben.

§32 verlangt ein Evaluationsset statt eines Gefuehls. Das hier ist es: 40
Auftraege, wie ein Mensch sie wirklich tippt, jeder mit der erwarteten
Absicht und der Erwartung, ob ein Werkzeug allein reicht.

Der Massstab ist NICHT "erkennt alles". Ein Regelwerk, das 100 % erreicht,
ist auf seine eigenen Testfaelle gebaut. Der Massstab ist:

    * die Absicht stimmt in den allermeisten Faellen,
    * es gibt keine GEFAEHRLICHEN Verwechslungen (ein Loeschauftrag darf nie
      als Schreibauftrag durchgehen),
    * Aufgaben, die ein Werkzeug loest, werden als solche erkannt - das ist
      der ganze Gewinn an Geschwindigkeit,
    * und der Bewerter laeuft OHNE Modell vollstaendig durch.

Die letzte Bedingung ist die wichtigste: Ein Einordner, der fuer jede
Einordnung einen Modellaufruf braucht, kostet genau das, was er sparen soll.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TMP = Path(tempfile.mkdtemp(prefix="jarvis_bewertung_")).resolve()
(TMP / "Zeus" / "00_Inbox").mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(TMP / "Zeus")
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vs")
os.environ["VAULT_GIT"] = "false"
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

import stubs  # noqa: E402,F401

from jarvis.kern.bewertung import (  # noqa: E402
    ABRUF, ANALYSE, FRAGE, GESPRAECH, LESEN, META, SCHREIBEN, UMBAUEN,
    WARTUNG, ZAEHLEN, Aufgabenbewerter, Befund)

FAILURES = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


# ═══════════════════════════════════════════════ Das Bewertungsset ═══
# (Auftrag, erwartete Absicht, Werkzeug reicht?)
SATZ = [
    # --- deterministisch loesbar: der Fast Path aus §20 ---------------
    ("Wie viele Dateien sind in meiner Inbox?", ZAEHLEN, True),
    ("Wie viele Notizen habe ich insgesamt?", ZAEHLEN, True),
    ("Zähl mir die offenen Punkte", ZAEHLEN, True),
    ("Finde meine Notizen über neuronale Netze", ABRUF, True),
    ("Such nach allem zum Thema Sandkisten", ABRUF, True),
    ("Zeig mir die Ordnerstruktur", ABRUF, True),
    ("Wo liegt die Notiz zur Verifikation?", ABRUF, True),
    ("Welche Notiz behandelt Backpropagation?", ABRUF, True),
    ("Lies mir die Notiz Sandkisten vor", LESEN, True),
    ("Was steht in der Notiz Verifikation?", LESEN, True),
    ("Prüf die Verweise in der Vault", WARTUNG, True),
    ("Gibt es tote Links?", WARTUNG, True),

    # --- braucht ein Modell -------------------------------------------
    ("Erstelle eine Notiz zum Thema Vektordatenbanken", SCHREIBEN, False),
    ("Schreib mir eine Übersicht über meine offenen Projekte", SCHREIBEN, False),
    ("Halte fest, dass das Treffen auf Freitag verschoben wurde", SCHREIBEN, False),
    ("Ergänze die Notiz Sandkisten um einen Abschnitt zu Containern", SCHREIBEN, False),
    ("Dokumentier den heutigen Stand", SCHREIBEN, False),
    ("Was ist der Unterschied zwischen Transformer und LSTM?", FRAGE, False),
    ("Erklär mir Backpropagation", FRAGE, False),
    ("Vergleiche meine Notizen zu Transformern", ANALYSE, False),
    ("Analysier meine Projektnotizen auf Widersprüche", ANALYSE, False),
    ("Was fällt dir an meiner Ablage auf?", ANALYSE, False),
    ("Bewerte, ob meine Notizen zum Thema vollständig sind", ANALYSE, False),

    # --- zerstoerend: darf NIE als Schreibauftrag durchgehen -----------
    ("Lösche die Notiz Wegwerfnotiz", UMBAUEN, False),
    ("Entferne alle alten Notizen aus dem Posteingang", UMBAUEN, False),
    ("Verschieb die Notiz Sandkisten nach 01_Wissen", UMBAUEN, False),
    ("Führ die beiden Notizen zu einer zusammen", UMBAUEN, False),
    ("Benenne den Ordner Projekte um", UMBAUEN, False),
    ("Räum meinen Posteingang auf", UMBAUEN, False),
    ("Wirf die Notiz in den Papierkorb", UMBAUEN, False),

    # --- Meta und Gespraech --------------------------------------------
    ("Was kannst du eigentlich?", META, False),
    ("Welche Werkzeuge hast du?", META, False),
    ("Warum hast du die Notizen nicht verschoben?", META, False),
    ("Hallo", GESPRAECH, False),
    ("Danke!", GESPRAECH, False),
    ("ok", GESPRAECH, False),

    # --- unscharf: soll als mehrdeutig auffallen ------------------------
    ("Kümmere dich mal drum", None, False),
    ("Mach was Sinnvolles mit meiner Inbox", None, False),
    ("Schau mal, ob da was nicht stimmt", None, False),
    ("Irgendwie ist meine Vault unordentlich", None, False),
]


def fall_absichten() -> None:
    """Trifft die regelbasierte Stufe die Absicht - ohne jeden Modellaufruf?"""
    bewerter = Aufgabenbewerter(llm=None)
    treffer, gesamt, daneben = 0, 0, []
    begonnen = time.time()
    for auftrag, erwartet, _ in SATZ:
        befund = bewerter.bewerten(auftrag)
        if erwartet is None:
            continue
        gesamt += 1
        if befund.absicht == erwartet:
            treffer += 1
        else:
            daneben.append(f"{auftrag[:38]!r}: {befund.absicht} statt {erwartet}")
    dauer = time.time() - begonnen

    quote = treffer / gesamt if gesamt else 0
    check("Absicht wird zuverlaessig erkannt", quote >= 0.88,
          f"{treffer}/{gesamt} = {quote:.0%}")
    if daneben:
        for zeile in daneben:
            print(f"      daneben: {zeile}")
    check("Alle Bewertungen ohne einen einzigen Modellaufruf",
          bewerter.gezaehlt["mit_modell"] == 0, bewerter.uebersicht())
    # Der Geschwindigkeitsgewinn ist der ganze Punkt: Ein Modellaufruf kostet
    # auf diesem Rechner gemessene 10 s (qwen3.8:27b, 21.09.).
    check("Und zwar schnell", dauer < 0.5,
          f"{len(SATZ)} Auftraege in {dauer * 1000:.0f} ms")


def fall_keine_gefaehrliche_verwechslung() -> None:
    """Die Verwechslung, die weh tut: loeschen als schreiben lesen.

    Genau das ist am 21.09. im echten CLI-Lauf passiert - der Schreibagent
    sah das Wort 'Notiz' in 'Loesche die Notiz Sandkisten' und legte eine
    NEUE Notiz dieses Namens an. Die Schritte liefen durch, die Verifikation
    war zufrieden, geloescht wurde nichts.
    """
    bewerter = Aufgabenbewerter(llm=None)
    zerstoerend = [a for a, e, _ in SATZ if e == UMBAUEN]
    falsch = []
    for auftrag in zerstoerend:
        befund = bewerter.bewerten(auftrag)
        if befund.absicht == SCHREIBEN:
            falsch.append(auftrag)
    check("Kein Loeschauftrag wird als Schreibauftrag gelesen",
          not falsch, ", ".join(f[:40] for f in falsch))

    riskant = [bewerter.bewerten(a) for a in
               ["Lösche die Notiz X", "Überschreib die Notiz Y",
                "Führ die Notizen zusammen", "Wirf das in den Papierkorb"]]
    check("Zerstoerende Auftraege tragen hohes Risiko",
          all(b.risiko >= 0.7 for b in riskant),
          ", ".join(f"{b.risiko:.2f}" for b in riskant))
    check("Und sie gelten nicht als deterministisch loesbar",
          not any(b.deterministisch_loesbar for b in riskant))

    harmlos = bewerter.bewerten("Wie viele Dateien liegen in der Inbox?")
    check("Eine Zaehlfrage traegt fast kein Risiko", harmlos.risiko <= 0.1,
          f"{harmlos.risiko:.2f}")


def fall_werkzeug_reicht() -> None:
    """§37, erste Frage: Reicht ein Werkzeug? Das ist der Fast Path."""
    bewerter = Aufgabenbewerter(llm=None)
    richtig, gesamt, daneben = 0, 0, []
    for auftrag, erwartet, werkzeug_reicht in SATZ:
        if erwartet is None:
            continue
        gesamt += 1
        befund = bewerter.bewerten(auftrag)
        if befund.deterministisch_loesbar == werkzeug_reicht:
            richtig += 1
        else:
            daneben.append(
                f"{auftrag[:36]!r}: {befund.deterministisch_loesbar} "
                f"statt {werkzeug_reicht}")
    quote = richtig / gesamt if gesamt else 0
    check("Erkennt, wann ein Werkzeug allein reicht", quote >= 0.88,
          f"{richtig}/{gesamt} = {quote:.0%}")
    for zeile in daneben:
        print(f"      daneben: {zeile}")

    # Die gefaehrliche Richtung ist die zweite: Etwas als deterministisch
    # ausgeben, das es nicht ist, heisst Qualitaet verlieren. Andersherum
    # kostet es nur Zeit.
    zu_optimistisch = [a for a, e, w in SATZ
                       if e is not None and not w
                       and bewerter.bewerten(a).deterministisch_loesbar]
    check("Nichts wird faelschlich fuer deterministisch gehalten",
          not zu_optimistisch, ", ".join(z[:40] for z in zu_optimistisch))


def fall_unscharf() -> None:
    """Unscharfe Auftraege muessen als unscharf auffallen - sonst raet das
    System statt zu fragen."""
    bewerter = Aufgabenbewerter(llm=None)
    unscharf = [a for a, e, _ in SATZ if e is None]
    werte = [(a, bewerter.bewerten(a).mehrdeutigkeit) for a in unscharf]
    for auftrag, wert in werte:
        print(f"      {wert:.2f}  {auftrag}")
    check("Unscharfe Auftraege haben hohe Mehrdeutigkeit",
          all(w >= 0.45 for _, w in werte),
          ", ".join(f"{w:.2f}" for _, w in werte))

    klar = bewerter.bewerten("Lies mir die Notiz Sandkisten vor")
    check("Ein klarer Auftrag hat niedrige Mehrdeutigkeit",
          klar.mehrdeutigkeit < 0.45, f"{klar.mehrdeutigkeit:.2f}")


def fall_komplexitaet() -> None:
    """Die Rangfolge muss stimmen: einfach < mittel < schwer."""
    bewerter = Aufgabenbewerter(llm=None)
    einfach = bewerter.bewerten("Wie viele Dateien sind in der Inbox?")
    mittel = bewerter.bewerten("Erstelle eine Notiz zum Thema Sandkisten")
    schwer = bewerter.bewerten(
        "Vergleiche zehn Notizen zur KI, erkenne Widersprüche und "
        "aktualisiere danach meine bestehende Übersicht")
    print(f"      einfach {einfach.komplexitaet:.2f}  "
          f"mittel {mittel.komplexitaet:.2f}  schwer {schwer.komplexitaet:.2f}")
    check("Komplexitaet steigt in der richtigen Reihenfolge",
          einfach.komplexitaet < mittel.komplexitaet < schwer.komplexitaet)
    check("Die schwere Aufgabe braucht mehrere Schritte", schwer.schritte >= 5,
          f"{schwer.schritte}")
    check("Die einfache braucht einen", einfach.schritte == 1,
          f"{einfach.schritte}")
    check("Die schwere verlangt Denken und Abruf",
          schwer.denken_noetig and schwer.abruf_noetig)


def fall_signatur() -> None:
    """§21: Die Signatur muss STABIL und grob sein - sonst taugt sie nicht
    als Schluessel fuer Metriken und Lernen."""
    bewerter = Aufgabenbewerter(llm=None)
    a = bewerter.bewerten("Erstelle eine Notiz zum Thema Sandkisten")
    b = bewerter.bewerten("Erstelle eine Notiz zum Thema Vektordatenbanken")
    print(f"      {a.signatur}")
    print(f"      {b.signatur}")
    check("Gleichartige Auftraege bekommen dieselbe Signatur",
          a.signatur == b.signatur, f"{a.signatur} vs {b.signatur}")

    c = bewerter.bewerten("Lösche die Notiz Sandkisten")
    check("Ein andersartiger Auftrag bekommt eine andere",
          c.signatur != a.signatur, c.signatur)
    check("Die Signatur nennt Absicht, Schreibzugriff und Risiko",
          c.signatur.startswith("umbauen.") and "high_risk" in c.signatur,
          c.signatur)
    check("Sie enthaelt keine Rohzahlen",
          not any(zeichen.isdigit() for zeichen in a.signatur), a.signatur)


def fall_modellstufe() -> None:
    """Die zweite Stufe: Wird sie nur bei Unklarheit gerufen - und wird die
    Antwort geprueft?"""
    class Zaehlmodell:
        def __init__(self, antwort=""):
            self.antwort = antwort
            self.rufe = 0
            self.schnell = []

        def chat(self, nachrichten, **kw):
            self.rufe += 1
            self.schnell.append(kw.get("schnell"))
            return self.antwort

    # a) klarer Auftrag -> gar nicht fragen
    modell = Zaehlmodell('{"absicht": "zaehlen"}')
    bewerter = Aufgabenbewerter(llm=modell)
    bewerter.bewerten("Wie viele Dateien sind in der Inbox?")
    check("Ein klarer Auftrag loest KEINEN Modellaufruf aus",
          modell.rufe == 0, f"{modell.rufe} Aufrufe")

    # b) unscharfer Auftrag -> fragen, und zwar das SCHNELLE Modell
    modell = Zaehlmodell('{"absicht": "wartung", "ziel": "Die Vault ordnen", '
                         '"komplexitaet": 0.8}')
    bewerter = Aufgabenbewerter(llm=modell)
    befund = bewerter.bewerten("Kümmere dich mal drum")
    check("Ein unscharfer Auftrag fragt nach", modell.rufe == 1,
          f"{modell.rufe} Aufrufe")
    check("Und zwar beim kleinen Modell", modell.schnell == [True],
          str(modell.schnell))
    check("Die Absicht wurde uebernommen", befund.absicht == "wartung",
          befund.absicht)
    check("Und die Herkunft steht dran", befund.quelle == "regeln+modell",
          befund.quelle)
    check("Die hoehere Komplexitaet wurde uebernommen",
          befund.komplexitaet >= 0.8, f"{befund.komplexitaet:.2f}")

    # c) erfundene Absicht -> verwerfen, nicht uebernehmen
    modell = Zaehlmodell('{"absicht": "zaubern", "risiko": 0.0}')
    bewerter = Aufgabenbewerter(llm=modell)
    befund = bewerter.bewerten("Kümmere dich mal drum")
    check("Eine erfundene Absicht wird verworfen",
          befund.absicht in ("frage", "schreiben", "umbauen", "wartung",
                             "analyse", "abruf", "lesen", "zaehlen", "meta",
                             "gespraech") and befund.quelle == "regeln",
          f"{befund.absicht} / {befund.quelle}")
    check("Und das wird gezaehlt", bewerter.gezaehlt["modell_verworfen"] == 1)

    # d) Das Modell darf das Risiko NICHT senken.
    modell = Zaehlmodell('{"absicht": "schreiben", "risiko": 0.0, '
                         '"mehrdeutigkeit": 0.0}')
    bewerter = Aufgabenbewerter(llm=modell)
    vorher = Aufgabenbewerter(llm=None).bewerten(
        "Kümmere dich mal drum und lösche den alten Kram")
    nachher = bewerter.bewerten("Kümmere dich mal drum und lösche den alten Kram")
    check("Das Modell kann das Risiko nicht kleinreden",
          nachher.risiko >= vorher.risiko,
          f"{vorher.risiko:.2f} -> {nachher.risiko:.2f}")

    # e) Ein kaputtes Modell darf nichts umwerfen.
    class Kaputt:
        def chat(self, *a, **k):
            raise RuntimeError("Server weg")
    bewerter = Aufgabenbewerter(llm=Kaputt())
    befund = bewerter.bewerten("Kümmere dich mal drum")
    check("Ein kaputtes Modell wirft den Befund nicht um",
          befund is not None and befund.quelle == "regeln", befund.quelle)


def fall_robust() -> None:
    """Der Bewerter darf an nichts sterben - er steht ganz vorne."""
    bewerter = Aufgabenbewerter(llm=None)
    for eingabe in ["", "   ", "?", "a" * 5000, "🙂🙂🙂", "/ziel", "\n\n\t",
                    "SELECT * FROM aufgaben; DROP TABLE ziele;"]:
        try:
            befund = bewerter.bewerten(eingabe)
            assert isinstance(befund, Befund)
            assert 0.0 <= befund.risiko <= 1.0
            assert 0.0 <= befund.komplexitaet <= 1.0
            assert befund.signatur
        except Exception as exc:
            check(f"Eingabe {eingabe[:20]!r} ueberstanden", False,
                  f"{type(exc).__name__}: {exc}")
            return
    check("Auch leere, riesige und wunderliche Eingaben ueberstehen es", True)


def main() -> int:
    print("=== TaskAssessor am Bewertungsset ===\n")
    for name, fall in (
        ("Absichten treffen", fall_absichten),
        ("Keine gefaehrliche Verwechslung", fall_keine_gefaehrliche_verwechslung),
        ("Reicht ein Werkzeug?", fall_werkzeug_reicht),
        ("Unscharfes faellt auf", fall_unscharf),
        ("Komplexitaet in der richtigen Reihenfolge", fall_komplexitaet),
        ("Signatur ist stabil", fall_signatur),
        ("Die Modellstufe", fall_modellstufe),
        ("Robustheit", fall_robust),
    ):
        print(f"\n--- {name} ---")
        fall()

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"❌ {len(FAILURES)} Pruefung(en) rot:")
        for f in FAILURES:
            print(f"   - {f}")
        return 1
    print("✅ Der Bewerter ordnet ein - ohne Modell, ohne Umweg.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
