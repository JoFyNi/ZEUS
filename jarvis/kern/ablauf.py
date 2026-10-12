"""Ablaeufe: gespeicherte Ketten aus Bedingung, Handlung, Warten und Freigabe.

Was das von einer Fertigkeit (`skills.py`) unterscheidet, ist nicht die
Groesse, sondern die Lebensdauer. Eine Fertigkeit ist eine Kette von
Werkzeugaufrufen, die in einem Zug durchlaeuft; bricht sie ab, faengt sie
beim naechsten Mal von vorn an. Ein Ablauf hat einen ZEIGER, und der liegt
in der Datenbank:

    Fertigkeit   drei Aufrufe, ein Zug, kein Gedaechtnis
    Ablauf       n Schritte, beliebig lange Pausen, Zeiger ueberlebt alles

Damit wird moeglich, was §22 verlangt: Warten, Freigabe, Verzweigung,
Wiederholung - Dinge, die per Definition laenger dauern als ein Zug.

Die Schrittarten, und warum genau diese:

    werkzeug     Der Normalfall. Laeuft ueber das Werkzeugtor, also mit
                 Recht, Budget, Freigabe, Idempotenz und Verifikation.
    ziel         Ein Teilziel an die Agentenschleife abgeben. Fuer alles,
                 was sich nicht als fester Aufruf hinschreiben laesst.
    bedingung    Weiter oder springen, je nach Umgebungswert.
    warten       Bis zu einem Zeitpunkt. Der Ablauf schlaeft wirklich - er
                 belegt keinen Arbeiter.
    freigabe     Halt, bis ein Mensch zustimmt.
    sprung       Zurueck oder vorwaerts; damit entstehen Schleifen.
    ende         Fertig, mit einem Befund.

Zwei Schranken, ohne die eine Ablaufsprache zur Falle wird:

1. **Ein Sprung kostet.** Jeder Ablauflauf hat ein Schrittkonto. Eine
   Schleife ohne Ausgang laeuft damit gegen eine Zahl statt gegen die
   Nacht. §53 nennt "Unbounded Loops" namentlich.

2. **Die Umgebung ist flach und klein.** Werte aus Schritten landen unter
   `{ergebnisN}` und benannten Schluesseln. Keine Ausdruecke, kein
   Rechnen, keine Vergleichssprache mit eigenem Parser - jede davon waere
   die naechste Stelle, an der etwas anders gelesen wird als gemeint.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional

from ..werkzeuge.ergebnis import ToolResult
from .aufgabenspeicher import (ABGEBROCHEN, BEREIT, FERTIG, GESCHEITERT,
                               LAEUFT, PAUSIERT, WARTET_AUF_FREIGABE)
from .werkzeugtor import FreigabeNoetig

# Mehr Schritte als das kann kein Ablauf tun, egal wie er springt.
SCHRITTKONTO = 200

ARTEN = ("werkzeug", "ziel", "bedingung", "warten", "freigabe", "sprung", "ende")


class Ablauffehler(ValueError):
    """Ein Ablauf, der so nicht laufen kann. Faellt beim SPEICHERN auf."""


def pruefen(schritte: list, werkzeugnamen=None) -> List[dict]:
    """Prueft einen Ablauf, bevor er gespeichert wird.

    Hier und nicht zur Laufzeit: Ein Ablauf, der erst beim dritten Schritt
    auffaellt, hat die ersten beiden schon getan. Geprueft wird alles, was
    sich ohne Ausfuehrung pruefen laesst - Arten, Werkzeugnamen, Sprungziele.
    """
    if not isinstance(schritte, list) or not schritte:
        raise Ablauffehler("Ein Ablauf ohne Schritte tut nichts.")
    if len(schritte) > 60:
        raise Ablauffehler(f"{len(schritte)} Schritte sind kein Ablauf mehr, "
                           f"sondern ein Programm. Hoechstens 60.")
    namen = set(werkzeugnamen or ())
    if not namen:
        try:
            from ..tools import TOOL_NAMES
            namen = set(TOOL_NAMES)
        except Exception:
            namen = set()

    geprueft: List[dict] = []
    for nummer, roh in enumerate(schritte):
        if not isinstance(roh, dict):
            raise Ablauffehler(f"Schritt {nummer + 1} ist kein Objekt.")
        schritt = dict(roh)
        art = str(schritt.get("art") or "werkzeug").strip().lower()
        if art not in ARTEN:
            raise Ablauffehler(
                f"Schritt {nummer + 1}: '{art}' ist keine bekannte Schrittart. "
                f"Moeglich: {', '.join(ARTEN)}")
        schritt["art"] = art
        if art == "werkzeug":
            werkzeug = str(schritt.get("werkzeug") or "").strip()
            if not werkzeug:
                raise Ablauffehler(f"Schritt {nummer + 1}: 'werkzeug' fehlt.")
            if namen and werkzeug not in namen:
                raise Ablauffehler(
                    f"Schritt {nummer + 1}: Das Werkzeug '{werkzeug}' gibt es "
                    f"nicht. Ein Ablauf mit einem erfundenen Werkzeug scheitert "
                    f"spaeter mitten im Lauf - deshalb faellt es hier auf.")
        if art == "ziel" and not str(schritt.get("ziel") or "").strip():
            raise Ablauffehler(f"Schritt {nummer + 1}: 'ziel' fehlt.")
        if art == "sprung":
            nach = schritt.get("nach")
            if not isinstance(nach, int) or not (1 <= nach <= len(schritte)):
                raise Ablauffehler(
                    f"Schritt {nummer + 1}: 'nach' muss eine Schrittnummer "
                    f"zwischen 1 und {len(schritte)} sein.")
        if art == "bedingung":
            for feld in ("sonst_nach", "dann_nach"):
                nach = schritt.get(feld)
                if nach is not None and not (isinstance(nach, int)
                                             and 1 <= nach <= len(schritte)):
                    raise Ablauffehler(
                        f"Schritt {nummer + 1}: '{feld}' zeigt auf {nach} - "
                        f"das gibt es nicht.")
        geprueft.append(schritt)
    return geprueft


class Ablaufwerk:
    """Speichert Ablaeufe und fuehrt ihre Laeufe Schritt fuer Schritt aus."""

    def __init__(self, speicher, tor, laeufer=None, *, bus=None,
                 melden: Optional[Callable[[str], None]] = None) -> None:
        self.speicher = speicher
        self.tor = tor
        self.laeufer = laeufer
        self.bus = bus
        self.melden = melden

    # ------------------------------------------------------------ Verwalten
    def sichern(self, name: str, schritte: list, *, beschreibung: str = "",
                ausloeser: Optional[dict] = None, aktiv: bool = True) -> dict:
        geprueft = pruefen(schritte, getattr(
            getattr(self.tor, "tools", None), "TOOL_NAMES", None))
        return self.speicher.ablauf_sichern(
            name, geprueft, beschreibung=beschreibung, ausloeser=ausloeser,
            aktiv=aktiv)

    def loeschen(self, name: str) -> bool:
        return self.speicher.ablauf_loeschen(name)

    def liste(self) -> List[dict]:
        return self.speicher.ablaeufe()

    # --------------------------------------------------------------- Starten
    def starten(self, name: str, umgebung: Optional[dict] = None,
                ziel_id: Optional[str] = None) -> dict:
        ablauf = self.speicher.ablauf(name)
        if not ablauf:
            raise Ablauffehler(f"Es gibt keinen Ablauf namens '{name}'.")
        if not ablauf["aktiv"]:
            raise Ablauffehler(f"Der Ablauf '{name}' ist abgeschaltet.")
        lauf = self.speicher.ablauflauf_anlegen(
            ablauf["id"], ablauf["name"], umgebung=dict(umgebung or {}),
            ziel_id=ziel_id)
        self._ereignis("ablauf.gestartet", ablauf=ablauf["name"], lauf=lauf["id"])
        self._sagen(f"Ablauf '{ablauf['name']}' gestartet.")
        return lauf

    # ---------------------------------------------------------- Ein Schritt
    def schritt(self, lauf_id: str) -> str:
        """Fuehrt GENAU EINEN Schritt aus. Gibt den Zustand des Laufs zurueck.

        Wie beim Aufgabenlaeufer: ein Durchgang, ein Schritt, Stand zurueck
        in die Datenbank. Zwischen zwei Schritten darf der Prozess sterben.
        """
        lauf = self.speicher.ablauflauf(lauf_id)
        if lauf is None:
            return ""
        if lauf["zustand"] in (FERTIG, GESCHEITERT, ABGEBROCHEN):
            return lauf["zustand"]

        ablauf = self.speicher.ablauf(lauf["ablauf_id"])
        if not ablauf:
            return self._beenden(lauf_id, GESCHEITERT,
                                 "Der Ablauf ist zwischenzeitlich verschwunden.")
        schritte = ablauf["schritte"]
        zeiger = int(lauf.get("zeiger") or 0)
        umgebung = dict(lauf.get("umgebung") or {})
        protokoll = list(lauf.get("protokoll") or [])

        if zeiger >= len(schritte):
            return self._beenden(lauf_id, FERTIG, "alle Schritte abgearbeitet",
                                 umgebung=umgebung, protokoll=protokoll)

        # Schrittkonto: Ein Sprung darf nicht ewig kosten (siehe Modulkopf).
        verbraucht = int(umgebung.get("_schritte") or 0) + 1
        if verbraucht > SCHRITTKONTO:
            return self._beenden(
                lauf_id, GESCHEITERT,
                f"Schrittkonto von {SCHRITTKONTO} erschoepft - der Ablauf "
                f"springt vermutlich im Kreis.", umgebung=umgebung,
                protokoll=protokoll)
        umgebung["_schritte"] = verbraucht

        schritt = dict(schritte[zeiger])
        art = schritt.get("art", "werkzeug")
        begonnen = time.time()
        try:
            weiter, notiz = self._ausfuehren(art, schritt, umgebung, zeiger,
                                             lauf, ablauf)
        except FreigabeNoetig as frage:
            self.speicher.ablauflauf_fortschreiben(
                lauf_id, zustand=WARTET_AUF_FREIGABE, umgebung=umgebung,
                protokoll=protokoll + [f"{zeiger + 1}. wartet auf Freigabe: "
                                       f"{frage.frage[:120]}"])
            self._ereignis("ablauf.wartet", ablauf=ablauf["name"], lauf=lauf_id,
                           schritt=zeiger + 1)
            return WARTET_AUF_FREIGABE
        except Exception as exc:
            protokoll.append(f"{zeiger + 1}. {art}: Fehler "
                             f"{type(exc).__name__}: {exc}")
            return self._beenden(lauf_id, GESCHEITERT,
                                 f"Schritt {zeiger + 1} ({art}): {exc}",
                                 umgebung=umgebung, protokoll=protokoll)

        protokoll.append(f"{zeiger + 1}. {art}: {notiz[:160]}")
        self.speicher.lauf_vermerken(
            lauf.get("aufgabe_id") or lauf_id, "ablauf", name=art,
            eingabe=str(schritt.get("werkzeug") or schritt.get("ziel") or "")[:200],
            ausgabe=notiz[:600], dauer=time.time() - begonnen,
            schritt=zeiger + 1)

        if weiter is None:                       # Ende
            return self._beenden(lauf_id, FERTIG, notiz, umgebung=umgebung,
                                 protokoll=protokoll)
        if weiter == "halt":                     # Warten auf einen Zeitpunkt
            self.speicher.ablauflauf_fortschreiben(
                lauf_id, zustand=PAUSIERT, umgebung=umgebung,
                protokoll=protokoll)
            return PAUSIERT

        self.speicher.ablauflauf_fortschreiben(
            lauf_id, zeiger=int(weiter), umgebung=umgebung, protokoll=protokoll,
            zustand=LAEUFT)
        if int(weiter) >= len(schritte):
            return self._beenden(lauf_id, FERTIG, "alle Schritte abgearbeitet",
                                 umgebung=umgebung, protokoll=protokoll)
        return LAEUFT

    def durchlaufen(self, lauf_id: str, max_schritte: int = 100) -> dict:
        """Laeuft, bis er fertig ist, wartet oder das Konto leer ist."""
        for _ in range(max(1, int(max_schritte))):
            zustand = self.schritt(lauf_id)
            if zustand != LAEUFT:
                break
        return self.speicher.ablauflauf(lauf_id) or {}

    def wecken(self, jetzt: Optional[float] = None) -> List[str]:
        """Laeufe, deren Wartezeit abgelaufen ist, wieder einreihen."""
        jetzt = time.time() if jetzt is None else float(jetzt)
        geweckt = []
        for lauf in self.speicher.ablauflaeufe(zustand=PAUSIERT):
            bis = float((lauf.get("umgebung") or {}).get("_warten_bis") or 0)
            if bis and bis > jetzt:
                continue
            umgebung = dict(lauf.get("umgebung") or {})
            umgebung.pop("_warten_bis", None)
            self.speicher.ablauflauf_fortschreiben(lauf["id"], zustand=LAEUFT,
                                                   umgebung=umgebung)
            geweckt.append(lauf["id"])
        return geweckt

    def fortsetzen_nach_freigabe(self) -> List[str]:
        """Laeufe, deren Freigabe inzwischen beantwortet ist, weiterfuehren."""
        weiter = []
        for lauf in self.speicher.ablauflaeufe(zustand=WARTET_AUF_FREIGABE):
            aufgabe_id = lauf.get("aufgabe_id")
            if not aufgabe_id:
                continue
            aufgabe = self.speicher.holen(aufgabe_id)
            if aufgabe is None:
                continue
            if aufgabe.zustand in (ABGEBROCHEN, GESCHEITERT):
                self._beenden(lauf["id"], ABGEBROCHEN,
                              "Freigabe wurde verweigert")
                weiter.append(lauf["id"])
            elif aufgabe.zustand not in (WARTET_AUF_FREIGABE,):
                self.speicher.ablauflauf_fortschreiben(lauf["id"], zustand=LAEUFT)
                weiter.append(lauf["id"])
        return weiter

    # --------------------------------------------------------- Die Schritte
    def _ausfuehren(self, art: str, schritt: dict, umgebung: dict,
                    zeiger: int, lauf: dict, ablauf: dict):
        """Gibt (naechster_zeiger | 'halt' | None, Notiz) zurueck."""
        if art == "werkzeug":
            return self._werkzeug(schritt, umgebung, zeiger, lauf)
        if art == "ziel":
            return self._ziel(schritt, umgebung, zeiger, lauf, ablauf)
        if art == "bedingung":
            return self._bedingung(schritt, umgebung, zeiger)
        if art == "warten":
            bis = time.time() + float(schritt.get("sekunden") or 0)
            umgebung["_warten_bis"] = bis
            # Zeiger schon weiterstellen: Nach dem Wecken soll der WARTEN-
            # Schritt nicht erneut greifen, sonst wartet der Lauf fuer immer.
            self.speicher.ablauflauf_fortschreiben(lauf["id"], zeiger=zeiger + 1)
            return "halt", f"wartet {int(schritt.get('sekunden') or 0)}s"
        if art == "freigabe":
            return self._freigabe(schritt, umgebung, zeiger, lauf)
        if art == "sprung":
            return int(schritt["nach"]) - 1, f"springt zu {schritt['nach']}"
        if art == "ende":
            return None, str(schritt.get("befund") or "Ende")
        return zeiger + 1, f"unbekannte Art '{art}' uebersprungen"

    def _werkzeug(self, schritt: dict, umgebung: dict, zeiger: int, lauf: dict):
        werkzeug = str(schritt.get("werkzeug"))
        parameter = _einsetzen(schritt.get("parameter") or {}, umgebung)
        ergebnis = self.tor.ausfuehren(
            werkzeug, parameter, aufgabe_id=lauf.get("aufgabe_id"),
            unbeaufsichtigt=bool(schritt.get("unbeaufsichtigt", True)),
            schritt=zeiger + 1)
        umgebung[f"ergebnis{zeiger + 1}"] = ergebnis.meldung
        umgebung[f"ok{zeiger + 1}"] = bool(ergebnis.ok)
        merke = str(schritt.get("merke") or "")
        if merke:
            umgebung[merke] = ergebnis.meldung
        if not ergebnis.ok and not schritt.get("darf_scheitern"):
            # Ein Ablauf, der ueber einen Fehlschlag hinwegläuft, meldet am
            # Ende Erfolg fuer etwas, das nicht geschehen ist. Das ist genau
            # der stille Fehlschlag, den dieses Projekt nicht will.
            raise RuntimeError(ergebnis.meldung[:300])
        return zeiger + 1, f"{werkzeug}: {ergebnis.meldung[:120]}"

    def _ziel(self, schritt: dict, umgebung: dict, zeiger: int, lauf: dict,
              ablauf: dict):
        if self.laeufer is None:
            raise RuntimeError("Fuer Teilziele braucht der Ablauf den Laeufer.")
        text = _text_einsetzen(str(schritt.get("ziel")), umgebung)
        ziel, wurzel = self.laeufer.ziel_aufnehmen(
            text, ergebnis=_text_einsetzen(str(schritt.get("ergebnis") or ""),
                                           umgebung),
            quelle=f"ablauf:{ablauf['name']}",
            daten={"ablauf_lauf": lauf["id"], "schritte":
                   schritt.get("schritte") or None})
        umgebung[f"ziel{zeiger + 1}"] = ziel.id
        return zeiger + 1, f"Teilziel angelegt: {text[:100]}"

    def _bedingung(self, schritt: dict, umgebung: dict, zeiger: int):
        wert = umgebung.get(str(schritt.get("feld") or ""))
        trifft = _vergleich(wert, schritt)
        if trifft:
            nach = schritt.get("dann_nach")
            ziel = int(nach) - 1 if isinstance(nach, int) else zeiger + 1
            return ziel, f"Bedingung trifft zu → {ziel + 1}"
        nach = schritt.get("sonst_nach")
        if isinstance(nach, int):
            return int(nach) - 1, f"Bedingung trifft nicht zu → {nach}"
        if schritt.get("sonst_ende"):
            return None, str(schritt.get("befund") or "Bedingung nicht erfuellt")
        return zeiger + 1, "Bedingung trifft nicht zu → weiter"

    def _freigabe(self, schritt: dict, umgebung: dict, zeiger: int, lauf: dict):
        aufgabe_id = lauf.get("aufgabe_id")
        frage = _text_einsetzen(str(schritt.get("frage")
                                    or "Soll der Ablauf weiterlaufen?"), umgebung)
        schluessel = f"ablauf:{lauf['id']}:{zeiger + 1}"
        if not aufgabe_id:
            # Ohne Aufgabe gibt es keine Ablage fuer die Frage. Statt
            # stillschweigend durchzulaufen, wird eine angelegt - sonst
            # waere die Freigabe eine Attrappe.
            aufgabe = self.speicher.anlegen(
                titel=f"Freigabe im Ablauf '{lauf.get('ablauf_name')}'",
                beschreibung=frage, art="ablauf", ablauf_lauf_id=lauf["id"])
            aufgabe_id = aufgabe.id
            # Die frische Aufgabe steht auf NEU, und von dort aus gibt es
            # keinen Weg nach WARTET_AUF_FREIGABE - die Uebergangstabelle
            # laesst ihn nicht zu, und das ist richtig so: Eine Aufgabe, die
            # noch nie lief, kann auch auf nichts warten. Sie wird deshalb
            # erst in den laufenden Zustand gebracht. Ohne diese zwei Zeilen
            # warf `freigabe_erbitten` einen Zustandsfehler, der eine Ebene
            # hoeher als "Schritt gescheitert" ankam - der Ablauf brach ab,
            # statt zu warten.
            self.speicher.wechseln(aufgabe_id, BEREIT, "Ablaufschritt Freigabe")
            self.speicher.wechseln(aufgabe_id, LAEUFT, "Ablauf laeuft")
            self.speicher.ablauflauf_fortschreiben(lauf["id"],
                                                   aufgabe_id=aufgabe_id)
        stand = self.speicher.freigabe_stand(aufgabe_id, schluessel)
        if stand is None:
            self.speicher.freigabe_erbitten(aufgabe_id, frage,
                                            risiko=str(schritt.get("risiko")
                                                       or "mittel"),
                                            moeglichkeiten=["ja", "nein"],
                                            schluessel=schluessel)
            raise FreigabeNoetig("ablauf", frage,
                                 str(schritt.get("risiko") or "mittel"),
                                 schluessel)
        if not stand.get("erteilt"):
            nach = schritt.get("sonst_nach")
            if isinstance(nach, int):
                return int(nach) - 1, "Freigabe verweigert → Ausweichweg"
            return None, "Freigabe verweigert - Ablauf beendet"
        umgebung[f"freigabe{zeiger + 1}"] = stand.get("antwort") or "ja"
        return zeiger + 1, "Freigabe erteilt"

    # --------------------------------------------------------------- Ende
    def _beenden(self, lauf_id: str, zustand: str, befund: str,
                 umgebung: Optional[dict] = None,
                 protokoll: Optional[list] = None) -> str:
        self.speicher.ablauflauf_fortschreiben(
            lauf_id, zustand=zustand, fehler=("" if zustand == FERTIG else befund),
            umgebung=umgebung, protokoll=protokoll)
        lauf = self.speicher.ablauflauf(lauf_id) or {}
        self._ereignis("ablauf.beendet", lauf=lauf_id,
                       ablauf=lauf.get("ablauf_name"), zustand=zustand,
                       befund=befund[:160])
        self._sagen(f"Ablauf '{lauf.get('ablauf_name')}' {zustand}: {befund[:100]}")
        return zustand

    def _ereignis(self, name: str, /, **daten) -> None:
        # Der Schraegstrich ist kein Schmuck: Ohne ihn kollidiert ein
        # Nutzlastfeld namens 'name' mit dem Parameter, und der TypeError
        # faellt an einer Stelle an, die mit dem Ereignis nichts zu tun hat.
        # Genau das ist beim Zeitplan passiert.
        if self.bus is not None:
            try:
                self.bus.melden(name, quelle="ablaufwerk", **daten)
            except Exception:
                pass

    def _sagen(self, text: str) -> None:
        if callable(self.melden):
            try:
                self.melden(text)
            except Exception:
                pass


# ------------------------------------------------------------------- Helfer
_PLATZ = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _text_einsetzen(text: str, umgebung: dict) -> str:
    def ersetzen(treffer):
        wert = umgebung.get(treffer.group(1))
        return str(wert) if wert is not None else treffer.group(0)
    return _PLATZ.sub(ersetzen, str(text or ""))


def _einsetzen(parameter: dict, umgebung: dict) -> dict:
    raus = {}
    for schluessel, wert in (parameter or {}).items():
        raus[schluessel] = (_text_einsetzen(wert, umgebung)
                            if isinstance(wert, str) else wert)
    return raus


def _vergleich(wert: Any, schritt: dict) -> bool:
    """Die paar Vergleiche, die ein Ablauf wirklich braucht."""
    if "ist" in schritt:
        return wert == schritt["ist"]
    if "ist_wahr" in schritt:
        return bool(wert) == bool(schritt["ist_wahr"])
    if "enthaelt" in schritt:
        return str(schritt["enthaelt"]).lower() in str(wert or "").lower()
    if "groesser" in schritt:
        try:
            return float(wert) > float(schritt["groesser"])
        except (TypeError, ValueError):
            return False
    if "kleiner" in schritt:
        try:
            return float(wert) < float(schritt["kleiner"])
        except (TypeError, ValueError):
            return False
    return bool(wert)
