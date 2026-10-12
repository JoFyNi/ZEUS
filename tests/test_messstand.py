"""Tests für den Messstand und die Bewährung von Lektionen.

Anlass sind Joshs eigene Notizen (Teil 4, §4.3):

    "Ohne Messung weißt du nicht, ob dein Agent besser wird. Und ohne das zu
    wissen, ist die gesamte Selbstverbesserung Theater."
    "Oft ist sie gesunken, weil sich der Kontext mit mittelmäßigen Lektionen
    füllt und das Modell ablenkt."

Gebaut waren Schleife, Gedächtnis, Reflexion und Fertigkeiten - die Messung
dazwischen nicht. Geprüft wird hier:

  * Erfolgsbedingungen werden im CODE ausgewertet, nicht vom Modell.
  * Der Durchlauf arbeitet auf einer KOPIE - die echte Vault bleibt unberührt.
  * "ohne Gedächtnis" unterdrückt Lektionen wirklich, sonst misst A/B nichts.
  * Eine Lektion wird erst nach mehrfacher Bewährung bestätigt und nach
    mehrfachem Misserfolg verworfen (der Tiefpass aus §4.2).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_mess_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis import evaluation  # noqa: E402
from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.learning import (  # noqa: E402
    BESTAETIGT, VERWORFEN, VORLAEUFIG, Lektion,
)

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def build_vault() -> None:
    (VAULT / "Deep Learning").mkdir(parents=True)
    (VAULT / "Deep Learning" / "Backprop.md").write_text(
        "# Backprop\n\n## Kern\nDer transponierte Signalflussgraph.\n"
        "Siehe [[Neuron]].\n", encoding="utf-8")
    (VAULT / "Deep Learning" / "Neuron.md").write_text(
        "# Neuron\n\nGewichtete Summe mit Kennlinie.\n", encoding="utf-8")


AUFGABEN = """## A1 Struktur
frage: Welche Ordner gibt es?
pruefung: kein_fehlschlag:
pruefung: antwort_enthaelt: Deep Learning

## A2 Notiz anlegen
frage: Lege eine Notiz "Messprobe" an.
pruefung: notiz_existiert: Messprobe

