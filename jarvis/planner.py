"""Deterministischer Schreib-Pfad: Absicht → Plan → Ausführung → Verifikation.

Warum es das gibt: Sich darauf zu verlassen, dass ein lokales Modell von sich
aus brav Werkzeuge aufruft, ist unzuverlässig - kleine Modelle beschreiben
lieber, was sie tun würden, statt es zu tun.

**Neu in v6:** Der Planer kann nicht mehr nur *anlegen*. Bis v5 landete jede
Schreibabsicht hier und wurde zwangsläufig zu "neue Notiz" - ein Auftrag wie
"verschiebe die Agenten-Notizen nach Deep Learning" hätte neue Dateien erzeugt
statt zu verschieben. Jetzt plant er eine Folge beliebiger Operationen aus dem
vollen Werkzeugkasten, prüft jede gegen die Werkzeug-Schemata und führt sie mit
Rücklesekontrolle aus.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .config import settings
from .tools import TOOL_NAMES, TOOL_PARAMS, ToolResult, VaultTools, WriteError
from .vault import fold, safe_filename

# Verben, die eine Schreibabsicht anzeigen - deutsch und englisch
WRITE_INTENT = re.compile(
    r"\b("
    r"erstell\w*|leg\w*\s+an|anleg\w*|schreib\w*|verfass\w*|generier\w*|"
    r"ergänz\w*|ergaenz\w*|erweiter\w*|aktualisier\w*|überarbeit\w*|ueberarbeit\w*|"
    r"änder\w*|aender\w*|korrigier\w*|umbenenn\w*|verschieb\w*|lösch\w*|loesch\w*|"
    r"verknüpf\w*|verknuepf\w*|verlink\w*|strukturier\w*|dokumentier\w*|"
    r"notier\w*|festhalt\w*|speicher\w*|ableg\w*|"
    r"zusammenführ\w*|zusammenfuehr\w*|zusammenfass\w*|aufteil\w*|sortier\w*|"
    # ACHTUNG: "ordne\w*" hat das SUBSTANTIV "Ordner" mitgefangen. Die
    # Frage "ist es richtig, dass du die Fertigkeit im Code ORDNER hast?"
    # galt dadurch als Schreibauftrag und löste vier Operationen aus, die
    # Notizen verschoben und eine überschrieben. Deshalb nur Verbformen.
    r"aufräum\w*|aufraeum\w*|bereinig\w*|ordne(?:t|te|n|st)?\b|tagge\w*|"
    # Listenarbeit ist auch Schreibarbeit. Ohne diese Formen ging "hake X
    # ab" gar nicht erst in den Planer, sondern in den Werkzeuglauf - und
    # dort antwortete das Modell "erledigt", ohne etwas anzufassen.
    r"abhak\w*|abgehakt|abgeschlossen|durchstreich\w*|"
    r"create|write|add|update|generate|rename|move|delete|link|merge|split|organize"
    r")\b",
    re.IGNORECASE,
)
# Trennbare Verben: "führe die Notizen zusammen", "lege eine Notiz an",
# "benenne die Datei um". Ohne diese Muster bleibt die halbe deutsche
# Befehlsform unerkannt, weil Verb und Vorsilbe auseinanderstehen.
TRENNBARE_VERBEN = re.compile(
    r"\b(?:führ|fuehr|fass|bring)\w*\b[^.?!]{0,60}\bzusammen\b"
    r"|\b(?:leg|füg|fueg|trag)\w*\b[^.?!]{0,60}\b(?:an|hinzu|ein)\b"
    r"|\b(?:benenn|schreib|sortier|bau|arbeit)\w*\b[^.?!]{0,60}\bum\b"
    r"|\b(?:räum|raeum|misch)\w*\b[^.?!]{0,60}\bauf\b"
    r"|\bteil\w*\b[^.?!]{0,60}\bauf\b"
    r"|\b(?:hak|streich|schlie[sß])\w*\b[^.?!]{0,60}\bab\b",
    re.IGNORECASE,
)

QUESTION_HINT = re.compile(r"^\s*(was|wie|warum|wieso|wer|wo|wann|welche\w*|erklär\w*|erklaer\w*)\b",
                           re.IGNORECASE)

# Fragen ÜBER das Tun - keine Aufträge. "Kannst du mir sagen wieso du es nicht
# schaffst, Daten zu löschen?" enthält 'löschen', ist aber eine Rückfrage.
# Ohne diese Erkennung wird aus jeder Nachfrage ein Schreibauftrag.
META_FRAGE = re.compile(
    r"\b(warum|wieso|weshalb|wodurch|woran\s+liegt)\b"
    r"|\b(?:schaffst|kannst|konntest|hast)\s+du\b[^?]{0,60}?\bnicht\b"
    r"|\bnicht\s+(?:geschafft|geklappt|funktioniert|gelungen|möglich|moeglich)\b"
    r"|\b(?:klappt|funktioniert|geht)\s+(?:das\s+|es\s+)?nicht\b"
    r"|\bwas\s+(?:ist|war)\s+(?:da\s+)?(?:schief|falsch)\b"
    r"|\berkläre?\s+mir\b|\berklaere?\s+mir\b"
    r"|\b(?:bist|wärst|waerst)\s+du\s+(?:in\s+der\s+lage|fähig|faehig)\b",
    re.IGNORECASE,
)

# Operationen, deren Inhalt das Modell erst noch schreiben muss
INHALT_NOETIG = {
    "notiz_erstellen": "inhalt",
    "notiz_ergaenzen": "inhalt",
    "notiz_ueberschreiben": "inhalt",
    "abschnitt_ersetzen": "inhalt",
    "abschnitt_einfuegen": "inhalt",
}
# Was der Planer anbieten darf (Lesewerkzeuge gehören nicht in einen Schreibplan)
PLANBARE_AKTIONEN = [
    "notiz_erstellen", "notiz_ergaenzen", "notiz_bearbeiten", "notiz_ueberschreiben",
    "abschnitt_ersetzen", "abschnitt_einfuegen", "abschnitt_loeschen",
    "notiz_verschieben", "notiz_loeschen", "notiz_wiederherstellen",
    "notizen_verknuepfen", "notizen_zusammenfuehren", "notiz_aufteilen",
    "auto_verlinken", "uebersichtsnotiz", "tags_setzen", "frontmatter_setzen",
    "ordner_anlegen", "ordner_umbenennen",
    "codeblock_hinzufuegen", "datei_importieren",
    "listenpunkt_hinzufuegen", "listenpunkt_erledigt",
]

# Womit Modelle die Aktionen sonst noch benennen. Ohne diese Tabelle fällt ein
# vollkommen brauchbarer Plan durch, nur weil "move_note" statt
# "notiz_verschieben" dasteht.
AKTIONS_SYNONYME = {
    "move": "notiz_verschieben", "move_note": "notiz_verschieben",
    "verschieben": "notiz_verschieben", "notiz_bewegen": "notiz_verschieben",
    "rename": "notiz_verschieben", "umbenennen": "notiz_verschieben",
    "create": "notiz_erstellen", "create_note": "notiz_erstellen",
    "new_note": "notiz_erstellen", "erstellen": "notiz_erstellen",
    "anlegen": "notiz_erstellen", "notiz_anlegen": "notiz_erstellen",
    "schreiben": "notiz_erstellen", "notiz_schreiben": "notiz_erstellen",
    "append": "notiz_ergaenzen", "add": "notiz_ergaenzen",
    "ergaenzen": "notiz_ergaenzen", "ergänzen": "notiz_ergaenzen",
    "erweitern": "notiz_ergaenzen", "notiz_erweitern": "notiz_ergaenzen",
    "update": "notiz_bearbeiten", "edit": "notiz_bearbeiten",
    "bearbeiten": "notiz_bearbeiten", "aendern": "notiz_bearbeiten",
    "ändern": "notiz_bearbeiten", "ersetzen": "notiz_bearbeiten",
    "overwrite": "notiz_ueberschreiben", "ueberschreiben": "notiz_ueberschreiben",
    "delete": "notiz_loeschen", "remove": "notiz_loeschen",
    "loeschen": "notiz_loeschen", "löschen": "notiz_loeschen",
    "link": "notizen_verknuepfen", "verlinken": "notizen_verknuepfen",
    "verknuepfen": "notizen_verknuepfen", "verknüpfen": "notizen_verknuepfen",
    "merge": "notizen_zusammenfuehren", "zusammenfuehren": "notizen_zusammenfuehren",
    "split": "notiz_aufteilen", "aufteilen": "notiz_aufteilen",
    "tag": "tags_setzen", "tags": "tags_setzen", "taggen": "tags_setzen",
    "create_folder": "ordner_anlegen", "mkdir": "ordner_anlegen",
    "ordner_erstellen": "ordner_anlegen", "folder": "ordner_anlegen",
    "section_replace": "abschnitt_ersetzen", "replace_section": "abschnitt_ersetzen",
    "insert_section": "abschnitt_einfuegen", "add_section": "abschnitt_einfuegen",
    "index": "uebersichtsnotiz", "moc": "uebersichtsnotiz", "overview": "uebersichtsnotiz",
    "autolink": "auto_verlinken", "import": "datei_importieren",
    "code": "codeblock_hinzufuegen", "add_code": "codeblock_hinzufuegen",
    # Listenpunkte. "add" bleibt bewusst bei notiz_ergaenzen: es ist zu
    # allgemein, um daraus einen Listeneintrag zu machen, und wer "add" sagt,
    # meint meistens Text. Nur die eindeutig listenbezogenen Namen zeigen
    # hierher.
    "add_task": "listenpunkt_hinzufuegen", "add_item": "listenpunkt_hinzufuegen",
    "add_todo": "listenpunkt_hinzufuegen", "task_add": "listenpunkt_hinzufuegen",
    "aufgabe_hinzufuegen": "listenpunkt_hinzufuegen",
    "listenpunkt": "listenpunkt_hinzufuegen", "punkt_hinzufuegen": "listenpunkt_hinzufuegen",
    "check_off": "listenpunkt_erledigt", "check": "listenpunkt_erledigt",
    "complete": "listenpunkt_erledigt", "done": "listenpunkt_erledigt",
    "abhaken": "listenpunkt_erledigt", "erledigt": "listenpunkt_erledigt",
    "aufgabe_erledigt": "listenpunkt_erledigt", "task_done": "listenpunkt_erledigt",
}

# Parameternamen, die Modelle gern statt der erwarteten verwenden
PARAM_SYNONYME = {
    "title": "titel", "note": "name", "notiz": "name", "datei": "name",
    "file": "name", "filename": "name", "dateiname": "name", "quelle": "name",
    "content": "inhalt", "text": "inhalt", "body": "inhalt",
    "folder": "ordner", "verzeichnis": "ordner", "zielordner": "ziel_ordner",
    "target": "ziel_ordner", "target_folder": "ziel_ordner", "nach_ordner": "ziel_ordner",
    "ziel": "ziel_ordner", "destination": "ziel_ordner", "to": "nach", "from": "von",
    "new_name": "neuer_name", "neuer_titel": "neuer_name", "rename_to": "neuer_name",
    "section": "abschnitt", "heading": "ueberschrift", "ueberschrift_neu": "ueberschrift",
    "sources": "quellen", "notes": "quellen", "labels": "tags",
    "search": "suchtext", "replace": "ersatztext", "old": "suchtext", "new": "ersatztext",
    "language": "sprache", "path": "pfad", "key": "schluessel", "value": "wert",
}

CONTENT_TEMPLATE = """## Kurzfassung
## Einordnung und Struktur
## Funktionsweise / Vorgehen
## Typische Probleme
## Lösungen und Gegenmaßnahmen
## Offene Fragen"""


