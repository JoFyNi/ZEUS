"""Was Zeus darf - und wie er mehr bekommt, ohne es sich selbst zu geben.

Der Auftrag lautete: "schaffe ihm die Möglichkeit, mehr Rechte zu erhalten"
und "sich selbst zu verbessern". Das Naheliegende wäre, ihm die Rechte einfach
zu geben. Das ist aus zwei Gründen falsch.

Der erste steht in ENTWICKLUNG.md: *Ein Modell, das seine eigene Leistung
bewertet, misst nichts.* Ein System, das sich selbst Rechte zuspricht, weil es
sich für zuverlässig hält, spricht sie sich immer zu.

Der zweite ist praktischer: Die Rechte, um die es geht, sind genau die, deren
Missbrauch man nicht bemerkt. Unbeaufsichtigt schreiben und den eigenen Code
ändern - beides hinterlässt keine Fehlermeldung, wenn es schiefgeht, sondern
eine Vault beziehungsweise ein Programm, das anders ist als gedacht.

Deshalb hier die Trennung, die Josh am 04.09. auch so entschieden hat:

    ZEUS SAMMELT BELEGE.  JOSH ERTEILT RECHTE.

Zeus führt Buch darüber, was in einer Stufe geglückt und was schiefgegangen
ist, und kann daraus einen ANTRAG formulieren - mit Zahlen, nicht mit
Beteuerungen. Erteilt wird per `.env` oder per Befehl, also von Hand.

Die Stufen bauen aufeinander auf. Jede höhere enthält alle tieferen.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# --------------------------------------------------------------- Die Stufen
LESEN = 0
SCHREIBEN = 1
AUTONOM = 2
SELBSTAENDERUNG = 3

RECHTESTUFEN: Dict[int, str] = {
    LESEN: "lesen – suchen, lesen, zeichnen, antworten. Ändert nichts.",
    SCHREIBEN: "schreiben – Notizen anlegen und ändern, wenn der Nutzer es "
               "beauftragt hat. (Standard)",
    AUTONOM: "autonom – ohne Rückfrage arbeiten: Zeitplaner, Autolauf, "
             "Wartung im Hintergrund.",
    SELBSTAENDERUNG: "selbstaenderung – Änderungen am eigenen Quellcode zur "
                     "Freigabe vorlegen und nach Freigabe übernehmen.",
}

# Welche Handlung welche Mindeststufe braucht. Die Tabelle ist die einzige
# Stelle, an der das steht - eine zweite Liste woanders läuft auseinander.
MINDESTSTUFE: Dict[str, int] = {
    "lesen": LESEN,
    "zeichnen": LESEN,
    "suchen": LESEN,
    # Netz und Quellcode sind LESEND, aber getrennt abschaltbar: Das eine
    # holt fremden Text in den Kontext, das andere zeigt den eigenen. Beides
    # gehoert zur Lesestufe, soll sich aber einzeln entziehen lassen, ohne
    # gleich alles Lesen zu sperren.
    "netz_lesen": LESEN,
    "code_lesen": LESEN,
    "vault_schreiben": SCHREIBEN,
    "fertigkeit_anlegen": SCHREIBEN,
    "verhalten_anlegen": SCHREIBEN,
    "unbeaufsichtigt_arbeiten": AUTONOM,
    "zeitplan": AUTONOM,
    "wartung": AUTONOM,
    "code_vorschlagen": SCHREIBEN,          # Vorschlagen ist immer erlaubt
    "code_patch_bauen": SELBSTAENDERUNG,    # Patch in die Quarantäne legen
    "code_uebernehmen": SELBSTAENDERUNG,    # nach Freigabe einspielen
}

# Woran sich eine Stufe bewährt: so viele Läufe, so hohe Quote. Bewusst
# streng - es geht um Rechte, deren Missbrauch niemand sofort bemerkt.
BEWAEHRUNG_LAEUFE = 20
BEWAEHRUNG_QUOTE = 0.9


@dataclass
class Bilanz:
    """Strichliste je Stufe. Zahlen, keine Bewertung."""
    geglueckt: int = 0
    gescheitert: int = 0
    zuletzt: float = 0.0

    @property
    def laeufe(self) -> int:
        return self.geglueckt + self.gescheitert

    @property
    def quote(self) -> float:
        return (self.geglueckt / self.laeufe) if self.laeufe else 0.0


class Rechteverwaltung:
    """Führt die Stufe, prüft Handlungen und sammelt Belege für einen Antrag."""

    def __init__(self, stufe: int = SCHREIBEN, ablage: Optional[Path] = None,
                 bus=None) -> None:
        self.stufe = _stufe_lesen(stufe)
        self.ablage = Path(ablage) if ablage else None
        self.bus = bus
        self.bilanz: Dict[int, Bilanz] = {s: Bilanz() for s in RECHTESTUFEN}
        # Abgelehnte Handlungen - die Liste ist die ehrlichste Begründung für
        # einen Antrag: "Das wollte ich tun und durfte nicht."
        self.verweigert: List[dict] = []
        self._laden()

    # ------------------------------------------------------------- Prüfen
    def darf(self, handlung: str) -> bool:
        return self.stufe >= MINDESTSTUFE.get(str(handlung), SELBSTAENDERUNG)

    def verlangen(self, handlung: str, zweck: str = "") -> bool:
        """Wie `darf`, merkt sich aber die Ablehnung samt Zweck.

        Der Unterschied ist der Punkt: Eine Ablehnung, die niemand erfährt,
        führt dazu, dass dieselbe Sache morgen wieder versucht wird. Eine
        Ablehnung, die aufgeschrieben wird, ist eine Begründung.
        """
        if self.darf(handlung):
            return True
        noetig = MINDESTSTUFE.get(str(handlung), SELBSTAENDERUNG)
        eintrag = {"handlung": handlung, "zweck": zweck[:200],
                   "noetig": noetig, "hatte": self.stufe, "zeit": time.time()}
        self.verweigert.append(eintrag)
        del self.verweigert[:-50]
        if self.bus:
            self.bus.melden("rechte.verweigert", quelle="rechte", **eintrag)
        return False

    def grund(self, handlung: str) -> str:
        """Ein Satz, warum es nicht ging - für die Antwort an den Nutzer."""
        noetig = MINDESTSTUFE.get(str(handlung), SELBSTAENDERUNG)
        return (f"'{handlung}' braucht Rechtestufe {noetig} "
                f"({RECHTESTUFEN[noetig].split(' – ')[0]}), aktuell ist Stufe "
                f"{self.stufe} eingestellt. Zu ändern in der .env über "
                f"RECHTE_STUFE oder mit '/rechte {noetig}'.")

    # ------------------------------------------------------------ Buchführen
    def vermerken(self, geglueckt: bool, stufe: Optional[int] = None) -> None:
        eintrag = self.bilanz.setdefault(_stufe_lesen(stufe if stufe is not None
                                                      else self.stufe), Bilanz())
        if geglueckt:
            eintrag.geglueckt += 1
        else:
            eintrag.gescheitert += 1
        eintrag.zuletzt = time.time()
        self._sichern()

    # -------------------------------------------------------------- Antrag
    def antrag(self) -> Optional[dict]:
        """Ist die nächste Stufe verdient? Gibt die Begründung zurück - oder None.

        Verdient heißt: In der AKTUELLEN Stufe wurde genug und gut genug
        gearbeitet. Nicht: Es gäbe jetzt gerade etwas zu tun, wofür die höhere
        Stufe nötig wäre. Der Unterschied ist der zwischen Bewährung und
        Bedarf, und nur die Bewährung ist ein Argument.
        """
        naechste = self.stufe + 1
        if naechste not in RECHTESTUFEN:
            return None
        eigene = self.bilanz.get(self.stufe, Bilanz())
        if eigene.laeufe < BEWAEHRUNG_LAEUFE or eigene.quote < BEWAEHRUNG_QUOTE:
            return None
        gewuenscht = [v for v in self.verweigert if v["noetig"] == naechste]
        return {
            "von": self.stufe,
            "auf": naechste,
            "was_das_bringt": RECHTESTUFEN[naechste],
            "laeufe": eigene.laeufe,
            "geglueckt": eigene.geglueckt,
            "quote": round(eigene.quote, 3),
            "wollte_aber_durfte_nicht": [v["handlung"] for v in gewuenscht[-5:]],
            "erteilen_mit": f"RECHTE_STUFE={naechste} in der .env, oder '/rechte {naechste}'",
        }

    def antrag_als_text(self) -> str:
        eintrag = self.antrag()
        if not eintrag:
            eigene = self.bilanz.get(self.stufe, Bilanz())
            fehlt = max(0, BEWAEHRUNG_LAEUFE - eigene.laeufe)
            return (f"Kein Antrag. Stufe {self.stufe}, {eigene.laeufe} Läufe, "
                    f"Quote {eigene.quote:.0%}. Für einen Antrag nötig: "
                    f"{BEWAEHRUNG_LAEUFE} Läufe bei mindestens "
                    f"{BEWAEHRUNG_QUOTE:.0%}"
                    + (f" – es fehlen noch {fehlt}." if fehlt else "."))
        zeilen = [
            f"**Antrag auf Stufe {eintrag['auf']}** (von {eintrag['von']})",
            f"Bewährung: {eintrag['geglueckt']} von {eintrag['laeufe']} Läufen "
            f"geglückt ({eintrag['quote']:.0%}).",
            f"Was die Stufe erlaubt: {eintrag['was_das_bringt']}",
        ]
        if eintrag["wollte_aber_durfte_nicht"]:
            zeilen.append("Zuletzt daran gescheitert: "
                          + ", ".join(eintrag["wollte_aber_durfte_nicht"]))
        zeilen.append(f"Erteilen mit: {eintrag['erteilen_mit']}")
        zeilen.append("Ich kann mir die Stufe nicht selbst geben – das ist Absicht.")
        return "\n".join(zeilen)

    def setzen(self, stufe: int, durch: str = "nutzer") -> int:
        """Stufe ändern. Nur von außen aufzurufen, nie vom Agenten selbst."""
        alt = self.stufe
        self.stufe = _stufe_lesen(stufe)
        self._sichern()
        if self.bus:
            self.bus.melden("rechte.geaendert", quelle="rechte", von=alt,
                            auf=self.stufe, durch=durch)
        return self.stufe

    # ------------------------------------------------------------- Ablage
    def _sichern(self) -> None:
        if not self.ablage:
            return
        try:
            self.ablage.parent.mkdir(parents=True, exist_ok=True)
            self.ablage.write_text(json.dumps({
                "stufe": self.stufe,
                "bilanz": {str(k): {"geglueckt": v.geglueckt,
                                    "gescheitert": v.gescheitert,
                                    "zuletzt": v.zuletzt}
                           for k, v in self.bilanz.items()},
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass       # Buchführung ist Beiwerk; ihr Ausfall bricht nichts ab.

    def _laden(self) -> None:
        if not self.ablage or not self.ablage.exists():
            return
        try:
            daten = json.loads(self.ablage.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        # Die STUFE wird bewusst NICHT aus der Datei übernommen - sie kommt
        # aus der .env. Sonst könnte ein Schreibzugriff auf diese Datei die
        # Rechte erhöhen, und das wäre genau die Hintertür, die es nicht geben
        # soll. Übernommen wird nur die Bilanz.
        for schluessel, wert in (daten.get("bilanz") or {}).items():
            try:
                self.bilanz[int(schluessel)] = Bilanz(
                    geglueckt=int(wert.get("geglueckt", 0)),
                    gescheitert=int(wert.get("gescheitert", 0)),
                    zuletzt=float(wert.get("zuletzt", 0.0)))
            except (TypeError, ValueError):
                continue

    def uebersicht(self) -> str:
        zeilen = [f"Rechtestufe {self.stufe}: {RECHTESTUFEN[self.stufe]}", ""]
        for stufe, text in RECHTESTUFEN.items():
            marke = "▶" if stufe == self.stufe else ("✔" if stufe < self.stufe else "·")
            eintrag = self.bilanz.get(stufe, Bilanz())
            bilanz = (f"  ({eintrag.geglueckt}/{eintrag.laeufe} geglückt)"
                      if eintrag.laeufe else "")
            zeilen.append(f"{marke} {stufe} {text}{bilanz}")
        return "\n".join(zeilen)


def _stufe_lesen(wert) -> int:
    try:
        stufe = int(wert)
    except (TypeError, ValueError):
        return SCHREIBEN
    return max(LESEN, min(SELBSTAENDERUNG, stufe))
