"""Tests gegen die Befunde aus Joshs Lauf mit `prompts/AAA.txt` (25.08.).

Vier Fehler, alle in derselben Kategorie: Der Lauf meldet Erfolg, und das
Ergebnis stimmt trotzdem nicht.

1. FALSCHE NOTIZ VERKNÜPFT. „ARVIS Architektur Overview" wurde stillschweigend
   auf die bestehende Notiz „Architektur" zurückgeführt, weil die
   Wortüberlappung durch das KÜRZERE der beiden Namen teilte - ein einziges
   gemeinsames Wort ergab damit den Wert 1,00. Drei Verknüpfungen landeten an
   einer Notiz, die damit nichts zu tun hatte. Sichtbar war das nur in einer
   beiläufigen Protokollzeile.

2. "INHALT ZU DÜNN". Zu „Tool Spezifikation sps generator" stand weder in der
   Vault noch im Modellwissen etwas - wohl aber im Auftrag selbst, den Josh
   eingefügt hatte. Dieser Text wurde nie an die Inhaltserzeugung gereicht.

3. WERKZEUGAUFRUF ALS PROSA. Das Modell schrieb
   `notiz_lesen(name="…")` als Antworttext. Der Aufruf wurde nicht getätigt,
   die Zeile Quelltext dem Nutzer als Antwort vorgesetzt.

4. ANZEIGE SCHNITT NAMEN AB. Im Protokoll stand
   `nach=Lauffähiger_Code_für_Agentenarchitekture` - ein fehlendes 'n', das
   wie ein Tippfehler des Modells aussah und keiner war.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_plan_"))
VAULT = TMP / "Zeus"
CODE = TMP / "code"
CODE.mkdir(parents=True)
VAULT.mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["CODE_ROOTS"] = str(CODE)
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.agent import _aufruf_aus_text  # noqa: E402
from jarvis.config import Settings  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.planner import Operation, WritePlan, WritePlanner  # noqa: E402
from jarvis.tools import VaultTools  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


# Joshs Auftrag, gekürzt auf das Wesentliche - lang genug, dass der
# Ausschnitt-Mechanismus greifen muss.
AUFTRAG = """# JARVIS: Autonome Agenten-Architektur (Plan -> Execute -> Reflect)

Du baust ein Agenten-Framework, in dem das LLM als Entscheidungsinstanz
fungiert und eine strukturierte Aufgabenliste als Gedaechtnis dient.

## 1. Architektur-Ueberblick
Das LLM denkt nicht in einer einzelnen Antwort, sondern in Schritten. Jeder
Schritt ist eine kleine Entscheidung, die auf der aktuellen Aufgabenliste
basiert.
""" + "\n".join(f"Fuellzeile {i} ohne besonderen Inhalt." for i in range(80)) + """