@dataclass
class Operation:
    aktion: str
    parameter: Dict[str, object] = field(default_factory=dict)
    auftrag: str = ""          # inhaltlicher Auftrag, falls Text erzeugt werden muss

    @property
    def titel(self) -> str:
        for schluessel in ("titel", "name", "ordner", "von", "alt"):
            if self.parameter.get(schluessel):
                return str(self.parameter[schluessel])
        return self.aktion

    def beschreibung(self) -> str:
        # Das Auslassungszeichen ist nicht Kosmetik. Ohne es stand im Protokoll
        # 'nach=Lauffähiger_Code_für_Agentenarchitekture' - ein Name, dem das
        # letzte 'n' fehlt und der genauso aussieht wie ein echter Tippfehler
        # des Modells. Man sucht dann den Fehler in den Daten statt in der
        # Anzeige.
        def kurz(wert) -> str:
            text = str(wert)
            return text if len(text) <= 40 else text[:40] + "…"

        knapp = ", ".join(f"{k}={kurz(v)}" for k, v in self.parameter.items()
                          if k not in ("inhalt",))
        return f"{self.aktion}({knapp})"


@dataclass
class WritePlan:
    absicht: str
    ordner: str = ""
    operationen: List[Operation] = field(default_factory=list)
    # Der ORIGINALTEXT des Auftrags. Klingt nach Beiwerk, ist aber der
    # Unterschied zwischen einer Notiz und "Inhalt zu dünn": Wenn jemand eine
    # 121-zeilige Spezifikation einfügt und daraus Notizen entstehen sollen,
    # steht das Material genau dort - und nirgends sonst. Ohne diesen Text
    # schrieb das Modell über ein Thema, zu dem es weder in der Vault noch im
    # Prompt etwas fand, und lieferte drei Sätze.
    auftragstext: str = ""
    # Nennt der Auftrag einen Ordner ("alle Code dateien"), steht er hier.
    # Operationen an Notizen AUSSERHALB werden dann verworfen - siehe
    # WritePlanner.geltungsbereich_pruefen().
    geltungsbereich: str = ""

    def __bool__(self) -> bool:
        return bool(self.operationen)

    def uebersicht(self) -> str:
        zeilen = [f"Vorhaben: {self.absicht}"]
        if self.ordner:
            zeilen.append(f"Zielordner: {self.ordner}")
        zeilen.append("")
        zeilen += [f"  {i:2}. {op.beschreibung()}" for i, op in enumerate(self.operationen, 1)]
        return "\n".join(zeilen)


@dataclass
class PlanErgebnis:
    plan: Optional[WritePlan]
    erstellt: List[dict] = field(default_factory=list)       # erfolgreiche Operationen
    fehlgeschlagen: List[dict] = field(default_factory=list)
    abgebrochen: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.erstellt) and not self.fehlgeschlagen

    def bericht(self, assistent: str, anrede: str) -> str:
        if self.abgebrochen:
            return self.abgebrochen
        zeilen: List[str] = []
        if self.erstellt:
            zeilen.append(f"**{len(self.erstellt)} Änderungen ausgeführt:**")
            for e in self.erstellt:
                pfad = e.get("pfad")
                zeilen.append(f"- {e['meldung']}" + (f"  `{pfad}`" if pfad else ""))
        if self.fehlgeschlagen:
            zeilen.append("")
            zeilen.append(f"**{len(self.fehlgeschlagen)} fehlgeschlagen – bitte ansehen, {anrede}:**")
            for e in self.fehlgeschlagen:
                zeile = f"- {e['titel']}: {e['fehler']}"
                if e.get("vorschlaege"):
                    zeile += "\n  Vorhanden ist z. B.: " + ", ".join(
                        f"[[{v}]]" for v in e["vorschlaege"][:4])
                zeilen.append(zeile)
            zeilen.append("")
            zeilen.append('Sagen Sie "nochmal versuchen", dann nehme ich die offenen erneut in Angriff.')
        if not zeilen:
            return "Es gab nichts zu tun."
        return "\n".join(zeilen)


# "Erstelle eine Fertigkeit namens X" ist KEIN Auftrag, eine Notiz anzulegen.
# Genau so wurde er aber behandelt: Das Wort "Erstelle" löste den Schreibplaner
# aus, und weil fertigkeit_definieren nicht in PLANBARE_AKTIONEN steht, blieb
# als einzige passende Aktion notiz_erstellen übrig. Heraus kam der Versuch,
# eine NOTIZ namens "Aufgabenanalyse" zu schreiben - und weil es dazu nichts zu
# schreiben gab, "Inhalt zu dünn". Der Auftrag war nie angekommen.
# Das Deutsche trennt seine Verben: "Lege eine Fertigkeit AN" - zwischen
# 'Lege' und 'an' steht das Objekt. Ein Ausdruck, der nur nach 'anlegen' sucht,
# findet genau die Formulierung nicht, die man am ehesten tippt.
_TUN = (r"erstell\w*|anleg\w*|leg\w*|definier\w*|bau\w*|schreib\w*|"
        r"entwickel\w*|einricht\w*|hinzuf[üu]g\w*|mach\w*")
