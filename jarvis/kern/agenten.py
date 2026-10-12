"""Fachagenten - Zerlegungswissen, kein zweiter Kern.

Der Auftrag verlangt Spezialagenten (§27) und verbietet im selben Atemzug
das Naheliegende: *"Agenten duerfen nicht ihre eigenen konkurrierenden
Task-Systeme besitzen. Der Kernel bleibt autoritativ."*

Das ist die richtige Grenze, und sie ist auch die schwierige. Die uebliche
Bauart - jeder Agent bekommt eine `run()`-Methode, eine eigene Schleife und
ein eigenes Gedaechtnis - fuehrt genau dorthin, wovon dieses Projekt schon
einmal zurueckgebaut hat: fuenf Stellen, die dasselbe anders tun, und
niemand weiss mehr, welche gerade laeuft.

Deshalb ist ein Agent hier KEIN Ausfuehrer. Er ist ein Zerleger mit
Fachwissen. Er beantwortet zwei Fragen und sonst nichts:

    passt(ziel)      Wie gut passe ich? 0.0 bis 1.0.
    zerlegen(ziel)   Welche Schritte waeren das, in meiner Sprache?

Alles Weitere - Reihenfolge, Zustand, Werkzeugaufruf, Recht, Budget,
Freigabe, Verifikation, Neuplanung, Persistenz - macht der Kern. Ein Agent
kann nichts davon umgehen, weil er nichts davon anfasst.

Der Gewinn ist trotzdem echt und kein Etikett: Ein Ziel wie "recherchiere X
und lege eine Notiz an" wird ohne Modellaufruf in vier richtige Schritte
zerlegt, weil der Rechercheagent weiss, dass Suchen vor Lesen und Lesen vor
Schreiben kommt. Vorher haette das Modell diese Kette jedes Mal neu erraten -
mit jedes Mal anderem Ergebnis.
"""
from __future__ import annotations

import re
from typing import List, Optional, Sequence

from .plantypen import Planschritt, Zerlegung


class Fachagent:
    """Gemeinsame Schnittstelle. Wer sie nicht erfuellt, ist kein Agent."""

    name = "agent"
    beschreibung = ""
    # Werkzeuge, die dieser Agent benutzt. Fehlt eines davon, meldet er
    # sich nicht zustaendig - ein Agent, der Werkzeuge braucht, die es nicht
    # gibt, erzeugt einen Plan, der sicher scheitert.
    braucht: Sequence[str] = ()
    _woerter: Sequence[str] = ()
    # Woerter, die diesen Agenten AUSSCHLIESSEN. Sie sind wichtiger als die
    # Trefferwoerter und werden zuerst geprueft.
    #
    # Der Anlass steht im CLI-Lauf vom 21.09.: Der Auftrag lautete "Loesche
    # die Notiz Sandkisten". Der Schreibagent sah das Wort "Notiz", meldete
    # sich zustaendig und legte eine NEUE Notiz mit dem Titel "Loesche die
    # Notiz Sandkisten" an. Die Schritte liefen durch, die Verifikation war
    # zufrieden, das Ziel galt als erreicht - und geloescht wurde nichts.
    #
    # Ein Gegenstand im Auftrag ("Notiz") sagt nichts darueber, WAS mit ihm
    # geschehen soll. Die Handlung tut es, und wer die falsche Handlung
    # ausfuehrt, ist nicht knapp daneben, sondern falsch.
    _verboten: Sequence[str] = ()

    def passt(self, ziel, vorhandene: Sequence[str] = ()) -> float:
        """Wie gut passt dieser Agent? 0.0 heisst: gar nicht."""
        if self.braucht and vorhandene and not set(self.braucht) <= set(vorhandene):
            return 0.0
        text = _text(ziel)
        if any(wort in text for wort in self._verboten):
            return 0.0
        treffer = sum(1 for wort in self._woerter if wort in text)
        if not treffer:
            return 0.0
        # Gedeckelt, damit ein Ziel mit vielen Stichwoertern nicht jeden
        # anderen Agenten verdraengt, nur weil es geschwaetzig ist.
        return min(1.0, 0.4 + 0.2 * treffer)

    def zerlegen(self, ziel) -> List[Planschritt]:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"


