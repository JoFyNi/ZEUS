"""Offene Punkte: festhalten, durchgehen, abhaken - nachprüfbar.

Bisher verschwand ein Fehlschlag im Gesprächsverlauf. Beim nächsten Start war
er weg, und niemand wusste mehr, was noch offen ist. Der Agent hat dann
entweder nichts getan oder dieselbe Sache erneut versucht, ohne zu wissen, dass
er sie schon zweimal versucht hatte.

Diese Liste lebt als Notiz in der Vault und ist damit für Zeus lesbar wie jede
andere. Drei Regeln, damit sie ehrlich bleibt:

1. EINTRAGEN passiert automatisch bei jedem fehlgeschlagenen Werkzeugaufruf.
   Niemand muss daran denken.
2. ABHAKEN passiert nur, wenn DERSELBE Vorgang später wirklich geklappt hat -
   nicht, weil das Modell sagt, es sei erledigt.
3. Wer mehrfach scheitert, wird als "festgefahren" markiert statt endlos
   wiederholt. Das ist die Information, die {user} braucht.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .config import settings

STATUS_OFFEN = "offen"
STATUS_ERLEDIGT = "erledigt"
STATUS_FESTGEFAHREN = "festgefahren"

# Ab so vielen Fehlversuchen gilt ein Punkt als festgefahren und wird
# {user} vorgelegt, statt weiter blind wiederholt zu werden.
GRENZE_FESTGEFAHREN = 3


@dataclass
class Aufgabe:
    schluessel: str
    werkzeug: str
    ziel: str
    beschreibung: str
    status: str = STATUS_OFFEN
    versuche: int = 0
    letzter_fehler: str = ""
    beleg: str = ""
    angelegt: str = ""
    geaendert: str = ""

    def zeile(self) -> str:
        marke = {STATUS_ERLEDIGT: "x", STATUS_FESTGEFAHREN: "!"}.get(self.status, " ")
        text = f"- [{marke}] **{self.werkzeug}** – {self.beschreibung}"
        if self.ziel:
            text += f" (`{self.ziel}`)"
        if self.status == STATUS_ERLEDIGT and self.beleg:
            text += f"\n      erledigt: {self.beleg}"
        elif self.letzter_fehler:
            text += f"\n      zuletzt: {self.letzter_fehler}"
        if self.versuche > 1:
            text += f"  ·  {self.versuche} Versuche"
        return text


def _schluessel(werkzeug: str, parameter: dict) -> str:
    """Was denselben Vorgang ausmacht: Werkzeug plus Ziel, nicht der ganze Aufruf.

    Bewusst NICHT alle Parameter: Der Agent probiert dieselbe Sache gern mit
    leicht anderer Beschreibung. Das ist derselbe offene Punkt, kein neuer.
    """
    ziel = _ziel_aus(parameter)
    # Bei Werkzeugen mit Quelle UND Ziel gehört beides zum Vorgang: A -> B ist
    # nicht dasselbe wie A -> C, und A -> "" (der Fehlversuch) schon gar nicht.
    zweitens = ""
    for feld in ("nach", "ziel", "zielordner", "neuer_name"):
        wert = (parameter or {}).get(feld)
        if isinstance(wert, (list, tuple)):
            wert = wert[0] if wert else ""
        if wert is not None and str(wert).strip():
            zweitens = str(wert).strip()[:60]
            break
    if ziel and feld_vorhanden(parameter):
        return f"{werkzeug}:{ziel.lower()}>{zweitens.lower()}"
    return f"{werkzeug}:{ziel.lower()}" if ziel else werkzeug


def feld_vorhanden(parameter: dict) -> bool:
    """Hat der Aufruf überhaupt ein Zielfeld - auch wenn es leer ist?"""
    return any(feld in (parameter or {})
               for feld in ("nach", "ziel", "zielordner", "neuer_name"))


def _ziel_aus(parameter: dict) -> str:
    for feld in ("name", "titel", "von", "quelle", "datei", "ordner", "alt", "abschnitt"):
        wert = (parameter or {}).get(feld)
        if isinstance(wert, (list, tuple)):
            wert = wert[0] if wert else ""
        if wert and str(wert).strip():
            return str(wert).strip()[:80]
    return ""


class Aufgabenliste:
    """Hält offene Punkte fest und hakt sie ab, wenn sie wirklich erledigt sind."""

    def __init__(self, tools, cfg=settings):
        self.tools = tools
        self.cfg = cfg
        self._eintraege: Dict[str, Aufgabe] = {}
        self._geladen = False

    # -------------------------------------------------------------- Ablage
    @property
    def datei(self) -> Path:
        ordner = self.tools.root / (self.cfg.learning_folder or "_Lernen")
        return ordner / "Offene_Punkte.md"

    @property
    def journal(self) -> Path:
        return self.cfg.work_dir / "aufgaben.jsonl"

    def laden(self) -> Dict[str, Aufgabe]:
        """Aus dem Journal lesen - das ist die maßgebliche Quelle.

        Die Notiz ist die lesbare Fassung für Obsidian; das Journal ist das,
        woraus wieder aufgebaut wird. Eine von Hand bearbeitete Notiz soll die
        Buchführung nicht kaputtmachen.
        """
        self._eintraege = {}
        self._geladen = True
        try:
            roh = self.journal.read_text(encoding="utf-8")
        except OSError:
            return self._eintraege
        for zeile in roh.splitlines():
            zeile = zeile.strip()
            if not zeile:
                continue
            try:
                daten = json.loads(zeile)
            except ValueError:
                continue
            try:
                aufgabe = Aufgabe(**{k: daten.get(k, "") for k in Aufgabe.__annotations__})
                aufgabe.versuche = int(daten.get("versuche") or 0)
            except TypeError:
                continue
            if aufgabe.schluessel:
                self._eintraege[aufgabe.schluessel] = aufgabe
        return self._eintraege

    @property
    def alle(self) -> Dict[str, Aufgabe]:
        if not self._geladen:
            self.laden()
        return self._eintraege

    def _sichern(self) -> bool:
        """Journal neu schreiben. Gibt zurück, ob es geklappt hat."""
        try:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            inhalt = "\n".join(
                json.dumps(asdict(a), ensure_ascii=False) for a in self._eintraege.values())
            self.journal.write_text(inhalt + ("\n" if inhalt else ""), encoding="utf-8")
            return True
        except OSError:
            # Kein pass ohne Grund: Die Liste ist Beiwerk und darf einen
            # laufenden Auftrag nicht abbrechen. Der Verlust fällt beim
            # nächsten /aufgaben auf, weil dann Einträge fehlen.
            return False

    # ------------------------------------------------------------ Eintragen
    def aus_fehlschlag(self, werkzeug: str, parameter: dict, meldung: str) -> Aufgabe:
        """Automatisch bei jedem fehlgeschlagenen Werkzeugaufruf."""
        schluessel = _schluessel(werkzeug, parameter)
        jetzt = dt.datetime.now().isoformat(timespec="seconds")
        aufgabe = self.alle.get(schluessel)
        if aufgabe is None:
            ziel = _ziel_aus(parameter)
            aufgabe = Aufgabe(
                schluessel=schluessel, werkzeug=werkzeug, ziel=ziel,
                beschreibung=_beschreiben(werkzeug, ziel),
                angelegt=jetzt,
            )
            self._eintraege[schluessel] = aufgabe
        elif aufgabe.status == STATUS_ERLEDIGT:
            # Es klappte schon einmal und scheitert jetzt wieder - wieder offen.
            aufgabe.status = STATUS_OFFEN
            aufgabe.beleg = ""
        aufgabe.versuche += 1
        aufgabe.letzter_fehler = str(meldung)[:200]
        aufgabe.geaendert = jetzt
        if aufgabe.versuche >= GRENZE_FESTGEFAHREN:
            aufgabe.status = STATUS_FESTGEFAHREN
        self._sichern()
        return aufgabe

    def erledigt_melden(self, werkzeug: str, parameter: dict, meldung: str) -> Optional[Aufgabe]:
        """Abhaken - aber nur, wenn genau dieser Punkt offen war.

        Ein erfolgreicher Aufruf hakt NICHT irgendetwas Ähnliches ab. Nur der
        Punkt mit demselben Schlüssel gilt als erledigt, und der Beleg ist die
        echte Rückmeldung des Werkzeugs, nicht die Prosa des Modells.
        """
        schluessel = _schluessel(werkzeug, parameter)
        aufgabe = self.alle.get(schluessel)
        if aufgabe is None or aufgabe.status == STATUS_ERLEDIGT:
            return None
        aufgabe.status = STATUS_ERLEDIGT
        aufgabe.beleg = str(meldung)[:200]
        aufgabe.geaendert = dt.datetime.now().isoformat(timespec="seconds")
        self._sichern()
        return aufgabe

    def eintragen(self, werkzeug: str, ziel: str, beschreibung: str) -> Aufgabe:
        """Punkt von Hand aufnehmen (z. B. aus einem Prüfbericht)."""
        schluessel = f"{werkzeug}:{ziel.lower()}" if ziel else werkzeug
        jetzt = dt.datetime.now().isoformat(timespec="seconds")
        aufgabe = self.alle.get(schluessel) or Aufgabe(
            schluessel=schluessel, werkzeug=werkzeug, ziel=ziel,
            beschreibung=beschreibung, angelegt=jetzt)
        aufgabe.beschreibung = beschreibung or aufgabe.beschreibung
        aufgabe.geaendert = jetzt
        self._eintraege[schluessel] = aufgabe
        self._sichern()
        return aufgabe

    # ------------------------------------------------------------- Abfragen
    def offene(self) -> List[Aufgabe]:
        return [a for a in self.alle.values() if a.status == STATUS_OFFEN]

    def festgefahren(self) -> List[Aufgabe]:
        return [a for a in self.alle.values() if a.status == STATUS_FESTGEFAHREN]

    def erledigte(self) -> List[Aufgabe]:
        return [a for a in self.alle.values() if a.status == STATUS_ERLEDIGT]

    def zusammenfassung(self) -> str:
        return (f"{len(self.offene())} offen, {len(self.festgefahren())} festgefahren, "
                f"{len(self.erledigte())} erledigt")

    def bericht(self) -> str:
        zeilen: List[str] = [self.zusammenfassung(), ""]
        for titel, liste in (("Festgefahren – brauchen eine Entscheidung", self.festgefahren()),
                             ("Offen", self.offene()),
                             ("Erledigt", self.erledigte()[-10:])):
            if not liste:
                continue
            zeilen.append(f"**{titel}**")
            for aufgabe in liste:
                zeilen.append(aufgabe.zeile())
            zeilen.append("")
        if not self.alle:
            zeilen = ["Nichts offen. Es ist bisher kein Werkzeugaufruf fehlgeschlagen."]
        return "\n".join(zeilen).rstrip()

    def fuer_prompt(self, anzahl: int = 4) -> str:
        """Kurzfassung für den Systemprompt - nur was noch aussteht."""
        wichtig = self.festgefahren() + self.offene()
        if not wichtig:
            return ""
        zeilen = ["OFFENE PUNKTE aus früheren Versuchen (nicht erneut identisch versuchen):"]
        for aufgabe in wichtig[:anzahl]:
            zeilen.append(f"- {aufgabe.werkzeug} auf '{aufgabe.ziel or '?'}' scheiterte "
                          f"{aufgabe.versuche}× – {aufgabe.letzter_fehler[:90]}")
        return "\n".join(zeilen)

    # --------------------------------------------------------- In die Vault
    def notiz_schreiben(self) -> str:
        """Die lesbare Fassung als Notiz - damit Zeus sie selbst finden kann."""
        heute = dt.date.today().isoformat()
        inhalt = (
            "---\n"
            'titel: "Offene Punkte"\n'
            "tags:\n  - system\n  - offen\n"
            f"aktualisiert: {heute}\n"
            "---\n\n"
            "# Offene Punkte\n\n"
            "Automatisch geführt. Ein Punkt entsteht bei jedem fehlgeschlagenen\n"
            "Werkzeugaufruf und wird abgehakt, sobald derselbe Vorgang wirklich\n"
            "geklappt hat – nicht, weil jemand sagt, es sei erledigt.\n\n"
            + self.bericht() + "\n"
        )
        try:
            self.datei.parent.mkdir(parents=True, exist_ok=True)
            self.datei.write_text(inhalt, encoding="utf-8")
            return str(self.datei.name)
        except OSError as exc:
            return f"(nicht schreibbar: {exc})"

    # ------------------------------------------------------- Durchgehen
    def abarbeiten(self, fortschritt=None) -> dict:
        """Offene Punkte der Reihe nach erneut versuchen - vom Code gesteuert.

        Kein Modell im Spiel: Es wird genau das wiederholt, was schon einmal
        versucht wurde, und nur was jetzt gelingt, wird abgehakt.
        """
        melden = fortschritt or (lambda _t: None)
        offen = self.offene() + self.festgefahren()
        if not offen:
            return {"geprueft": 0, "erledigt": [], "weiterhin_offen": []}

        erledigt: List[str] = []
        weiterhin: List[str] = []
        for aufgabe in offen:
            pruefer = NACHPRUEFUNG.get(aufgabe.werkzeug)
            if pruefer is None:
                weiterhin.append(f"{aufgabe.werkzeug} ({aufgabe.ziel}): nicht selbst prüfbar")
                continue
            melden(f"prüfe {aufgabe.werkzeug} · {aufgabe.ziel or '—'}")
            try:
                ok, beleg = pruefer(self.tools, aufgabe)
            except Exception as exc:
                weiterhin.append(f"{aufgabe.werkzeug} ({aufgabe.ziel}): Prüfung scheiterte ({exc})")
                continue
            if ok:
                aufgabe.status = STATUS_ERLEDIGT
                aufgabe.beleg = beleg[:200]
                aufgabe.geaendert = dt.datetime.now().isoformat(timespec="seconds")
                erledigt.append(f"{aufgabe.werkzeug} ({aufgabe.ziel}): {beleg[:80]}")
            else:
                aufgabe.letzter_fehler = beleg[:200]
                weiterhin.append(f"{aufgabe.werkzeug} ({aufgabe.ziel}): {beleg[:80]}")
        self._sichern()
        self.notiz_schreiben()
        return {"geprueft": len(offen), "erledigt": erledigt, "weiterhin_offen": weiterhin}


# ------------------------------------------------------------------ Helfer
def _beschreiben(werkzeug: str, ziel: str) -> str:
    texte = {
        "notiz_erstellen": "Notiz anlegen",
        "notiz_ergaenzen": "Notiz ergänzen",
        "notiz_verschieben": "Notiz verschieben",
        "notiz_loeschen": "Notiz löschen",
        "notizen_verknuepfen": "Notizen verknüpfen",
        "notizen_zusammenfuehren": "Dubletten zusammenführen",
        "links_bereinigen": "Tote Links bereinigen",
        "abschnitt_ersetzen": "Abschnitt ersetzen",
        "ordner_anlegen": "Ordner anlegen",
    }
    grund = texte.get(werkzeug, werkzeug.replace("_", " "))
    return f"{grund}: {ziel}" if ziel else grund


# Nachprüfungen: Ist der Punkt inzwischen von selbst erledigt?
# Bewusst nur dort, wo sich das OHNE Schreibzugriff feststellen lässt.
def _pruef_notiz_da(tools, aufgabe: Aufgabe):
    note = tools.resolve_note(aufgabe.ziel) if aufgabe.ziel else None
    return (True, f"Notiz '{note.stem}' existiert ({note.rel_path})") if note else \
        (False, f"Notiz '{aufgabe.ziel}' gibt es weiterhin nicht")


def _pruef_notiz_weg(tools, aufgabe: Aufgabe):
    note = tools.resolve_note(aufgabe.ziel) if aufgabe.ziel else None
    return (False, f"Notiz '{aufgabe.ziel}' liegt weiterhin da") if note else \
        (True, f"'{aufgabe.ziel}' ist nicht mehr in der Vault")


def _pruef_verknuepfung(tools, aufgabe: Aufgabe):
    """Die Verknüpfung selbst muss dastehen - nicht nur die Quellnotiz.

    _pruef_notiz_da hätte hier gereicht, um abzuhaken, dass die Quelle
    existiert. Das ist aber nicht die Aufgabe: Die Aufgabe war, einen Link zu
    setzen. Ein Punkt, der abgehakt wird, ohne dass die Sache erledigt ist,
    ist schlimmer als gar keine Liste.
    """
    ziel = aufgabe.schluessel.split(">", 1)[1] if ">" in aufgabe.schluessel else ""
    quelle = tools.resolve_note(aufgabe.ziel) if aufgabe.ziel else None
    if quelle is None:
        return False, f"Quellnotiz '{aufgabe.ziel}' gibt es nicht"
    if not ziel:
        return False, "Kein Ziel angegeben – der Aufruf war von vornherein unvollständig"
    try:
        text = quelle.path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"'{quelle.stem}' nicht lesbar ({exc})"
    if re.search(r"\[\[\s*" + re.escape(ziel) + r"\s*[\]|#]", text, re.IGNORECASE):
        return True, f"[[{ziel}]] steht in '{quelle.stem}'"
    return False, f"In '{quelle.stem}' steht kein Link auf '{ziel}'"


def _pruef_links_sauber(tools, aufgabe: Aufgabe):
    ergebnis = tools.links_pruefen(reparieren=False)
    offen = len(ergebnis.daten.get("kaputt") or [])
    return (True, "keine toten Links mehr") if offen == 0 else \
        (False, f"{offen} tote Links weiterhin offen")


def _pruef_quelle_lesbar(tools, aufgabe: Aufgabe):
    """Lässt sich die Quelldatei inzwischen lesen - und wenn nein, warum nicht?

    Vorher stand hier "nicht selbst prüfbar", obwohl genau das prüfbar ist:
    Der Aufruf ist rein lesend und folgenlos. Und die Antwort ist für Josh
    brauchbarer als der Punkt selbst, denn sie unterscheidet zwei Fälle, die
    ganz verschiedene Gegenmaßnahmen verlangen - falscher Pfad gegen
    fehlende Freigabe.
    """
    ziel = (aufgabe.ziel or "").strip()
    if not ziel:
        return False, "Kein Dateiname angegeben"
    ergebnis = tools.quellcode_lesen(datei=ziel, bis_zeile=1)
    if ergebnis.ok:
        return True, f"'{ziel}' ist jetzt lesbar"

    # Liegt die Datei unter anderem Namen in einer freigegebenen Wurzel?
    name = ziel.replace("\\", "/").rsplit("/", 1)[-1]
    treffer = []
    for wurzel in tools.cfg.code_root_list:
        try:
            treffer += [p for p in wurzel.rglob(name) if p.is_file()][:3]
        except OSError:
            # Nicht lesbares Verzeichnis - dann eben ohne Vorschlag weiter.
            continue
    if treffer:
        vorschlaege = ", ".join(str(p) for p in treffer[:3])
        return False, (f"'{ziel}' so nicht, aber die Datei liegt hier: {vorschlaege} "
                       f"– mit diesem Pfad erneut aufrufen")
    wurzeln = ", ".join(str(w) for w in tools.cfg.code_root_list) or "(keine)"
    return False, (f"'{ziel}' existiert in keiner freigegebenen Wurzel ({wurzeln}). "
                   f"Entweder ist der Pfad falsch, oder CODE_ROOTS in der .env "
                   f"muss um das Verzeichnis ergänzt werden")


def _pruef_zusammenfuehren(tools, aufgabe: Aufgabe):
    """Wurden die Dubletten inzwischen zusammengeführt?

    Prüfbar, sobald zwei Namen im Schlüssel stehen: Ist eine der beiden Notizen
    verschwunden, ist die Sache erledigt. Steht kein zweiter Name da, war der
    Aufruf von vornherein unvollständig - und das ist ein Befund, keine
    Ratlosigkeit.
    """
    if ">" not in aufgabe.schluessel:
        return False, ("Es wurden nie zwei Notizen benannt. notizen_zusammenfuehren "
                       "braucht 'quellen' (mindestens zwei vorhandene Namen) und "
                       "'ziel'. Erst notizen_suchen aufrufen, dann mit den "
                       "gefundenen Namen zusammenführen")
    erste = aufgabe.ziel
    zweite = aufgabe.schluessel.split(">", 1)[1]
    a = tools.resolve_note(erste) if erste else None
    b = tools.resolve_note(zweite) if zweite else None
    if a and b and a.rel_path != b.rel_path:
        return False, f"'{a.stem}' und '{b.stem}' liegen weiterhin getrennt"
    if not a and not b:
        return False, f"Weder '{erste}' noch '{zweite}' ist auffindbar"
    ueberlebend = a or b
    return True, f"nur noch '{ueberlebend.stem}' vorhanden – zusammengeführt"


NACHPRUEFUNG = {
    "quellcode_lesen": _pruef_quelle_lesbar,
    "code_auslesen": _pruef_quelle_lesbar,
    "notizen_zusammenfuehren": _pruef_zusammenfuehren,
    "notiz_erstellen": _pruef_notiz_da,
    "notiz_ergaenzen": _pruef_notiz_da,
    "notiz_bearbeiten": _pruef_notiz_da,
    "notiz_verschieben": _pruef_notiz_da,
    "abschnitt_ersetzen": _pruef_notiz_da,
    "abschnitt_einfuegen": _pruef_notiz_da,
    "tags_setzen": _pruef_notiz_da,
    "frontmatter_setzen": _pruef_notiz_da,
    "notizen_verknuepfen": _pruef_verknuepfung,
    "notiz_loeschen": _pruef_notiz_weg,
    "links_bereinigen": _pruef_links_sauber,
    "links_pruefen": _pruef_links_sauber,
}