_FERTIGKEIT = r"fertigkeit(?:en)?|f[äa]higkeit(?:en)?|skill(?:s)?"
# Steht zwischen Verb und Fertigkeit ein anderes Objekt, ist DAS gemeint:
# "Erstelle eine NOTIZ über Fertigkeiten im Sport" will eine Notiz, nicht
# einen ausführbaren Ablauf. Ohne diesen Ausschluss wäre die Erkennung so
# übereifrig wie der Fehler, den sie beheben soll - nur in die andere Richtung.
_ANDERES_OBJEKT = r"notiz|datei|dokument|note|eintrag|ordner|liste"
FERTIGKEIT_INTENT = re.compile(
    rf"\b(?:{_FERTIGKEIT})\b[^.?!]{{0,80}}?\b(?:{_TUN})\b"
    rf"|\b(?:{_TUN})\b(?:(?!\b(?:{_ANDERES_OBJEKT})\b)[^.?!]){{0,40}}?"
    rf"\b(?:{_FERTIGKEIT})\b",
    re.IGNORECASE)


# --------------------------------------------------- Welche Art Fertigkeit?
# Seit es zwei Arten gibt, reicht "er will eine Fertigkeit" nicht mehr. Am
# 04.09. wollte Josh eine Darstellungs-GEWOHNHEIT ("stelle mir Abläufe visuell
# dar, einfach im Chat") und bekam den Versuch, daraus eine Werkzeugkette zu
# bauen - die einzige Art, die es gab. Zeus hat das selbst gemerkt, konnte
# aber nichts anderes anbieten und legte am Ende eine Notiz an.
#
# Die Entscheidung fällt hier, in Python, an Wörtern - nicht im Modell an
# Selbsteinschätzung. Ein Modell, das entscheiden soll, welche von zwei
# ähnlichen Formen es nimmt, nimmt mal die eine und mal die andere.

# Wörter, die auf die ANTWORT zielen: wie etwas gesagt, gezeigt, geformt wird.
_VERHALTEN_WORT = re.compile(
    r"\b(darstell\w*|dargestellt|visualisier\w*|veranschaulich\w*|zeichn\w*|"
    r"schaubild\w*|diagramm\w*|grafisch|bildlich|visuell\w*|"
    r"antwort\w*|formulier\w*|erkl[äa]r\w*|ausgeb\w*|ausgabe|wiedergab\w*|"
    r"im chat|als text|stil|tonfall|knapp|ausf[üu]hrlich|"
    r"immer wenn|sobald|jedes mal|standardm[äa][sß]ig|grunds[äa]tzlich)\b",
    re.IGNORECASE)

# Wörter, die auf eine Handlung an der VAULT zielen.
_ABLAUF_WORT = re.compile(
    r"\b(notiz\w*|note[sn]?|vault|ordner|datei\w*|verkn[üu]pf\w*|verlink\w*|"
    r"tags?|frontmatter|abschnitt\w*|suchen|recherchier\w*|"
    r"abspeicher\w*|speicher\w*|ablegen|importier\w*|"
    r"verschieb\w*|zusammenf[üu]hr\w*|aufteil\w*)\b",
    re.IGNORECASE)


def skill_art(text: str) -> str:
    """'verhalten' oder 'ablauf' - welche Fertigkeitsart ist gemeint?

    Die Regel in einem Satz: Wer beschreibt, WIE geantwortet werden soll, will
    ein Verhalten. Wer beschreibt, WAS an der Vault geschehen soll, will einen
    Ablauf. Steht beides drin, gewinnt das Verhalten - eine Verhaltensanleitung
    darf ein Werkzeug aufrufen, ein Ablaufplan aber keine Haltung ausdrücken.
    """
    if not text:
        return "ablauf"
    verhalten = len(_VERHALTEN_WORT.findall(text))
    ablauf = len(_ABLAUF_WORT.findall(text))
    if verhalten and verhalten >= ablauf:
        return "verhalten"
    if ablauf:
        return "ablauf"
    # Weder das eine noch das andere: Ohne eine einzige Vault-Aktion im Auftrag
    # lässt sich gar kein Ablaufplan bauen - dann ist es ein Verhalten.
    return "verhalten"


def looks_like_skill_request(text: str) -> bool:
    """Will der Nutzer eine FERTIGKEIT anlegen statt einer Notiz?

    Die Unterscheidung muss vor dem Schreibplaner fallen. Danach ist sie nicht
    mehr zu treffen: Der Planer kennt nur Aktionen an der Vault, und unter
    denen sieht "erstelle …" immer wie notiz_erstellen aus.
    """
    if not text:
        return False
    if META_FRAGE.search(text):
        return False
    if QUESTION_HINT.match(text.strip()) and "?" in text:
        return False
    return bool(FERTIGKEIT_INTENT.search(text))


def looks_like_write_request(text: str) -> bool:
    """Erkennt, ob der Nutzer eine Änderung an der Vault will.

    Wichtig ist die Gegenrichtung: Eine Frage DARF nicht als Auftrag durchgehen.
    "Kannst du die Notiz verschieben?" ist eine höfliche Aufforderung,
    "Kannst du mir sagen wieso du nichts verschieben kannst?" ist eine Rückfrage -
    beide enthalten dasselbe Verb.
    """
    if not text:
        return False
    if not (WRITE_INTENT.search(text) or TRENNBARE_VERBEN.search(text)):
        return False
    if META_FRAGE.search(text):
        return False
    if QUESTION_HINT.match(text.strip()) and "?" in text:
        return False
    return True


# Aktionen, die eine BESTEHENDE Notiz dort anfassen, wo sie liegt. Nur für
# sie gilt die Ordnerschranke. Alles andere (verschieben, anlegen,
# zusammenführen, aufteilen, Ordner anlegen) bringt etwas IN den Ordner.
ORTSGEBUNDEN = {
    "notizen_verknuepfen", "auto_verlinken", "links_bereinigen",
    "notiz_ergaenzen", "notiz_bearbeiten", "notiz_ueberschreiben",
    "abschnitt_ersetzen", "abschnitt_einfuegen", "abschnitt_loeschen",
    "block_ersetzen", "codeblock_hinzufuegen",
    "tags_setzen", "frontmatter_setzen", "notiz_loeschen",
}


# Welcher Parameter benennt die Notiz, die WIRKLICH geaendert wird? Der
# Unterschied entscheidet, und er wurde am 21.09. teuer bezahlt:
#
#   "erstelle die Notiz und fuege sie verlinkt in die Aufgaben hinzu"
#
# Der Planer leitete den Geltungsbereich "Aufgaben" ab und verwarf danach
# JEDE Verknuepfung, deren ZIEL in "Wissen" lag - viermal, und jedes
# "nochmal versuchen" erzeugte denselben Fehlschlag. Dabei ist genau das der
# Sinn einer Verknuepfung: Sie zeigt aus dem einen Ordner in den anderen.
# Geaendert wird dabei nur die Notiz auf der VON-Seite.
_GEAENDERT_VON = {
    "notizen_verknuepfen": ("von",),      # "nach" wird nur referenziert
    "auto_verlinken": ("name", "ordner"),
}
_GEAENDERT_STANDARD = ("name", "quelle", "notiz")


def _geplante_namen(operationen) -> list:
    """Notiznamen, die der Plan selbst erst erzeugt."""
    # Welcher Parameter den neuen Namen traegt, ist je Werkzeug verschieden -
    # eine zweite Liste woanders liefe auseinander, deshalb steht sie hier.
    felder = {
        "notiz_erstellen": "titel",
        "uebersichtsnotiz": "titel",
        "notizen_zusammenfuehren": "ziel_titel",
    }
    namen = []
    for op in operationen:
        feld = felder.get(op.aktion)
        wert = op.parameter.get(feld) if feld else None
        if wert:
            namen.append(str(wert))
            # Der Dateiname ersetzt Leerzeichen durch Unterstriche; das Modell
            # nennt im Folgeschritt mal so, mal so.
            namen.append(str(wert).replace(" ", "_"))
    return namen