# --------------------------------------------------------------- Recherche
class RechercheAgent(Fachagent):
    """Etwas herausfinden und das Ergebnis festhalten.

    Die Reihenfolge Suchen -> Lesen -> Schreiben steht hier fest und nicht
    im Prompt. Sie ist der haeufigste Fall und der, den ein mittelgrosses
    Modell am zuverlaessigsten falsch zerlegt: Es schreibt zuerst und sucht
    danach die Belege.
    """

    name = "recherche"
    beschreibung = "Suchen, lesen, zusammentragen, als Notiz ablegen"
    braucht = ("notizen_suchen", "notiz_erstellen")
    _woerter = ("recherch", "finde heraus", "suche", "zusammentragen",
                "ueberblick", "überblick", "uebersicht", "übersicht",
                "was weiss", "was weiß", "informier", "analyse", "vergleich")
    _verboten = ("loesch", "lösch", "entferne", "entfern", "weg damit", "papierkorb", "verschieb", "umbenenn", "zusammenf", "vereinig", "verschmelz")

    def zerlegen(self, ziel) -> List[Planschritt]:
        thema = _thema(ziel)
        titel = _titel(ziel, thema)
        return [
            Planschritt(beschreibung=f"Bestand zu '{thema}' durchsuchen",
                        werkzeug="notizen_suchen",
                        parameter={"frage": thema, "anzahl": 8},
                        erwartung="Treffer aus der Vault liegen vor"),
            Planschritt(beschreibung=f"Ergebnisse zu '{thema}' sichten und ordnen",
                        erwartung="Die Kernaussagen stehen fest",
                        braucht=[1]),
            Planschritt(beschreibung=f"Uebersicht '{titel}' anlegen",
                        werkzeug="notiz_erstellen",
                        parameter={"titel": titel, "inhalt": "{ergebnis2}"},
                        erwartung="Die Notiz liegt in der Vault",
                        braucht=[2]),
        ]


# ------------------------------------------------------------------ Schreiben
class SchreibAgent(Fachagent):
    """Eine Notiz anlegen oder ergaenzen - mit vorheriger Pruefung auf Dubletten."""

    name = "schreiben"
    beschreibung = "Notizen anlegen und ergaenzen, ohne Dubletten zu bauen"
    braucht = ("notizen_suchen", "notiz_erstellen")
    _woerter = ("notiz", "schreib", "lege an", "erstelle", "dokumentier",
                "halte fest", "protokoll", "bericht")
    _verboten = ("loesch", "lösch", "entferne", "entfern", "weg damit", "papierkorb", "verschieb", "umbenenn", "zusammenf", "vereinig", "verschmelz")

    def zerlegen(self, ziel) -> List[Planschritt]:
        thema = _thema(ziel)
        titel = _titel(ziel, thema)
        return [
            # Der erste Schritt ist der, den man weglaesst - und dann liegen
            # zwei Notizen zum selben Thema in der Vault. Genau dieser Fall
            # steht mehrfach im Protokoll.
            Planschritt(beschreibung=f"Pruefen, ob es zu '{thema}' schon etwas gibt",
                        werkzeug="notizen_suchen",
                        parameter={"frage": thema, "anzahl": 5},
                        erwartung="Bekannt, ob eine Notiz schon existiert"),
            # `inhalt` MUSS mit: Das Werkzeug fordert es als Pflichtfeld, und
            # ein Plan, dem es fehlt, scheitert zuverlaessig mit "fehlt:
            # inhalt". Genau so im Abnahmetest aufgetreten. Der Auftrag des
            # Nutzers ist der ehrlichste Inhalt, den dieser Agent hat - er
            # erfindet nichts dazu.
            Planschritt(beschreibung=f"Notiz '{titel}' schreiben",
                        werkzeug="notiz_erstellen",
                        parameter={"titel": titel, "inhalt": "{auftrag}"},
                        erwartung="Die Datei liegt auf der Platte",
                        braucht=[1]),
        ]


