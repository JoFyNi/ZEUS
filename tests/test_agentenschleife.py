"""Die Agentenschleife von Ende zu Ende - mit echten Dateien, ohne Modell.

Das ist die Reihe, auf die es ankommt. Die anderen pruefen Bausteine; diese
prueft, ob aus ihnen ein SYSTEM geworden ist. Gefahren wird der Weg aus §40
des Auftrags, und zwar durch den echten Code:

    Ziel -> Aufgabengraph -> Werkzeuge -> Zwischenergebnisse ->
    ein Schritt scheitert absichtlich -> Verifikation merkt es ->
    Neuplanung -> anderer Weg -> Erfolg -> Verifikation -> Abschluss

Kein Ollama. Der Zerleger bekommt seine Schritte als Vorgabe (so arbeiten
Zeitplan, Ablauf und Regelwerk ohnehin), und fuer die Neuplanung steht ein
kleines Ersatzmodell bereit, das eine feste JSON-Antwort liefert. Das ist
kein Weichspuelen: Geprueft wird der Weg durch Speicher, Tor, Verifikation
und Laeufer - und der ist an jeder Stelle echt. Was das Modell antwortet,
ist der Eingabewert des Tests, nicht sein Gegenstand.

Die drei Faelle, die der Auftrag namentlich verlangt, stehen hier:

    §46 Werkzeugfehler     ein Werkzeug meldet ehrlich einen Fehler
    §47 Falscher Erfolg    ein Werkzeug meldet ok, ohne etwas zu tun
    §41 Absturz            der Prozess stirbt mitten in der Aufgabe
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TMP = Path(tempfile.mkdtemp(prefix="jarvis_schleife_")).resolve()
VAULT = TMP / "Zeus"
(VAULT / "00_Inbox").mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["VAULT_GIT"] = "false"
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

import stubs  # noqa: E402,F401

from jarvis.kern.ausfuehrung import Aufgabenlaeufer            # noqa: E402
from jarvis.kern.aufgabenspeicher import (                     # noqa: E402
    ABGEBROCHEN, ART_SCHRITT, ART_ZIEL, BEREIT, FERTIG, GESCHEITERT, LAEUFT,
    NEUPLANUNG, PAUSIERT, WARTET_AUF_FREIGABE, ZIEL_ERREICHT,
    Aufgabenspeicher)
from jarvis.kern.budget import Budget                          # noqa: E402
from jarvis.kern.planung import Zerleger                       # noqa: E402
from jarvis.kern.rechte import Rechteverwaltung                # noqa: E402
from jarvis.kern.warteschlange import Arbeiterstube            # noqa: E402
from jarvis.kern.werkzeugtor import Werkzeugtor                # noqa: E402
from jarvis.werkzeuge.ergebnis import ToolResult               # noqa: E402

FAILURES = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


# --------------------------------------------------------------- Werkzeuge
class Probewerkzeuge:
    """Echte Dateien, echte Fehlschlaege - nur ohne Index und Modell.

    Die Verifikation sieht wirklich auf der Platte nach. Deshalb muessen
    diese Werkzeuge wirklich schreiben; ein Attrappen-Werkzeug, das nur
    True zurueckgibt, wuerde den Test wertlos machen (und genau das ist
    der Fehler, den §50 verbietet).
    """

    def __init__(self, wurzel: Path, cfg=None):
        self.root = Path(wurzel)
        self.cfg = cfg
        self.aufrufe = []
        # Werkzeuge, die beim naechsten Aufruf scheitern sollen.
        self.sabotage = {}

    # --- was die Verifikation von einem Werkzeugsatz erwartet -------------
    def _notes(self):
        # Der Papierkorb gehoert NICHT zum Bestand - genau wie beim echten
        # Index. Ohne diese Ausnahme findet die Loeschpruefung die Notiz im
        # Papierkorb wieder und meldet, sie liege noch an ihrem Platz.
        return [p for p in self.root.rglob("*.md")
                if "99_Papierkorb" not in p.parts]

    def notiz_exakt(self, name):
        for pfad in self._notes():
            if pfad.stem.lower() == str(name).lower():
                return type("N", (), {"path": str(pfad), "stem": pfad.stem})()
        return None

    resolve_note = notiz_exakt

    # --- Ausfuehrung ------------------------------------------------------
    def ausfuehren(self, werkzeug: str, parameter: dict) -> ToolResult:
        self.aufrufe.append((werkzeug, dict(parameter or {})))
        art = self.sabotage.pop(werkzeug, None)
        if art == "fehler":
            return ToolResult(False, f"'{werkzeug}' ist fehlgeschlagen: "
                                     f"Quelle nicht auffindbar.", {})
        if art == "luege":
            # §47: Erfolg behaupten, ohne etwas zu tun. Die Datei entsteht
            # NICHT - die Verifikation muss das merken.
            return ToolResult(True, "Notiz angelegt.",
                              {"pfad": f"00_Inbox/{parameter.get('titel', 'X')}.md"})
        funktion = getattr(self, f"_w_{werkzeug}", None)
        if funktion is None:
            return ToolResult(False, f"Unbekanntes Werkzeug '{werkzeug}'.", {})
        return funktion(dict(parameter or {}))

    def _w_notizen_suchen(self, p):
        treffer = [n.stem for n in self._notes()][:5]
        return ToolResult(True, f"{len(treffer)} Treffer: {', '.join(treffer)}",
                          {"treffer": treffer})

    def _w_notiz_lesen(self, p):
        note = self.notiz_exakt(p.get("name", ""))
        if not note:
            return ToolResult(False, f"Notiz '{p.get('name')}' nicht gefunden.", {})
        text = Path(note.path).read_text(encoding="utf-8")
        return ToolResult(True, text[:400], {"pfad": str(
            Path(note.path).relative_to(self.root))})

    def _w_notiz_erstellen(self, p):
        ordner = str(p.get("ordner") or "00_Inbox")
        titel = str(p.get("titel") or "Ohne Titel")
        ziel = self.root / ordner / f"{titel}.md"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(f"# {titel}\n\n{p.get('inhalt') or ''}\n", encoding="utf-8")
        return ToolResult(True, f"Notiz '{titel}' angelegt.",
                          {"pfad": str(ziel.relative_to(self.root))})

    def _w_notiz_ergaenzen(self, p):
        note = self.notiz_exakt(p.get("name", ""))
        if not note:
            return ToolResult(False, f"Notiz '{p.get('name')}' nicht gefunden.", {})
        pfad = Path(note.path)
        pfad.write_text(pfad.read_text(encoding="utf-8") + "\n"
                        + str(p.get("inhalt") or ""), encoding="utf-8")
        return ToolResult(True, "ergaenzt",
                          {"pfad": str(pfad.relative_to(self.root))})

    def _w_notiz_loeschen(self, p):
        note = self.notiz_exakt(p.get("name", ""))
        if not note:
            return ToolResult(False, "nicht gefunden", {})
        papierkorb = self.root / "99_Papierkorb"
        papierkorb.mkdir(exist_ok=True)
        pfad = Path(note.path)
        (papierkorb / pfad.name).write_text(pfad.read_text(encoding="utf-8"),
                                            encoding="utf-8")
        pfad.unlink()
        return ToolResult(True, "geloescht", {})


class Probemodell:
    """Ein Modell, das feste Antworten aus einer Liste gibt."""

    def __init__(self, antworten=None):
        self.antworten = list(antworten or [])
        self.fragen = []

    def chat(self, nachrichten, **kw):
        self.fragen.append(nachrichten[-1].get("content", ""))
        if not self.antworten:
            return ""
        return self.antworten.pop(0)


class Konf:
    """Die paar Einstellungen, die Tor und Laeufer wirklich lesen."""
    trash_folder = "99_Papierkorb"
    self_extension = False
    freigabe_ab_risiko = "hoch"
    budget_modellaufrufe = 10
    budget_werkzeugaufrufe = 40
    budget_laufzeit = 120
    budget_versuche = 3
    budget_neuplanungen = 2
    budget_zeichen = 500_000


def bauen(name: str, *, rechtestufe: int = 1, llm=None,
          unbeaufsichtigt: bool = True):
    """Ein vollstaendiger Kern in vier Zeilen - wie ihn `aufbau.py` baut."""
    ordner = TMP / name
    ordner.mkdir(exist_ok=True)
    cfg = Konf()
    speicher = Aufgabenspeicher(ordner / "aufgaben.sqlite3")
    tools = Probewerkzeuge(VAULT, cfg)
    rechte = Rechteverwaltung(stufe=rechtestufe, ablage=ordner / "rechte.json")
    tor = Werkzeugtor(tools, cfg, rechte=rechte, speicher=speicher)
    zerleger = Zerleger(llm=llm,
                        werkzeugnamen={"notizen_suchen", "notiz_lesen",
                                       "notiz_erstellen", "notiz_ergaenzen",
                                       "notiz_loeschen"},
                        parameternamen={"notizen_suchen": ["frage"],
                                        "notiz_lesen": ["name"],
                                        "notiz_erstellen": ["titel", "inhalt",
                                                            "ordner"],
                                        "notiz_ergaenzen": ["name", "inhalt"],
                                        "notiz_loeschen": ["name"]})
    laeufer = Aufgabenlaeufer(speicher, tor, zerleger=zerleger, llm=llm, cfg=cfg,
                              unbeaufsichtigt=unbeaufsichtigt)
    stube = Arbeiterstube(speicher, laeufer, arbeiter=1)
    return speicher, tools, rechte, tor, laeufer, stube


# ============================================================ die Testfaelle
def fall_ziel_bis_ende() -> None:
    """§40: Ziel, Graph, mehrere Werkzeuge, Artefakt, persistenter Abschluss."""
    speicher, tools, _, _, laeufer, stube = bauen("ende_zu_ende")
    ziel, wurzel = laeufer.ziel_aufnehmen(
        "Erstelle eine Uebersichtsnotiz zum Thema Sandkiste",
        ergebnis="Eine Notiz 'Sandkiste' liegt in 00_Inbox",
        daten={"schritte": [
            {"beschreibung": "Bestand durchsehen", "werkzeug": "notizen_suchen",
             "parameter": {"frage": "Sandkiste"}, "braucht": []},
            {"beschreibung": "Uebersicht schreiben", "werkzeug": "notiz_erstellen",
             "parameter": {"titel": "Sandkiste", "ordner": "00_Inbox",
                           "inhalt": "Was die Sandkiste kann und was nicht."},
             "erwartung": "Die Datei existiert", "braucht": [1]},
        ]})
    check("Ziel angelegt", ziel.offen and ziel.id, ziel.beschreibung[:40])

    bericht = stube.abarbeiten(max_runden=20, frist=30)
    wurzel = speicher.holen(wurzel.id)
    kinder = speicher.kinder(wurzel.id)

    check("Aufgabengraph entstanden", len(kinder) == 2,
          f"{len(kinder)} Schritte")
    check("Abhaengigkeit wurde beachtet",
          [k.daten.get("nummer") for k in kinder] == [1, 2]
          and all(k.zustand == FERTIG for k in kinder),
          ", ".join(f"{k.daten.get('nummer')}:{k.zustand}" for k in kinder))
    check("Wurzel ist fertig", wurzel.zustand == FERTIG, wurzel.zustand)
    check("Ziel gilt als erreicht",
          speicher.ziel(ziel.id).zustand == ZIEL_ERREICHT,
          speicher.ziel(ziel.id).zustand)
    check("Die Datei liegt wirklich da", (VAULT / "00_Inbox" / "Sandkiste.md").exists())

    artefakte = []
    for kind in kinder:
        artefakte.extend(speicher.artefakte(kind.id))
    check("Artefakt verzeichnet", any("Sandkiste" in a["ort"] for a in artefakte),
          str([a["ort"] for a in artefakte]))

    telemetrie = []
    for kind in kinder:
        telemetrie.extend(speicher.laeufe(kind.id))
    check("Telemetrie je Schritt vorhanden",
          any(z["art"] == "werkzeug" for z in telemetrie),
          f"{len(telemetrie)} Zeilen")
    check("Werkzeuge wirklich gelaufen",
          [a[0] for a in tools.aufrufe] == ["notizen_suchen", "notiz_erstellen"],
          str([a[0] for a in tools.aufrufe]))
    check("Bilanz stimmt", bericht["fertig"] >= 3, json.dumps(bericht["fertig"]))
    speicher.schliessen()


def fall_falscher_erfolg() -> None:
    """§47: Ein Werkzeug behauptet Erfolg, ohne etwas zu tun."""
    speicher, tools, _, _, laeufer, stube = bauen("falscher_erfolg")
    tools.sabotage["notiz_erstellen"] = "luege"
    ziel, wurzel = laeufer.ziel_aufnehmen(
        "Lege die Notiz Luegennotiz an",
        ergebnis="Die Notiz existiert",
        daten={"schritte": [
            {"beschreibung": "Notiz anlegen", "werkzeug": "notiz_erstellen",
             "parameter": {"titel": "Luegennotiz", "ordner": "00_Inbox"},
             "braucht": []},
            {"beschreibung": "Bestand pruefen", "werkzeug": "notizen_suchen",
             "parameter": {"frage": "Luegennotiz"}, "braucht": [1]},
        ]})
    # Nur EIN Durchgang bis zum Urteil - hier geht es allein darum, dass die
    # Luege auffliegt, nicht darum, wie es danach weitergeht.
    stube.runde()                       # zerlegen
    stube.runde()                       # der luegende Schritt

    kinder = speicher.kinder(wurzel.id)
    erster = [k for k in kinder if k.daten.get("nummer") == 1]
    check("Der luegende Schritt ist NICHT fertig",
          bool(erster) and erster[0].zustand != FERTIG,
          erster[0].zustand if erster else "kein Schritt")
    check("Er steht auf Neuplanung, nicht auf fertig",
          bool(erster) and erster[0].zustand == NEUPLANUNG,
          erster[0].zustand if erster else "-")
    check("Die Datei existiert tatsaechlich nicht",
          not (VAULT / "00_Inbox" / "Luegennotiz.md").exists())
    check("Das Ziel wurde NICHT als erreicht gemeldet",
          speicher.ziel(ziel.id).zustand != ZIEL_ERREICHT,
          speicher.ziel(ziel.id).zustand)
    check("Der Folgeschritt lief nicht an",
          [a[0] for a in tools.aufrufe] == ["notiz_erstellen"],
          str([a[0] for a in tools.aufrufe]))
    laeufe = speicher.laeufe(erster[0].id) if erster else []
    check("Das Urteil steht in der Telemetrie",
          any(z["urteil"] == "durchgefallen" for z in laeufe),
          str([z["urteil"] for z in laeufe]))
    speicher.schliessen()


def fall_werkzeugfehler_und_neuplanung() -> None:
    """§46 + §15: Ein Fehler fuehrt zu einem ANDEREN Weg, nicht zum Abbruch."""
    alternative = json.dumps({
        "begruendung": "Die Quelle gibt es nicht - stattdessen neu anlegen.",
        "schritte": [{"beschreibung": "Notiz stattdessen neu anlegen",
                      "werkzeug": "notiz_erstellen",
                      "parameter": {"titel": "Ersatznotiz", "ordner": "00_Inbox",
                                    "inhalt": "aus der Neuplanung"},
                      "erwartung": "Datei liegt da", "braucht": []}]})
    llm = Probemodell([alternative])
    speicher, tools, _, _, laeufer, stube = bauen("neuplanung", llm=llm)
    tools.sabotage["notiz_lesen"] = "fehler"

    ziel, wurzel = laeufer.ziel_aufnehmen(
        "Uebernimm den Inhalt der Quellnotiz",
        ergebnis="Eine Notiz mit dem Inhalt liegt vor",
        daten={"schritte": [
            {"beschreibung": "Quellnotiz lesen", "werkzeug": "notiz_lesen",
             "parameter": {"name": "GibtEsNicht"}, "braucht": []},
        ]})
    stube.abarbeiten(max_runden=30, frist=30)

    kinder = [k for k in speicher.kinder(wurzel.id)
              if k.daten.get("generation") == 1]
    schritt = kinder[0] if kinder else None
    check("Der Schritt wurde neu geplant",
          bool(schritt) and schritt.neuplanungen >= 1,
          f"neuplanungen={schritt.neuplanungen if schritt else '-'}")
    check("Das Werkzeug wurde gewechselt",
          bool(schritt) and schritt.daten.get("werkzeug") == "notiz_erstellen",
          str(schritt.daten.get("werkzeug") if schritt else "-"))
    check("Der alternative Weg hat geklappt",
          bool(schritt) and schritt.zustand == FERTIG,
          schritt.zustand if schritt else "-")
    check("Die Ersatznotiz liegt auf der Platte",
          (VAULT / "00_Inbox" / "Ersatznotiz.md").exists())
    check("Der Fehlschlag steht im Verlauf",
          any(e["nach"] == NEUPLANUNG for e in speicher.verlauf(schritt.id))
          if schritt else False)
    check("Das Modell hat die erfolglosen Aufrufe gesehen",
          any("GibtEsNicht" in f for f in llm.fragen),
          f"{len(llm.fragen)} Modellfragen")
    check("Ziel doch noch erreicht",
          speicher.ziel(ziel.id).zustand == ZIEL_ERREICHT,
          speicher.ziel(ziel.id).zustand)
    speicher.schliessen()


def fall_ohne_alternative() -> None:
    """Wenn es keinen anderen Weg gibt, wird das gesagt - nicht verschwiegen."""
    llm = Probemodell([json.dumps({"begruendung": "kein Weg", "schritte": []}),
                       json.dumps({"begruendung": "kein Weg", "schritte": []}),
                       json.dumps({"schritte": []})])
    speicher, tools, _, _, laeufer, stube = bauen("ohne_weg", llm=llm)
    ziel, wurzel = laeufer.ziel_aufnehmen(
        "Lies eine Notiz, die es nicht gibt",
        daten={"schritte": [
            {"beschreibung": "Nicht vorhandene Notiz lesen",
             "werkzeug": "notiz_lesen", "parameter": {"name": "Phantom"},
             "braucht": []}]})
    stube.abarbeiten(max_runden=40, frist=40)

    kinder = speicher.kinder(wurzel.id)
    check("Der Schritt ist gescheitert - nicht 'fertig'",
          bool(kinder) and kinder[0].zustand == GESCHEITERT,
          kinder[0].zustand if kinder else "-")
    briefe = speicher.tote_briefe()
    check("Es liegt ein toter Brief vor", len(briefe) >= 1,
          briefe[0]["grund"][:60] if briefe else "keiner")
    check("Das Ziel gilt nicht als erreicht",
          speicher.ziel(ziel.id).zustand != ZIEL_ERREICHT,
          speicher.ziel(ziel.id).zustand)
    speicher.schliessen()


def fall_rechte_greifen() -> None:
    """§17: Auf Lesestufe laeuft kein schreibendes Werkzeug - wirklich nicht."""
    speicher, tools, rechte, tor, _, _ = bauen("rechte", rechtestufe=0)
    ergebnis = tor.ausfuehren("notiz_erstellen",
                              {"titel": "Verbotene", "ordner": "00_Inbox"})
    check("Der Aufruf wurde abgewiesen", not ergebnis.ok, ergebnis.meldung[:70])
    check("Nichts wurde geschrieben",
          not (VAULT / "00_Inbox" / "Verbotene.md").exists())
    check("Das Werkzeug wurde gar nicht erst gerufen",
          not tools.aufrufe, str(tools.aufrufe))
    check("Die Ablehnung steht in der Rechtebilanz",
          any(v["handlung"] == "vault_schreiben" for v in rechte.verweigert))
    lesen = tor.ausfuehren("notizen_suchen", {"frage": "x"})
    check("Lesen geht weiterhin", lesen.ok, lesen.meldung[:50])
    speicher.schliessen()


def fall_freigabe() -> None:
    """§18 und §42: Eine riskante Handlung wartet - und wird dann ausgefuehrt."""
    speicher, tools, _, _, laeufer, stube = bauen("freigabe")
    (VAULT / "00_Inbox" / "Wegwerfnotiz.md").write_text("# weg\n\nInhalt",
                                                        encoding="utf-8")
    ziel, wurzel = laeufer.ziel_aufnehmen(
        "Loesche die Wegwerfnotiz",
        daten={"schritte": [
            {"beschreibung": "Notiz loeschen", "werkzeug": "notiz_loeschen",
             "parameter": {"name": "Wegwerfnotiz"}, "braucht": []}]})
    stube.abarbeiten(max_runden=10, frist=20)

    kinder = speicher.kinder(wurzel.id)
    schritt = kinder[0] if kinder else None
    check("Die Aufgabe wartet auf eine Freigabe",
          bool(schritt) and schritt.zustand == WARTET_AUF_FREIGABE,
          schritt.zustand if schritt else "-")
    check("Die Notiz liegt noch da",
          (VAULT / "00_Inbox" / "Wegwerfnotiz.md").exists())
    check("Das Werkzeug wurde NICHT gerufen", not tools.aufrufe, str(tools.aufrufe))

    offen = speicher.offene_freigaben()
    check("Die Frage steht in der Ablage", len(offen) == 1,
          offen[0]["frage"][:70] if offen else "keine")

    speicher.freigabe_beantworten(offen[0]["id"], True, "ja, weg damit")
    stube.abarbeiten(max_runden=10, frist=20)
    schritt = speicher.holen(schritt.id)
    check("Nach der Freigabe lief es durch", schritt.zustand == FERTIG,
          schritt.zustand)
    check("Jetzt ist die Notiz weg",
          not (VAULT / "00_Inbox" / "Wegwerfnotiz.md").exists())
    check("Und liegt im Papierkorb",
          (VAULT / "99_Papierkorb" / "Wegwerfnotiz.md").exists())
    speicher.schliessen()


def fall_freigabe_abgelehnt() -> None:
    """§42, zweiter Teil: Ein Nein bricht ab, statt es doch zu tun."""
    speicher, tools, _, _, laeufer, stube = bauen("freigabe_nein")
    (VAULT / "00_Inbox" / "Bleibtnotiz.md").write_text("# bleibt", encoding="utf-8")
    ziel, wurzel = laeufer.ziel_aufnehmen(
        "Loesche die Bleibtnotiz",
        daten={"schritte": [
            {"beschreibung": "Notiz loeschen", "werkzeug": "notiz_loeschen",
             "parameter": {"name": "Bleibtnotiz"}, "braucht": []}]})
    stube.abarbeiten(max_runden=10, frist=20)
    offen = speicher.offene_freigaben()
    speicher.freigabe_beantworten(offen[0]["id"], False, "nein, die brauche ich")
    stube.abarbeiten(max_runden=10, frist=20)

    kinder = speicher.kinder(wurzel.id)
    check("Der Schritt ist abgebrochen",
          kinder[0].zustand == ABGEBROCHEN, kinder[0].zustand)
    check("Die Notiz ist unangetastet",
          (VAULT / "00_Inbox" / "Bleibtnotiz.md").exists())
    check("Das Loeschwerkzeug lief nie", not tools.aufrufe, str(tools.aufrufe))
    check("Das Ziel gilt nicht als erreicht",
          speicher.ziel(ziel.id).zustand != ZIEL_ERREICHT,
          speicher.ziel(ziel.id).zustand)
    speicher.schliessen()


def fall_idempotenz() -> None:
    """§33: Dieselbe Wirkung wird nicht zweimal ausgeloest."""
    speicher, tools, _, tor, _, _ = bauen("idempotenz")
    aufgabe = speicher.anlegen("Notiz einmal anlegen")
    erst = tor.ausfuehren("notiz_erstellen",
                          {"titel": "Einmalig", "ordner": "00_Inbox",
                           "inhalt": "erster Durchgang"},
                          aufgabe_id=aufgabe.id)
    check("Der erste Aufruf lief", erst.ok, erst.meldung[:60])
    # Inhalt aendern, damit sich ein zweiter Durchlauf bemerkbar machen wuerde
    pfad = VAULT / "00_Inbox" / "Einmalig.md"
    pfad.write_text("# Einmalig\n\nvon Hand geaendert", encoding="utf-8")
    zweit = tor.ausfuehren("notiz_erstellen",
                           {"titel": "Einmalig", "ordner": "00_Inbox",
                            "inhalt": "zweiter Durchgang"},
                           aufgabe_id=aufgabe.id)
    check("Der zweite Aufruf wurde abgefangen",
          zweit.daten.get("schon_geschehen") is True, zweit.meldung[:70])
    check("Die Datei wurde nicht ueberschrieben",
          "von Hand geaendert" in pfad.read_text(encoding="utf-8"))
    check("Das Werkzeug lief nur einmal",
          [a[0] for a in tools.aufrufe].count("notiz_erstellen") == 1,
          str([a[0] for a in tools.aufrufe]))
    # Die Sperre gilt der AUFGABE, nicht der Welt: Ein anderer Auftrag darf
    # dieselbe Handlung sehr wohl noch einmal tun.
    andere = speicher.anlegen("Dieselbe Notiz, anderer Auftrag")
    dritt = tor.ausfuehren("notiz_erstellen",
                           {"titel": "Einmalig", "ordner": "00_Inbox",
                            "inhalt": "dritter Durchgang"},
                           aufgabe_id=andere.id)
    check("Eine andere Aufgabe wird nicht gesperrt",
          not dritt.daten.get("schon_geschehen"), dritt.meldung[:60])
    speicher.schliessen()


def fall_budget() -> None:
    """§30: Ist das Budget alle, haelt die Aufgabe an - sie stirbt nicht."""
    speicher, tools, _, tor, laeufer, stube = bauen("budget")
    ziel, wurzel = laeufer.ziel_aufnehmen(
        "Drei Notizen anlegen",
        daten={"schritte": [
            {"beschreibung": f"Notiz {n} anlegen", "werkzeug": "notiz_erstellen",
             "parameter": {"titel": f"Budget{n}", "ordner": "00_Inbox"},
             "braucht": []} for n in (1, 2, 3)]})
    stube.runde()                       # zerlegen, noch mit vollem Budget
    # Ab jetzt ist kein einziger Werkzeugaufruf mehr erlaubt. Null heisst
    # null - nicht "Voreinstellung", siehe budget.py.
    laeufer.cfg.budget_werkzeugaufrufe = 0
    stube.abarbeiten(max_runden=12, frist=20)
    kinder = speicher.kinder(wurzel.id)
    angehalten = [k for k in kinder if k.zustand == PAUSIERT]
    check("Mindestens eine Aufgabe wurde angehalten", len(angehalten) >= 1,
          str([k.zustand for k in kinder]))
    check("Kein Werkzeug ist trotzdem gelaufen", not tools.aufrufe,
          str([a[0] for a in tools.aufrufe]))
    check("Der Grund steht dran",
          bool(angehalten) and "Budget" in (angehalten[0].fehler or ""),
          angehalten[0].fehler[:60] if angehalten else "-")
    check("Zwischenstand blieb erhalten (nicht abgebrochen)",
          all(k.zustand != GESCHEITERT for k in kinder),
          str([k.zustand for k in kinder]))

    # Fortsetzen gibt frisches Budget
    laeufer.cfg.budget_werkzeugaufrufe = 40
    check("Fortsetzen bestaetigt",
          bool(angehalten) and stube.fortsetzen(angehalten[0].id))
    stube.abarbeiten(max_runden=12, frist=20)
    check("Danach lief sie durch",
          speicher.holen(angehalten[0].id).zustand == FERTIG,
          speicher.holen(angehalten[0].id).zustand)
    speicher.schliessen()


def fall_absturz() -> None:
    """§41: Der Prozess stirbt mitten in der Aufgabe. Der Stand ueberlebt."""
    ordner = TMP / "absturz"
    ordner.mkdir(exist_ok=True)
    datenbank = ordner / "aufgaben.sqlite3"

    # --- erster "Prozess" -------------------------------------------------
    speicher = Aufgabenspeicher(datenbank)
    cfg = Konf()
    tools = Probewerkzeuge(VAULT, cfg)
    tor = Werkzeugtor(tools, cfg, rechte=Rechteverwaltung(stufe=1),
                      speicher=speicher)
    zerleger = Zerleger(llm=None, werkzeugnamen={"notiz_erstellen",
                                                 "notizen_suchen"},
                        parameternamen={"notiz_erstellen": ["titel", "ordner"],
                                        "notizen_suchen": ["frage"]})
    laeufer = Aufgabenlaeufer(speicher, tor, zerleger=zerleger, cfg=cfg)
    stube = Arbeiterstube(speicher, laeufer, arbeiter=1)
    ziel, wurzel = laeufer.ziel_aufnehmen(
        "Zwei Notizen nacheinander anlegen",
        ergebnis="Beide Notizen liegen vor",
        daten={"schritte": [
            {"beschreibung": "Erste anlegen", "werkzeug": "notiz_erstellen",
             "parameter": {"titel": "AbsturzEins", "ordner": "00_Inbox"},
             "braucht": []},
            {"beschreibung": "Zweite anlegen", "werkzeug": "notiz_erstellen",
             "parameter": {"titel": "AbsturzZwei", "ordner": "00_Inbox"},
             "braucht": [1]}]})
    stube.runde()            # zerlegt
    stube.runde()            # erster Schritt
    # Jetzt den Absturz nachstellen: eine Aufgabe steht auf LAEUFT und
    # niemand arbeitet mehr daran. Vorher die Blockade loesen, damit der
    # zweite Schritt ueberhaupt bereit ist - sonst stellt der Test einen
    # Absturz nach, bei dem gar nichts lief.
    speicher.blockaden_loesen()
    offen = [a for a in speicher.alle(nur_offene=True) if a.zustand == BEREIT]
    if offen:
        speicher.wechseln(offen[0].id, LAEUFT, "Absturz nachgestellt")
        speicher.aktualisieren(offen[0].id, arbeiter="tot", belegt_bis=1.0)
    speicher.schliessen()
    del speicher, laeufer, stube, tor

    check("Die erste Notiz war vor dem Absturz da",
          (VAULT / "00_Inbox" / "AbsturzEins.md").exists())

    # --- zweiter "Prozess": Neustart --------------------------------------
    speicher = Aufgabenspeicher(datenbank)
    check("Die Aufgabe wurde wiedergefunden",
          len(speicher.alle(nur_offene=True)) >= 1,
          f"{len(speicher.alle(nur_offene=True))} offen")
    check("Das Ziel steht noch", speicher.ziel(ziel.id) is not None)

    tools2 = Probewerkzeuge(VAULT, cfg)
    tor2 = Werkzeugtor(tools2, cfg, rechte=Rechteverwaltung(stufe=1),
                       speicher=speicher)
    laeufer2 = Aufgabenlaeufer(speicher, tor2, zerleger=zerleger, cfg=cfg)
    stube2 = Arbeiterstube(speicher, laeufer2, arbeiter=1)
    wieder = stube2.wiederaufnehmen()
    check("Der unterbrochene Stand wurde erkannt", len(wieder) >= 1,
          f"{len(wieder)} zur Neuplanung")
    check("Er wurde NICHT blind fortgesetzt",
          all(a.zustand == NEUPLANUNG for a in wieder),
          str([a.zustand for a in wieder]))

    # Ohne Modell findet die Neuplanung keinen anderen Weg. Der Zwischenstand
    # muss trotzdem erhalten sein - das ist der Punkt des Tests.
    stube2.abarbeiten(max_runden=20, frist=25)
    check("Die erste Notiz ist noch da (nichts wurde verloren)",
          (VAULT / "00_Inbox" / "AbsturzEins.md").exists())
    erledigt = [a for a in speicher.aufgaben_zum_ziel(ziel.id)
                if a.zustand == FERTIG]
    check("Der bereits erledigte Schritt blieb erledigt", len(erledigt) >= 1,
          f"{len(erledigt)} fertig")
    laeufe = speicher.laeufe(erledigt[0].id) if erledigt else []
    check("Die Telemetrie ueberlebte den Neustart", len(laeufe) >= 1,
          f"{len(laeufe)} Zeilen")
    speicher.schliessen()


def fall_parallel() -> None:
    """Zwei Arbeiter, keine Aufgabe doppelt - der Kern jeder Warteschlange."""
    speicher, tools, _, _, laeufer, stube = bauen("parallel")
    ziel, wurzel = laeufer.ziel_aufnehmen(
        "Drei unabhaengige Notizen anlegen",
        daten={"schritte": [
            {"beschreibung": f"Notiz P{n}", "werkzeug": "notiz_erstellen",
             "parameter": {"titel": f"Parallel{n}", "ordner": "00_Inbox"},
             "braucht": []} for n in (1, 2, 3)]})
    stube.runde()                       # zerlegen
    bereit = speicher.alle(zustand=BEREIT)
    check("Drei Schritte sind gleichzeitig bereit", len(bereit) == 3,
          f"{len(bereit)} bereit")

    gezogen = [speicher.beanspruchen(f"arbeiter-{n}") for n in (1, 2, 3, 4)]
    kennungen = [a.id for a in gezogen if a is not None]
    check("Jeder Arbeiter bekam eine andere Aufgabe",
          len(kennungen) == len(set(kennungen)) == 3, str(len(kennungen)))
    check("Der vierte ging leer aus", gezogen[3] is None)
    speicher.schliessen()


def fall_kontext() -> None:
    """§23: Der Kontext wird zusammengestellt und beschnitten, nicht geklebt."""
    from jarvis.kern.kontext import Kontextwerk

    speicher, _, _, _, laeufer, _ = bauen("kontext")
    ziel = speicher.ziel_anlegen("Marktanalyse erstellen",
                                 ergebnis="Eine Notiz liegt vor",
                                 bedingungen=["keine externen Quellen"])
    wurzel = speicher.anlegen("Wurzel", ziel_id=ziel.id, art=ART_ZIEL)
    erster = speicher.anlegen("Bestand durchsuchen", eltern_id=wurzel.id,
                              ziel_id=ziel.id, art=ART_SCHRITT,
                              daten={"nummer": 1, "generation": 1})
    speicher.aktualisieren(erster.id, ergebnis="12 Treffer: A, B, C")
    zweiter = speicher.anlegen("Uebersicht schreiben", eltern_id=wurzel.id,
                               ziel_id=ziel.id, art=ART_SCHRITT,
                               daten={"nummer": 2, "generation": 1,
                                      "erwartung": "Die Datei liegt da",
                                      "gescheiterte_aufrufe":
                                          ["notiz_lesen(name=Phantom)"]})
    speicher.aktualisieren(zweiter.id, fehler="Notiz 'Phantom' nicht gefunden")

    werk = Kontextwerk(speicher=speicher)
    kontext = werk.fuer_schritt(speicher.holen(zweiter.id), ziel)
    text = kontext.text()
    for name, marke in (("das Ziel", "Marktanalyse"),
                        ("der Schritt", "Uebersicht schreiben"),
                        ("die Erwartung", "Die Datei liegt da"),
                        ("die Randbedingung", "keine externen Quellen"),
                        ("das Ergebnis des Vorgaengers", "12 Treffer"),
                        ("der letzte Fehler", "Phantom")):
        check(f"Im Kontext steht {name}", marke in text, marke)

    # Der eigentliche Punkt: Bei knappem Platz faellt NACH RANG weg - und
    # das Wichtigste bleibt stehen. Genau das fehlte am 18.09., als Ollama
    # den zu langen Prompt VORNE abschnitt und die Anweisungen verlor.
    eng = werk.fuer_schritt(speicher.holen(zweiter.id), ziel, platz=140)
    check("Bei knappem Platz bleibt der Auftrag stehen",
          "Marktanalyse" in eng.text(), eng.bericht())
    check("Und das Weggelassene wird benannt", bool(eng.ausgelassen),
          ", ".join(eng.ausgelassen))
    check("Der Platz wird eingehalten",
          sum(b.laenge for b in eng.bloecke) <= eng.platz,
          f"{sum(b.laenge for b in eng.bloecke)}/{eng.platz}")
    check("Ohne Quellen wirft es nicht",
          Kontextwerk().fuer_schritt(speicher.holen(zweiter.id), None) is not None)
    speicher.schliessen()


def main() -> int:
    print("=== Die Agentenschleife von Ende zu Ende ===\n")
    for name, fall in (
        ("Ziel bis zum Abschluss", fall_ziel_bis_ende),
        ("Falscher Erfolg wird entlarvt", fall_falscher_erfolg),
        ("Werkzeugfehler fuehrt zur Neuplanung", fall_werkzeugfehler_und_neuplanung),
        ("Kein anderer Weg - ehrlich gescheitert", fall_ohne_alternative),
        ("Rechte halten wirklich auf", fall_rechte_greifen),
        ("Freigabe erteilt", fall_freigabe),
        ("Freigabe verweigert", fall_freigabe_abgelehnt),
        ("Idempotenz", fall_idempotenz),
        ("Budget haelt an statt abzubrechen", fall_budget),
        ("Absturz und Wiederaufnahme", fall_absturz),
        ("Zwei Arbeiter, keine Doppelarbeit", fall_parallel),
        ("Kontext wird zusammengestellt und beschnitten", fall_kontext),
    ):
        print(f"\n--- {name} ---")
        fall()

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"❌ {len(FAILURES)} Pruefung(en) rot:")
        for f in FAILURES:
            print(f"   - {f}")
        return 1
    print("✅ Die Agentenschleife laeuft Ende zu Ende.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
