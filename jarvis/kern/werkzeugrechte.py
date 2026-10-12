"""Was ein Werkzeug anrichtet - und wer das darf.

Bis hierher gab es zwei getrennte Welten. `rechte.py` kannte Handlungen wie
`vault_schreiben` und `unbeaufsichtigt_arbeiten`; `tools.py` kannte 41
Werkzeuge. Zwischen beiden stand nichts. Nachgemessen am 21.09.:

    grep -n "rechte" jarvis/werkzeuge/*.py  ->  null Treffer

Das heisst im Klartext: Die Rechtestufe stand in der `.env`, wurde beim
Hochfahren gelesen, in `/kern` angezeigt - und hat NIE einen Werkzeugaufruf
aufgehalten. Auf Stufe 0 ("lesen - aendert nichts") konnte `notiz_loeschen`
laufen. Eine Schranke, die nichts abhaelt, ist keine Schranke, sondern eine
Beschriftung.

Diese Tabelle ist das fehlende Zwischenstueck. Sie ordnet jedem Werkzeug vier
Dinge zu, und jedes davon beantwortet eine Frage, die sonst geraten wird:

    recht        Welche Handlung aus rechte.MINDESTSTUFE es braucht.
    risiko       Ob ein Mensch zustimmen muss, bevor es laeuft (§18).
    wirkung      Ob es ueberhaupt etwas veraendert - nur dann ist
                 Idempotenz ein Thema (§33).
    schluessel   Woraus sich die Wiedererkennung einer Wirkung ergibt.
                 `notiz_erstellen` mit demselben Titel im selben Ordner ist
                 dieselbe Wirkung; `notizen_suchen` hat gar keine.

Zwei Festlegungen, die aus dem Betrieb kommen:

1. UNBEKANNTE WERKZEUGE GELTEN ALS VERAENDERND. Wer ein Werkzeug hinzufuegt
   und diese Tabelle vergisst, bekommt die strengere Behandlung, nicht die
   laxere. Andersherum waere die Luecke unsichtbar - und genau diese Sorte
   Luecke faellt erst auf, wenn etwas weg ist.

2. FERTIGKEITEN ERBEN DAS SCHAERFSTE IHRER SCHRITTE. Eine selbstgebaute
   Fertigkeit ist eine Kette von Werkzeugaufrufen. Ihr Recht ist das
   hoechste Recht ihrer Glieder, ihr Risiko das hoechste Risiko. Sonst waere
   "bau dir eine Fertigkeit, die loescht" der Weg an der Schranke vorbei.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from .aufgabenspeicher import (RISIKO_GERING, RISIKO_HOCH, RISIKO_MITTEL,
                               RISIKO_RANG)

# Die Rechte-Handlungen, auf die hier verwiesen wird. Sie stehen in
# rechte.MINDESTSTUFE; hier stehen nur die Namen, damit es keinen Ring gibt.
LESEN = "lesen"
SUCHEN = "suchen"
ZEICHNEN = "zeichnen"
SCHREIBEN = "vault_schreiben"
NETZ_LESEN = "netz_lesen"
CODE_LESEN = "code_lesen"


class Werkzeugart:
    """Was ein Werkzeug mit der Welt macht - eine von drei Antworten."""
    LESEND = "lesend"           # aendert nichts, darf beliebig oft laufen
    SCHREIBEND = "schreibend"   # aendert die Vault
    ZERSTOEREND = "zerstoerend" # entfernt oder ueberschreibt Bestehendes


# (recht, risiko, wirkung, schluesselfelder)
# Die Schluesselfelder sagen, WORAUS sich "dieselbe Wirkung" ergibt. Leer
# heisst: keine Wirkung, also auch keine Idempotenzsperre.
_TABELLE: Dict[str, Tuple[str, str, str, tuple]] = {
    # -------------------------------------------------------------- Lesen
    "notiz_lesen":            (LESEN, RISIKO_GERING, Werkzeugart.LESEND, ()),
    "notizen_suchen":         (SUCHEN, RISIKO_GERING, Werkzeugart.LESEND, ()),
    "struktur_anzeigen":      (LESEN, RISIKO_GERING, Werkzeugart.LESEND, ()),
    "verwandte_finden":       (SUCHEN, RISIKO_GERING, Werkzeugart.LESEND, ()),
    "bloecke_anzeigen":       (LESEN, RISIKO_GERING, Werkzeugart.LESEND, ()),
    "links_pruefen":          (LESEN, RISIKO_GERING, Werkzeugart.LESEND, ()),
    "code_auslesen":          (CODE_LESEN, RISIKO_GERING, Werkzeugart.LESEND, ()),
    "code_dateien":           (CODE_LESEN, RISIKO_GERING, Werkzeugart.LESEND, ()),
    "code_suchen":            (CODE_LESEN, RISIKO_GERING, Werkzeugart.LESEND, ()),
    "quellcode_lesen":        (CODE_LESEN, RISIKO_GERING, Werkzeugart.LESEND, ()),
    "struktur_zeichnen":      (ZEICHNEN, RISIKO_GERING, Werkzeugart.LESEND, ()),

    # ---------------------------------------------------------------- Netz
    # Netzzugriff ist LESEND, aber nicht harmlos: Er holt fremden Text in den
    # Kontext. Deshalb ein eigenes Recht, das sich getrennt abschalten laesst.
    "web_suchen":             (NETZ_LESEN, RISIKO_GERING, Werkzeugart.LESEND, ()),
    "web_lesen":              (NETZ_LESEN, RISIKO_GERING, Werkzeugart.LESEND, ()),

    # ---------------------------------------------------------- Schreibend
    "notiz_erstellen":        (SCHREIBEN, RISIKO_GERING, Werkzeugart.SCHREIBEND,
                               ("titel", "ordner")),
    "notiz_ergaenzen":        (SCHREIBEN, RISIKO_GERING, Werkzeugart.SCHREIBEND,
                               ("name", "inhalt", "abschnitt")),
    "listenpunkt_hinzufuegen": (SCHREIBEN, RISIKO_GERING, Werkzeugart.SCHREIBEND,
                                ("name", "text", "abschnitt")),
    "listenpunkt_erledigt":   (SCHREIBEN, RISIKO_GERING, Werkzeugart.SCHREIBEND,
                               ("name", "text")),
    "codeblock_hinzufuegen":  (SCHREIBEN, RISIKO_GERING, Werkzeugart.SCHREIBEND,
                               ("name", "abschnitt", "code")),
    "abschnitt_einfuegen":    (SCHREIBEN, RISIKO_GERING, Werkzeugart.SCHREIBEND,
                               ("name", "ueberschrift")),
    "notizen_verknuepfen":    (SCHREIBEN, RISIKO_GERING, Werkzeugart.SCHREIBEND,
                               ("von", "nach")),
    "tags_setzen":            (SCHREIBEN, RISIKO_GERING, Werkzeugart.SCHREIBEND,
                               ("name", "tags", "modus")),
    "frontmatter_setzen":     (SCHREIBEN, RISIKO_GERING, Werkzeugart.SCHREIBEND,
                               ("name", "schluessel")),
    "ordner_anlegen":         (SCHREIBEN, RISIKO_GERING, Werkzeugart.SCHREIBEND,
                               ("pfad",)),
    "uebersichtsnotiz":       (SCHREIBEN, RISIKO_GERING, Werkzeugart.SCHREIBEND,
                               ("ordner", "titel")),
    "datei_importieren":      (SCHREIBEN, RISIKO_MITTEL, Werkzeugart.SCHREIBEND,
                               ("pfad", "titel")),
    "codebasis_importieren":  (SCHREIBEN, RISIKO_MITTEL, Werkzeugart.SCHREIBEND,
                               ("muster", "ordner")),
    "notiz_wiederherstellen": (SCHREIBEN, RISIKO_GERING, Werkzeugart.SCHREIBEND,
                               ("name", "ordner")),

    # --------------------------------------------------------- Zerstoerend
    # Alles, was Bestehendes ueberschreibt oder entfernt. Diese Werkzeuge
    # verlangen im unbeaufsichtigten Betrieb eine Freigabe - sie sind es,
    # deren Fehlgriff man erst Tage spaeter bemerkt.
    "notiz_loeschen":         (SCHREIBEN, RISIKO_HOCH, Werkzeugart.ZERSTOEREND,
                               ("name",)),
    "notiz_ueberschreiben":   (SCHREIBEN, RISIKO_HOCH, Werkzeugart.ZERSTOEREND,
                               ("name",)),
    "notiz_verschieben":      (SCHREIBEN, RISIKO_MITTEL, Werkzeugart.ZERSTOEREND,
                               ("name", "ziel_ordner", "neuer_name")),
    "notiz_bearbeiten":       (SCHREIBEN, RISIKO_MITTEL, Werkzeugart.ZERSTOEREND,
                               ("name", "suchtext", "ersatztext")),
    "abschnitt_ersetzen":     (SCHREIBEN, RISIKO_MITTEL, Werkzeugart.ZERSTOEREND,
                               ("name", "abschnitt")),
    "abschnitt_loeschen":     (SCHREIBEN, RISIKO_HOCH, Werkzeugart.ZERSTOEREND,
                               ("name", "abschnitt")),
    "block_ersetzen":         (SCHREIBEN, RISIKO_MITTEL, Werkzeugart.ZERSTOEREND,
                               ("name", "block")),
    "notizen_zusammenfuehren": (SCHREIBEN, RISIKO_HOCH, Werkzeugart.ZERSTOEREND,
                                ("ziel_titel", "ordner")),
    "notiz_aufteilen":        (SCHREIBEN, RISIKO_MITTEL, Werkzeugart.ZERSTOEREND,
                               ("name", "ordner")),
    "ordner_umbenennen":      (SCHREIBEN, RISIKO_HOCH, Werkzeugart.ZERSTOEREND,
                               ("alt", "neu")),
    "links_bereinigen":       (SCHREIBEN, RISIKO_MITTEL, Werkzeugart.ZERSTOEREND,
                               ("name", "modus")),
    "auto_verlinken":         (SCHREIBEN, RISIKO_MITTEL, Werkzeugart.SCHREIBEND,
                               ("ordner",)),
    "vault_wartung":          (SCHREIBEN, RISIKO_MITTEL, Werkzeugart.SCHREIBEND,
                               ("reparieren",)),
    # rueckgaengig nimmt die letzte Aenderung zurueck. Das ist selbst eine
    # Aenderung - und sie darf NIE aus einer Wiederholung entstehen, sonst
    # nimmt ein zweiter Anlauf zwei Schritte zurueck statt einen.
    "rueckgaengig":           (SCHREIBEN, RISIKO_HOCH, Werkzeugart.ZERSTOEREND, ()),

    # ------------------------------------------------------- Selbstausbau
    "fertigkeit_definieren":  ("fertigkeit_anlegen", RISIKO_GERING,
                               Werkzeugart.SCHREIBEND, ("name",)),
    "verhalten_definieren":   ("verhalten_anlegen", RISIKO_GERING,
                               Werkzeugart.SCHREIBEND, ("name",)),
}

# Was ein Werkzeug bekommt, das in der Tabelle fehlt: die strenge Behandlung.
# Siehe Modulkopf, Festlegung 1.
_UNBEKANNT = (SCHREIBEN, RISIKO_MITTEL, Werkzeugart.SCHREIBEND, ())


class Werkzeugmerkmal:
    """Die vier Antworten zu einem Werkzeug, als ein Ding statt als Tupel."""

    __slots__ = ("name", "recht", "risiko", "wirkung", "schluesselfelder",
                 "bekannt")

    def __init__(self, name: str, recht: str, risiko: str, wirkung: str,
                 schluesselfelder: tuple, bekannt: bool = True) -> None:
        self.name = name
        self.recht = recht
        self.risiko = risiko
        self.wirkung = wirkung
        self.schluesselfelder = tuple(schluesselfelder)
        self.bekannt = bekannt

    @property
    def veraendert(self) -> bool:
        return self.wirkung != Werkzeugart.LESEND

    @property
    def zerstoert(self) -> bool:
        return self.wirkung == Werkzeugart.ZERSTOEREND

    @property
    def rang(self) -> int:
        return RISIKO_RANG.get(self.risiko, 1)

    def __repr__(self) -> str:
        return (f"<Werkzeugmerkmal {self.name} recht={self.recht} "
                f"risiko={self.risiko} wirkung={self.wirkung}>")


def merkmal(name: str, fertigkeiten=None) -> Werkzeugmerkmal:
    """Die Merkmale eines Werkzeugs - auch fuer selbstgebaute Fertigkeiten.

    `fertigkeiten` ist die Fertigkeitsverwaltung des Agenten (optional). Ist
    der Name eine Fertigkeit, wird ihre Kette ausgewertet und das SCHAERFSTE
    Glied bestimmt Recht und Risiko. Ohne das waere die Fertigkeit der Weg an
    der Schranke vorbei - siehe Modulkopf, Festlegung 2.
    """
    name = str(name or "")
    eintrag = _TABELLE.get(name)
    if eintrag is not None:
        return Werkzeugmerkmal(name, *eintrag)

    fertigkeit = None
    if fertigkeiten is not None:
        try:
            fertigkeit = (fertigkeiten.alle or {}).get(name)
        except Exception:
            fertigkeit = None
    if fertigkeit is None:
        return Werkzeugmerkmal(name, *_UNBEKANNT, bekannt=False)

    return _aus_fertigkeit(name, fertigkeit)


def _aus_fertigkeit(name: str, fertigkeit) -> Werkzeugmerkmal:
    """Recht und Risiko einer Fertigkeit aus ihren Schritten ableiten."""
    schaerfstes = Werkzeugmerkmal(name, LESEN, RISIKO_GERING,
                                  Werkzeugart.LESEND, ("name",))
    ordnung = {Werkzeugart.LESEND: 0, Werkzeugart.SCHREIBEND: 1,
               Werkzeugart.ZERSTOEREND: 2}
    hoechstes_recht = LESEN
    for schritt in list(getattr(fertigkeit, "schritte", []) or []):
        # Ein Schritt sieht aus wie 'notiz_erstellen(titel="...")'. Der Name
        # steht vor der Klammer; alles danach interessiert hier nicht.
        text = str(schritt)
        werkzeugname = text.split("(", 1)[0].strip()
        teil = _TABELLE.get(werkzeugname)
        if teil is None:
            teil = _UNBEKANNT
        recht, risiko, wirkung, _ = teil
        if RISIKO_RANG.get(risiko, 1) > schaerfstes.rang:
            schaerfstes.risiko = risiko
        if ordnung.get(wirkung, 1) > ordnung.get(schaerfstes.wirkung, 0):
            schaerfstes.wirkung = wirkung
        if _recht_rang(recht) > _recht_rang(hoechstes_recht):
            hoechstes_recht = recht
    schaerfstes.recht = hoechstes_recht
    return schaerfstes


# Grobe Rangfolge der Rechte-Handlungen, nur um die schaerfste zu finden.
# Die echte Mindeststufe steht in rechte.MINDESTSTUFE - hier wird nur
# verglichen, nicht entschieden.
_RECHT_RANG = {LESEN: 0, SUCHEN: 0, ZEICHNEN: 0, CODE_LESEN: 0,
               NETZ_LESEN: 1, "fertigkeit_anlegen": 2, "verhalten_anlegen": 2,
               SCHREIBEN: 3}


def _recht_rang(recht: str) -> int:
    return _RECHT_RANG.get(str(recht), 3)


def wirkungsschluessel(name: str, parameter: dict,
                       merkmale: Optional[Werkzeugmerkmal] = None) -> str:
    """Woran sich "dasselbe schon getan" erkennen laesst - oder leer.

    Leer heisst ausdruecklich: Diese Wirkung laesst sich NICHT wiedererkennen,
    also greift keine Sperre. Das ist ehrlicher als ein geratener Schluessel:
    Ein falscher Schluessel unterdrueckt eine Aenderung, die haette laufen
    muessen - und das faellt erst auf, wenn etwas fehlt.
    """
    merkmale = merkmale or merkmal(name)
    if not merkmale.veraendert or not merkmale.schluesselfelder:
        return ""
    teile = [str(name)]
    for feld in merkmale.schluesselfelder:
        wert = (parameter or {}).get(feld)
        if isinstance(wert, (list, tuple)):
            wert = ",".join(str(w) for w in wert)
        teile.append(f"{feld}={str(wert or '').strip().lower()[:120]}")
    return "|".join(teile)


def alle_merkmale() -> Dict[str, Werkzeugmerkmal]:
    """Die ganze Tabelle - fuer `/werkzeugrechte` und fuer die Tests."""
    return {name: Werkzeugmerkmal(name, *eintrag)
            for name, eintrag in _TABELLE.items()}


def uebersicht() -> str:
    zeilen = ["Werkzeug                    Recht              Risiko   Wirkung"]
    for name in sorted(_TABELLE):
        eintrag = Werkzeugmerkmal(name, *_TABELLE[name])
        zeilen.append(f"{name:<27} {eintrag.recht:<18} {eintrag.risiko:<8} "
                      f"{eintrag.wirkung}")
    return "\n".join(zeilen)