# ------------------------------------------------------------------- Ordnung
class OrdnungsAgent(Fachagent):
    """Aufraeumen: Verweise pruefen, reparieren, verknuepfen."""

    name = "ordnung"
    beschreibung = "Vault pruefen, tote Verweise reparieren, verknuepfen"
    braucht = ("links_pruefen", "vault_wartung")
    _woerter = ("aufraeum", "ordnung", "wartung", "verweis", "link",
                "verknuepf", "verknüpf", "sortier", "struktur")

    def zerlegen(self, ziel) -> List[Planschritt]:
        return [
            Planschritt(beschreibung="Verweise pruefen",
                        werkzeug="links_pruefen",
                        parameter={"reparieren": False},
                        erwartung="Liste der toten Verweise liegt vor"),
            Planschritt(beschreibung="Vault-Wartung mit Reparatur laufen lassen",
                        werkzeug="vault_wartung",
                        parameter={"reparieren": True},
                        erwartung="Index stimmt, Verweise repariert",
                        braucht=[1]),
        ]


# ----------------------------------------------------------------- Netzsuche
class NetzAgent(Fachagent):
    """Etwas im Internet nachsehen - mit ausdruecklichem Bezug auf Quellen."""

    name = "netz"
    beschreibung = "Im Netz nachschlagen und das Gefundene belegen"
    braucht = ("web_suchen", "notiz_erstellen")
    _woerter = ("im netz", "im internet", "online", "webseite", "website",
                "aktuelle", "nachrichten", "recherchiere online")

    def zerlegen(self, ziel) -> List[Planschritt]:
        thema = _thema(ziel)
        return [
            Planschritt(beschreibung=f"Im Netz nach '{thema}' suchen",
                        werkzeug="web_suchen",
                        parameter={"frage": thema, "anzahl": 5},
                        erwartung="Treffer mit Quellenangabe liegen vor"),
            Planschritt(beschreibung="Das Gefundene mit Quellen festhalten",
                        werkzeug="notiz_erstellen",
                        parameter={"titel": _titel(ziel, thema),
                                   "inhalt": "{ergebnis1}"},
                        erwartung="Notiz mit Quellenangaben existiert",
                        braucht=[1]),
        ]


# -------------------------------------------------------------------- Code
class CodeAgent(Fachagent):
    """Quellcode ansehen und das Gelesene festhalten. Aendert nichts."""

    name = "code"
    beschreibung = "Quellcode lesen, durchsuchen und beschreiben"
    braucht = ("code_suchen", "quellcode_lesen")
    _woerter = ("code", "quelltext", "quellcode", "funktion", "modul",
                "klasse", "implementier", "datei ")

    def zerlegen(self, ziel) -> List[Planschritt]:
        thema = _thema(ziel)
        return [
            Planschritt(beschreibung=f"Im Quellcode nach '{thema}' suchen",
                        werkzeug="code_suchen",
                        parameter={"begriff": thema, "anzahl": 10},
                        erwartung="Fundstellen liegen vor"),
            Planschritt(beschreibung="Die Fundstellen einordnen",
                        erwartung="Klar, was der Code tut", braucht=[1]),
        ]


