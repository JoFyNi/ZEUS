"""Die Sperre gegen Selbstaenderung an der Sperre selbst.

`kern/selbst.py` fuehrt die Liste UNANTASTBAR: Dateien, die ein Vorschlag nie
anfassen darf, weil ein Fehler darin den Rueckweg zerstoert - allen voran
selbst.py, denn wer sie aendern darf, kann seine eigene Pruefung abschaffen.

Der Vergleich lief frueher gegen die ROHE Zeichenkette, geschrieben wurde aber
gegen den AUFGELOESTEN Pfad. Vier Schreibweisen kamen dadurch durch. Hier wird
jede davon nachgestellt.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from jarvis.kern.selbst import UNANTASTBAR, Quarantaene  # noqa: E402

FAILURES = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


def main() -> int:
    quelle = Path(__file__).resolve().parents[1]
    tmp = Path(tempfile.mkdtemp(prefix="jarvis_sperre_"))
    wurzel = tmp / "projekt"
    wurzel.mkdir()
    for teil in ("jarvis", "tests"):
        shutil.copytree(quelle / teil, wurzel / teil,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (wurzel / ".env").write_text("LEER=1", encoding="utf-8")

    werk = Quarantaene(wurzel=wurzel, ablage=tmp / "quarantaene")
    ziel = wurzel / "jarvis" / "kern" / "selbst.py"
    inhalt = ziel.read_text(encoding="utf-8")
    geaendert = inhalt.replace("MAX_GEAENDERTE_ZEILEN = 120",
                               "MAX_GEAENDERTE_ZEILEN = 999", 1)
    assert geaendert != inhalt, "Ankerzeile fuer den Test nicht gefunden"

    varianten = [
        ("direkt", "jarvis/kern/selbst.py"),
        ("Windows-Trenner", "jarvis\\kern\\selbst.py"),
        ("fuehrender Punkt", "./jarvis/kern/selbst.py"),
        ("Umweg ueber ..", "jarvis/kern/../kern/selbst.py"),
        ("doppelter Trenner", "jarvis//kern/selbst.py"),
        ("Punkt in der Mitte", "jarvis/./kern/selbst.py"),
        ("Grossschreibung", "jarvis/kern/Selbst.py"),
    ]
    for beschreibung, pfad in varianten:
        v = werk.einreichen(pfad, geaendert, "Pruefung der Sperre")
        gesperrt = "ausgenommen" in (v.abgelehnt_weil or "").lower()
        check(f"Abgewiesen: {beschreibung}", gesperrt,
              "" if gesperrt else f"zustand={v.zustand} pfad={pfad}")

    # tools.py haengt genauso an der Sperre - Sicherung und Journal leben darin.
    werkzeugdatei = wurzel / "jarvis" / "tools.py"
    v = werk.einreichen("jarvis/../jarvis/tools.py",
                        werkzeugdatei.read_text(encoding="utf-8") + "\n# Notiz\n",
                        "Pruefung")
    check("tools.py ist ueber denselben Umweg ebenfalls gesperrt",
          "ausgenommen" in (v.abgelehnt_weil or "").lower(),
          v.abgelehnt_weil[:60] if v.abgelehnt_weil else v.zustand)

    # Eine gewoehnliche Datei muss weiterhin durchgehen, sonst waere die
    # Selbstentwicklung insgesamt tot statt nur die Sperre dicht.
    harmlos = wurzel / "jarvis" / "vorhaben.py"
    if harmlos.exists():
        v = werk.einreichen("jarvis/vorhaben.py",
                            harmlos.read_text(encoding="utf-8") + "\n# Notiz\n",
                            "Pruefung")
        check("Eine gewoehnliche Datei wird weiterhin angenommen",
              v.zustand == "neu", f"zustand={v.zustand} grund={v.abgelehnt_weil[:50]}")

    # Fuehrende ".." entschaerft schon `lstrip("./")` in einreichen(), sie
    # landen als gewoehnlicher Name im Baum und scheitern daran, dass es die
    # Datei nicht gibt. Der echte Ausbruchsweg sitzt in der Pfadmitte - dort
    # greift erst resolve() + relative_to().
    v = werk.einreichen("jarvis/../../woanders.py", "x = 1", "Pruefung")
    check("Ausbruch ueber .. in der Pfadmitte wird verwehrt",
          "heraus" in (v.abgelehnt_weil or ""), v.abgelehnt_weil[:60])
    v = werk.einreichen("../../woanders.py", "x = 1", "Pruefung")
    check("Fuehrende .. landen im Baum und scheitern dort",
          v.zustand == "durchgefallen", f"{v.zustand}: {v.abgelehnt_weil[:50]}")

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
