"""Pruefung der Antwort - was sich nachrechnen und nachschlagen laesst, wird es.

Derselbe Grundsatz wie im Tatsachenbericht (bericht.py): Was der Code
gemessen hat, steht ueber dem, was das Modell behauptet. Dort geht es um
Werkzeugergebnisse; hier um vier Dinge, die bisher niemand nachsah:

    Rechnung   "3 x 120 = 380" - ein kleines Modell verrechnet sich, und der
               Fehler zieht sich durch alle folgenden Zahlen.
    Grenze     Der Nutzer sagte "hoechstens 500 Euro", die Antwort kommt
               "insgesamt auf 620 Euro".
    Quelle     [[Notizname]], den es in der Vault nicht gibt - eine erfundene
               Quellenangabe sieht genauso aus wie eine echte.
    Herkunft   Die Notizen gaben zur Frage nichts her (Beleglage, siehe
               kontextwahl.py), und die Antwort tut trotzdem so, als wuesste sie.

Was hier NICHT passiert: Der Modelltext wird nie umgeschrieben. Befunde
werden darunter angehaengt, sichtbar als Pruefung - der Nutzer sieht beides
und kann selbst urteilen.

Zur Wortwahl der Befunde: Sie vermeiden das Wort "nicht". Der Messstand
(evaluation.py) prueft Ehrlichkeit u. a. mit `antwort_enthaelt: nicht`; ein
angehaengter Befund mit "nicht" liesse eine erfundene Antwort dort bestehen,
und die Messung saehe eine Ehrlichkeit, die das Modell nicht hatte.
"""
from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from ..index import tokens
from .texte import VERAENDERNDE_WERKZEUGE
from .triage import Weg


@dataclass
class Befund:
    art: str            # rechnung | grenze | quelle | herkunft
    text: str


# ================================================================ Rechnung
_RECHENZEICHEN = re.compile(r"[+\-−*/×·÷^²³]")
_SCHWANZ = re.compile(r"([\d\s.,+\-−*/×·÷^()%²³]+)$")
_KOPF = re.compile(r"^\s*(-?\d[\d.,]*)")
_UNGEFAEHR = re.compile(r"(≈|~|\bca\.|\betwa\b|\brund\b|\bungef(ae|ä)hr\b|\bcirca\b|"
                        r"\babout\b|\bapprox)", re.IGNORECASE)
_ERLAUBT = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
            ast.Div: operator.truediv, ast.Pow: operator.pow}


def _auswerten(knoten):
    """Nur Zahlen und die vier Grundrechenarten plus Potenz - kein eval()."""
    if isinstance(knoten, ast.Expression):
        return _auswerten(knoten.body)
    if isinstance(knoten, ast.Constant) and isinstance(knoten.value, (int, float)):
        return float(knoten.value)
    if isinstance(knoten, ast.UnaryOp) and isinstance(knoten.op, (ast.USub, ast.UAdd)):
        wert = _auswerten(knoten.operand)
        return -wert if isinstance(knoten.op, ast.USub) else wert
    if isinstance(knoten, ast.BinOp) and type(knoten.op) in _ERLAUBT:
        links, rechts = _auswerten(knoten.left), _auswerten(knoten.right)
        if isinstance(knoten.op, ast.Pow) and (abs(rechts) > 64 or abs(links) > 1e6):
            raise ValueError("Potenz zu gross")
        return _ERLAUBT[type(knoten.op)](links, rechts)
    raise ValueError("nicht erlaubter Ausdruck")


def _zahl_normieren(zahl: str, konvention: str) -> str:
    """'1.234,5' -> '1234.5' (de) bzw. '1,234.5' -> '1234.5' (en)."""
    if konvention == "de":
        zahl = re.sub(r"\.(?=\d{3}(?!\d))", "", zahl)
        return zahl.replace(",", ".")
    zahl = re.sub(r",(?=\d{3}(?!\d))", "", zahl)
    return zahl.replace(",", ".")


def _als_python(ausdruck: str, konvention: str) -> str:
    ausdruck = (ausdruck.replace("×", "*").replace("·", "*").replace("÷", "/")
                .replace("−", "-").replace("^", "**")
                .replace("²", "**2").replace("³", "**3"))
    ausdruck = re.sub(r"(\d[\d.,]*)\s*%", r"(\1/100)", ausdruck)
    return re.sub(r"\d[\d.,]*", lambda m: _zahl_normieren(m.group(0), konvention), ausdruck)


def _werte(ausdruck: str) -> List[float]:
    """Alle Lesarten (deutsche und englische Zahlschreibweise), die aufgehen."""
    werte: List[float] = []
    for konvention in ("de", "en"):
        try:
            wert = _auswerten(ast.parse(_als_python(ausdruck, konvention), mode="eval"))
        except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError):
            continue
        if math.isfinite(wert):
            werte.append(wert)
    return werte