# ---------------------------------------------------------------- Entfernen
class EntfernenAgent(Fachagent):
    """Eine Notiz aus dem Weg raeumen - erst finden, dann loeschen.

    Der Suchschritt davor ist kein Beiwerk: `notiz_loeschen` braucht einen
    Namen, der wirklich existiert. Ein geratener Name trifft entweder nichts
    (dann ist nichts geschehen) oder das Falsche (dann ist etwas weg, das
    bleiben sollte). Der zweite Fall ist der teure.

    Das Loeschen selbst traegt Risiko 'hoch' (werkzeugrechte.py). Unbe-
    aufsichtigt wartet es deshalb auf eine Freigabe - dieser Agent plant es,
    er entscheidet es nicht.
    """

    name = "entfernen"
    beschreibung = "Notizen suchen und nach Rueckfrage entfernen"
    braucht = ("notizen_suchen", "notiz_loeschen")
    _woerter = ("loesch", "lösch", "entferne", "entfern", "weg damit",
                "in den papierkorb", "raeume weg", "räume weg")

    def zerlegen(self, ziel) -> List[Planschritt]:
        thema = _gegenstand(ziel)
        return [
            Planschritt(beschreibung=f"'{thema}' im Bestand suchen",
                        werkzeug="notizen_suchen",
                        parameter={"frage": thema, "anzahl": 5},
                        erwartung="Die gemeinte Notiz ist gefunden"),
            Planschritt(beschreibung=f"'{thema}' entfernen",
                        werkzeug="notiz_loeschen",
                        parameter={"name": thema,
                                   "grund": "vom Nutzer beauftragt"},
                        erwartung="Die Notiz liegt im Papierkorb, nicht mehr "
                                  "an ihrem Platz",
                        braucht=[1]),
        ]


class Agentenregister:
    """Waehlt den passenden Agenten - und sagt, warum.

    Die Begruendung ist kein Beiwerk. Ein Planer, der stumm einen Agenten
    waehlt, ist bei der ersten falschen Wahl nicht mehr zu untersuchen -
    und dann raet man, statt nachzusehen.
    """

    def __init__(self, agenten: Optional[Sequence[Fachagent]] = None,
                 werkzeugnamen: Optional[Sequence[str]] = None) -> None:
        self.agenten: List[Fachagent] = list(agenten) if agenten is not None \
            else [RechercheAgent(), NetzAgent(), EntfernenAgent(),
                  OrdnungsAgent(), CodeAgent(), SchreibAgent()]
        self.werkzeuge = set(werkzeugnamen or ())
        self.wahlen: List[dict] = []

    def anmelden(self, agent: Fachagent) -> Fachagent:
        self.agenten.append(agent)
        return agent

    def waehlen(self, ziel):
        """Der beste Agent und seine Punktzahl - oder (None, 0.0)."""
        bewertet = [(a, a.passt(ziel, self.werkzeuge)) for a in self.agenten]
        bewertet = [(a, p) for a, p in bewertet if p > 0]
        if not bewertet:
            return None, 0.0
        bewertet.sort(key=lambda paar: paar[1], reverse=True)
        agent, punkte = bewertet[0]
        self.wahlen.append({"ziel": _text(ziel)[:80], "agent": agent.name,
                            "punkte": round(punkte, 2),
                            "andere": [(a.name, round(p, 2))
                                       for a, p in bewertet[1:3]]})
        del self.wahlen[:-50]
        return agent, punkte

    def zerlegen(self, ziel, mindestpunkte: float = 0.6) -> Optional[Zerlegung]:
        """Eine Zerlegung vom passenden Agenten - oder None.

        `mindestpunkte` ist bewusst hoch. Ein Agent, der nur knapp passt,
        liefert einen Plan, der aussieht wie ein Plan und am Ziel vorbeigeht.
        Lieber das Modell fragen als schlecht raten.
        """
        agent, punkte = self.waehlen(ziel)
        if agent is None or punkte < mindestpunkte:
            return None
        schritte = agent.zerlegen(ziel)
        if not schritte:
            return None
        return Zerlegung(schritte=schritte, quelle=f"agent:{agent.name}",
                         begruendung=f"{agent.beschreibung} (Passung "
                                     f"{punkte:.0%})")

    def uebersicht(self) -> str:
        zeilen = [f"{len(self.agenten)} Fachagent(en):"]
        for agent in self.agenten:
            fehlt = (set(agent.braucht) - self.werkzeuge) if self.werkzeuge else set()
            marke = "✔" if not fehlt else "!"
            zeilen.append(f"  {marke} {agent.name:<12} {agent.beschreibung}")
            if fehlt:
                zeilen.append(f"      ruht: es fehlen {', '.join(sorted(fehlt))}")
        if self.wahlen:
            zeilen.append("")
            zeilen.append("Zuletzt gewaehlt:")
            for wahl in self.wahlen[-5:]:
                zeilen.append(f"  {wahl['agent']} ({wahl['punkte']:.0%}) fuer "
                              f"„{wahl['ziel']}“")
        return "\n".join(zeilen)


