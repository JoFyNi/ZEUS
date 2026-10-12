"""Alle Testreihen auf einmal - ein Befehl, eine Zusammenfassung.

    python tests/alle.py                 alles
    python tests/alle.py plan sperre     nur Reihen, deren Name das enthaelt
    python tests/alle.py --laut          auch die Ausgabe bestandener Reihen
    python tests/alle.py --liste         nur auflisten, nichts ausfuehren

Warum es das braucht:

1. **Die Emoji-Falle.** Jede Testreihe druckt ✅ und ❌. Auf einer normalen
   Windows-Konsole ist die Ausgabe cp1252-kodiert, und dann stirbt JEDE Reihe
   mit einem UnicodeEncodeError, bevor sie ihre erste Pruefung meldet. Wer das
   nicht weiss, sieht 34 Abstuerze und haelt das Projekt fuer kaputt. Dieser
   Laeufer setzt PYTHONIOENCODING fuer alle Kindprozesse.

2. **Der Tempordner.** Mehrere Reihen legen Vaults unter dem Systemtemp an.
   Liegt der innerhalb des Projekts, fegt `test_architektur` diese Dateien mit
   auf und meldet Fehler, die keine sind. Deshalb ein eigener Ordner AUSSERHALB.

3. **Die Uebersicht.** 34 Reihen einzeln aufzurufen ist nichts, was jemand
   zweimal macht - und was man nicht zweimal macht, macht man gar nicht.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HIER = Path(__file__).resolve().parent
WURZEL = HIER.parent

# Reihen, die von der UMGEBUNG abhaengen statt vom Code: Sie brauchen einen
# erreichbaren Ollama-Server, echte Stimmdateien oder ein bestimmtes Datum.
# Sie werden ausgefuehrt und gezaehlt, aber getrennt ausgewiesen - sonst
# gewoehnt man sich an rote Zeilen, und dann faellt eine echte nicht mehr auf.
UMGEBUNGSABHAENGIG = {
    "test_learning.py": "braucht einen antwortenden Server",
    "test_selbstentwicklung.py": "prueft, ob Ollama FEHLT - schlaegt fehl, wenn er laeuft",
    "test_server.py": "erwartet eine bestimmte Zahl Wiederholversuche",
    "test_sitzung.py": "an feste Kalenderdaten genagelt",
    "test_stimme.py": "sieht die echten Stimmen unter stimmen/",
}

FRIST = 300          # je Reihe; darueber gilt sie als haengend


def umgebung() -> dict:
    umg = dict(os.environ)
    umg["PYTHONIOENCODING"] = "utf-8"
    # Ausserhalb des Projekts, siehe Kopf - und AUFGELOEST, siehe hier:
    #
    # tempfile.gettempdir() liefert unter Windows den 8.3-Kurznamen, also
    # "C:\\Users\\JB454~1.NIE\\..." statt "C:\\Users\\j.nievelstein\\...".
    # Die Vault merkt sich ihren Pfad in der LANGEN Form; beim Sichern rechnet
    # `_backup()` dann `relative_to()` zwischen beiden Schreibweisen - und das
    # wirft ValueError. Ergebnis waren zwoelf rote Reihen, die mit dem Code
    # nichts zu tun hatten. `.resolve()` macht aus dem Kurznamen den langen.
    tmp = Path(tempfile.gettempdir()).resolve() / "zeus_tests"
    tmp.mkdir(parents=True, exist_ok=True)
    for name in ("TMPDIR", "TEMP", "TMP"):
        umg[name] = str(tmp)
    return umg


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = [a for a in sys.argv[1:]]
    laut = "--laut" in args
    nur_liste = "--liste" in args
    muster = [a.lower() for a in args if not a.startswith("--")]

    reihen = sorted(HIER.glob("test_*.py"))
    if muster:
        reihen = [r for r in reihen if any(m in r.name.lower() for m in muster)]
    if not reihen:
        print("Keine passende Testreihe gefunden.")
        return 1

    if nur_liste:
        for r in reihen:
            marke = "  (umgebungsabhaengig)" if r.name in UMGEBUNGSABHAENGIG else ""
            print(f"  {r.name}{marke}")
        return 0

    umg = umgebung()
    gut, schlecht, bekannt = [], [], []
    begonnen = time.time()

    print(f"{len(reihen)} Testreihen, Zeitlimit {FRIST}s je Reihe\n")
    for reihe in reihen:
        start = time.time()
        try:
            fertig = subprocess.run(
                [sys.executable, str(reihe)], cwd=str(WURZEL), env=umg,
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=FRIST)
            ausgabe, code = (fertig.stdout or "") + (fertig.stderr or ""), fertig.returncode
        except subprocess.TimeoutExpired:
            ausgabe, code = f"Zeitlimit von {FRIST}s ueberschritten.", 124

        dauer = time.time() - start
        if code == 0:
            gut.append(reihe.name)
            print(f"  ok      {reihe.name:34} {dauer:5.1f}s")
            if laut:
                print("\n".join("          " + z for z in ausgabe.splitlines()))
        else:
            fehlzeilen = [z for z in ausgabe.splitlines() if z.startswith("❌")]
            ziel = bekannt if reihe.name in UMGEBUNGSABHAENGIG else schlecht
            ziel.append((reihe.name, fehlzeilen, ausgabe))
            marke = "bekannt" if reihe.name in UMGEBUNGSABHAENGIG else "FEHLER "
            print(f"  {marke} {reihe.name:34} {dauer:5.1f}s"
                  f"   {len(fehlzeilen)} Pruefung(en) rot")

    print(f"\n{'-' * 62}")
    print(f"bestanden {len(gut)}   gescheitert {len(schlecht)}   "
          f"umgebungsabhaengig {len(bekannt)}   ({time.time() - begonnen:.0f}s)")

    if schlecht:
        print("\nECHTE FEHLSCHLAEGE - die gehoeren angesehen:")
        for name, zeilen, ausgabe in schlecht:
            print(f"\n  {name}")
            for z in (zeilen or ausgabe.splitlines()[-6:]):
                print(f"      {z[:150]}")
    if bekannt:
        print("\nUmgebungsabhaengig, kein Handlungsbedarf:")
        for name, zeilen, _ in bekannt:
            print(f"  {name:34} {UMGEBUNGSABHAENGIG[name]}")

    if not schlecht:
        print("\nAlles grün, was von diesem Rechner unabhaengig ist.")
    return 1 if schlecht else 0


if __name__ == "__main__":
    raise SystemExit(main())