## A3 Unerfüllbar
frage: Irgendetwas.
pruefung: notiz_existiert: Gibt_Es_Nie
"""


def main() -> int:
    build_vault()
    agent = JarvisAgent(index=KnowledgeIndex(embeddings=stubs.StubEmbedding()))
    agent.llm.chat = (lambda self, m, **k: "Ordner: Deep Learning.").__get__(agent.llm)
    agent.sync()

    # ------------------------------------------------- 1. Aufgabendatei lesen
    stand = evaluation.Messstand(agent, agent.cfg)
    stand.aufgabendatei.parent.mkdir(parents=True, exist_ok=True)
    stand.aufgabendatei.write_text(AUFGABEN, encoding="utf-8")

    aufgaben = evaluation.aufgaben_lesen(stand.aufgabendatei)
    check("Alle drei Aufgaben werden gelesen", len(aufgaben) == 3,
          ", ".join(a.kennung for a in aufgaben))
    check("Mehrere Bedingungen je Aufgabe", len(aufgaben[0].pruefungen) == 2,
          str(len(aufgaben[0].pruefungen)))
    check("Bedingung mit Pfeil wird zerlegt",
          evaluation._pruefung_lesen("ordner_ist: A -> B").wert == "B")

    # Ein Block ohne Bedingung ist keine Aufgabe – sonst gilt er als "erfüllt"
    ohne = evaluation.aufgaben_lesen_text if hasattr(evaluation, "aufgaben_lesen_text") else None
    datei = TMP / "ohne.md"
    datei.write_text("## X\nfrage: Nur eine Frage ohne Bedingung.\n", encoding="utf-8")
    check("Aufgabe ohne Bedingung wird verworfen",
          evaluation.aufgaben_lesen(datei) == [],
          str(evaluation.aufgaben_lesen(datei)))

    # ------------------------------------------- 2. Bedingungen im Code geprüft
    class FakeAntwort:
        def __init__(self, text="", calls=None, sources=None):
            self.text = text
            self.tool_calls = calls or []
            self._q = sources or []

        @property
        def sources(self):
            return self._q

    class FakeCall:
        def __init__(self, werkzeug, ok=True, meldung=""):
            self.werkzeug = werkzeug
            from jarvis.tools import ToolResult

            self.ergebnis = ToolResult(ok, meldung or werkzeug)

    P = evaluation.Pruefung
    ok, grund = evaluation.PRUEFUNGEN["notiz_existiert"](
        agent.tools, FakeAntwort(), None, P("notiz_existiert", "Backprop"))
    check("notiz_existiert findet die echte Notiz", ok, grund)

    ok, _ = evaluation.PRUEFUNGEN["notiz_existiert"](
        agent.tools, FakeAntwort(), None, P("notiz_existiert", "Gibt_Es_Nicht"))
    check("notiz_existiert meldet Fehlen", not ok)

    ok, grund = evaluation.PRUEFUNGEN["ordner_ist"](
        agent.tools, FakeAntwort(), None, P("ordner_ist", "Backprop", "Deep Learning"))
    check("ordner_ist prüft den Ordner", ok, grund)

    ok, _ = evaluation.PRUEFUNGEN["link_vorhanden"](
        agent.tools, FakeAntwort(), None, P("link_vorhanden", "Backprop", "Neuron"))
    check("link_vorhanden findet den Wikilink", ok)

    ok, _ = evaluation.PRUEFUNGEN["kein_fehlschlag"](
        agent.tools, FakeAntwort(calls=[FakeCall("notiz_erstellen", ok=False, meldung="kaputt")]),
        None, P("kein_fehlschlag"))
    check("kein_fehlschlag erkennt einen gescheiterten Aufruf", not ok)

    ok, _ = evaluation.PRUEFUNGEN["antwort_enthaelt_nicht"](
        agent.tools, FakeAntwort(text="Hier steht Quatsch"), None,
        P("antwort_enthaelt_nicht", "Quatsch"))
    check("antwort_enthaelt_nicht schlägt an", not ok)

    # ------------------------------------------------- 3. Bewertung einer Aufgabe
    ergebnis = stand._bewerten(agent, aufgaben[0],
                               FakeAntwort(text="Ordner: Deep Learning."), 1.0)
    check("Erfüllte Aufgabe gilt als geglückt", ergebnis.geglueckt,
          "; ".join(ergebnis.verfehlt))
    ergebnis = stand._bewerten(agent, aufgaben[2], FakeAntwort(), 1.0)
    check("Unerfüllbare Aufgabe gilt als gescheitert", not ergebnis.geglueckt)
    check("Der Grund wird konkret genannt",
          bool(ergebnis.verfehlt) and "Gibt_Es_Nie" in ergebnis.verfehlt[0],
          "; ".join(ergebnis.verfehlt))

    # ---------------------------------------- 4. Der Lauf fasst die Vault nicht an
    vorher = sorted(p.name for p in VAULT.rglob("*.md"))
    laeufe = stand.messen(varianten=["mit"], grenze=2, fortschritt=lambda t: None)
    nachher = sorted(p.name for p in VAULT.rglob("*.md"))
    check("Ein Messlauf verändert die echte Vault nicht", vorher == nachher,
          f"{len(vorher)} → {len(nachher)}")
    check("Der Lauf liefert Ergebnisse",
          bool(laeufe) and len(laeufe["mit"].ergebnisse) == 2,
          str(len(laeufe["mit"].ergebnisse)) if laeufe else "keine")

    # -------------------------------------- 5. "ohne Gedächtnis" wirkt wirklich
    agent.lernwerk.lektion_speichern("Immer zuerst die Struktur prüfen.")
    agent.gedaechtnis_aus = False
    mit_prompt = agent.system_prompt(with_tools=True, frage="Struktur prüfen")
    agent.gedaechtnis_aus = True
    ohne_prompt = agent.system_prompt(with_tools=True, frage="Struktur prüfen")
    agent.gedaechtnis_aus = False
    check("Mit Gedächtnis stehen die Regeln im Prompt", "GELERNTE REGELN" in mit_prompt)
    check("Ohne Gedächtnis stehen sie NICHT im Prompt",
          "GELERNTE REGELN" not in ohne_prompt)
    check("Ohne Gedächtnis fehlt auch die Fertigkeiten-Anleitung",
          "fertigkeit_definieren" not in ohne_prompt)

    # ------------------------------------------------ 6. Urteil ist unbestechlich
    from jarvis.evaluation import PRUEFUNGEN, Durchlauf, Ergebnis

    def lauf(name, geglueckt_bis, anzahl=20, lektionen=2):
        """Ein Durchlauf MIT vermerkten Prompt-Daten.

        Ohne 'lektionen' wären beide Varianten wortgleich gewesen - dann darf
        das Urteil gar keine Aussage über das Gedächtnis treffen. Genau das
        prüft Abschnitt 6b.
        """
        return Durchlauf(name, [
            Ergebnis(f"A{i}", i < geglueckt_bis,
                     kontext={"lektionen": lektionen, "kontext_zeichen": 3000})
            for i in range(anzahl)])

    besser = lauf("mit", 16)
    schlechter = lauf("ohne", 10)
    urteil = stand._urteil(besser, schlechter)
    check("Ein Gewinn wird als Gewinn benannt", "helfen" in urteil, urteil[:80])

    urteil = stand._urteil(schlechter, besser)
    check("Ein Verlust wird als Verlust benannt", "schaden" in urteil, urteil[:80])
    check("Und nennt die richtige Gegenmaßnahme", "Abrufqualität" in urteil)

    gleich = lauf("ohne", 16)
    urteil = stand._urteil(besser, gleich)
    check("Kein Unterschied wird nicht schöngeredet", "Kein messbarer" in urteil,
          urteil[:80])

    klein = Durchlauf("mit", [Ergebnis("A1", True, kontext={"lektionen": 1}),
                              Ergebnis("A2", False, kontext={"lektionen": 1})])
    urteil = stand._urteil(klein, Durchlauf("ohne", [Ergebnis("A1", False),
                                                     Ergebnis("A2", False)]))
    check("Bei zu wenigen Aufgaben steht ein Vorbehalt dabei", "Vorbehalt" in urteil)

    # ------------------------------- 6b. Der Fall, der die Messung leerlaufen ließ
    # Bei Josh lieferten "mit" und "ohne" praktisch dieselben Zahlen. Der Grund
    # war nicht, dass das Gedächtnis nichts bringt - sondern dass gar keine
    # Regel in den Prompt kam. Dann sind beide Prompts identisch und die Quoten
    # MÜSSEN gleich sein. Das als "kein messbarer Unterschied" zu verkaufen,
    # wäre eine Aussage über etwas, das nie stattgefunden hat.
    leer_mit = lauf("mit", 16, lektionen=0)
    leer_ohne = lauf("ohne", 16, lektionen=0)
    urteil = stand._urteil(leer_mit, leer_ohne)
    check("Ohne eingespielte Regeln wird KEINE Wirkung behauptet",
          "nichts verglichen" in urteil.lower(), urteil[:100])
    check("Und es steht dabei, woran das liegen kann",
          "zu wenige Lektionen" in urteil or "Abruf findet sie nicht" in urteil)
    check("Der irreführende Satz taucht dort NICHT auf",
          "Kein messbarer" not in urteil)

    # Die Kontrollgruppe: Prüfen die Aufgaben überhaupt das Wissen der Vault?
    blind_gut = lauf("blind", 15)          # blind fast so gut wie mit Zugriff
    urteil = stand._urteil(besser, schlechter, blind_gut)
    check("Wenn 'blind' fast gleich gut ist, warnt das Urteil",
          "Warnung zur Aussagekraft" in urteil, urteil[:100])

    blind_schlecht = lauf("blind", 2)
    urteil = stand._urteil(besser, schlechter, blind_schlecht)
    check("Wenn 'blind' klar schlechter ist, gilt der Abruf als belegt",
          "prüfen tatsächlich Wissen" in urteil, urteil[:100])
    check("Und die eigentliche Aussage steht trotzdem da", "helfen" in urteil)

    # ------------------- 6c. Streuung: der Fall zweier gegenteiliger Urteile
    # Josh maß denselben Aufgabensatz zweimal. Einmal kam "Regeln schaden -20",
    # einmal "Regeln helfen +40" heraus. Beide Berichte lasen sich gleich
    # überzeugend. Wenn dieselbe Variante zwischen ihren Runden stärker
    # schwankt als der Unterschied ZWISCHEN den Varianten, misst man die
    # Schwankung - und das muss dastehen statt einer Zahl.
    def mehrrunden(name, quoten, lektionen=2):
        runden = [lauf(name, int(q * 20), 20, lektionen) for q in quoten]
        kopf = runden[0]
        kopf.runden = runden
        return kopf

    schwankend_mit = mehrrunden("mit", [0.6, 1.0, 0.8])     # 60/100/80 %
    schwankend_ohne = mehrrunden("ohne", [0.8, 0.6, 0.9])
    check("Die Streuung wird als Spannweite berechnet",
          schwankend_mit.streuung == 40.0, str(schwankend_mit.streuung))
    check("Und der Mittelwert über alle Runden",
          abs(schwankend_mit.mittlere_quote - 0.8) < 0.01,
          str(schwankend_mit.mittlere_quote))

    urteil = stand._urteil(schwankend_mit, schwankend_ohne)
    check("Ist die Streuung größer als der Effekt, wird nichts behauptet",
          "verschwindet in der Streuung" in urteil, urteil[:140])
    check("Weder Nutzen noch Schaden werden dann benannt",
          "helfen:" not in urteil and "schaden:" not in urteil)
    check("Stattdessen wird auf mehr AUFGABEN verwiesen, nicht mehr Runden",
          "Mehr Aufgaben helfen hier mehr" in urteil)

    # Ein Effekt, der die Streuung deutlich übersteigt, gilt weiter
    stabil_mit = mehrrunden("mit", [0.95, 1.0, 0.95])
    stabil_ohne = mehrrunden("ohne", [0.3, 0.35, 0.3])
    urteil = stand._urteil(stabil_mit, stabil_ohne)
    check("Ein Effekt deutlich über der Streuung wird weiterhin benannt",
          "helfen" in urteil and "verschwindet" not in urteil, urteil[:140])

    # Ohne Runden: bei kleinem Unterschied auf die Wiederholung hinweisen
    knapp_mit = lauf("mit", 11, 20)      # 55 %
    knapp_ohne = lauf("ohne", 10, 20)    # 50 %
    urteil = stand._urteil(knapp_mit, knapp_ohne)
    check("Ein knapper Unterschied ohne Wiederholung wird als solcher markiert",
          "3x" in urteil, urteil[:200])

    # ------------------- 6d. Gruppen: WO es hakt, nicht nur DASS
    gemischt = Durchlauf("mit", [
        Ergebnis("A1", True), Ergebnis("A2", True), Ergebnis("A3", True),
        Ergebnis("D1", False), Ergebnis("D2", False), Ergebnis("D3", False),
        Ergebnis("E1", True),
    ])
    gruppen = gemischt.nach_gruppe()
    check("Ergebnisse werden nach Aufgabengruppe aufgeteilt",
          gruppen == {"A": (3, 3), "D": (0, 3), "E": (1, 1)}, str(gruppen))

    text = stand.bericht({"mit": gemischt})
    check("Der Bericht zeigt, in welcher Gruppe es hakt",
          "### Wo es hakt" in text and "| D | 0/3 |" in text,
          [z for z in text.splitlines() if z.startswith("| D")][:1])
    check("Und erklärt, was ein Ausfall in D bedeutet",
          "Kettenlänge" in text)

    # Die mitgelieferte Vorlage muss den Anspruch auch einlösen
    import tempfile as _tf
    from jarvis.evaluation import VORLAGE, aufgaben_lesen as _lesen

    vorlage_pfad = Path(_tf.mkdtemp()) / "Pruefaufgaben.md"
    vorlage_pfad.write_text(VORLAGE, encoding="utf-8")
    aus_vorlage = _lesen(vorlage_pfad)
    check("Die Vorlage enthält mindestens 20 Aufgaben",
          len(aus_vorlage) >= 20, f"{len(aus_vorlage)}")
    marken = {a.kennung[0] for a in aus_vorlage}
    check("Und deckt alle fünf Gruppen ab",
          marken >= {"A", "B", "C", "D", "E"}, str(sorted(marken)))
    check("Jede Aufgabe hat mindestens eine prüfbare Bedingung",
          all(a.pruefungen for a in aus_vorlage))
    bekannt = set(PRUEFUNGEN)
    unbekannt = [p.art for a in aus_vorlage for p in a.pruefungen if p.art not in bekannt]
    check("Alle verwendeten Bedingungen sind implementiert",
          not unbekannt, str(unbekannt))

    # Zählwerte aus den Einzelergebnissen
    check("Regeln im Prompt werden aufsummiert", besser.lektionen_gesamt == 40,
          str(besser.lektionen_gesamt))
    check("Kontextlänge wird aufsummiert", besser.kontext_zeichen == 60000,
          str(besser.kontext_zeichen))

    unterschiede = stand._unterschiede(besser, schlechter)
    check("Umschläge zwischen den Varianten werden benannt", len(unterschiede) == 6,
          str(len(unterschiede)))

    # ------------------------------------- 7. Lektionen: Bewährung statt Glauben
    eintrag = Lektion(text="Test")
    check("Neue Lektion ist zunächst vorläufig", eintrag.status == VORLAEUFIG)
    for _ in range(3):
        eintrag.verwendet += 1
        eintrag.bewaehrt += 1
    eintrag.bewerten()
    check("Nach dreimaliger Bewährung: bestätigt", eintrag.status == BESTAETIGT,
          f"{eintrag.bewaehrt}/{eintrag.verwendet} → {eintrag.status}")

    schlecht = Lektion(text="Schlecht")
    for _ in range(5):
        schlecht.verwendet += 1
    schlecht.bewerten()
    check("Nach fünf Fehlschlägen: verworfen", schlecht.status == VERWORFEN,
          f"{schlecht.bewaehrt}/{schlecht.verwendet} → {schlecht.status}")

    einmal = Lektion(text="Einmal")
    einmal.verwendet, einmal.bewaehrt = 1, 1
    einmal.bewerten()
    check("Ein einzelner Erfolg macht noch keine Regel",
          einmal.status == VORLAEUFIG,
          "sonst wäre die Lernrate zu hoch")

    # Zeilenformat: neu schreiben und wieder einlesen
    zeile = eintrag.als_zeile()
    zurueck = Lektion.aus_zeile(zeile)
    check("Zähler überleben das Schreiben und Lesen",
          zurueck.status == BESTAETIGT and zurueck.verwendet == 3 and zurueck.bewaehrt == 3,
          zeile)
    alt = Lektion.aus_zeile("- Alte Regel  <!-- 2026-01-01 12:00 -->")
    check("Alte Zeilen ohne Zähler bleiben lesbar",
          alt.text == "Alte Regel" and alt.status == VORLAEUFIG, alt.text)

    # Verworfene tauchen nicht mehr im Prompt auf
    agent.lernwerk._lektionen_cache = [
        Lektion("Gute Regel", BESTAETIGT, 5, 5),
        Lektion("Schlechte Regel", VERWORFEN, 6, 1),
        Lektion("Neue Regel", VORLAEUFIG, 1, 1),
    ]
    ausgewaehlt = agent.lernwerk.relevante_lektionen("Regel", anzahl=5)
    check("Verworfene Lektion kommt nicht in den Prompt",
          "Schlechte Regel" not in ausgewaehlt, str(ausgewaehlt))
    check("Bestätigte steht vor vorläufiger",
          ausgewaehlt and ausgewaehlt[0] == "Gute Regel", str(ausgewaehlt))

    # Bewertung nach der Aufgabe schreibt die Zähler fort
    agent.lernwerk.lektionen_bewerten(geglueckt=True)
    bilanz = {e.text: e for e in agent.lernwerk.lektionen_voll()}
    check("Der Einsatz wird gezählt",
          bilanz["Neue Regel"].verwendet == 2 and bilanz["Neue Regel"].bewaehrt == 2,
          f"{bilanz['Neue Regel'].bewaehrt}/{bilanz['Neue Regel'].verwendet}")
    check("Die verworfene Regel bleibt unangetastet",
          bilanz["Schlechte Regel"].verwendet == 6)

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