# ------------------------------------------------------------------- Helfer
def _text(ziel) -> str:
    teile = [str(getattr(ziel, "beschreibung", "") or ""),
             str(getattr(ziel, "ergebnis", "") or "")]
    if not any(teile):
        teile = [str(ziel)]
    return " ".join(teile).lower()


_FUELLWOERTER = re.compile(
    r"^\s*(bitte|kannst du|koenntest du|könntest du|mach|erstelle|schreibe|"
    r"lege|recherchiere|finde|suche|erzeuge|baue|leg)\s+", re.IGNORECASE)


def _thema(ziel) -> str:
    """Das Thema aus der Zielbeschreibung - ohne die Hoeflichkeitsfloskeln.

    Bewusst grob. Ein Themenextraktor mit Anspruch waere eine weitere Stelle,
    an der etwas falsch geraten wird; hier reicht es, die immer gleichen
    Anfangsfloskeln wegzunehmen.
    """
    text = str(getattr(ziel, "beschreibung", "") or str(ziel)).strip()
    frueher = None
    while frueher != text:
        frueher = text
        text = _FUELLWOERTER.sub("", text).strip()
    text = re.sub(r"^(eine|einen|ein|die|der|das|dem|zum|zu)\s+", "", text,
                  flags=re.IGNORECASE).strip()
    text = re.sub(r"^(thema|themas|thematik)\s+", "", text,
                  flags=re.IGNORECASE).strip()
    # Ein Auftrag nennt oft zwei Dinge in einem Satz: "recherchiere X UND lege
    # eine Notiz an". Nur der erste Teil ist das Thema; der zweite beschreibt
    # den Schritt, den der Agent ohnehin kennt. Ohne diesen Schnitt landete
    # der halbe Auftragssatz als Suchbegriff und als Notiztitel.
    text = re.split(r"\s+(?:und|sowie|danach|anschlie(?:ss|ß)end|dann)\s+",
                    text, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return (text or str(ziel))[:120]


_ENTFERN_VORSPANN = re.compile(
    r"^\s*(?:bitte\s+)?(?:loesche|lösche|entferne|entsorge|raeume|räume|wirf)\s+"
    r"(?:die|den|das|alle)?\s*(?:notiz|datei|seite)?\s*", re.IGNORECASE)


def _gegenstand(ziel) -> str:
    """WORAUF sich ein Entfern-Auftrag bezieht - ohne die Handlung davor.

    Aus "Loesche die Notiz Sandkisten" wird "Sandkisten". Ohne diesen
    Schnitt hiesse die zu loeschende Notiz "Loesche die Notiz Sandkisten",
    und die gibt es nicht - der Schritt scheiterte, statt zu wirken.
    """
    text = str(getattr(ziel, "beschreibung", "") or str(ziel)).strip()
    gekuerzt = _ENTFERN_VORSPANN.sub("", text).strip()
    gekuerzt = re.split(r"\s+(?:aus|von|in)\s+(?:dem|der|den)\s+",
                        gekuerzt, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    gekuerzt = gekuerzt.rstrip(".!? ")
    return (gekuerzt or _thema(ziel))[:120]


def _titel(ziel, thema: str) -> str:
    """Ein brauchbarer Notiztitel: das Thema, ohne Satzzeichen, gekappt."""
    roh = re.split(r"[.!?\n]", thema)[0].strip()
    roh = re.sub(r"^(uebersicht|übersicht|notiz|bericht)\s+(zu|ueber|über)\s+",
                 "", roh, flags=re.IGNORECASE).strip()
    roh = re.sub(r'[<>:"/\\|?*]', "", roh)
    return (roh[:80] or "Ohne Titel").strip()
