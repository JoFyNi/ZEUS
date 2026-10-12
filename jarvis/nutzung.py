"""Werkzeugnutzung mitschreiben - die Datengrundlage für "was brauche ich wirklich?".

Bisher gab es zwei Protokolle, und beide beantworten die Frage nicht:

* `.jarvis/journal.jsonl` hält nur ÄNDERUNGEN fest. Jedes lesende Werkzeug -
  notizen_suchen, notiz_lesen, struktur_anzeigen - taucht dort nie auf. Wer
  daraus "nie benutzt" ableitet, behauptet für die halbe Werkzeugliste etwas
  Falsches.
* `.jarvis/aufgaben.jsonl` hält nur FEHLSCHLÄGE fest, und auch die nur, solange
  sie offen sind.

Was fehlte, ist die schlichte Strichliste: Wie oft wurde jedes Werkzeug
aufgerufen, wie oft ging es schief, wann zuletzt. Erst damit lässt sich sagen,
welche Werkzeugbeschreibung das Modell nicht versteht (nie aufgerufen), welches
Werkzeug unzuverlässig ist (hohe Fehlerquote) - und welches Aufgabenfeld in der
Praxis gar nicht vorkommt.

Absichtlich anspruchslos: eine kleine JSON-Datei, bei jedem Aufruf gelesen und
geschrieben. Bei einem Werkzeugaufruf alle paar Sekunden ist das nicht messbar,
und es gibt keinen Hintergrundzustand, der beim Absturz verloren geht.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Dict

from .config import settings

DATEINAME = "werkzeugnutzung.json"


def pfad(cfg=settings) -> Path:
    return cfg.work_dir / DATEINAME


def lesen(cfg=settings) -> Dict[str, dict]:
    """Die Strichliste - leer, wenn noch nie etwas mitgeschrieben wurde."""
    try:
        roh = json.loads(pfad(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(roh, dict):
        return {}
    sauber: Dict[str, dict] = {}
    for name, wert in roh.items():
        if not isinstance(wert, dict):
            continue
        try:
            sauber[str(name)] = {
                "aufrufe": int(wert.get("aufrufe") or 0),
                "fehler": int(wert.get("fehler") or 0),
                "zuletzt": str(wert.get("zuletzt") or ""),
            }
        except (TypeError, ValueError):
            continue
    return sauber


def vermerken(werkzeug: str, geglueckt: bool, cfg=settings) -> bool:
    """Einen Aufruf zählen. Gibt False zurück, wenn es nicht geschrieben werden konnte.

    Das Mitschreiben darf einen laufenden Auftrag niemals zum Scheitern bringen;
    deshalb wird jeder Fehler geschluckt - aber als Rückgabewert gemeldet, damit
    der Aufrufer es wenigstens erfahren KANN.
    """
    werkzeug = str(werkzeug or "").strip()
    if not werkzeug:
        return False
    daten = lesen(cfg)
    eintrag = daten.setdefault(werkzeug, {"aufrufe": 0, "fehler": 0, "zuletzt": ""})
    eintrag["aufrufe"] = int(eintrag.get("aufrufe") or 0) + 1
    if not geglueckt:
        eintrag["fehler"] = int(eintrag.get("fehler") or 0) + 1
    eintrag["zuletzt"] = dt.datetime.now().isoformat(timespec="seconds")
    try:
        cfg.work_dir.mkdir(parents=True, exist_ok=True)
        ziel = pfad(cfg)
        vorlaeufig = ziel.with_suffix(".tmp")
        vorlaeufig.write_text(json.dumps(daten, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        vorlaeufig.replace(ziel)          # atomar: nie eine halbe Datei
        return True
    except OSError:
        return False


def fehlerquote(eintrag: dict) -> float:
    aufrufe = int(eintrag.get("aufrufe") or 0)
    return (int(eintrag.get("fehler") or 0) / aufrufe) if aufrufe else 0.0