def _nachkommastellen(zahl: str) -> int:
    treffer = re.search(r"[.,](\d{1,2})$", zahl)
    return len(treffer.group(1)) if treffer else 0


def _zeigen(wert: float, deutsch: bool) -> str:
    if abs(wert - round(wert)) < 1e-9:
        return str(int(round(wert)))
    text = f"{wert:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if deutsch else text


def rechnungen_pruefen(text: str) -> List[Befund]:
    """'Ausdruck = Zahl' nachrechnen. Nur diese Form: rechts eine blanke Zahl.

    Ketten wie "3 x 28 = 84 + 2 = 86" sind Rechenwege, keine Gleichungen;
    geprueft wird dort nur das Glied, das auf eine blanke Zahl endet.
    Variablen ("x2 + 3 = 5"), Prozent bei Plus/Minus ("100 + 10 % = 110" ist
    kaufmaennische Kurzschrift) und Code (`==`, `:=`) bleiben aussen vor.
    """
    befunde: List[Befund] = []
    for zeile in (text or "").splitlines():
        if "=" not in zeile or "==" in zeile or ":=" in zeile:
            continue
        glieder = re.split(r"(?<![<>!=])=(?!=)", zeile)
        for links, rechts in zip(glieder, glieder[1:]):
            befund = _gleichung_pruefen(links, rechts)
            if befund:
                befunde.append(befund)
    return befunde[:5]


def _gleichung_pruefen(links: str, rechts: str) -> Optional[Befund]:
    schwanz, kopf = _SCHWANZ.search(links), _KOPF.match(rechts)
    if not schwanz or not kopf:
        return None
    roh = schwanz.group(1)
    ausdruck = roh.strip()
    beginn = schwanz.start(1) + (len(roh) - len(roh.lstrip()))
    if beginn > 0 and links[beginn - 1].isalnum():
        return None                         # Variable oder Bezeichner davor
    ende = kopf.end(1)
    if ende < len(rechts) and (rechts[ende].isalpha() or rechts[ende] in "_(["):
        return None                         # "= 5a", "= 3(x+1)"
    ergebnis = kopf.group(1).rstrip(".,")
    if not _RECHENZEICHEN.search(ausdruck.lstrip("-−")) or not re.search(r"\d", ausdruck):
        return None
    if "%" in ausdruck and re.search(r"[+\-−]", ausdruck):
        return None
    berechnet = _werte(ausdruck)
    behauptet = _werte(ergebnis)
    if not berechnet or not behauptet:
        return None
    ungefaehr = bool(_UNGEFAEHR.search(links[-12:] + rechts[:12]))
    for soll in berechnet:
        for ist in behauptet:
            toleranz = 0.5 * 10 ** -_nachkommastellen(ergebnis) + 1e-9 * max(1.0, abs(soll))
            if ungefaehr:
                toleranz = max(toleranz, 0.01 * abs(soll))
            if abs(soll - ist) <= toleranz:
                return None
    deutsch = "," in ausdruck + ergebnis
    return Befund("rechnung", f"Rechenprüfung: „{ausdruck} = {ergebnis}“ – nachgerechnet "
                              f"ergibt {ausdruck} = {_zeigen(berechnet[0], deutsch)}.")


# ================================================================== Grenze
_EINHEITEN = {
    "%": "%", "€": "eur", "euro": "eur", "eur": "eur", "$": "usd", "usd": "usd",
    "dollar": "usd", "stunden": "h", "stunde": "h", "std": "h", "h": "h",
    "minuten": "min", "minute": "min", "tage": "tag", "tagen": "tag", "tag": "tag",
    "wochen": "woche", "woche": "woche", "monate": "monat", "monaten": "monat",
    "seiten": "seite", "zeichen": "zeichen", "wörter": "wort", "woerter": "wort",
    "gb": "gb", "mb": "mb", "kb": "kb", "tb": "tb", "kg": "kg", "km": "km",
    "stück": "stueck", "stueck": "stueck", "personen": "person", "leute": "person",
    "zeilen": "zeile", "schritte": "schritt", "schritten": "schritt",
}
_EINHEIT = r"(%|€|euro|eur|\$|usd|dollar|stunden|stunde|std|h|minuten|minute|tage[n]?|tag|" \
           r"wochen|woche|monate[n]?|seiten|zeichen|w(?:ö|oe)rter|gb|mb|kb|tb|kg|km|" \
           r"st(?:ü|ue)ck|personen|leute|zeilen|schritte[n]?)"
