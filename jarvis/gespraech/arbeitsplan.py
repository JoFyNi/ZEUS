"""Der Arbeitsplan: was zu tun ist, was davon erledigt ist, was als Nächstes kommt.

Das ist der Kasten, der im Zielbild zwischen Wahrnehmung und Handlung steht
und in Zeus bisher fehlte. Ohne ihn lautet die Frage an das Modell nach jedem
Werkzeugaufruf erneut "was tust du jetzt?" - und ein mittelgroßes Modell
antwortet darauf jedes Mal dasselbe.

Der Beleg steht im Protokoll vom 18.09., 13:50 Uhr. Auftrag: zwei Notizen zu
einer zusammenführen. Zeus las beide Notizen ERFOLGREICH und las danach
dieselbe Notiz noch sieben Mal, bis die Schleife abbrach:

    ✔ notiz_lesen (name=Aufgabenliste)            Inhalt von 'Aufgabenliste'
    ✔ notiz_lesen (name=Aufgaben und offene …)    Inhalt von 'Aufgaben und …'
    ✖ notiz_lesen (name=Aufgabenliste)            Identischer Aufruf …
    ✖ notiz_lesen (name=Aufgabenliste)            Identischer Aufruf …
    …

Die Meldung "Identischer Aufruf" ist dabei nicht die Lösung, sondern das
Pflaster an genau der Stelle, an der der Plan stehen müsste. Sie verhindert
den Schaden und bringt die Aufgabe keinen Schritt weiter.

Die Arbeitsteilung folgt der Regel, die in diesem Projekt schon dreimal
gezogen wurde (ENTWICKLUNG.md, Muster 2.3):

    Das Modell ZERLEGT den Auftrag einmal.
    Python FÜHRT BUCH, welcher Schritt dran ist.

Ein Modell, das sich über fünf Runden merken soll, wo es steht, vergisst es.
Eine Liste in Python vergisst es nicht.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

# --------------------------------------------------------------- Zustände
OFFEN = "offen"
LAEUFT = "laeuft"
ERLEDIGT = "erledigt"
GESCHEITERT = "gescheitert"

# Nach so vielen Fehlversuchen gilt ein Schritt als gescheitert. Zwei, weil
# der erste Versuch an einem Tippfehler im Namen scheitern kann, der zweite
# aber schon dieselbe Ursache hätte wie der erste.
MAX_VERSUCHE = 2

# Höchstzahl Schritte. Ein Plan mit zwölf Schritten ist kein Plan mehr,
# sondern ein Programm - und es passt nicht ins Kontextfenster.
MAX_SCHRITTE = 6


@dataclass
class Planschritt:
    nummer: int
    beschreibung: str
    erwartet: str = ""              # erwartetes Werkzeug, falls bekannt
    zustand: str = OFFEN
    ergebnis: str = ""              # Kurzfassung, nicht der volle Text
    versuche: int = 0

    def zeile(self) -> str:
        marke = {OFFEN: "[ ]", LAEUFT: "[>]",
                 ERLEDIGT: "[x]", GESCHEITERT: "[!]"}.get(self.zustand, "[ ]")
        text = f"{marke} {self.nummer}. {self.beschreibung}"
        if self.zustand is ERLEDIGT and self.ergebnis:
            text += f"  → {self.ergebnis[:80]}"
        elif self.zustand == GESCHEITERT and self.ergebnis:
            text += f"  → gescheitert: {self.ergebnis[:60]}"
        return text


@dataclass
class Arbeitsplan:
    auftrag: str
    schritte: List[Planschritt] = field(default_factory=list)
    # Kennung im persistenten Speicher, falls der Plan dort gebucht wurde.
    # Leer heisst: nur im Arbeitsspeicher, etwa weil der Speicher streikte.
    aufgabe_id: str = ""

    # ------------------------------------------------------------- Zustand
    @property
    def aktueller(self) -> Optional[Planschritt]:
        """Der Schritt, der gerade dran ist - der erste, der nicht fertig ist."""
        for s in self.schritte:
            if s.zustand in (OFFEN, LAEUFT):
                return s
        return None

    @property
    def fertig(self) -> bool:
        return self.aktueller is None

    @property
    def erledigt_zahl(self) -> int:
        return sum(1 for s in self.schritte if s.zustand == ERLEDIGT)

    # ----------------------------------------------------------- Fortschritt
    def vermerken(self, werkzeug: str, ok: bool, meldung: str) -> None:
        """Bucht das Ergebnis eines Werkzeugaufrufs auf den laufenden Schritt.

        Bewusst grob: Wir versuchen NICHT, den Aufruf einem beliebigen Schritt
        zuzuordnen. Das Modell arbeitet den Plan der Reihe nach ab, also gilt
        das Ergebnis dem aktuellen Schritt. Eine feinere Zuordnung wäre eine
        weitere Stelle, an der etwas falsch geraten werden kann.
        """
        schritt = self.aktueller
        if schritt is None:
            return
        schritt.zustand = LAEUFT
        schritt.versuche += 1
        if ok:
            schritt.zustand = ERLEDIGT
            schritt.ergebnis = (meldung or "").strip()
        elif schritt.versuche >= MAX_VERSUCHE:
            # Nicht ewig am selben Schritt hängen. Gescheitert wird vermerkt
            # und am Ende gemeldet - verschwiegen wird nichts.
            schritt.zustand = GESCHEITERT
            schritt.ergebnis = (meldung or "").strip()

    def offene_melden(self) -> str:
        """Was am Ende nicht geklappt hat - für die Antwort an den Nutzer."""
        schlecht = [s for s in self.schritte if s.zustand == GESCHEITERT]
        offen = [s for s in self.schritte if s.zustand in (OFFEN, LAEUFT)]
        teile = []
        if schlecht:
            teile.append("Nicht geschafft: "
                         + "; ".join(f"{s.nummer}. {s.beschreibung}" for s in schlecht))
        if offen:
            teile.append("Nicht mehr angefangen: "
                         + "; ".join(f"{s.nummer}. {s.beschreibung}" for s in offen))
        return " ".join(teile)

    # -------------------------------------------------------------- Ausgabe
    def als_anweisung(self) -> str:
        """Der Plan, wie ihn das Modell in jeder Runde zu sehen bekommt.

        Kurz gehalten: Der Plan fährt in JEDER Runde mit und darf deshalb
        nicht selbst zum Platzproblem werden. Deshalb Zustandsmarken statt
        Prosa und gekappte Ergebnisse.
        """
        zeilen = ["ARBEITSPLAN für diesen Auftrag:"]
        zeilen += [s.zeile() for s in self.schritte]
        schritt = self.aktueller
        if schritt is None:
            zeilen.append("")
            zeilen.append("Alle Schritte sind abgearbeitet. Rufe KEIN weiteres "
                          "Werkzeug auf. Fasse jetzt in Prosa zusammen, was du "
                          "getan hast.")
        else:
            zeilen.append("")
            zeilen.append(f"Dein NÄCHSTER Schritt ist Nummer {schritt.nummer}: "
                          f"{schritt.beschreibung}")
            if schritt.erwartet:
                zeilen.append(f"Vorgesehenes Werkzeug: {schritt.erwartet}")
            zeilen.append("Führe NUR diesen einen Schritt aus. Die bereits mit "
                          "[x] markierten Schritte sind erledigt - wiederhole sie "
                          "nicht, ihre Ergebnisse stehen oben im Verlauf.")
        return "\n".join(zeilen)


# ------------------------------------------------------------- Plan erzeugen
_MUSTER = re.compile(r"^\s*(?:\d+[.)]\s*)?(.+?)\s*$")

# Ein Plan lohnt nur für mehrstufige Aufträge. Für "was heißt EOF" wäre er
# reine Verschwendung - ein Modellaufruf und Platz im Prompt für nichts.
_MEHRSTUFIG = re.compile(
    r"\b(zusammenf(ü|ue)g|zusammenfassen|verschmelz|vergleich|"
    r"(ü|ue)berarbeite|erg(ä|ae)nze|aufr(ä|ae)um|sortier|"
    r"beide|alle\s+\w+notizen|nacheinander|danach|anschlie(ß|ss)end)\b",
    re.IGNORECASE)


def braucht_plan(frage: str, schreibabsicht: bool) -> bool:
    """Lohnt sich ein Arbeitsplan für diesen Auftrag?

    Zwei Anzeichen: erkennbare Schreibabsicht plus Wörter, die auf mehrere
    Schritte deuten - oder zwei ausdrücklich genannte Notizen in Anführungs-
    zeichen, denn zwei Gegenstände heißen mindestens zwei Lesevorgänge.
    """
    text = str(frage or "")
    if len(re.findall(r'"[^"]{3,}"', text)) >= 2:
        return True
    return bool(schreibabsicht and _MEHRSTUFIG.search(text))


_ZERLEGE_PROMPT = """Zerlege den folgenden Auftrag in 2 bis {max_schritte} \
ausführbare Schritte.

