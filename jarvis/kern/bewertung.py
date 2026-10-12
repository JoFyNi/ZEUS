"""Der TaskAssessor: Was fuer eine Aufgabe ist das ueberhaupt?

Die Frage, die bisher niemand gestellt hat. Der Weg durch das System sah so
aus:

    Auftrag -> Fachagent (Stichwort?) -> sonst Modell -> Schritte

Damit entschied ein Stichwortvergleich ueber alles. "Wie viele Dateien
liegen in meiner Inbox?" und "Vergleiche zehn Notizen, finde Widersprueche
und aktualisiere die Uebersicht" liefen durch dieselbe Tuer und bekamen
dieselbe Behandlung. Die erste Frage braucht ein Werkzeug und eine halbe
Sekunde; die zweite braucht Abruf, Synthese und das grosse Modell.

Dieses Modul beantwortet die Frage VOR der Zerlegung, und zwar so billig wie
moeglich. Es loest die Aufgabe NICHT - es beschreibt sie.

    Auftrag -> BEFUND -> (Router) -> Zerlegung -> Ausfuehrung

Die zwei Stufen, und warum in dieser Reihenfolge
------------------------------------------------
1. REGELN. Wortmuster, Zahlen, Anfuehrungszeichen, bekannte Werkzeug- und
   Ordnernamen. Kostet Mikrosekunden, ist bei jedem Durchlauf gleich und
   braucht keinen Server. Das deckt den grossen Teil ab: Wer "wie viele
   Dateien" schreibt, will zaehlen, und dafuer gibt es ein Werkzeug.

2. MODELL, aber nur bei echter Unklarheit. Wenn die Regeln keine Absicht
   erkennen oder sich widersprechen, darf ein KLEINES Modell nachbessern.
   Sein Vorschlag wird in Python geprueft und darf nur die Felder aendern,
   bei denen die Regeln unsicher waren.

Die zweite Stufe laeuft also nicht "zur Sicherheit". Ein Modellaufruf fuer
eine Frage, die ein regulaerer Ausdruck beantwortet, ist genau der Aufwand,
den dieses Projekt vermeiden will - und er kostet hier gemessene 10 Sekunden
(qwen3.8:27b, 21.09.).

Was der Befund NICHT tut
------------------------
Er entscheidet nicht ueber Rechte, Budgets oder Freigaben. `risiko` ist ein
Hinweis fuer das Routing, keine Erlaubnis. Die Schranken sitzen weiterhin im
`Werkzeugtor`, und das bleibt so: Eine Einschaetzung, die aus Text abgeleitet
wird, darf nie ueber eine Loeschung entscheiden.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

# --------------------------------------------------------------- Absichten
# Absichtlich wenige und grob. Eine feine Taxonomie klingt besser und trifft
# schlechter: Je mehr Klassen, desto haeufiger sitzt eine Aufgabe zwischen
# zweien, und dann entscheidet der Zufall der Wortwahl.
ZAEHLEN = "zaehlen"        # deterministisch beantwortbar
ABRUF = "abruf"            # finden, suchen, nachschlagen
LESEN = "lesen"            # eine bestimmte Notiz ansehen
FRAGE = "frage"            # Wissensfrage ans Modell
SCHREIBEN = "schreiben"    # Notiz anlegen oder ergaenzen
UMBAUEN = "umbauen"        # verschieben, loeschen, zusammenfuehren
ANALYSE = "analyse"        # vergleichen, Widersprueche, Synthese
WARTUNG = "wartung"        # Verweise, Index, Aufraeumen
META = "meta"              # Frage ueber Zeus selbst
GESPRAECH = "gespraech"    # Gruss, Dank, Smalltalk

ABSICHTEN = (ZAEHLEN, ABRUF, LESEN, FRAGE, SCHREIBEN, UMBAUEN, ANALYSE,
             WARTUNG, META, GESPRAECH)

# --------------------------------------------------------------- Faehigkeiten
VAULT_LESEN = "vault_read"
VAULT_SCHREIBEN = "vault_write"
ABRUF_F = "retrieval"
DENKEN = "reasoning"
PRUEFUNG = "verification"
NETZ = "netz"
CODE = "code_read"
ZERSTOEREND = "destruktiv"

FAEHIGKEITEN = (VAULT_LESEN, VAULT_SCHREIBEN, ABRUF_F, DENKEN, PRUEFUNG,
                NETZ, CODE, ZERSTOEREND)


@dataclass
class Befund:
    """Was fuer eine Aufgabe das ist - gemessen an ihrem Text, nicht geloest.

    Die Zahlen liegen zwischen 0 und 1 und sind SCHAETZUNGEN. Sie taugen zum
    Vergleichen und zum Routing, nicht als Aussage ueber die Welt. Wer sie
    fuer Messwerte haelt, baut eine Kette von Entscheidungen auf Vermutungen.
    """
    auftrag: str
    absicht: str = FRAGE
    ziel: str = ""
    faehigkeiten: List[str] = field(default_factory=list)

    komplexitaet: float = 0.3
    mehrdeutigkeit: float = 0.2
    unsicherheit: float = 0.3
    risiko: float = 0.0

    denken_noetig: bool = False
    abruf_noetig: bool = False
    schreiben_noetig: bool = False
    werkzeug_noetig: bool = False
    netz_noetig: bool = False

    schritte: int = 1
    pruefaufwand: float = 0.3
    kontextgroesse: int = 0
    eilig: bool = False

    quelle: str = "regeln"                       # regeln | regeln+modell
    begruendung: List[str] = field(default_factory=list)

    # ------------------------------------------------------------ Signatur
    @property
    def signatur(self) -> str:
        """Die Aufgabensignatur aus §21 - fuer Routing, Metriken und Lernen.

        Grob gestuft und nicht aus den Rohzahlen zusammengesetzt: Eine
        Signatur mit `komplexitaet=0.7241` waere bei jeder Aufgabe anders
        und damit als Schluessel wertlos.
        """
        schreibt = "write" if self.schreiben_noetig else "read_only"
        risiko = _stufe(self.risiko, ("low_risk", "medium_risk", "high_risk"))
        denken = _stufe(self.komplexitaet,
                        ("low_reasoning", "medium_reasoning", "high_reasoning"))
        kontext = _stufe(min(1.0, self.kontextgroesse / 12000),
                         ("low_context", "medium_context", "high_context"))
        return ".".join([self.absicht, schreibt, risiko, denken, kontext])

    @property
    def deterministisch_loesbar(self) -> bool:
        """Reicht ein Werkzeug? Die erste Frage aus §37.

        Drei Bedingungen, und alle drei muessen stimmen: Es ist eine Absicht,
        fuer die es ein Werkzeug gibt; es ist nichts zu denken; und der Text
        ist eindeutig genug, dass die Parameter feststehen.
        """
        return (self.absicht in (ZAEHLEN, LESEN, ABRUF, WARTUNG)
                and not self.denken_noetig
                and self.mehrdeutigkeit < 0.4)

    def als_dict(self) -> dict:
        return {
            "auftrag": self.auftrag[:200], "absicht": self.absicht,
            "ziel": self.ziel, "faehigkeiten": list(self.faehigkeiten),
            "komplexitaet": round(self.komplexitaet, 2),
            "mehrdeutigkeit": round(self.mehrdeutigkeit, 2),
            "unsicherheit": round(self.unsicherheit, 2),
            "risiko": round(self.risiko, 2),
            "denken_noetig": self.denken_noetig,
            "abruf_noetig": self.abruf_noetig,
            "schreiben_noetig": self.schreiben_noetig,
            "werkzeug_noetig": self.werkzeug_noetig,
            "schritte": self.schritte,
            "pruefaufwand": round(self.pruefaufwand, 2),
            "kontextgroesse": self.kontextgroesse,
            "eilig": self.eilig, "signatur": self.signatur,
            "quelle": self.quelle, "begruendung": list(self.begruendung),
        }

    def bericht(self) -> str:
        zeilen = [
            f"{self.absicht}  ({self.quelle})",
            f"  Signatur       {self.signatur}",
            f"  Komplexitaet   {_balken(self.komplexitaet)} {self.komplexitaet:.2f}",
            f"  Mehrdeutigkeit {_balken(self.mehrdeutigkeit)} {self.mehrdeutigkeit:.2f}",
            f"  Unsicherheit   {_balken(self.unsicherheit)} {self.unsicherheit:.2f}",
            f"  Risiko         {_balken(self.risiko)} {self.risiko:.2f}",
            f"  Faehigkeiten   {', '.join(self.faehigkeiten) or '(keine)'}",
            f"  Schritte       ~{self.schritte}",
            f"  Werkzeug reicht: {'ja' if self.deterministisch_loesbar else 'nein'}",
        ]
        if self.begruendung:
            zeilen.append("  Weil:")
            zeilen += [f"    - {g}" for g in self.begruendung]
        return "\n".join(zeilen)


def _stufe(wert: float, namen) -> str:
    return namen[0] if wert < 0.34 else (namen[1] if wert < 0.67 else namen[2])


def _balken(wert: float, breite: int = 10) -> str:
    voll = max(0, min(breite, round(wert * breite)))
    return "█" * voll + "·" * (breite - voll)


# =========================================================== die Wortmuster
# Jedes Muster steht hier, weil es einen echten Auftrag trifft - nicht, weil
# es die Liste vollstaendiger aussehen laesst. Ein Muster, das nie greift,
# ist totes Gewicht; eines, das zu breit greift, ist schlimmer als keines.

_ZAEHLEN = re.compile(
    r"\b(wie\s*viele?|anzahl|z(ae|ä)hl|wieviel)", re.IGNORECASE)
_ABRUF = re.compile(
    r"\b(find|such|liste|welche\s+notiz|wo\s+(ist|liegt|steht)|"
    r"gibt\s+es\s+(eine|ein)\b|hast\s+du\s+(etwas|was)|"
    r"zeig(e)?\s+mir\s+(die\s+)?(ordner|struktur|(ue|ü)bersicht))",
    re.IGNORECASE)
# LESEN ist eng gefasst: Es geht um den INHALT einer bestimmten Notiz.
# "Zeig mir die Ordnerstruktur" ist ein Abruf, kein Lesen - und mit einem
# weiten "zeig mir ..." waere jede Anzeige hier gelandet.
_LESEN = re.compile(
    r"\b(lies|lese\b|inhalt\s+von|was\s+steht\s+in|"
    r"zeig(e)?\s+mir\s+(die\s+)?notiz)", re.IGNORECASE)
# Trennbare Verben sind im Deutschen der Normalfall, und ihre Vorsilbe
# steht am SATZENDE: "lege ... an", "fuege ... hinzu", "trage ... ein",
# "halte ... fest". Ein Muster, das die Vorsilbe direkt hinter dem Verb
# sucht, verfehlt genau die Auftraege, die ein Mensch wirklich tippt.
#
# Gemessen: "Lege nacheinander zwei Merkzettel an" galt damit als Frage,
# nicht als Schreibauftrag - und der daraus abgeleitete Vertrag verbot
# ausgerechnet `notiz_erstellen`.
_SCHREIBEN = re.compile(
    r"\b(erstell|schreib|notier|dokumentier|erg(ae|ä)nz|protokollier|"
    r"leg(e|t|st)?\s+.{0,40}?\ban\b|leg(e)?\s+(eine|ein)\b|"
    r"f(ue|ü)g(e|t|st)?\s+.{0,40}?\bhinzu\b|"
    r"trag(e|t|st)?\s+.{0,40}?\bein\b|"
    r"halt(e|et|)\s+.{0,40}?\bfest\b|"
    r"vermerk|verzeichne|schreib\s+.{0,30}?\bauf\b)", re.IGNORECASE)
_UMBAUEN = re.compile(
    r"\b(l(oe|ö)sch|entfern|verschieb|umbenenn|benenn\w*\s+.{0,20}?um\b|"
    r"zusammenf(ue|ü)hr|f(ue|ü)hr\w*\s+.{0,40}?zusammen\b|"
    r"vereinig|verschmelz|sortier|r(ae|ä)um|in\s+den\s+papierkorb|"
    r"wirf\s+.{0,30}?papierkorb)", re.IGNORECASE)
# 'Unterschied' steht bewusst NICHT hier. "Was ist der Unterschied zwischen
# Transformer und LSTM?" ist eine Wissensfrage ans Modell, keine Analyse des
# eigenen Bestands - das Wort allein traegt die Unterscheidung nicht.
_ANALYSE = re.compile(
    r"\b(vergleich|analysier|widerspr(ue|ü)ch|gegen(ue|ü)ber|"
    r"bewert|beurteil|synthes|"
    r"was\s+f(ae|ä)llt\s+.{0,40}?auf\b)", re.IGNORECASE)
_WARTUNG = re.compile(
    r"\b(wartung|verweis|links?\b|index\b|reindex|aufr(ae|ä)um|"
    r"tote\s+link|kaputte\s+link)", re.IGNORECASE)
_META = re.compile(
    r"\b(was\s+kannst\s+du|wer\s+bist\s+du|welche\s+werkzeuge|"
    r"wie\s+funktionierst|deine\s+(rechte|f(ae|ä)higkeiten|module)|"
    r"warum\s+(hast|konntest)\s+du)", re.IGNORECASE)
_GESPRAECH = re.compile(
    r"^\s*(hallo|hi|moin|guten\s+(morgen|tag|abend)|danke|dankesch(oe|ö)n|"
    r"servus|tsch(ue|ü)ss|ok(ay)?|alles\s+klar)\b[\s.!?]*$", re.IGNORECASE)
_NETZ = re.compile(
    r"\b(im\s+(netz|internet)|online|webseite|website|https?://|"
    r"aktuelle?\s+(nachricht|meldung|kurs|preis))", re.IGNORECASE)
_CODE = re.compile(
    r"\b(quellcode|quelltext|\.py\b|funktion\s+\w+|modul\s+\w+|"
    r"implementier|refactor)", re.IGNORECASE)

# Woerter, die MEHRERE Schritte ankuendigen. Sie erhoehen die Komplexitaet,
# entscheiden aber nicht allein - "und" kommt in jedem zweiten Satz vor.
_MEHRSTUFIG = re.compile(
    r"\b(dann|danach|anschlie(ss|ß)end|zuerst|"
    r"und\s+(dann|danach|aktualisier|erg(ae|ä)nz)|"
    r"alle\s+\w+|jede[srn]?\s+\w+|nacheinander|mehrere)", re.IGNORECASE)

# Woerter, die auf UNSCHAERFE deuten: Der Nutzer weiss selbst noch nicht
# genau, was herauskommen soll.
_UNSCHARF = re.compile(
    r"\b(irgendwie|vielleicht|k(oe|ö)nntest|schau\s+mal|sieh\s+dir|"
    r"was\s+meinst|ich\s+wei(ss|ß)\s+nicht|so\s+etwas|ungef(ae|ä)hr|"
    r"k(ue|ü)mmer\w*\s+dich|mach\s+(mal|was|etwas))", re.IGNORECASE)

_EILIG = re.compile(r"\b(sofort|dringend|schnell|jetzt\s+gleich|eilt)\b",
                    re.IGNORECASE)

# Zerstoerende Woerter, getrennt von _UMBAUEN: Verschieben ist umbauen,
# Loeschen ist umbauen UND riskant.
#
# KEIN abschliessendes \b nach einem Wortstamm. Das war der Fehler in der
# ersten Fassung und er hat JEDES Stammmuster stillgelegt: "l(oe|ö)sch\b"
# trifft "Loesche" nicht, weil nach "loesch" ein "e" folgt und dort keine
# Wortgrenze ist. Das Bewertungsset hat es sofort gezeigt - ein Loeschauftrag
# wurde als harmlose Frage gelesen, Risiko 0.05.
_ZERSTOEREND = re.compile(
    r"\b(l(oe|ö)sch|entfern|(ue|ü)berschreib|verwerf|wegwerf|"
    r"zusammenf(ue|ü)hr|f(ue|ü)hr\w*\s+.{0,40}?zusammen\b|"
    r"papierkorb)", re.IGNORECASE)


class Aufgabenbewerter:
    """Beurteilt eine Aufgabe, bevor sie geplant wird.

    `llm` ist optional. Ohne Modell arbeitet der Bewerter rein regelbasiert
    und liefert trotzdem immer einen Befund - das ist Absicht: Ein System,
    das ohne Sprachmodell nicht einmal mehr einschaetzen kann, was vor ihm
    liegt, hat das Modell zum Grundsystem gemacht.
    """

    # Ab dieser Mehrdeutigkeit lohnt sich die Nachfrage beim Modell. Darunter
    # waere sie Aufwand fuer nichts.
    SCHWELLE_NACHFRAGEN = 0.45

    def __init__(self, llm=None, cfg=None, werkzeugnamen=None,
                 melden=None, modell_erlaubt: bool = True) -> None:
        self.llm = llm
        self.cfg = cfg
        self.melden = melden
        self.modell_erlaubt = bool(modell_erlaubt)
        self.werkzeugnamen = set(werkzeugnamen or ())
        if not self.werkzeugnamen:
            try:
                from ..tools import TOOL_NAMES
                self.werkzeugnamen = set(TOOL_NAMES)
            except Exception:
                self.werkzeugnamen = set()
        # Zahlen fuers Protokoll - ohne sie laesst sich spaeter nicht sagen,
        # wie oft die zweite Stufe ueberhaupt gebraucht wurde.
        self.gezaehlt = {"befunde": 0, "nur_regeln": 0, "mit_modell": 0,
                         "modell_verworfen": 0}

    # ------------------------------------------------------------- Einstieg
    def bewerten(self, auftrag: str, *, kontextgroesse: int = 0,
                 nachfragen: Optional[bool] = None) -> Befund:
        """Der Befund zu einem Auftrag. Wirft nie."""
        auftrag = str(auftrag or "").strip()
        befund = self._regelbefund(auftrag, kontextgroesse)
        self.gezaehlt["befunde"] += 1

        darf_fragen = self.modell_erlaubt if nachfragen is None else nachfragen
        if darf_fragen and self.llm is not None and self._lohnt_nachfrage(befund):
            verfeinert = self._verfeinern(befund)
            if verfeinert is not None:
                self.gezaehlt["mit_modell"] += 1
                return verfeinert
            self.gezaehlt["modell_verworfen"] += 1

        self.gezaehlt["nur_regeln"] += 1
        return befund

    def _lohnt_nachfrage(self, befund: Befund) -> bool:
        """Nur bei echter Unklarheit - siehe Modulkopf, Stufe 2.

        Ausdruecklich NICHT bei hoher Komplexitaet: Eine Aufgabe kann
        aufwendig und trotzdem voellig eindeutig sein ("verschiebe alle
        Notizen aus der Inbox nach 01_Wissen"). Aufwand ist kein Grund zu
        fragen; Unklarheit ist einer.
        """
        if befund.deterministisch_loesbar:
            return False            # ein Werkzeug reicht - nicht fragen
        return (befund.mehrdeutigkeit >= self.SCHWELLE_NACHFRAGEN
                or befund.absicht == FRAGE and befund.komplexitaet >= 0.5)

    # --------------------------------------------------------- Stufe 1: Regeln
    def _regelbefund(self, auftrag: str, kontextgroesse: int) -> Befund:
        b = Befund(auftrag=auftrag, kontextgroesse=int(kontextgroesse or 0))
        text = auftrag
        gruende: List[str] = []

        if not text:
            b.absicht = GESPRAECH
            b.mehrdeutigkeit = 1.0
            b.begruendung = ["leerer Auftrag"]
            return b

        # --- Absicht. Reihenfolge ist Rangfolge: Die spezifischsten Muster
        #     zuerst, damit "loesche die Notiz X" nicht als Schreibauftrag
        #     durchgeht, nur weil "Notiz" darin steht.
        if _GESPRAECH.match(text):
            b.absicht = GESPRAECH; gruende.append("Gruss oder Dank, kein Auftrag")
        elif _META.search(text):
            b.absicht = META; gruende.append("Frage ueber Zeus selbst")
        elif _ZAEHLEN.search(text):
            b.absicht = ZAEHLEN; gruende.append("fragt nach einer Anzahl")
        elif _UMBAUEN.search(text):
            b.absicht = UMBAUEN; gruende.append("verlangt eine Umstellung am Bestand")
        elif _ANALYSE.search(text):
            b.absicht = ANALYSE; gruende.append("verlangt Vergleich oder Synthese")
        elif _WARTUNG.search(text):
            b.absicht = WARTUNG; gruende.append("betrifft Verweise, Index oder Ordnung")
        elif _SCHREIBEN.search(text):
            b.absicht = SCHREIBEN; gruende.append("verlangt etwas Geschriebenes")
        elif _LESEN.search(text):
            b.absicht = LESEN; gruende.append("verlangt den Inhalt einer Notiz")
        elif _ABRUF.search(text):
            b.absicht = ABRUF; gruende.append("sucht etwas im Bestand")
        else:
            b.absicht = FRAGE; gruende.append("keine Handlung erkennbar - Wissensfrage")

        # --- Faehigkeiten
        faehig = set()
        if b.absicht in (ZAEHLEN, ABRUF, LESEN, ANALYSE, WARTUNG, UMBAUEN):
            faehig.add(VAULT_LESEN)
        if b.absicht in (ABRUF, ANALYSE, FRAGE):
            faehig.add(ABRUF_F); b.abruf_noetig = True
        if b.absicht in (SCHREIBEN, UMBAUEN, WARTUNG):
            faehig.add(VAULT_SCHREIBEN); faehig.add(PRUEFUNG)
            b.schreiben_noetig = True
        if b.absicht in (ANALYSE, FRAGE, SCHREIBEN):
            faehig.add(DENKEN); b.denken_noetig = True
        if _NETZ.search(text):
            faehig.add(NETZ); b.netz_noetig = True
            gruende.append("verweist auf das Internet")
        if _CODE.search(text):
            faehig.add(CODE); gruende.append("betrifft Quellcode")
        if _ZERSTOEREND.search(text):
            faehig.add(ZERSTOEREND)
        b.werkzeug_noetig = bool(faehig & {VAULT_LESEN, VAULT_SCHREIBEN,
                                           ABRUF_F, NETZ, CODE})
        b.faehigkeiten = sorted(faehig)

        # --- Risiko. NUR ein Hinweis fuers Routing; die Schranke sitzt im
        #     Werkzeugtor und bleibt dort (siehe Modulkopf).
        if _ZERSTOEREND.search(text):
            b.risiko = 0.75; gruende.append("nennt eine zerstoerende Handlung")
        elif b.schreiben_noetig:
            b.risiko = 0.3
        else:
            b.risiko = 0.05
        if re.search(r"\balle\b|\bs(ae|ä)mtliche\b|\bganze[sn]?\b", text, re.I) \
                and b.schreiben_noetig:
            b.risiko = min(1.0, b.risiko + 0.2)
            gruende.append("betrifft viele Notizen auf einmal")

        # --- Komplexitaet
        komplex = {GESPRAECH: 0.02, META: 0.1, ZAEHLEN: 0.1, LESEN: 0.15,
                   ABRUF: 0.25, WARTUNG: 0.35, SCHREIBEN: 0.45,
                   UMBAUEN: 0.5, FRAGE: 0.4, ANALYSE: 0.75}[b.absicht]
        if _MEHRSTUFIG.search(text):
            komplex += 0.2; gruende.append("mehrere Schritte angekuendigt")
        anfuehrungen = len(re.findall(r'"[^"]{3,}"', text))
        if anfuehrungen >= 2:
            komplex += 0.1
            gruende.append(f"{anfuehrungen} Gegenstaende ausdruecklich genannt")
        if len(text) > 220:
            komplex += 0.1; gruende.append("langer Auftrag")
        b.komplexitaet = max(0.0, min(1.0, komplex))

        # --- Mehrdeutigkeit
        unklar = 0.12
        if _UNSCHARF.search(text):
            unklar += 0.45; gruende.append("unscharf formuliert")
        if b.absicht == FRAGE and b.komplexitaet >= 0.5:
            unklar += 0.15
        if len(text.split()) <= 2 and b.absicht not in (GESPRAECH, META):
            unklar += 0.3; gruende.append("sehr kurzer Auftrag")
        # Ein ausdruecklich genannter Gegenstand macht vieles eindeutig.
        if anfuehrungen or self._nennt_werkzeug(text):
            unklar = max(0.0, unklar - 0.2)
        b.mehrdeutigkeit = max(0.0, min(1.0, unklar))

        # --- Unsicherheit: Wie wahrscheinlich ist es, dass der erste Weg
        #     nicht genuegt? Steigt mit Komplexitaet UND Unklarheit.
        b.unsicherheit = max(0.0, min(
            1.0, 0.15 + 0.45 * b.komplexitaet + 0.4 * b.mehrdeutigkeit))

        # --- Schritte und Pruefaufwand
        grund = {GESPRAECH: 1, META: 1, ZAEHLEN: 1, LESEN: 1, ABRUF: 1,
                 FRAGE: 2, WARTUNG: 2, SCHREIBEN: 2, UMBAUEN: 3, ANALYSE: 4}
        b.schritte = grund[b.absicht] + (2 if _MEHRSTUFIG.search(text) else 0) \
            + max(0, anfuehrungen - 1)
        # Was sich mechanisch pruefen laesst, ist billig zu pruefen. Eine
        # Zusammenfassung ist es nicht - dafuer gibt es keinen Dateitest.
        b.pruefaufwand = {ZAEHLEN: 0.1, LESEN: 0.1, ABRUF: 0.2, WARTUNG: 0.3,
                          SCHREIBEN: 0.35, UMBAUEN: 0.4, META: 0.2,
                          GESPRAECH: 0.05, FRAGE: 0.7, ANALYSE: 0.85}[b.absicht]

        b.eilig = bool(_EILIG.search(text))
        b.ziel = self._ziel_formulieren(b, text)
        b.begruendung = gruende
        return b

    def _nennt_werkzeug(self, text: str) -> bool:
        klein = text.lower()
        return any(name in klein for name in self.werkzeugnamen)

    @staticmethod
    def _ziel_formulieren(b: Befund, text: str) -> str:
        """Ein Satz, was erreicht sein soll. Bewusst aus dem Auftrag, nicht
        erfunden: Ein umformuliertes Ziel ist eine Interpretation, und die
        gehoert nicht in die erste, billige Stufe."""
        kurz = re.sub(r"\s+", " ", text).strip()
        return kurz[:180]

    # -------------------------------------------------- Stufe 2: kleines Modell
    def _verfeinern(self, b: Befund) -> Optional[Befund]:
        """Laesst ein KLEINES Modell nachbessern - und prueft dessen Antwort.

        Uebernommen wird nur, was die Regeln ohnehin nicht wussten. Das
        Modell darf die Absicht schaerfen und das Ziel formulieren; es darf
        NICHT das Risiko senken. Sonst waere ein freundlich formulierter
        Loeschauftrag plotzlich harmlos - und das Routing traefe eine
        Entscheidung, die der Text nicht hergibt.
        """
        prompt = _PROMPT.format(
            auftrag=b.auftrag[:900],
            absichten=", ".join(ABSICHTEN),
            faehigkeiten=", ".join(FAEHIGKEITEN),
            regelurteil=json.dumps(
                {"absicht": b.absicht, "komplexitaet": round(b.komplexitaet, 2),
                 "mehrdeutigkeit": round(b.mehrdeutigkeit, 2)},
                ensure_ascii=False))
        try:
            roh = self.llm.chat([{"role": "user", "content": prompt}],
                                temperature=0.0, format_json=True, schnell=True)
        except TypeError:
            try:
                roh = self.llm.chat([{"role": "user", "content": prompt}])
            except Exception as exc:
                self._sagen(f"Bewertung ohne Modell: {exc}")
                return None
        except Exception as exc:
            self._sagen(f"Bewertung ohne Modell: {exc}")
            return None

        daten = _json_finden(roh or "")
        if not isinstance(daten, dict):
            return None

        absicht = str(daten.get("absicht") or "").strip().lower()
        if absicht not in ABSICHTEN:
            # Ein erfundener Klassenname ist kein Ergebnis. Lieber beim
            # Regelurteil bleiben als eine Absicht uebernehmen, die es im
            # Routing gar nicht gibt.
            self._sagen(f"Modell schlug Absicht '{absicht}' vor - die gibt es nicht.")
            return None

        neu = Befund(**{**b.__dict__})
        neu.begruendung = list(b.begruendung)
        neu.absicht = absicht
        neu.quelle = "regeln+modell"
        neu.begruendung.append(f"Modell schaerfte die Absicht: {b.absicht} -> {absicht}")

        ziel = str(daten.get("ziel") or "").strip()
        if 5 < len(ziel) <= 300:
            neu.ziel = ziel

        # Zahlen nur nach OBEN uebernehmen. Ein Modell, das eine Aufgabe fuer
        # harmloser haelt, als die Regeln sie lesen, bekommt hier kein Gehoer -
        # die Regeln haben das Wort "loeschen" gesehen, das Modell hat es
        # bewertet.
        for feld in ("komplexitaet", "mehrdeutigkeit", "unsicherheit", "risiko"):
            wert = daten.get(feld)
            if isinstance(wert, (int, float)) and 0.0 <= float(wert) <= 1.0:
                if float(wert) > getattr(neu, feld):
                    setattr(neu, feld, float(wert))
                    neu.begruendung.append(f"Modell hob {feld} auf {float(wert):.2f}")

        zusatz = daten.get("faehigkeiten")
        if isinstance(zusatz, list):
            erlaubt = [f for f in zusatz if f in FAEHIGKEITEN]
            neu.faehigkeiten = sorted(set(neu.faehigkeiten) | set(erlaubt))

        # Abgeleitete Merkmale neu ziehen, damit sie zur neuen Absicht passen.
        neu.denken_noetig = neu.denken_noetig or DENKEN in neu.faehigkeiten
        neu.abruf_noetig = neu.abruf_noetig or ABRUF_F in neu.faehigkeiten
        neu.schreiben_noetig = neu.schreiben_noetig or VAULT_SCHREIBEN in neu.faehigkeiten
        return neu

    def _sagen(self, text: str) -> None:
        if callable(self.melden):
            try:
                self.melden(text)
            except Exception:
                pass

    # -------------------------------------------------------------- Auskunft
    def uebersicht(self) -> str:
        z = self.gezaehlt
        if not z["befunde"]:
            return "Noch keine Aufgabe bewertet."
        anteil = z["mit_modell"] / z["befunde"]
        return (f"{z['befunde']} Bewertung(en): {z['nur_regeln']} allein aus "
                f"Regeln, {z['mit_modell']} mit Modell ({anteil:.0%}), "
                f"{z['modell_verworfen']} Modellantwort(en) verworfen.")


_PROMPT = """Du bist eine Einordnungsstufe, KEIN Loeser. Beschreibe die
folgende Aufgabe, aber bearbeite sie NICHT.

AUFGABE: {auftrag}

Eine regelbasierte Vorstufe kam zu diesem Urteil:
{regelurteil}

Moegliche Absichten (waehle GENAU EINE): {absichten}
Moegliche Faehigkeiten: {faehigkeiten}

Antworte AUSSCHLIESSLICH mit JSON:
{{"absicht": "...", "ziel": "ein Satz, was am Ende erreicht sein soll",
"faehigkeiten": ["..."], "komplexitaet": 0.0, "mehrdeutigkeit": 0.0,
"unsicherheit": 0.0, "risiko": 0.0}}

Die Zahlen liegen zwischen 0 und 1. Rate nicht ins Blaue: Wenn der Auftrag
klar ist, gehoert dort eine kleine Zahl hin."""


def _json_finden(text: str):
    """Das erste vollstaendige JSON-Objekt aus einer Modellantwort."""
    text = str(text or "")
    try:
        return json.loads(text)
    except ValueError:
        pass
    start = text.find("{")
    while start >= 0:
        tiefe = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                tiefe += 1
            elif text[i] == "}":
                tiefe -= 1
                if tiefe == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None