_OBEN = r"h(?:ö|oe)chstens|maximal|max\.?|nicht\s+mehr\s+als|bis\s+zu|unter|weniger\s+als|" \
        r"at\s+most|no\s+more\s+than|up\s+to"
_UNTEN = r"mindestens|minimal|min\.|nicht\s+weniger\s+als|wenigstens|(?:ü|ue)ber|mehr\s+als|" \
         r"at\s+least"
_GRENZE = re.compile(rf"\b({_OBEN}|{_UNTEN})\s+(\d[\d.,]*)\s*{_EINHEIT}(?!\w)", re.IGNORECASE)
_SUMME = re.compile(
    r"\b(gesamt\w*|insgesamt|summe|total|zusammen|endbetrag|endsumme|in\s+summe|"
    r"kommt?\s+auf|kommen\s+auf|macht)\b[^\n\d]{0,30}?(\d[\d.,]*)\s*" + _EINHEIT + r"(?!\w)",
    re.IGNORECASE)


def _einheit(roh: str) -> str:
    return _EINHEITEN.get(roh.lower().rstrip("."), roh.lower())


def grenzen_pruefen(text: str, vorgaben: Sequence[str]) -> List[Befund]:
    """Gesamtwerte der Antwort gegen Ober- und Untergrenzen aus den Vorgaben.

    Nur bei gleicher Einheit und nur fuer ausdruecklich als Summe genannte
    Werte ("insgesamt", "Gesamtkosten"). Ein Zwischenwert ueber der Grenze
    ist kein Verstoss. Mehrdeutige Zahlen ("1.200" = 1200 oder 1,2) zaehlen
    nur, wenn JEDE Lesart die Grenze verletzt.
    """
    grenzen = []
    for vorgabe in vorgaben:
        for treffer in _GRENZE.finditer(vorgabe):
            art = treffer.group(1).lower()
            oben = bool(re.fullmatch(_OBEN, art, re.IGNORECASE))
            streng = bool(re.match(r"(unter|weniger|(ü|ue)ber|mehr)", art))
            grenzen.append((oben, streng, _werte(treffer.group(2)),
                            _einheit(treffer.group(3)), vorgabe))
    if not grenzen:
        return []
    befunde: List[Befund] = []
    for summe in _SUMME.finditer(text or ""):
        werte, einheit = _werte(summe.group(2)), _einheit(summe.group(3))
        for oben, streng, grenzwerte, grenzeinheit, vorgabe in grenzen:
            if einheit != grenzeinheit or not werte or not grenzwerte:
                continue
            grenze = grenzwerte[0]
            if oben:
                verletzt = all((w >= grenze) if streng else (w > grenze) for w in werte)
            else:
                verletzt = all((w <= grenze) if streng else (w < grenze) for w in werte)
            if verletzt:
                befunde.append(Befund(
                    "grenze", f"Grenzprüfung: Die Antwort nennt „{summe.group(0).strip()}“ – "
                              f"die Vorgabe lautet „{vorgabe}“."))
    return befunde[:3]


# ================================================================== Quelle
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
_ZURUECKHALTUNG = re.compile(
    r"(nicht\s+(in\s+(deinen|ihren|den|meinen|seinen)\s+notizen|belegt|bekannt|sicher|"
    r"feststellbar|ermittelbar|beantwortbar)|kein\w*\s+(angabe|information|beleg|eintrag|"
    r"notiz|hinweis|daten)\w*|wei(ss|ß)\s+ich\s+nicht|kann\s+ich\s+(leider\s+)?nicht\s+"
    r"(sagen|beantworten|feststellen|finden|herausfinden)|unklar|unbekannt|"
    r"eigene\s+einsch(ae|ä)tzung|vermutlich|wahrscheinlich|allgemein\w*\s+wissen|"
    r"i\s+don'?t\s+know|not\s+in\s+your\s+notes|no\s+information|cannot\s+determine|"
    r"unclear)", re.IGNORECASE)
_FRAGEWORT = re.compile(
    r"^\s*(was|wer|wie|wo|wann|warum|wieso|weshalb|welche[rsnm]?|wieviel\w*|woher|wohin|"
    r"womit|wozu|gibt\s+es|hast\s+du|habe\s+ich|hatte\s+ich|kennst\s+du|wei(ss|ß)t\s+du|"
    r"ist|sind|what|who|how|where|when|why|which|is|are|do|does|did)\b", re.IGNORECASE)

HERKUNFT_HINWEIS = ("Herkunft: Zu dieser Frage fand sich in deinen Notizen kein Beleg – "
                    "was oben steht, stammt aus dem Modell und ist ungeprüft.")


def ist_frage(text: str) -> bool:
    text = (text or "").strip()
    return text.endswith("?") or bool(_FRAGEWORT.match(text))