Auftrag: {auftrag}

Verfügbare Werkzeuge: {werkzeuge}

Regeln:
- Jeder Schritt ist EINE Handlung, kein Zwischenziel.
- Lesen kommt vor Verarbeiten, Verarbeiten vor Schreiben.
- Der letzte Schritt ist das, was der Nutzer am Ende sehen will.
- Keine Schritte für Dinge, die der Auftrag nicht verlangt.

Antworte AUSSCHLIESSLICH mit JSON in dieser Form:
{{"schritte": [{{"beschreibung": "...", "werkzeug": "notiz_lesen"}}, ...]}}
"""


def plan_erzeugen(auftrag: str, llm, werkzeugnamen, melden=None) -> Optional[Arbeitsplan]:
    """Lässt das Modell EINMAL zerlegen und prüft das Ergebnis in Python.

    Schlägt irgendetwas fehl, kommt None zurück und die Schleife arbeitet wie
    bisher. Ein kaputter Plan wäre schlimmer als keiner: Er lenkt das Modell
    auf Schritte, die der Nutzer nie wollte.
    """
    namen = sorted(werkzeugnamen)
    prompt = _ZERLEGE_PROMPT.format(
        auftrag=str(auftrag)[:1200],
        werkzeuge=", ".join(namen[:40]),
        max_schritte=MAX_SCHRITTE,
    )
    # Erst das schnelle Modell, dann das grosse. Der zweite Versuch ist kein
    # Luxus: Am 18.09. stand FAST_MODEL auf einem Modell, das auf dem Server
    # gar nicht lag. Ollama antwortete mit 404, der Plan entfiel lautlos, und
    # die Schleife lief in genau die Wiederholung, die der Plan verhindern
    # soll. Eine Einstellung, die auf ein fehlendes Modell zeigt, darf die
    # Planung nicht abschalten - sie darf sie hoechstens verlangsamen.
    nachricht = [{"role": "user", "content": prompt}]
    roh = None
    for schnell in (True, False):
        try:
            roh = llm.chat(nachricht, temperature=0.0, format_json=True,
                           schnell=schnell)
        except TypeError:
            # Ältere Signatur ohne die Feinsteuerung - dann eben ohne.
            try:
                roh = llm.chat(nachricht)
            except Exception as exc:
                if melden:
                    melden(f"Arbeitsplan nicht erzeugbar: {exc}")
                return None
        except Exception as exc:
            if schnell:
                if melden:
                    melden(f"Schnelles Modell nicht verfuegbar ({exc}) - "
                           f"Plan wird mit dem Hauptmodell erstellt.")
                continue
            if melden:
                melden(f"Arbeitsplan nicht erzeugbar: {exc}")
            return None
        if (roh or "").strip():
            break

    daten = _json_finden(roh or "")
    if not isinstance(daten, dict):
        return None
    rohschritte = daten.get("schritte")
    if not isinstance(rohschritte, list) or not rohschritte:
        return None

    schritte: List[Planschritt] = []
    for eintrag in rohschritte[:MAX_SCHRITTE]:
        if isinstance(eintrag, str):
            beschreibung, werkzeug = eintrag, ""
        elif isinstance(eintrag, dict):
            beschreibung = str(eintrag.get("beschreibung")
                               or eintrag.get("schritt") or "").strip()
            werkzeug = str(eintrag.get("werkzeug") or "").strip()
        else:
            continue
        treffer = _MUSTER.match(beschreibung or "")
        beschreibung = treffer.group(1) if treffer else ""
        if not beschreibung or len(beschreibung) < 4:
            continue
        # Ein erfundener Werkzeugname im Plan führt das Modell in die Irre -
        # lieber ohne Vorschlag als mit einem falschen.
        if werkzeug and werkzeug not in werkzeugnamen:
            werkzeug = ""
        schritte.append(Planschritt(nummer=len(schritte) + 1,
                                    beschreibung=beschreibung[:160],
                                    erwartet=werkzeug))

    # Ein einzelner Schritt ist kein Plan - dafür lohnt der Aufwand nicht.
    if len(schritte) < 2:
        return None
    return Arbeitsplan(auftrag=str(auftrag), schritte=schritte)


def _json_finden(text: str):
    """Das erste vollständige JSON-Objekt aus einer Modellantwort."""
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