def _geaenderte_parameter(op) -> tuple:
    """Die Parameter, die auf eine tatsaechlich geaenderte Notiz zeigen."""
    besonders = _GEAENDERT_VON.get(op.aktion)
    if besonders is None:
        return _GEAENDERT_STANDARD
    if op.aktion == "notizen_verknuepfen" and op.parameter.get("beidseitig"):
        # Beidseitig heisst: beide Seiten bekommen einen Eintrag, also
        # gehoeren auch beide in den Geltungsbereich.
        return ("von", "nach")
    return besonders


def _im_geltungsbereich(ordner_der_notiz: str, geltungsbereich: str) -> bool:
    """Liegt die Notiz im genannten Ordner oder darunter?"""
    a = (ordner_der_notiz or "").strip("/").lower()
    b = (geltungsbereich or "").strip("/").lower()
    return not b or a == b or a.startswith(b + "/")


# Wie viel Gespraech der Planer mitbekommt. Zwei Zuege reichen fuer den Fall,
# um den es geht ("erstelle die neue Notiz so wie besprochen"), und halten den
# Prompt schmal - der Planer hat mit Ordner- und Notizliste schon genug zu
# lesen.
VERLAUF_ZUEGE = 4
VERLAUF_ZEICHEN = 900


def _verlauf_als_text(verlauf: Sequence[dict]) -> str:
    """Die letzten Zuege als Block fuer den Planerprompt.

    Ohne ihn war ein Anschlussauftrag nicht planbar: "erstelle die neue Notiz
    so wie besprochen" enthaelt weder Notiznamen noch Thema, und der Planer sah
    nur diesen einen Satz. Herausgekommen ist eine Notiz mit erfundenem Titel -
    dieselbe Klasse Fehler wie beim Fertigkeitswunsch, nur eine Ebene tiefer.

    Ein leerer Verlauf ergibt eine leere Zeichenkette, keinen leeren Block:
    eine Ueberschrift ohne Inhalt liest ein Modell als "hier stand nichts,
    also gab es kein Gespraech" - und das stimmt dann zwar, kostet aber Platz.
    """
    zuege = [z for z in (verlauf or [])
             if str(z.get("content") or "").strip()][-VERLAUF_ZUEGE:]
    if not zuege:
        return ""
    zeilen = ["GESPRÄCHSVERLAUF (worauf sich der Auftrag bezieht):"]
    for zug in zuege:
        wer = "Nutzer" if zug.get("role") == "user" else "Du"
        text = " ".join(str(zug.get("content") or "").split())[:VERLAUF_ZEICHEN]
        zeilen.append(f"{wer}: {text}")
    return "\n".join(zeilen)