def zurueckhaltend(text: str) -> bool:
    """Kennzeichnet die Antwort selbst, dass sie nichts Belegtes hat?"""
    return bool(_ZURUECKHALTUNG.search(text or ""))


class PruefungTeil:
    """Wird in JarvisAgent eingemischt - siehe agent.py."""

    def _antwort_pruefen(self, text: str, frage: str, weg: Optional[Weg],
                         calls: Sequence = ()) -> str:
        """Befunde unter die Antwort haengen. Wirft nie; ohne Befund unveraendert."""
        if not bool(getattr(self.cfg, "antwort_pruefen", True)) or not (text or "").strip():
            return text
        try:
            befunde = self._befunde_sammeln(text, frage, weg, calls)
        except Exception as exc:            # noqa: BLE001
            try:
                self.tools.log.warnung(f"Antwortprüfung misslang: {type(exc).__name__}: "
                                       f"{exc}", "pruefung")
            except Exception:               # noqa: BLE001
                pass
            return text
        runde = getattr(self, "letzte_runde", None)
        if runde is not None:
            runde.befunde = len(befunde)
        if isinstance(getattr(self, "letzter_kontext", None), dict):
            self.letzter_kontext["pruefung"] = [{"art": b.art, "text": b.text} for b in befunde]
        if not befunde:
            return text
        return (text.rstrip() + "\n\n---\n**Prüfung:**\n"
                + "\n".join(f"- {b.text}" for b in befunde))

    def _befunde_sammeln(self, text: str, frage: str, weg: Optional[Weg],
                         calls: Sequence) -> List[Befund]:
        befunde = rechnungen_pruefen(text)
        arbeit = getattr(self, "_arbeit", None)
        vorgaben = [s for s, _ in (arbeit.vorgaben if arbeit else [])]
        befunde += grenzen_pruefen(text, vorgaben + [frage])
        befunde += self._quellen_pruefen(text, calls)
        if (weg is Weg.VAULT and arbeit is not None and arbeit.beleg == "keine"
                and not getattr(self, "wissen_aus", False)
                and ist_frage(frage) and not zurueckhaltend(text)
                and not self._zitiert_vorhandenes(text)
                and not self._aufrufe_belegen(frage, calls)):
            befunde.append(Befund("herkunft", HERKUNFT_HINWEIS))
        return befunde

    # --------------------------------------------------------------- Quellen
    def _notiz_bekannt(self, name: str) -> bool:
        name = name.strip()
        try:
            struktur = self.structure
        except Exception:                   # noqa: BLE001
            return True                     # ohne Struktur kein Urteil
        for kandidat in {name, name.rsplit("/", 1)[-1], name.replace(" ", "_"),
                         name.replace("_", " ")}:
            if kandidat and struktur.resolve_link(kandidat) is not None:
                return True
        return False

    def _quellen_pruefen(self, text: str, calls: Sequence) -> List[Befund]:
        """[[Namen]] in der Antwort, die es in der Vault nicht gibt.

        Was in dieser Runde angelegt oder umbenannt wurde, kennt die Struktur
        womoeglich noch nicht - das zaehlt als vorhanden.
        """
        frisch = set()
        for call in calls or []:
            if getattr(call, "werkzeug", "") in VERAENDERNDE_WERKZEUGE:
                for wert in (getattr(call, "parameter", {}) or {}).values():
                    if isinstance(wert, str):
                        frisch.add(wert.strip().lower())
        befunde: List[Befund] = []
        gesehen = set()
        for treffer in _WIKILINK.finditer(text or ""):
            name = treffer.group(1).strip()
            if not name or name.lower() in gesehen:
                continue
            gesehen.add(name.lower())
            if name.lower() in frisch or self._notiz_bekannt(name):
                continue
            befunde.append(Befund("quelle", f"Quellenprüfung: Zu [[{name}]] existiert in "
                                            f"der Vault keine Notiz – als Quelle ist die "
                                            f"Angabe unbelegt."))
        return befunde[:5]

    def _zitiert_vorhandenes(self, text: str) -> bool:
        return any(self._notiz_bekannt(t.group(1)) for t in _WIKILINK.finditer(text or ""))

    @staticmethod
    def _aufrufe_belegen(frage: str, calls: Sequence) -> bool:
        """Hat ein erfolgreiches Werkzeug in dieser Runde etwas zur Frage geliefert?"""
        kern = set(tokens(frage))
        for call in calls or []:
            ergebnis = getattr(call, "ergebnis", None)
            if ergebnis is None or not getattr(ergebnis, "ok", False):
                continue
            try:
                inhalt = ergebnis.to_model()
            except Exception:               # noqa: BLE001
                inhalt = str(getattr(ergebnis, "meldung", ""))
            if kern & set(tokens(inhalt)):
                return True
        return False
