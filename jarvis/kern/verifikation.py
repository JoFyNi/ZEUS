"""Verifikation: hat die Änderung wirklich stattgefunden?

Das ist der Befund, den die eigene BESTANDSAUFNAHME als wichtigsten nennt und
der seit dem 01.09. offen steht:

    "Verifikation. Das ist der wichtigste Befund. Es gibt eine
     Rücklesekontrolle auf Byte-Ebene - die Datei enthält, was hineingeschrieben
     wurde. Es gibt KEINE Prüfung auf Sach-Ebene.
     grep "def _verifizieren" jarvis/tools.py liefert null Treffer."

Am 21.09. nachgemessen: immer noch null Treffer, jetzt über das ganze Paket.

Konkret heisst das: `notiz_verschieben` meldet `ok=True`, wenn `os.replace`
nicht geworfen hat. Niemand prüft danach, ob

    * die Quelle wirklich verschwunden ist,
    * das Ziel wirklich existiert,
    * der Inhalt derselbe ist,
    * die Verweise in anderen Notizen mitgezogen wurden.

Das ist genau die Sorte Fehler, die dieses Projekt am teuersten bezahlt:
Der Nutzer glaubt die Erfolgsmeldung und merkt Tage später, dass etwas fehlt.
In ENTWICKLUNG.md heisst das Muster 2.1, "stille Fehlschläge".

Die Arbeitsteilung hier:

    Das WERKZEUG sagt, was es getan haben will.
    Die VERIFIKATION sieht unabhängig nach, ob es stimmt.

"Unabhängig" ist der Punkt. Die Prüfung fragt das Dateisystem und den Index,
nicht das Werkzeug und schon gar nicht das Modell. Ein Prüfer, der dem
Geprüften glaubt, prüft nichts.

Bewusste Grenze: Geprüft wird, was sich MECHANISCH prüfen lässt - Existenz,
Abwesenheit, Inhalt, Zahl. Ob eine Zusammenfassung inhaltlich gut ist, steht
hier nicht zur Debatte; dafür gibt es die Bewertung in evaluation.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Urteile
BESTANDEN = "bestanden"
DURCHGEFALLEN = "durchgefallen"
NICHT_PRUEFBAR = "nicht_pruefbar"


@dataclass
class Pruefergebnis:
    urteil: str
    werkzeug: str
    befunde: List[str] = field(default_factory=list)
    geprueft: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # NICHT_PRUEFBAR gilt als durchgelassen, aber NICHT als bestanden.
        # Der Unterschied ist wichtig: "konnte ich nicht pruefen" ist keine
        # Bestaetigung, und wer das gleichsetzt, hat wieder eine Behauptung.
        return self.urteil != DURCHGEFALLEN

    def bericht(self) -> str:
        if self.urteil == BESTANDEN:
            return f"geprüft: {', '.join(self.geprueft)}"
        if self.urteil == NICHT_PRUEFBAR:
            return "nicht mechanisch prüfbar"
        return "PRÜFUNG FEHLGESCHLAGEN: " + "; ".join(self.befunde)


class Verifikation:
    """Sieht nach, ob ein Werkzeug getan hat, was es meldet."""

    def __init__(self, tools, cfg=None):
        self.tools = tools
        self.cfg = cfg if cfg is not None else getattr(tools, "cfg", None)
        self._pruefer: Dict[str, Callable] = {
            "notiz_erstellen": self._notiz_erstellen,
            "notiz_loeschen": self._notiz_loeschen,
            "notiz_verschieben": self._notiz_verschieben,
            "notiz_ueberschreiben": self._notiz_geaendert,
            "notiz_ergaenzen": self._notiz_geaendert,
            "notiz_bearbeiten": self._notiz_geaendert,
            "abschnitt_ersetzen": self._notiz_geaendert,
            "abschnitt_einfuegen": self._notiz_geaendert,
            "notizen_zusammenfuehren": self._zusammengefuehrt,
            "notizen_verknuepfen": self._verknuepft,
            "tags_setzen": self._tags,
            "ordner_anlegen": self._ordner,
        }

    # ----------------------------------------------------------- Einstieg
    def pruefen(self, werkzeug: str, parameter: dict, ergebnis) -> Pruefergebnis:
        """Prüft das Ergebnis EINES Werkzeugaufrufs."""
        if not getattr(ergebnis, "ok", False):
            # Ein Werkzeug, das selbst Fehler meldet, braucht keine
            # Gegenprobe - es hat ja nichts behauptet.
            return Pruefergebnis(NICHT_PRUEFBAR, werkzeug,
                                 geprueft=["Werkzeug meldete selbst einen Fehler"])
        pruefer = self._pruefer.get(werkzeug)
        if pruefer is None:
            return Pruefergebnis(NICHT_PRUEFBAR, werkzeug)
        try:
            return pruefer(dict(parameter or {}), ergebnis)
        except Exception as exc:
            # Eine kaputte Pruefung darf nie eine geglueckte Aenderung
            # verwerfen. Sie meldet sich als nicht pruefbar und faellt auf.
            return Pruefergebnis(NICHT_PRUEFBAR, werkzeug,
                                 befunde=[f"Prüfung selbst gescheitert: "
                                          f"{type(exc).__name__}: {exc}"])

    # ------------------------------------------------------------- Prüfer
    def _notiz_erstellen(self, p: dict, e) -> Pruefergebnis:
        pfad = self._pfad_aus(e)
        befunde, geprueft = [], []
        if not pfad or not pfad.exists():
            befunde.append(f"die angelegte Datei gibt es nicht: {pfad}")
        else:
            geprueft.append("Datei liegt auf der Platte")
            text = self._lesen(pfad)
            if not (text or "").strip():
                befunde.append("die angelegte Notiz ist leer")
            else:
                geprueft.append(f"{len(text)} Zeichen Inhalt")
            inhalt = str(p.get("inhalt") or "")
            if inhalt:
                # Nicht Zeichen fuer Zeichen: Frontmatter und Ueberschrift
                # kommen dazu. Eine markante Zeile muss aber drin sein.
                marke = self._markante_zeile(inhalt)
                if marke and marke not in (text or ""):
                    befunde.append("der übergebene Inhalt steht nicht in der Datei")
                elif marke:
                    geprueft.append("übergebener Inhalt wiedergefunden")
        return self._urteil("notiz_erstellen", befunde, geprueft)

    def _notiz_loeschen(self, p: dict, e) -> Pruefergebnis:
        name = str(p.get("name") or "")
        befunde, geprueft = [], []
        note = self.tools.notiz_exakt(name) if name else None
        if note and Path(note.path).exists():
            befunde.append(f"'{name}' liegt weiterhin an seinem Platz")
        else:
            geprueft.append("Notiz ist an ihrem Platz nicht mehr da")
        # Geloescht heisst hier: im Papierkorb. Ist sie dort NICHT, ist sie
        # wirklich weg - und das waere ein Bruch der eigenen Zusage
        # "Nichts geht unwiederbringlich verloren".
        papierkorb = self._papierkorb()
        if papierkorb and papierkorb.exists():
            stamm = re.sub(r"\W+", "_", name).strip("_").lower()
            gefunden = any(stamm in d.name.lower() for d in papierkorb.glob("*.md"))
            if gefunden:
                geprueft.append("Sicherheitskopie liegt im Papierkorb")
            else:
                befunde.append("keine Kopie im Papierkorb gefunden")
        return self._urteil("notiz_loeschen", befunde, geprueft)

    def _notiz_verschieben(self, p: dict, e) -> Pruefergebnis:
        """Der Fall, den die BESTANDSAUFNAHME namentlich nennt."""
        name = str(p.get("name") or "")
        befunde, geprueft = [], []
        ziel = self._pfad_aus(e)
        if ziel and ziel.exists():
            geprueft.append(f"Ziel existiert: {ziel.name}")
        else:
            befunde.append(f"am Zielort liegt nichts: {ziel}")

        # Die Quelle muss WEG sein. Ein Verschieben, das kopiert, hinterlaesst
        # zwei Wahrheiten - und die naechste Suche findet die falsche.
        alt = (e.daten or {}).get("vorher") or (e.daten or {}).get("quelle")
        if alt:
            alter_pfad = self.tools.root / str(alt)
            if alter_pfad.exists():
                befunde.append(f"die Quelle liegt noch da: {alt}")
            else:
                geprueft.append("Quelle ist verschwunden")
        elif name:
            treffer = [n for n in self.tools._notes()
                       if n.stem.lower() == name.lower()]
            if len(treffer) > 1:
                befunde.append(f"'{name}' existiert jetzt {len(treffer)}-mal")
            else:
                geprueft.append("es gibt weiterhin genau eine Notiz des Namens")
        return self._urteil("notiz_verschieben", befunde, geprueft)

    def _notiz_geaendert(self, p: dict, e) -> Pruefergebnis:
        pfad = self._pfad_aus(e)
        befunde, geprueft = [], []
        if not pfad or not pfad.exists():
            befunde.append(f"die geänderte Datei gibt es nicht: {pfad}")
            return self._urteil("aenderung", befunde, geprueft)
        geprueft.append("Datei vorhanden")
        text = self._lesen(pfad) or ""
        neu = str(p.get("inhalt") or p.get("text") or p.get("neu") or "")
        if neu:
            marke = self._markante_zeile(neu)
            if marke and marke not in text:
                befunde.append("der neue Text steht nicht in der Datei")
            elif marke:
                geprueft.append("neuer Text wiedergefunden")
        if not text.strip():
            befunde.append("die Notiz ist nach der Änderung leer")
        return self._urteil("aenderung", befunde, geprueft)

    def _zusammengefuehrt(self, p: dict, e) -> Pruefergebnis:
        pfad = self._pfad_aus(e)
        befunde, geprueft = [], []
        if not pfad or not pfad.exists():
            befunde.append("die Zielnotiz der Zusammenführung fehlt")
            return self._urteil("notizen_zusammenfuehren", befunde, geprueft)
        text = self._lesen(pfad) or ""
        geprueft.append(f"Zielnotiz vorhanden, {len(text)} Zeichen")
        quellen = p.get("quellen")
        if isinstance(quellen, (list, tuple)):
            for quelle in quellen:
                stamm = str(quelle).strip()
                if not stamm:
                    continue
                # Eine Zusammenfuehrung, deren Ergebnis die Quelle nicht
                # erwaehnt, hat sie nicht zusammengefuehrt. Genau dieser Fall
                # trat am 18.09. auf: Die Notiz wurde mit sich selbst vereint.
                if stamm.lower().replace(" ", "_") in text.lower().replace(" ", "_"):
                    geprueft.append(f"'{stamm}' kommt im Ergebnis vor")
                else:
                    befunde.append(f"von '{stamm}' steht nichts im Ergebnis")
            if pfad.stem.lower() in {str(q).strip().lower().replace(" ", "_")
                                     for q in quellen}:
                befunde.append("Ziel und Quelle sind dieselbe Notiz")
        return self._urteil("notizen_zusammenfuehren", befunde, geprueft)

    def _verknuepft(self, p: dict, e) -> Pruefergebnis:
        von, nach = str(p.get("von") or ""), str(p.get("nach") or "")
        befunde, geprueft = [], []
        quelle = self.tools.notiz_exakt(von) or self.tools.resolve_note(von)
        if not quelle:
            befunde.append(f"die Notiz '{von}' ist nicht auffindbar")
            return self._urteil("notizen_verknuepfen", befunde, geprueft)
        text = self._lesen(Path(quelle.path)) or ""
        ziel = self.tools.notiz_exakt(nach) or self.tools.resolve_note(nach)
        name = ziel.stem if ziel else nach
        if f"[[{name}]]" in text or f"[[{name}|" in text:
            geprueft.append(f"Verweis [[{name}]] steht in '{quelle.stem}'")
        else:
            befunde.append(f"in '{quelle.stem}' steht kein Verweis auf '{name}'")
        # Ein Link, dessen Ziel es nicht gibt, ist ein toter Link - genau das,
        # was auto_verlinken frueher reihenweise erzeugte, ohne es zu merken.
        if not ziel:
            befunde.append(f"das Ziel '{nach}' existiert nicht - toter Verweis")
        else:
            geprueft.append("Ziel existiert")
        return self._urteil("notizen_verknuepfen", befunde, geprueft)

    def _tags(self, p: dict, e) -> Pruefergebnis:
        name = str(p.get("name") or "")
        note = self.tools.notiz_exakt(name) or self.tools.resolve_note(name)
        befunde, geprueft = [], []
        if not note:
            return self._urteil("tags_setzen", [f"'{name}' nicht auffindbar"], [])
        text = self._lesen(Path(note.path)) or ""
        roh = p.get("tags")
        gewuenscht = roh if isinstance(roh, (list, tuple)) else \
            [t.strip() for t in str(roh or "").split(",") if t.strip()]
        for tag in gewuenscht:
            sauber = str(tag).strip().lstrip("#")
            if sauber and sauber.lower() in text.lower():
                geprueft.append(f"Tag '{sauber}' steht drin")
            elif sauber:
                befunde.append(f"Tag '{sauber}' fehlt in der Datei")
        return self._urteil("tags_setzen", befunde, geprueft)

    def _ordner(self, p: dict, e) -> Pruefergebnis:
        name = str(p.get("name") or p.get("ordner") or "")
        pfad = self.tools.root / name if name else None
        if pfad and pfad.is_dir():
            return self._urteil("ordner_anlegen", [], [f"Ordner '{name}' existiert"])
        return self._urteil("ordner_anlegen", [f"Ordner '{name}' wurde nicht angelegt"], [])

    # -------------------------------------------------------------- Helfer
    def _urteil(self, werkzeug: str, befunde: List[str],
                geprueft: List[str]) -> Pruefergebnis:
        if befunde:
            return Pruefergebnis(DURCHGEFALLEN, werkzeug, befunde, geprueft)
        if not geprueft:
            return Pruefergebnis(NICHT_PRUEFBAR, werkzeug)
        return Pruefergebnis(BESTANDEN, werkzeug, [], geprueft)

    def _pfad_aus(self, ergebnis) -> Optional[Path]:
        rel = (getattr(ergebnis, "daten", None) or {}).get("pfad")
        return (self.tools.root / str(rel)) if rel else None

    def _papierkorb(self) -> Optional[Path]:
        ordner = getattr(self.cfg, "trash_folder", "") if self.cfg else ""
        return (self.tools.root / ordner) if ordner else None

    @staticmethod
    def _lesen(pfad: Path) -> Optional[str]:
        try:
            return pfad.read_text(encoding="utf-8")
        except OSError:
            return None

    @staticmethod
    def _markante_zeile(text: str) -> str:
        """Die längste Zeile ohne Sonderzeichen - als Wiedererkennungsmerkmal.

        Nicht der ganze Text: Beim Schreiben kommen Frontmatter, Ueberschrift
        und Verweisabschnitt dazu, und Zeilenenden werden normalisiert. Eine
        markante Zeile ueberlebt das.
        """
        zeilen = [z.strip() for z in str(text or "").splitlines()]
        brauchbar = [z for z in zeilen
                     if len(z) > 25 and not z.startswith(("#", "-", "*", ">", "|"))]
        return max(brauchbar, key=len)[:120] if brauchbar else ""