class WritePlanner:
    """Plant und führt Änderungen aus - mit Kontrolle nach jedem Schritt."""

    def __init__(self, agent, cfg=settings):
        self.agent = agent
        self.cfg = cfg
        # Warum ein Plan verworfen wurde - wird dem Nutzer gezeigt statt verschluckt
        self.diagnose: List[str] = []

    # ------------------------------------------------------------------ Plan
    def _aktionsliste(self) -> str:
        from .tools import TOOL_SCHEMAS

        aus_schema = {t["name"]: t for t in TOOL_SCHEMAS}
        zeilen = []
        for name in PLANBARE_AKTIONEN:
            schema = aus_schema.get(name)
            if not schema:
                continue
            pflicht = ", ".join(schema["required"]) or "-"
            optional = ", ".join(k for k in schema["parameters"] if k not in schema["required"])
            zeilen.append(f'- "{name}": Pflicht: {pflicht}'
                          + (f"; optional: {optional}" if optional else "")
                          + f" — {schema['description'].split('.')[0]}")
        return "\n".join(zeilen)

    def plan_erstellen(self, auftrag: str, kontext: str = "",
                       verlauf: Optional[Sequence[dict]] = None) -> Optional[WritePlan]:
        ordnerliste = ", ".join(self.agent.structure.folder_paths) or "(noch keine)"
        notizen = sorted(self.agent.index.notes, key=lambda n: n.rel_path)
        # Dateiname UND Titel anbieten: das Modell greift sonst zum Titel und
        # trifft den Dateinamen nicht.
        eintraege = []
        for n in notizen[:120]:
            if fold(n.title) and fold(n.title) != fold(n.stem):
                eintraege.append(f"{n.stem} (Titel: {n.title})")
            else:
                eintraege.append(n.stem)
        notizliste = "\n".join(f"  - {e}" for e in eintraege) or "  (keine)"
        if len(notizen) > 120:
            notizliste += f"\n  … und {len(notizen) - 120} weitere"
        prompt = f"""Der Nutzer hat folgenden Auftrag erteilt:
---
{auftrag}
---

VORHANDENE ORDNER: {ordnerliste}
VORHANDENE NOTIZEN:\n{notizliste}

{_verlauf_als_text(verlauf or [])}

KONTEXT AUS DEN NOTIZEN:
{kontext[:2500]}

Plane die nötigen Schritte. Verfügbare Aktionen:
{self._aktionsliste()}

Regeln:
- Wähle die Aktion, die der Nutzer WIRKLICH meint. "verschiebe" ist notiz_verschieben,
  nicht notiz_erstellen. "ergänze" ist notiz_ergaenzen. "führe zusammen" ist
  notizen_zusammenfuehren.
- Bei Aktionen, die Text erzeugen, schreibe den Inhalt NICHT selbst, sondern gib
  unter "auftrag" an, worum es inhaltlich gehen soll.
- Verwende bei "name", "von", "nach" und "quellen" AUSSCHLIESSLICH Namen aus der
  Liste VORHANDENE NOTIZEN oben - buchstabengetreu abgeschrieben. Erfinde keine
  Titel und formuliere keine um. Gibt es die passende Notiz nicht, plane
  notiz_erstellen statt einer Bearbeitung.
- Verwende bei Ordnern nur Pfade aus VORHANDENE ORDNER.
- Höchstens {self.cfg.max_files_per_run} Schritte.

Antworte ausschließlich mit diesem JSON:
{{"absicht": "kurz was passieren soll",
  "ordner": "Zielordner für neue Notizen, falls zutreffend",
  "operationen": [
    {{"aktion": "<eine der obigen>", "parameter": {{"...": "..."}}, "auftrag": "nur wenn Text erzeugt wird"}}
  ]}}"""

        self.diagnose = []
        # 1. Versuch: kleines, schnelles Modell mit vollem Prompt
        plan = self._versuch(prompt, schnell=True)
        if plan:
            return self._auftrag_anheften(plan, auftrag)

        # 2. Versuch: großes Modell, deutlich einfacherer Prompt.
        # Kleine Modelle scheitern an langen Schemata - ein knapper Prompt mit
        # Beispiel bringt sie viel zuverlässiger auf das richtige Format.
        self.diagnose.append("erster Versuch ohne Ergebnis – vereinfachter zweiter Versuch")
        plan = self._versuch(self._einfacher_prompt(auftrag), schnell=False, temperatur=0.0)
        if plan:
            return self._auftrag_anheften(plan, auftrag)

        # 3. Versuch: ohne Modell, direkt aus dem Satz
        plan = self.ersatzplan(auftrag)
        if plan:
            self.diagnose.append("Plan regelbasiert aus dem Auftrag abgeleitet")
        return self._auftrag_anheften(plan, auftrag)

    @staticmethod
    def _auftrag_anheften(plan: Optional[WritePlan], auftrag: str) -> Optional[WritePlan]:
        """Den Originalauftrag am Plan festhalten - er ist Quellenmaterial."""
        if plan is not None and not plan.auftragstext:
            plan.auftragstext = str(auftrag or "")
        return plan

    def _versuch(self, prompt: str, schnell: bool,
                 temperatur: float = 0.1) -> Optional[WritePlan]:
        try:
            roh = self.agent.llm.chat(
                [{"role": "user", "content": prompt}], temperature=temperatur,
                format_json=True, schnell=schnell,
            )
        except Exception as exc:
            self.diagnose.append(f"Modell antwortete nicht: {exc}")
            return None
        if not (roh or "").strip():
            self.diagnose.append("Modell lieferte eine leere Antwort")
            return None
        plan = self._plan_parsen(roh)
        if plan is None and not self.diagnose:
            self.diagnose.append(f"Antwort war kein verwertbares JSON: {roh.strip()[:160]}")
        return plan

    def _einfacher_prompt(self, auftrag: str) -> str:
        """Knapper Prompt mit Beispiel - für Modelle, die am großen scheitern."""
        notizen = ", ".join(sorted(n.stem for n in self.agent.index.notes)[:40]) or "(keine)"
        ordner = ", ".join(self.agent.structure.folder_paths) or "(keine)"
        return f"""Auftrag: {auftrag}

Notizen: {notizen}
Ordner: {ordner}

Wähle EINE Aktion aus dieser Liste:
notiz_erstellen(titel), notiz_ergaenzen(name), notiz_verschieben(name, ziel_ordner),
notiz_loeschen(name), notizen_verknuepfen(von, nach), tags_setzen(name, tags),
ordner_anlegen(pfad), uebersichtsnotiz(ordner)

Beispiel für die Antwort:
{{"operationen": [{{"aktion": "notiz_verschieben",
  "parameter": {{"name": "Gleichstrom", "ziel_ordner": "Elektrotechnik"}}}}]}}

Antworte NUR mit JSON in genau diesem Format."""

    # ------------------------------------------------------------ Ersatzplan
    def ersatzplan(self, auftrag: str) -> Optional[WritePlan]:
        """Plan ohne Modell - direkt aus dem Satz gelesen.

        Wenn das Modell keinen brauchbaren Plan liefert, ist der Auftrag deshalb
        noch lange nicht unverständlich. "verschiebe X nach Y" lässt sich ohne
        jede Sprachmodell-Magie erkennen.
        """
        text = (auftrag or "").strip()
        operationen: List[Operation] = []

        muster = [
            # verschiebe/schiebe X nach|in Y
            (r"(?:verschieb|schieb|bewege?)\w*\s+(?:die\s+|das\s+|den\s+)?(?:notiz\s+|datei\s+)?"
             r"[\"'\[]*(.+?)[\"'\]]*\s+(?:nach|in|zu|unter)\s+[\"'\[]*(.+?)[\"'\]]*\s*[.!?]?$",
             lambda m: Operation("notiz_verschieben",
                                 {"name": m.group(1).strip(), "ziel_ordner": m.group(2).strip()})),
            # benenne X in Y um
            (r"benenn\w*\s+(?:die\s+|das\s+)?(?:notiz\s+|datei\s+)?[\"'\[]*(.+?)[\"'\]]*\s+"
             r"(?:in|zu|nach)\s+[\"'\[]*(.+?)[\"'\]]*\s+um\b",
             lambda m: Operation("notiz_verschieben",
                                 {"name": m.group(1).strip(), "neuer_name": m.group(2).strip()})),
            # verknüpfe X mit Y
            (r"(?:verknüpf|verknuepf|verlink)\w*\s+[\"'\[]*(.+?)[\"'\]]*\s+mit\s+"
             r"[\"'\[]*(.+?)[\"'\]]*\s*[.!?]?$",
             lambda m: Operation("notizen_verknuepfen",
                                 {"von": m.group(1).strip(), "nach": m.group(2).strip()})),
            # lösche X
            (r"(?:lösch|loesch|entfern)\w*\s+(?:die\s+|das\s+|den\s+)?(?:notiz\s+|datei\s+)?"
             r"[\"'\[]*(.+?)[\"'\]]*\s*[.!?]?$",
             lambda m: Operation("notiz_loeschen", {"name": m.group(1).strip()})),
            # lege Ordner X an / erstelle Ordner X
            (r"(?:erstell|leg|mach)\w*\s+(?:einen\s+)?(?:neuen\s+)?ordner\s+"
             r"[\"'\[]*(.+?)[\"'\]]*\s*(?:an)?\s*[.!?]?$",
             lambda m: Operation("ordner_anlegen", {"pfad": m.group(1).strip()})),
            # ergänze in X ...
            (r"(?:ergänz|ergaenz|erweiter)\w*\s+(?:in\s+|die\s+|das\s+)?"
             r"[\"'\[]*(.+?)[\"'\]]*\s+(?:um|mit)\s+(.+)$",
             lambda m: Operation("notiz_ergaenzen", {"name": m.group(1).strip()},
                                 auftrag=m.group(2).strip())),
            # übersicht für ordner X
            (r"(?:übersicht|uebersicht|index|moc)\s+(?:für|fuer|zu|von)\s+"
             r"[\"'\[]*(.+?)[\"'\]]*\s*[.!?]?$",
             lambda m: Operation("uebersichtsnotiz", {"ordner": m.group(1).strip()})),
        ]

        for regel, bauen in muster:
            treffer = re.search(regel, text, re.IGNORECASE)
            if treffer:
                operationen.append(bauen(treffer))
                break

        if not operationen:
            return None
        return WritePlan(absicht=text[:120], operationen=operationen)

    def _plan_parsen(self, roh: str) -> Optional[WritePlan]:
        daten = _json_laden(roh)
        if not isinstance(daten, dict):
            self.diagnose.append(f"kein JSON-Objekt erhalten: {str(roh).strip()[:160]}")
            return None
        # Manche Modelle antworten mit einer einzelnen Operation statt einer Liste
        if "aktion" in daten and "operationen" not in daten:
            daten = {"absicht": daten.get("absicht", ""), "operationen": [daten]}

        # Achtung: eine leere Liste ist falsy - mit "or" verkettet fiele sie durch
        # und der Fall "Modell lieferte nichts" bliebe unbemerkt.
        rohe_ops = None
        for schluessel in ("operationen", "operations", "schritte", "steps", "actions"):
            if daten.get(schluessel) is not None:
                rohe_ops = daten[schluessel]
                break
        operationen: List[Operation] = []

        if isinstance(rohe_ops, list) and not rohe_ops:
            self.diagnose.append("Modell lieferte eine leere Operationsliste")
        if isinstance(rohe_ops, list):
            for eintrag in rohe_ops:
                op = self._operation_pruefen(eintrag)
                if op:
                    operationen.append(op)
                if len(operationen) >= self.cfg.max_files_per_run:
                    break

        # Rückwärtskompatibel: ältere Planform mit reiner Notizliste
        if not operationen:
            for eintrag in (daten.get("notizen") or daten.get("notes") or []):
                if isinstance(eintrag, str):
                    eintrag = {"titel": eintrag}
                if not isinstance(eintrag, dict):
                    continue
                titel = _titel_saeubern(eintrag.get("titel") or eintrag.get("title")
                                        or eintrag.get("name") or "")
                if not titel:
                    continue
                operationen.append(Operation(
                    aktion="notiz_erstellen",
                    parameter={"titel": titel, "tags": _liste(eintrag.get("tags")),
                               "ordner": str(eintrag.get("ordner") or "")},
                    auftrag=str(eintrag.get("auftrag") or eintrag.get("beschreibung") or ""),
                ))
                if len(operationen) >= self.cfg.max_files_per_run:
                    break

        if not operationen:
            return None
        return WritePlan(
            absicht=str(daten.get("absicht") or daten.get("intent") or "Änderungen an der Vault"),
            ordner=str(daten.get("ordner") or daten.get("folder") or "").strip(),
            operationen=operationen,
        )

    def _operation_pruefen(self, eintrag) -> Optional[Operation]:
        """Nur bekannte Aktionen mit gültigen Parametern kommen durch.

        Verworfene Operationen landen mit Begründung in self.diagnose - sonst
        steht am Ende nur "Kein verwertbarer Plan" und niemand weiß, warum.
        """
        if not isinstance(eintrag, dict):
            self.diagnose.append(f"Eintrag ist kein Objekt: {str(eintrag)[:60]}")
            return None
        aktion = str(eintrag.get("aktion") or eintrag.get("action")
                     or eintrag.get("werkzeug") or eintrag.get("tool")
                     or eintrag.get("operation") or "").strip()
        aktion = AKTIONS_SYNONYME.get(aktion.lower(), aktion)
        if aktion not in TOOL_NAMES or aktion not in PLANBARE_AKTIONEN:
            self.diagnose.append(f"unbekannte Aktion '{aktion or '(leer)'}'")
            return None

        rohe_parameter = eintrag.get("parameter") or eintrag.get("parameters") \
            or eintrag.get("args") or {}
        if not isinstance(rohe_parameter, dict):
            rohe_parameter = {}
        # Manche Modelle legen die Parameter flach neben "aktion"
        for schluessel, wert in eintrag.items():
            if schluessel in ("aktion", "action", "werkzeug", "tool", "operation",
                              "parameter", "parameters", "args", "auftrag"):
                continue
            rohe_parameter.setdefault(schluessel, wert)

        # Synonyme auf die echten Parameternamen abbilden
        erlaubt = set(TOOL_PARAMS.get(aktion, []))
        parameter = {}
        for schluessel, wert in rohe_parameter.items():
            name = schluessel if schluessel in erlaubt else PARAM_SYNONYME.get(
                str(schluessel).lower(), schluessel)
            if name in erlaubt:
                parameter[name] = wert
        if "titel" in parameter:
            parameter["titel"] = _titel_saeubern(parameter["titel"])

        auftrag = str(eintrag.get("auftrag") or eintrag.get("beschreibung") or "").strip()
        inhaltsfeld = INHALT_NOETIG.get(aktion)
        if inhaltsfeld and not auftrag and isinstance(parameter.get(inhaltsfeld), str):
            # Modell hat den Inhalt doch mitgeliefert - als Auftrag verwenden,
            # damit daraus ein vollständiger Text wird
            auftrag = str(parameter.get(inhaltsfeld))
        if inhaltsfeld:
            parameter.pop(inhaltsfeld, None)

        # Pflichtfelder prüfen (das erzeugte Inhaltsfeld ausgenommen)
        from .tools import TOOL_SCHEMAS

        schema = next((t for t in TOOL_SCHEMAS if t["name"] == aktion), None)
        if schema:
            fehlend = [p for p in schema["required"]
                       if p != inhaltsfeld and not parameter.get(p)]
            if fehlend:
                self.diagnose.append(
                    f"{aktion}: Pflichtangabe fehlt ({', '.join(fehlend)}); "
                    f"geliefert wurde: {', '.join(rohe_parameter) or 'nichts'}"
                )
                return None
        return Operation(aktion=aktion, parameter=parameter, auftrag=auftrag)

    # -------------------------------------------------------- Namen auflösen
    def geltungsbereich_pruefen(self, plan: WritePlan, ordner: str,
                                melde: Optional[Callable[[str, str], None]] = None
                                ) -> List[dict]:
        """Nennt der Auftrag einen Ordner, darf keine Operation ihn verlassen.

        Aus dem Protokoll vom 01.09.: Auf „erstelle für alle Code dateien
        Verlinkungen" plante er fünf `notizen_verknuepfen` - und zwar zwischen
        `01_Skript_Deep_Learning`, `10_Transformer_Attention_und_Kontext` und
        `14_RAG_Optimierung`. Alles echte Notizen, keine einzige davon im
        Ordner `_System/Code`. Die Namensauflösung ließ sie durch, weil sie nur
        fragt „gibt es die Notiz?", nicht „gehört sie zum Auftrag?".

        Deshalb diese zweite Schranke. Sie verwirft nicht still, sondern sagt
        bei jedem verworfenen Schritt, warum - sonst wirkt es, als hätte der
        Agent die Hälfte vergessen.
        """
        melde = melde or (lambda art, text: None)
        if not ordner:
            return []
        tools: VaultTools = self.agent.tools
        # WICHTIG: Die Schranke gilt nur für Aktionen, die eine BESTEHENDE
        # Notiz an Ort und Stelle anfassen. Verschieben, Anlegen und
        # Zusammenführen bringen etwas IN den Ordner - das ist bei einem
        # Auftrag "räum den Ordner auf" genau das Gewünschte, kein Verstoß.
        # Ohne diese Unterscheidung verwarf die erste Fassung jedes
        # notiz_verschieben, weil die Notiz naturgemäß noch woanders lag.

        verworfen: List[dict] = []
        gueltige: List[Operation] = []

        for op in plan.operationen:
            if op.aktion not in ORTSGEBUNDEN:
                gueltige.append(op)
                continue
            draussen: List[str] = []
            for schluessel in _geaenderte_parameter(op):
                roh = op.parameter.get(schluessel)
                if not roh:
                    continue
                note = tools.notiz_exakt(str(roh)) or tools.resolve_note(str(roh))
                if note and not _im_geltungsbereich(note.folder, ordner):
                    draussen.append(f"{note.stem} liegt in „{note.folder or 'Wurzel'}“")
            if draussen:
                verworfen.append({
                    "titel": op.titel,
                    "fehler": (f"Der Auftrag gilt für „{ordner}“, aber "
                               f"{'; '.join(draussen)}."),
                    "vorschlaege": [],
                })
                melde("arbeit", f"übergangen – {'; '.join(draussen)}")
                continue
            # Ordnerparameter, die gar nicht gesetzt sind, auf den Auftrag
            # festnageln. Sonst legt er im Wurzelverzeichnis an.
            for schluessel in ("ordner", "ziel_ordner"):
                if schluessel in TOOL_PARAMS.get(op.aktion, ()) \
                        and not op.parameter.get(schluessel):
                    op.parameter[schluessel] = ordner
            gueltige.append(op)

        plan.operationen = gueltige
        return verworfen

    def namen_aufloesen(self, plan: WritePlan,
                        melde: Optional[Callable[[str, str], None]] = None) -> List[dict]:
        """Bringt erfundene Namen mit dem echten Bestand zusammen - VOR der Ausführung.

        Der häufigste Grund für fehlgeschlagene Bearbeitungen: Das Modell schreibt
        einen Notiznamen, den es so nicht gibt ("Erfolgreiche Optimierung von
        nicht-linearen Modellen"), weil es aus dem Kontext einen Titel formuliert
        statt einen Dateinamen zu übernehmen. Früher scheiterte die Operation erst
        bei der Ausführung. Jetzt wird der Name vorher aufgelöst - und wenn das
        misslingt, verschwindet die Operation mit einer brauchbaren Begründung
        statt mit "nicht gefunden".
        """
        melde = melde or (lambda art, text: None)
        tools: VaultTools = self.agent.tools
        probleme: List[dict] = []
        gueltige: List[Operation] = []

        # Was ein FRUEHERER Schritt desselben Plans erst anlegt, gibt es beim
        # Aufloesen noch nicht - aufgeloest wird aber fuer den ganzen Plan auf
        # einmal, VOR der Ausfuehrung. Am 21.09. kostete das den zweiten
        # Schritt eines zweischrittigen Plans:
        #
        #   erstelle ein Werkzeug zum Online-Surfen
        #   -> Plan steht: 2 Schritte (notiz_erstellen, tags_setzen)
        #   -> FEHLER: Notiz "Werkzeug_Online_Surfen" gibt es nicht
        #   -> danach erst: Notiz 'Werkzeug Online Surfen' angelegt
        #
        # Der Fehler wurde gemeldet, BEVOR die Notiz entstand. Deshalb hier
        # ein Gedaechtnis fuer das, was der Plan noch erzeugen wird - dieselbe
        # Loesung wie beim Probelauf der Fertigkeiten (skills.py).
        entsteht_noch = {fold(str(n)) for n in _geplante_namen(plan.operationen)}

        for op in plan.operationen:
            fehler = None
            for schluessel in ("name", "von", "nach"):
                roh = op.parameter.get(schluessel)
                if not roh:
                    continue
                if fold(str(roh)) in entsteht_noch:
                    melde("arbeit", f"„{roh}“ entsteht in einem früheren Schritt")
                    continue
                note = tools.resolve_note(str(roh))
                if note:
                    if fold(str(roh)) != fold(note.stem):
                        melde("arbeit", f"„{roh}“ → [[{note.stem}]]")
                    op.parameter[schluessel] = note.stem
                else:
                    fehler = {
                        "titel": str(roh),
                        "fehler": f"Notiz „{roh}“ gibt es nicht.",
                        "vorschlaege": tools.aehnliche_namen(str(roh)),
                    }
                    break

            if not fehler and isinstance(op.parameter.get("quellen"), (list, tuple)):
                aufgeloest, offen = [], []
                for eintrag in op.parameter["quellen"]:
                    note = tools.resolve_note(str(eintrag))
                    (aufgeloest if note else offen).append(note.stem if note else str(eintrag))

                # Für welche Aktionen sind Quellen der ZWECK der Sache? Nur
                # dort darf ihr Fehlen den Schritt scheitern lassen.
                #
                # Vorher galt die Regel für jede Aktion mit einem Feld
                # 'quellen' - auch für notiz_erstellen, wo es eine freiwillige
                # Zugabe ist. Das Modell hängte bei einer NEUEN Notiz eine
                # leere Liste an, und der Schritt starb mit "Zu wenige der
                # genannten Notizen existieren ()" - leere Klammern, weil es
                # gar keine gab. Der Benutzer wollte eine Notiz aus dem Text
                # anlegen, den er selbst mitgeliefert hatte; gesucht wurde
                # stattdessen nach Vorlagen, die es nie geben sollte.
                QUELLEN_NOETIG = {"notizen_zusammenfuehren", "uebersichtsnotiz"}

                if not op.parameter["quellen"]:
                    # Leere Liste: als hätte niemand etwas angegeben.
                    op.parameter.pop("quellen", None)
                elif op.aktion in QUELLEN_NOETIG and len(aufgeloest) < 2:
                    fehler = {
                        "titel": op.titel,
                        "fehler": (f"{op.aktion} braucht mindestens zwei vorhandene "
                                   f"Notizen. Nicht gefunden: "
                                   f"{', '.join(offen) or '(keine angegeben)'}."),
                        "vorschlaege": tools.aehnliche_namen(" ".join(offen) or op.titel),
                    }
                elif aufgeloest:
                    op.parameter["quellen"] = aufgeloest
                else:
                    # Nichts davon existiert, aber die Aktion braucht es auch
                    # nicht. Weglassen und weiterarbeiten - eine geplante
                    # Zugabe darf den Auftrag nicht zum Scheitern bringen.
                    op.parameter.pop("quellen", None)
                    melde("arbeit", f"Quellen ({', '.join(offen[:3])}) gibt es "
                                    f"nicht – wird ohne sie angelegt")

            # Ordner: vorhandene bevorzugen, neue nur wo es Sinn ergibt
            if not fehler:
                for schluessel in ("ordner", "ziel_ordner", "alt"):
                    roh = op.parameter.get(schluessel)
                    if not roh:
                        continue
                    treffer = self.agent.structure.match_folder(str(roh), threshold=0.55)
                    if treffer:
                        op.parameter[schluessel] = treffer[0][0]
                    elif op.aktion in ("ordner_umbenennen", "uebersichtsnotiz") \
                            and schluessel in ("alt", "ordner"):
                        fehler = {
                            "titel": str(roh),
                            "fehler": f"Ordner „{roh}“ gibt es nicht.",
                            "vorschlaege": self.agent.structure.folder_paths[:6],
                        }
                        break

            if fehler:
                probleme.append(fehler)
                melde("fehler", f"{fehler['fehler']} "
                                f"Vorhanden: {', '.join(fehler['vorschlaege'][:3])}")
            else:
                gueltige.append(op)

        plan.operationen = gueltige
        return probleme

    # ------------------------------------------------------------- Ausführung
    def plan_ausfuehren(
        self,
        plan: WritePlan,
        on_event: Optional[Callable[[str, str], None]] = None,
    ) -> PlanErgebnis:
        melde = on_event or (lambda art, text: None)
        ergebnis = PlanErgebnis(plan=plan)
        tools: VaultTools = self.agent.tools
        tools.lauf_zuruecksetzen()
        neu_erstellt: List[str] = []

        # Erst Namen mit dem echten Bestand abgleichen, dann arbeiten.
        ergebnis.fehlgeschlagen.extend(self.namen_aufloesen(plan, melde))
        # Danach die Ordnerschranke: Ein Auftrag über „alle Code dateien" darf
        # keine Deep-Learning-Notiz anfassen, auch wenn es sie gibt.
        ergebnis.fehlgeschlagen.extend(
            self.geltungsbereich_pruefen(plan, getattr(plan, "geltungsbereich", ""), melde))

        for nummer, op in enumerate(plan.operationen, 1):
            melde("arbeit", f"[{nummer}/{len(plan.operationen)}] {op.beschreibung()}")
            parameter = dict(op.parameter)

            if op.aktion == "notiz_erstellen" and not parameter.get("ordner"):
                parameter["ordner"] = plan.ordner

            inhaltsfeld = INHALT_NOETIG.get(op.aktion)
            if inhaltsfeld:
                try:
                    text = self._inhalt_erzeugen(op, plan)
                except Exception as exc:
                    ergebnis.fehlgeschlagen.append(
                        {"titel": op.titel, "fehler": f"Inhalt nicht erzeugbar: {exc}"})
                    melde("fehler", f"„{op.titel}“: Inhalt nicht erzeugbar – {exc}")
                    continue
                if len(text.strip()) < 60:
                    ergebnis.fehlgeschlagen.append(
                        {"titel": op.titel, "fehler": "Modell lieferte zu wenig Inhalt."})
                    melde("fehler", f"„{op.titel}“: Inhalt zu dünn, nicht gespeichert")
                    continue
                parameter[inhaltsfeld] = text

            treffer = tools.ausfuehren(op.aktion, parameter)
            if treffer.ok:
                ergebnis.erstellt.append({
                    "titel": op.titel, "aktion": op.aktion,
                    "meldung": treffer.meldung,
                    "pfad": treffer.daten.get("pfad") or treffer.daten.get("nachher", ""),
                })
                melde("schreiben", f"✔ {treffer.meldung}")
                if op.aktion == "notiz_erstellen" and treffer.daten.get("pfad"):
                    neu_erstellt.append(op.titel)
            else:
                ergebnis.fehlgeschlagen.append({"titel": op.titel, "fehler": treffer.meldung})
                melde("fehler", f"✖ {op.titel}: {treffer.meldung}")

        # Frisch angelegte Notizen untereinander verknüpfen, damit der Graph hält
        if len(neu_erstellt) > 1:
            for erste, zweite in zip(neu_erstellt, neu_erstellt[1:]):
                tools.notizen_verknuepfen(erste, zweite, beschreibung=plan.absicht[:60],
                                          beidseitig=True)
        return ergebnis

    def _inhalt_erzeugen(self, op: Operation, plan: WritePlan) -> str:
        thema = op.parameter.get("titel") or op.parameter.get("name") or plan.absicht
        abschnitt = op.parameter.get("abschnitt") or op.parameter.get("ueberschrift")
        auftrag = op.auftrag or f"Erkläre '{thema}' vollständig."
        ziel = self.cfg.note_target_words

        if op.aktion == "notiz_erstellen":
            gliederung = (f"Gliedere den Text exakt mit diesen Überschriften:\n"
                          f"{CONTENT_TEMPLATE}\n\n"
                          f"Beginne direkt mit \"## Kurzfassung\" – keine H1-Überschrift.")
        elif abschnitt:
            gliederung = (f"Schreibe NUR den Fließtext für den Abschnitt \"{abschnitt}\" – "
                          f"ohne die Überschrift selbst zu wiederholen.")
        else:
            gliederung = ("Schreibe einen zusammenhängenden Abschnitt in Markdown, "
                          "passend zum Rest der Notiz.")

        # Der Inhalt wurde bisher OHNE jeden Bezug zur Vault erzeugt - reine
        # Modellerinnerung. Bei mehrdeutigen Begriffen ging das schief: aus
        # "Vault" (die Notizsammlung) wurde HashiCorp Vault samt Secret
        # Engines und TLS-Zertifikaten. Deshalb kommt der eigene Bestand
        # zuerst und hat Vorrang vor allem, was das Modell zu wissen glaubt.
        kontext, quellen = self._bestand_zum_thema(thema, plan.absicht)
        bezug = ""
        if kontext:
            bezug = (f"\nDAS STEHT BEREITS IN DER VAULT – baue darauf auf, "
                     f"widersprich ihm nicht:\n{kontext}\n"
                     f"Verweise auf diese Notizen als [[Name]], wo es passt.\n")
        else:
            bezug = ("\nZu diesem Thema steht noch nichts in der Vault. Schreibe nur, "
                     "was du fachlich verantworten kannst, und bleibe beim Wortsinn "
                     "des Themas aus dem Auftrag.\n")

        # Der Auftragstext selbst ist Quellenmaterial - und oft das einzige.
        # Wer eine mehrseitige Spezifikation einfügt und daraus Notizen machen
        # lässt, hat das Material mitgeliefert; es stand nur nirgends im
        # Prompt. Genau daran scheiterte "Tool Spezifikation sps generator"
        # dreimal mit "Inhalt zu dünn": Weder Vault noch Modellwissen kannten
        # das Thema, und der Text, der es beschrieb, wurde nie mitgeschickt.
        vorlage = self._auftragsausschnitt(plan.auftragstext, thema)
        if vorlage:
            bezug += (f"\nDER NUTZER HAT DAZU SELBST VORGABEN GEMACHT. Sie haben "
                      f"VORRANG vor allem, was du sonst über das Thema weißt – "
                      f"arbeite sie aus, statt sie zu wiederholen:\n"
                      f"---\n{vorlage}\n---\n")

        prompt = f"""Schreibe Fachtext auf {self.cfg.language}.

THEMA: {thema}
AUFTRAG: {auftrag}
ÜBERGEORDNETES VORHABEN: {plan.absicht}
{bezug}
{gliederung}

Anforderungen:
- Fachlich korrekt und konkret; keine Platzhalter, keine Meta-Sätze.
- Erkläre WIE etwas funktioniert, nicht nur DASS es existiert.
- Formeln in LaTeX ($...$), Codebeispiele in ```-Blöcken, wo es hilft.
- Ungefähr {ziel} Wörter.
- Schreibe über GENAU dieses Thema. Wenn ein Wort mehrdeutig ist, gilt die
  Bedeutung aus dem Kontext oben, nicht die bekanntere aus deinem Weltwissen."""
        text = self.agent.llm.chat(
            [{"role": "system", "content": self._inhalt_system_prompt()},
             {"role": "user", "content": prompt}],
            temperature=self.cfg.content_temperature,
            num_predict=self.cfg.content_num_predict,
        )
        if quellen:
            self.diagnose.append(f"Inhalt für '{thema}' gestützt auf: "
                                 + ", ".join(quellen[:4]))
        return text

    def _inhalt_system_prompt(self) -> str:
        """Begriffe festnageln, die das Modell sonst falsch auflöst."""
        return (
            f"Du schreibst Fachnotizen auf {self.cfg.language} für das persönliche "
            f"Second Brain von {self.cfg.user_title}. "
            f"WICHTIG: \"Vault\" bezeichnet hier IMMER diese Obsidian-Notizsammlung – "
            f"niemals HashiCorp Vault, keinen Passwort-Safe, keinen Tresor. "
            f"\"Notiz\" ist eine Markdown-Datei darin. "
            f"Antworte ausschließlich mit dem Notizinhalt, ohne Vorrede."
        )

    def _auftragsausschnitt(self, auftragstext: str, thema: str,
                            grenze: int = 3000) -> str:
        """Der Teil des Auftrags, der zu DIESEM Thema gehört.

        Ein 121-zeiliger Auftrag komplett in jeden Notiz-Prompt zu kippen,
        wäre der falsche Weg: Er verdrängt den Kontext aus der Vault und
        beschreibt zu vier Fünfteln andere Notizen. Gesucht wird deshalb die
        Stelle, an der das Thema vorkommt, und darum herum ein Fenster.
        Findet sich nichts, wird der Anfang genommen - dort steht bei einem
        Auftrag fast immer, worum es überhaupt geht.
        """
        text = str(auftragstext or "").strip()
        if not text:
            return ""
        if len(text) <= grenze:
            return text

        from .index import tokens

        begriffe = [w for w in tokens(thema) if len(w) > 3]
        zeilen = text.splitlines()
        beste, bestwert = 0, 0
        for nummer, zeile in enumerate(zeilen):
            klein = zeile.lower()
            wert = sum(1 for w in begriffe if w in klein)
            if wert > bestwert:
                beste, bestwert = nummer, wert
        if not bestwert:
            return text[:grenze]

        # Fenster um die Fundstelle, großzügig nach unten: Eine Überschrift
        # steht vor ihrem Abschnitt, nicht dahinter.
        von = max(0, beste - 10)
        ausschnitt: List[str] = []
        laenge = 0
        for zeile in zeilen[von:]:
            if laenge + len(zeile) > grenze:
                break
            ausschnitt.append(zeile)
            laenge += len(zeile) + 1
        return "\n".join(ausschnitt).strip()

    def _bestand_zum_thema(self, thema: str, absicht: str) -> Tuple[str, List[str]]:
        """Was die Vault zum Thema schon hergibt - als Erdung für den Text."""
        frage = f"{thema} {absicht}".strip()[:200]
        try:
            treffer = self.agent.index.search(frage, k=max(3, self.cfg.top_k // 2))
        except Exception:
            return "", []
        if not treffer:
            return "", []
        teile: List[str] = []
        quellen: List[str] = []
        budget = max(800, self.cfg.prompt_char_budget // 4)
        for hit in treffer:
            try:
                text, kapitel = self.agent.index.expand_hit(hit, frage)
            except Exception:
                continue
            name = str(hit.metadata.get("stem")
                       or Path(str(hit.metadata.get("filename") or "")).stem)
            if not name or not text:
                continue
            kopf = f"### {name}" + (f" – {kapitel}" if kapitel else "")
            teile.append(f"{kopf}\n{text.strip()}")
            quellen.append(name)
            if sum(len(t) for t in teile) >= budget:
                break
        return "\n\n".join(teile)[:budget], quellen

    # ----------------------------------------------------------- Wiederholung
    def fehlende_wiederholen(self, ergebnis: PlanErgebnis,
                             on_event: Optional[Callable[[str, str], None]] = None) -> PlanErgebnis:
        if not ergebnis.plan or not ergebnis.fehlgeschlagen:
            return ergebnis
        offen = {e["titel"] for e in ergebnis.fehlgeschlagen}
        rest = WritePlan(
            absicht=ergebnis.plan.absicht,
            ordner=ergebnis.plan.ordner,
            operationen=[op for op in ergebnis.plan.operationen if op.titel in offen],
            auftragstext=ergebnis.plan.auftragstext,
        )
        neu = self.plan_ausfuehren(rest, on_event)
        return PlanErgebnis(
            plan=ergebnis.plan,
            erstellt=ergebnis.erstellt + neu.erstellt,
            fehlgeschlagen=neu.fehlgeschlagen,
        )


# ------------------------------------------------------------------- Helfer
def _titel_saeubern(roh) -> str:
    titel = str(roh or "").strip()
    titel = re.sub(r"\.md$", "", titel, flags=re.IGNORECASE)
    return titel.replace("_", " ").strip()


def _liste(wert) -> List[str]:
    if isinstance(wert, str):
        return [t.strip() for t in wert.split(",") if t.strip()]
    if isinstance(wert, (list, tuple)):
        return [str(t).strip().lstrip("#") for t in wert if str(t).strip()]
    return []


def _json_laden(roh: str):
    """Robustes JSON-Parsen: Codefences, Vortext und Nachtext tolerieren."""
    if not roh:
        return None
    text = roh.strip()
    for kandidat in _json_kandidaten(text):
        try:
            return json.loads(kandidat)
        except ValueError:
            continue
    return None


def _json_kandidaten(text: str) -> List[str]:
    kandidaten: List[str] = []
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.DOTALL):
        kandidaten.append(match.group(1).strip())
    if text.startswith("{"):
        kandidaten.append(text)
    start = text.find("{")
    ende = text.rfind("}")
    if start >= 0 and ende > start:
        kandidaten.append(text[start : ende + 1])
    return kandidaten
