"""Die Befehlsliste - EINE Liste für Terminal und Oberfläche.

Der Anlass: Die Oberfläche kannte `/help` und `/werkzeuge` nicht. Der ganze
Befehlssatz steckte in `ui.py._command()`, also im Terminalteil; die
Weboberfläche hat "/help" stumpf als Frage ans Modell geschickt und bekam
eine Halluzination zurück.

Die Antwort darauf ist nicht "dieselbe Liste zweimal pflegen", sondern eine
Liste an einer Stelle:

* `ui.py` erzeugt seine Hilfetabelle daraus,
* die Oberfläche holt sie über `/api/befehle` für die Vorschlagsliste,
* und beide sprechen dieselben Namen und Kurznamen.

`wo` sagt, wo ein Befehl etwas tun kann. `terminal` heißt nicht "verboten",
sondern "braucht die Konsole" - Stimme, Bildschirm leeren, mitlaufende
Anzeige. Die Oberfläche zeigt solche Befehle trotzdem an und sagt beim Aufruf
ehrlich, warum sie hier nichts bewirken. Verschweigen wäre schlimmer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

# Womit sich das Argument eines Befehls sinnvoll vervollständigen lässt.
# Die Oberfläche holt die passende Liste über /api/vorschlaege.
ARGUMENTARTEN = ("frei", "keins", "fertigkeit", "notiz", "ordner", "datei",
                 "codedatei", "stimme", "sprache", "an_aus", "zahl", "wahl")


@dataclass
class Befehl:
    name: str                                   # der Hauptname, ohne Schrägstrich
    kurz: str                                   # eine Zeile, was er tut
    gruppe: str                                 # für die Gliederung der Vorschläge
    aliase: List[str] = field(default_factory=list)
    argument: str = "keins"                     # eine der ARGUMENTARTEN
    argumenthilfe: str = ""                     # z. B. "<Thema>"
    wahl: List[str] = field(default_factory=list)   # feste Auswahl, wenn art="wahl"
    wo: str = "beide"                           # beide | terminal
    dauert: bool = False                        # läuft merklich lange

    @property
    def alle_namen(self) -> List[str]:
        return [self.name, *self.aliase]

    def als_dict(self) -> dict:
        return {
            "name": self.name, "kurz": self.kurz, "gruppe": self.gruppe,
            "aliase": self.aliase, "argument": self.argument,
            "argumenthilfe": self.argumenthilfe, "wahl": self.wahl,
            "wo": self.wo, "dauert": self.dauert,
        }


BEFEHLE: List[Befehl] = [
    # ------------------------------------------------------------ Wissen
    Befehl("notiz", "Wissen zum Thema bündeln und als Notiz ablegen", "Wissen",
           argument="frei", argumenthilfe="<Thema>", dauert=True),
    Befehl("merke", "Letzte Antwort als Notiz speichern", "Wissen",
           aliase=["speichern"], argument="frei", argumenthilfe="[Thema]"),
    Befehl("quellen", "Quellen der letzten Antwort", "Wissen"),
    Befehl("baum", "Ordnerbaum der Vault anzeigen", "Wissen", aliase=["tree"]),
    Befehl("struktur", "Kompakte Struktur-Übersicht", "Wissen", aliase=["overview"]),
    Befehl("sync", "Geänderte Notizen nachindexieren", "Wissen", dauert=True),
    Befehl("reindex", "Index komplett neu aufbauen", "Wissen",
           aliase=["neuaufbau"], dauert=True),

    # ------------------------------------------------------------ Vault pflegen
    Befehl("rueckgaengig", "Letzte Änderung an der Vault zurücknehmen", "Vault",
           aliase=["rückgängig", "undo"]),
    Befehl("verlauf", "Änderungsjournal anzeigen", "Vault",
           aliase=["aenderungen", "änderungen"]),
    Befehl("links", "Kaputte [[Links]] finden und reparieren", "Vault",
           aliase=["linkcheck"], argument="wahl", argumenthilfe="[reparieren]",
           wahl=["reparieren"]),
    Befehl("wartung", "Voller Wartungslauf: Index, Links, Verwaiste, Dubletten",
           "Vault", aliase=["maintenance"], dauert=True),
    Befehl("trocken", "Trockenlauf – zeigt an, schreibt aber nichts", "Vault",
           aliase=["dryrun"], argument="an_aus", argumenthilfe="an|aus"),
    Befehl("regeln", "Verbindliche Arbeitsregeln aus dem Regelordner", "Vault",
           aliase=["grundregeln"], argument="wahl", argumenthilfe="[voll|anlegen]",
           wahl=["voll", "anlegen"]),

    # ------------------------------------------------------------ Werkzeuge
    Befehl("werkzeuge", "Was der Agent an der Vault tun kann", "Werkzeuge",
           aliase=["tools"]),
    Befehl("fertigkeiten", "Selbst gebaute Abläufe – geprüft, mit Grund für Gesperrtes",
           "Werkzeuge", aliase=["skills"]),
    Befehl("fertigkeit", "Eine Fertigkeit ausführen und das Protokoll sehen",
           "Werkzeuge", aliase=["skill"], argument="fertigkeit",
           argumenthilfe="<name> [thema]", dauert=True),
    Befehl("verhalten", "Verhaltensfertigkeiten – was ändert, WIE geantwortet wird",
           "Werkzeuge", aliase=["haltung"], argument="frei",
           argumenthilfe="[Satz zum Ausprobieren]"),
    Befehl("zeichne", "Einen Ablauf zeichnen: /zeichne A -> B; B -> C",
           "Werkzeuge", aliase=["diagramm"], argument="frei",
           argumenthilfe="<A -> B; B -> C>"),

    # ------------------------------------------------------- Betriebssystem
    Befehl("kern", "Zustand des Betriebssystems: Module, Auftraege, Rechte",
           "System", aliase=["system", "os"], argument="wahl",
           argumenthilfe="[auftraege|ereignisse]", wahl=["auftraege", "ereignisse"]),
    Befehl("rechte", "Was Zeus darf – und was er sich verdient hat", "System",
           aliase=["stufe"], argument="wahl", argumenthilfe="[0|1|2|3|antrag]",
           wahl=["0", "1", "2", "3", "antrag"]),
    Befehl("selbst", "Aenderungsvorschlaege am eigenen Code, in Quarantaene",
           "System", aliase=["selbstentwicklung"], argument="frei",
           argumenthilfe="[uebernehmen|verwerfen <kennung>]"),

    # -------------------------------------------------- Ziele und Auftraege
    Befehl("ziel", "Ein Ziel aufgeben, an dem Zeus selbstaendig arbeitet",
           "Auftraege", aliase=["kuemmere", "kümmere", "auftrag"],
           argument="frei", argumenthilfe="<was erreicht werden soll>",
           dauert=True),
    Befehl("ziele", "Woran gerade gearbeitet wird - mit Zustand und Schritten",
           "Auftraege", aliase=["vorhaben"], argument="frei",
           argumenthilfe="[alle|<kennung>]"),
    Befehl("arbeiten", "Die Warteschlange jetzt im Vordergrund abarbeiten",
           "Auftraege", aliase=["abarbeiten", "los"], argument="wahl",
           argumenthilfe="[runde]", wahl=["runde"], dauert=True),
    Befehl("freigeben", "Wartende Entscheidungen beantworten",
           "Auftraege", aliase=["freigabe", "entscheiden"], argument="frei",
           argumenthilfe="[<nr> ja|nein <Begruendung>]"),
    Befehl("termine", "Zeitgesteuerte Auftraege - anzeigen, anlegen, abschalten",
           "Auftraege", aliase=["zeitplan", "plan"], argument="frei",
           argumenthilfe="[neu <abstand> <auftrag>|aus <kennung>]"),
    Befehl("ablaeufe", "Gespeicherte Ablaeufe anzeigen und starten",
           "Auftraege", aliase=["ablauf", "workflows"], argument="frei",
           argumenthilfe="[start <name>|zeigen <name>]"),

    # ------------------------------------------------------------ Aufgaben
    Befehl("aufgaben", "Deine Aufgabenliste als Stichpunkte", "Aufgaben",
           aliase=["todo", "liste"], argument="wahl",
           argumenthilfe="[alle|neu <Titel>]", wahl=["alle", "neu"]),
    Befehl("weiter", "Nächste offene Aufgabe holen und einen Plan VORSCHLAGEN",
           "Aufgaben", aliase=["leerlauf", "naechste", "nächste"], argument="wahl",
           argumenthilfe="[ja|an|aus]", wahl=["ja", "an", "aus"], dauert=True),
    Befehl("offene", "Offene Punkte aus fehlgeschlagenen Aufrufen", "Aufgaben",
           aliase=["offen", "punkte", "fehlschlaege", "fehlschläge"],
           argument="wahl", argumenthilfe="[abarbeiten]", wahl=["abarbeiten"]),

    # ------------------------------------------------------------ Eigener Code
    Befehl("code", "Eigene Quelldateien auflisten", "Code",
           aliase=["quellcode"], argument="codedatei", argumenthilfe="[Muster]"),
    Befehl("codebasis", "Quellcode als Notizen in die Vault übernehmen", "Code",
           aliase=["selbstcheck"], dauert=True),
    Befehl("codereview", "Eigenen Code prüfen – statisch in Sekunden", "Code",
           aliase=["codepruefung", "codeprüfung"], argument="wahl",
           argumenthilfe="[tief] [n]", wahl=["tief"], dauert=True),
    Befehl("architektur", "Aufbau vermessen: Größe, Verzweigungen, Kopplung, Tests",
           "Code", aliase=["aufbau"], dauert=True),

    # ------------------------------------------------------------ Messen
    Befehl("tempo", "Misst das Tempo und nennt die Ursache", "Messen",
           aliase=["speed", "benchmark"], dauert=True),
    Befehl("hardware", "GPU, VRAM, Ladezustand des Modells", "Messen",
           aliase=["gpu"]),
    Befehl("messen", "Prüfaufgaben durchlaufen: wird der Agent besser?", "Messen",
           aliase=["eval", "evaluation"], argument="wahl",
           argumenthilfe="[mit|ohne|blind|alle|verlauf] [N]",
           wahl=["mit", "ohne", "blind", "alle", "verlauf"], dauert=True),
    Befehl("server", "Welcher Ollama-Server gilt, und von Hand umschalten", "Messen",
           aliase=["ollama"], argument="wahl", argumenthilfe="[ausweich|haupt|pruefen]",
           wahl=["ausweich", "haupt", "pruefen"]),
    Befehl("doktor", "Konfiguration prüfen (.env, Vault, Ollama)", "Messen",
           aliase=["doctor", "diagnose"]),
    Befehl("status", "Modelle, Index, Sprache", "Messen"),

    # ------------------------------------------------------------ Lernen
    Befehl("lernen", "Kennzahlen, gelernte Regeln, offene Wissenslücken", "Lernen",
           aliase=["kennzahlen"]),
    Befehl("lektionen", "Gelernte Regeln zeigen – mit Bewährungsbilanz", "Lernen",
           argument="wahl", argumenthilfe="[konsolidieren]", wahl=["konsolidieren"]),
    Befehl("sitzung", "Sitzungsgedächtnis zeigen oder jetzt verdichten", "Lernen",
           argument="wahl", argumenthilfe="[merken]", wahl=["merken"]),
    Befehl("sitzungen", "Alle Sitzungsnotizen mit Bewährungsbilanz auflisten",
           "Lernen", aliase=["sitzungsnotizen"]),
    Befehl("reflektieren", "Rückblick auf die Sitzung mit Verbesserungen", "Lernen",
           aliase=["reflexion"], dauert=True),

    # ------------------------------------------------------------ Sitzung
    Befehl("hilfe", "Diese Befehlsliste", "Sitzung", aliase=["help", "h", "?"]),
    Befehl("prompt", "Eine Textdatei als eine einzige Eingabe schicken", "Sitzung",
           aliase=["datei", "einlesen"], argument="datei", argumenthilfe="<datei>"),
    Befehl("vault", "Vault-Pfad setzen und in die .env schreiben", "Sitzung",
           argument="frei", argumenthilfe="<Pfad>"),
    Befehl("leeren", "Gesprächsverlauf vergessen", "Sitzung", aliase=["clear"]),

    # ------------------------------------------------------------ Nur im Terminal
    Befehl("sprich", "Sprachausgabe schalten", "Stimme", aliase=["sprechen"],
           argument="an_aus", argumenthilfe="an|aus", wo="terminal"),
    Befehl("stimmen", "Alle installierten Stimmen auflisten", "Stimme",
           aliase=["voices"], wo="terminal"),
    Befehl("stimme", "Stimme wechseln und in der .env sichern", "Stimme",
           aliase=["voice"], argument="stimme", argumenthilfe="<Nr|Name|installieren|liste>",
           wo="terminal"),
    Befehl("sprache", "Antwortsprache umstellen", "Stimme", aliase=["language"],
           argument="sprache", argumenthilfe="<Deutsch|English>", wo="terminal"),
    Befehl("hoeren", "Spracheingabe erzwingen", "Stimme",
           aliase=["hören", "mic"], wo="terminal"),
    Befehl("cls", "Bildschirm leeren", "Sitzung", aliase=["bildschirm"], wo="terminal"),
    Befehl("ansicht", "Mitlaufende Anzeige schalten", "Sitzung", aliase=["live"],
           argument="an_aus", argumenthilfe="an|aus", wo="terminal"),
    Befehl("ende", "Beenden", "Sitzung", aliase=["exit", "quit", "q"], wo="terminal"),
]


def nach_name() -> Dict[str, Befehl]:
    """Jeder Name und jeder Kurzname zeigt auf seinen Befehl."""
    tabelle: Dict[str, Befehl] = {}
    for befehl in BEFEHLE:
        for name in befehl.alle_namen:
            tabelle[name.lower()] = befehl
    return tabelle


def finden(eingabe: str) -> Optional[Befehl]:
    return nach_name().get(str(eingabe or "").lstrip("/").strip().lower())


def als_liste(nur: str = "") -> List[dict]:
    """Für die Oberfläche. nur='beide' filtert die reinen Terminalbefehle weg."""
    return [b.als_dict() for b in BEFEHLE if not nur or b.wo == nur or b.wo == "beide"]


# Was KEIN Schrägstrichbefehl ist, aber in die Hilfe gehört, weil man es
# sonst nie erfährt.
OHNE_SCHRAEGSTRICH: Sequence = (
    ("*(einfach lostippen)*", "Frage an die Vault"),
    ("`@Ordner frage…`", "Suche auf einen Ordner beschränken"),
    ("`[` beim Tippen", "Werkzeug- und Fertigkeitsliste – auswählen fügt `[name]` ein"),
    ("`/` am Zeilenanfang", "Befehlsliste mit Argumenten"),
    ("*(leere Eingabe + Enter)*", "Spracheingabe starten (nur Terminal)"),
    ("*(mehrzeilig einfügen)*", "Wird automatisch als EINE Eingabe genommen"),
    ("Zeile mit drei Anführungszeichen",
     "Öffnet/schließt eine mehrzeilige Eingabe von Hand"),
)


def hilfetabelle(fuer: str = "terminal") -> str:
    """Die Hilfe als Markdown - aus der Liste erzeugt, nicht daneben gepflegt.

    Genau daran ist es vorher gescheitert: Die Tabelle stand als Text in
    ui.py, die Oberfläche hatte gar keine. Wer eine Liste an zwei Stellen
    pflegt, pflegt sie irgendwann an einer.
    """
    zeilen = ["**Befehle**", "", "| Befehl | Wirkung |", "|---|---|"]
    for marke, text in OHNE_SCHRAEGSTRICH:
        zeilen.append(f"| {marke} | {text} |")

    gruppen: Dict[str, List[Befehl]] = {}
    for befehl in BEFEHLE:
        gruppen.setdefault(befehl.gruppe, []).append(befehl)

    for gruppe, eintraege in gruppen.items():
        zeilen.append(f"| **{gruppe}** | |")
        for befehl in eintraege:
            marke = f"`/{befehl.name}"
            if befehl.argumenthilfe:
                marke += f" {befehl.argumenthilfe}"
            marke += "`"
            text = befehl.kurz
            if befehl.wo == "terminal" and fuer != "terminal":
                text += " *(nur im Terminal)*"
            if befehl.dauert:
                text += " *(dauert)*"
            zeilen.append(f"| {marke} | {text} |")
    return "\n".join(zeilen)