## Tool Spezifikation sps generator
Der sps_generator erzeugt aus einer Bauteilliste ein SPS-Programm nach
IEC 61131-3. Eingang ist eine CSV mit Sensoren und Aktoren, Ausgang ist
Structured Text. Abhaengigkeit: pdf_parser liest die Spezifikationen.
Priorisierung hoch, weil drei aktive Projekte darauf warten.
Fehlerbehandlung: unbekannte Bauteiltypen werden als Kommentar markiert,
nicht stillschweigend uebersprungen.
""" + "\n".join(f"Nachspann {i}." for i in range(40))


def aufbauen():
    (VAULT / "Wissen").mkdir(parents=True, exist_ok=True)
    (VAULT / "Wissen" / "Architektur.md").write_text(
        "# Architektur\n\nAufbau des Agenten: Schleife, Werkzeuge, Gedächtnis.\n",
        encoding="utf-8")
    (VAULT / "Wissen" / "Lernende_Agenten.md").write_text(
        "# Lernende Agenten\n\nAgenten, die aus Rückmeldung lernen.\n",
        encoding="utf-8")
    index = KnowledgeIndex(Settings(), embeddings=stubs.StubEmbedding())
    index.sync()
    return VaultTools(index, Settings())


class FakeLLM:
    """Merkt sich den letzten Prompt - darum geht es beim Inhaltstest."""

    def __init__(self, antwort: str = "## Kurzfassung\nText.\n"):
        self.antwort = antwort
        self.prompts: list = []

    def chat(self, messages, **kw):
        self.prompts.append(messages[-1]["content"])
        return self.antwort


class FakeAgent:
    def __init__(self, tools, llm):
        self.tools = tools
        self.llm = llm
        self.index = tools.index
        self.structure = tools.index.structure
        self.cfg = tools.cfg


def main() -> int:
    tools = aufbauen()
    cfg = Settings()

    # ============ 1. Namensauflösung: ein Wort ist keine Identifikation
    # Der Aufbau ist zweistufig und soll es bleiben: LESEND darf großzügig
    # aufgelöst werden - ein ungefährer Treffer ist besser als keiner. Was
    # zählt, ist die EINSTUFUNG: Ein solcher Treffer muss als unsicher
    # markiert sein, damit Schreibzugriffe ihn ablehnen können.
    treffer = tools.resolve_note("ARVIS Architektur Overview")
    stufe = tools.letzte_aufloesung
    check("Ein Treffer über ein einzelnes Wort gilt als UNSICHER",
          treffer is None or stufe in tools.UNSICHERE_STUFEN,
          f"→ {treffer.stem if treffer else 'nichts'} ({stufe})")
    check("Die Wortüberlappung allein trägt ihn nicht mehr",
          stufe != "wortueberlappung", stufe)
    check("Und die Buchstabenähnlichkeit auch nicht",
          stufe != "tippfehler", stufe)

    from jarvis.vault import similarity

    check("Ein Einwortname ist keine Teilmengen-Übereinstimmung mehr",
          similarity("ARVIS Architektur Overview", "Architektur") < 0.74,
          f"{similarity('ARVIS Architektur Overview', 'Architektur'):.2f}")
    check("Zwei Wörter in einem längeren Namen zählen weiterhin",
          similarity("deep learning", "01 skript deep learning") >= 0.85,
          f"{similarity('deep learning', '01 skript deep learning'):.2f}")
    check("Ein echter Tippfehler wird weiterhin erkannt",
          similarity("Depp Learning", "Deep Learning") >= 0.85)

    check("Der exakte Name wird weiterhin gefunden",
          (tools.resolve_note("Architektur") or None) is not None
          and tools.resolve_note("Architektur").stem == "Architektur")
    check("Unterstriche stören weiterhin nicht",
          (tools.resolve_note("Lernende Agenten") or None) is not None
          and tools.resolve_note("Lernende Agenten").stem == "Lernende_Agenten")

    # ============ 2. Verknüpfen verweigert eine geratene Notiz
    ergebnis = tools.notizen_verknuepfen(von="ARVIS Architektur Overview",
                                         nach="Lernende Agenten")
    check("Verknüpfen mit geratenem Namen wird abgelehnt",
          not ergebnis.ok, ergebnis.meldung[:100])
    check("Die Ablehnung nennt den Namen, der gemeint sein könnte",
          "Architektur" in ergebnis.meldung
          or bool(ergebnis.daten.get("gemeint_vielleicht")),
          str(ergebnis.daten)[:120])
    inhalt = (VAULT / "Wissen" / "Architektur.md").read_text(encoding="utf-8")
    check("Und die fremde Notiz bleibt unangetastet",
          "Lernende" not in inhalt, inhalt[:80])

    check("Zwei buchstabengetreue Namen lassen sich weiterhin verknüpfen",
          tools.notizen_verknuepfen(von="Architektur",
                                    nach="Lernende_Agenten").ok)

    # Die Sperre sitzt zentral in ausfuehren() - sie muss deshalb für JEDES
    # schreibende Werkzeug gelten, nicht nur für die zwei, an denen der Fehler
    # auffiel. Sonst fehlt sie beim nächsten Werkzeug, das jemand hinzufügt.
    from jarvis.tools import SCHREIBENDE_WERKZEUGE

    ungeschuetzt = []
    for werkzeug in ("notiz_ergaenzen", "notiz_loeschen", "tags_setzen",
                     "notiz_verschieben", "abschnitt_ersetzen",
                     "frontmatter_setzen", "codeblock_hinzufuegen"):
        antwort = tools.ausfuehren(werkzeug, {"name": "ARVIS Architektur Overview",
                                              "inhalt": "x", "text": "x", "tags": ["a"],
                                              "ziel_ordner": "Wissen", "code": "x",
                                              "ueberschrift": "K", "schluessel": "a",
                                              "wert": "b"})
        if antwort.ok or "ungefähr" not in antwort.meldung:
            ungeschuetzt.append(f"{werkzeug}: {antwort.meldung[:60]}")
    check("Jedes schreibende Werkzeug lehnt den geratenen Namen ab",
          not ungeschuetzt, "; ".join(ungeschuetzt)[:200])

    check("Ein LESENDES Werkzeug darf den ungefähren Treffer weiterhin nutzen",
          tools.ausfuehren("notiz_lesen", {"name": "ARVIS Architektur Overview"}).ok)

    check("Die Prüfung greift bei allen Schreibwerkzeugen mit Namensparameter",
          all(w in SCHREIBENDE_WERKZEUGE
              for w in ("notiz_ergaenzen", "tags_setzen", "notizen_verknuepfen")))

    # Ein exakt benanntes Ziel bleibt beschreibbar
    check("Der exakte Name wird weiterhin geschrieben",
          tools.ausfuehren("tags_setzen",
                           {"name": "Architektur", "tags": ["test"]}).ok)

    # ============ 3. Der Auftragstext dient als Quellenmaterial
    llm = FakeLLM()
    planer = WritePlanner(FakeAgent(tools, llm), cfg)
    plan = WritePlan(
        absicht="Notizen zur Agentenarchitektur anlegen",
        operationen=[Operation("notiz_erstellen",
                               {"titel": "Tool Spezifikation sps generator"})],
        auftragstext=AUFTRAG,
    )
    planer._inhalt_erzeugen(plan.operationen[0], plan)
    prompt = llm.prompts[-1]
    check("Der Originalauftrag landet im Inhalts-Prompt",
          "VORGABEN GEMACHT" in prompt, prompt[:120])
    check("Und zwar der ABSCHNITT zum Thema, nicht der ganze Text",
          "IEC 61131-3" in prompt and "Fuellzeile 5 " not in prompt,
          f"{len(prompt)} Zeichen")
    check("Die Vorgaben haben ausdrücklich Vorrang vor dem Modellwissen",
          "VORRANG" in prompt)

    # Ohne Auftragstext darf nichts erfunden werden
    llm.prompts.clear()
    ohne = WritePlan(absicht="x", operationen=list(plan.operationen))
    planer._inhalt_erzeugen(ohne.operationen[0], ohne)
    check("Ohne Auftragstext steht auch keine Vorgabe im Prompt",
          "VORGABEN GEMACHT" not in llm.prompts[-1])

    # Der Ausschnitt selbst
    kurz = planer._auftragsausschnitt(AUFTRAG, "Tool Spezifikation sps generator")
    check("Der Ausschnitt trifft die richtige Stelle",
          "sps_generator" in kurz, kurz[:80])
    check("Der Ausschnitt bleibt im Rahmen", len(kurz) <= 3000, str(len(kurz)))
    check("Ein kurzer Auftrag wird unverändert durchgereicht",
          planer._auftragsausschnitt("Lege eine Notiz an.", "Notiz")
          == "Lege eine Notiz an.")
    check("Ohne Auftragstext kommt nichts zurück",
          planer._auftragsausschnitt("", "Thema") == "")

    # ============ 4. Als Prosa geschriebener Werkzeugaufruf
    erkannt = _aufruf_aus_text('notiz_lesen(name="_System/Code/Pruefungen/Code-Prüfung")')
    check("Ein hingeschriebener Aufruf wird erkannt",
          erkannt is not None and erkannt[0] == "notiz_lesen", str(erkannt))
    check("Und die Parameter werden gelesen",
          erkannt is not None
          and erkannt[1].get("name") == "_System/Code/Pruefungen/Code-Prüfung",
          str(erkannt))
    check("Auch in Backticks",
          _aufruf_aus_text('`notiz_lesen(name="X")`') is not None)
    check("Mehrere Parameter",
          (_aufruf_aus_text('notiz_erstellen(titel="A", ordner="B")') or (None, {}))[1]
          == {"titel": "A", "ordner": "B"})

    # Was NICHT ausgeführt werden darf
    check("Ein Aufruf mitten im Fließtext wird NICHT ausgeführt",
          _aufruf_aus_text(
              'Dafür nimmst du notiz_lesen(name="X") und danach weiter.') is None)
    check("Ein unbekanntes Werkzeug wird nicht ausgeführt",
          _aufruf_aus_text('rm_rf(pfad="/")') is None)
    check("Prosa ohne Aufruf bleibt Prosa",
          _aufruf_aus_text("Ich habe die Notiz gelesen.") is None)
    check("Ein Aufruf ohne Parameter zählt nicht",
          _aufruf_aus_text("struktur_anzeigen()") is None)
    check("Mehrzeiliger Text wird nicht ausgeführt",
          _aufruf_aus_text('notiz_lesen(name="X")\nund noch etwas') is None)

    # ============ 5. Anzeige schneidet sichtbar ab
    lang = Operation("notizen_verknuepfen",
                     {"von": "Architektur",
                      "nach": "Lauffähiger_Code_für_Agentenarchitekturen"})
    text = lang.beschreibung()
    check("Ein abgeschnittener Name wird als abgeschnitten markiert",
          "…" in text, text)
    check("Ein kurzer Name bleibt ohne Zusatz",
          "…" not in Operation("ordner_anlegen", {"pfad": "_System"}).beschreibung())

    # ============ 6. Offene Punkte: prüfbar statt "nicht selbst prüfbar"
    from jarvis.aufgaben import NACHPRUEFUNG, Aufgabe

    check("quellcode_lesen ist nachprüfbar", "quellcode_lesen" in NACHPRUEFUNG)
    check("notizen_zusammenfuehren ist nachprüfbar",
          "notizen_zusammenfuehren" in NACHPRUEFUNG)

    (CODE / "codereview.py").write_text("# Prüfung\n", encoding="utf-8")
    tools.cfg.code_roots = str(CODE)
    falsch = Aufgabe(schluessel="quellcode_lesen:agent/codereview.py",
                     werkzeug="quellcode_lesen", ziel="agent/codereview.py",
                     beschreibung="quellcode lesen")
    ok, beleg = NACHPRUEFUNG["quellcode_lesen"](tools, falsch)
    check("Ein falscher Pfad wird nicht als erledigt abgehakt", not ok, beleg[:90])
    check("Aber der richtige Pfad wird vorgeschlagen",
          "codereview.py" in beleg and str(CODE) in beleg, beleg[:140])

    richtig = Aufgabe(schluessel="quellcode_lesen:codereview.py",
                      werkzeug="quellcode_lesen", ziel="codereview.py",
                      beschreibung="quellcode lesen")
    ok, beleg = NACHPRUEFUNG["quellcode_lesen"](tools, richtig)
    check("Ein lesbarer Pfad wird abgehakt", ok, beleg[:90])

    fehlt = Aufgabe(schluessel="quellcode_lesen:gibtsnicht.py",
                    werkzeug="quellcode_lesen", ziel="gibtsnicht.py",
                    beschreibung="quellcode lesen")
    ok, beleg = NACHPRUEFUNG["quellcode_lesen"](tools, fehlt)
    check("Fehlt die Datei überall, wird CODE_ROOTS genannt",
          not ok and "CODE_ROOTS" in beleg, beleg[:120])

    unvollstaendig = Aufgabe(schluessel="notizen_zusammenfuehren",
                             werkzeug="notizen_zusammenfuehren", ziel="",
                             beschreibung="Dubletten zusammenführen")
    ok, beleg = NACHPRUEFUNG["notizen_zusammenfuehren"](tools, unvollstaendig)
    check("Ein Aufruf ohne zwei Namen wird als solcher benannt",
          not ok and "notizen_suchen" in beleg, beleg[:120])

    getrennt = Aufgabe(schluessel="notizen_zusammenfuehren:architektur>lernende_agenten",
                       werkzeug="notizen_zusammenfuehren", ziel="Architektur",
                       beschreibung="Dubletten zusammenführen")
    ok, beleg = NACHPRUEFUNG["notizen_zusammenfuehren"](tools, getrennt)
    check("Zwei getrennte Notizen gelten nicht als zusammengeführt",
          not ok, beleg[:90])

    (VAULT / "Wissen" / "Lernende_Agenten.md").unlink()
    tools.index.sync()
    ok, beleg = NACHPRUEFUNG["notizen_zusammenfuehren"](tools, getrennt)
    check("Ist eine der beiden weg, gilt der Punkt als erledigt", ok, beleg[:90])

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
