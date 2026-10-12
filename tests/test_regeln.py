"""Tests zu den zwei gescheiterten Aufträgen vom 31.08. und zum Regelordner.

FALL 1 – „Erstelle eine Fertigkeit namens Aufgabenanalyse"
    Geplant wurde `notiz_erstellen(titel=Aufgabenanalyse)`, gescheitert an
    „Inhalt zu dünn". Ursache: Das Wort „Erstelle" löste den Schreibplaner
    aus, und weil `fertigkeit_definieren` nicht in PLANBARE_AKTIONEN steht,
    blieb als passendste Aktion notiz_erstellen übrig. Der Auftrag - einen
    ausführbaren ABLAUF anzulegen - ist nie angekommen.

FALL 2 – Notiz über eine SBIOS-Flash-Beschädigung
    „Zu wenige der genannten Notizen existieren ()" - mit leeren Klammern,
    weil es gar keine gab. Das Modell hatte an eine NEUE Notiz eine leere
    `quellen`-Liste gehängt; die Prüfung verlangte zwei auffindbare Notizen
    für jede Aktion mit diesem Feld. Josh wollte eine Notiz aus dem Text
    anlegen, den er selbst mitgeliefert hatte - gesucht wurde stattdessen
    nach Vorlagen, die es nie geben sollte.

Dazu der neue Regelordner: verbindliche Vorgaben in der Vault, für den Agenten
lesbar und schreibgeschützt.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_regeln_"))
VAULT = TMP / "Zeus"
VAULT.mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis import regeln as R  # noqa: E402
from jarvis.config import Settings  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.planner import (Operation, WritePlan,  # noqa: E402
                            looks_like_skill_request, looks_like_write_request)
from jarvis.tools import VaultTools  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


FERTIGKEIT = ("Erstelle eine Fertigkeit namens Aufgabenanalyse. Die Fertigkeit "
              "soll die Aufgabenliste durchgehen, die Aufgaben lesen und "
              "ausarbeiten.")
SBIOS = ("Erstelle unter Windows Problemen eine neue Datei, es geht um eine "
         "SBIOS Flash-Beschädigung bei einem Dell Optiplex 7010. Lösungsansatz: "
         "Flash-BIOS USB-Stick erstellen, die BIOS Update .exe in BIOS_ING.rcv "
         "umbenennen, dann Strg+Esc gedrückt halten und das Netzkabel einstecken.")


def main() -> int:
    cfg = Settings()

    # ============================== 1. Fertigkeit ≠ Notiz
    check("„Erstelle eine Fertigkeit …“ wird als Fertigkeitswunsch erkannt",
          looks_like_skill_request(FERTIGKEIT), "")
    check("Die SBIOS-Notiz gilt NICHT als Fertigkeitswunsch",
          not looks_like_skill_request(SBIOS))
    check("Aber weiterhin als Schreibauftrag", looks_like_write_request(SBIOS))

    for satz in ("Lege eine Fertigkeit an, die Notizen verknüpft",
                 "Baue mir einen Skill für die Wochenübersicht",
                 "definiere eine fähigkeit zum aufräumen"):
        check(f"„{satz[:38]}…“ ebenfalls", looks_like_skill_request(satz), satz)

    for satz in ("Welche Fertigkeiten hast du?",
                 "Wieso kannst du keine Fertigkeit anlegen?",
                 "Erstelle eine Notiz über Fertigkeiten im Sport"):
        check(f"„{satz[:38]}…“ NICHT", not looks_like_skill_request(satz), satz)

    # Der Prompt muss den Wunsch auch bedienen
    from jarvis.agent import JarvisAgent

    index = KnowledgeIndex(cfg, embeddings=stubs.StubEmbedding())
    index.sync()
    agent = JarvisAgent(cfg, index=index)
    agent._fertigkeit_gewuenscht = True
    prompt = agent.system_prompt("", with_tools=True)
    check("Der Prompt fordert dann den Werkzeugaufruf ein",
          "fertigkeit_definieren" in prompt and "in dieser Antwort" in prompt,
          "")
    check("Und untersagt ausdrücklich die Ersatz-Notiz",
          "keine Notiz an" in prompt, "")
    agent._fertigkeit_gewuenscht = False
    check("Ohne den Wunsch steht die Aufforderung nicht im Prompt",
          "in dieser Antwort" not in agent.system_prompt("", with_tools=True))

    # ============================== 2. Leere Quellenliste tötet den Plan nicht
    (VAULT / "IT Probleme" / "Windows").mkdir(parents=True, exist_ok=True)
    (VAULT / "IT Probleme" / "Windows" / "Windows-Fehler.md").write_text(
        "# Windows-Fehler\n\nSammlung.\n", encoding="utf-8")
    index.sync()
    tools = VaultTools(index, cfg)

    class FakeAgent:
        def __init__(self):
            self.tools = tools
            self.index = index
            self.structure = index.structure
            self.cfg = cfg

    from jarvis.planner import WritePlanner

    planer = WritePlanner(FakeAgent(), cfg)
    meldungen = []

    def melde(art, text):
        meldungen.append(text)

    # Genau der Fall: neue Notiz, leere Quellenliste
    plan = WritePlan(absicht="SBIOS", operationen=[
        Operation("notiz_erstellen", {"titel": "SBIOS Flash Beschädigung",
                                      "ordner": "IT Probleme/Windows",
                                      "quellen": []})])
    fehler = planer.namen_aufloesen(plan, melde)
    check("Eine leere Quellenliste lässt den Schritt NICHT scheitern",
          not fehler, str(fehler)[:120])
    check("Und wird stillschweigend entfernt",
          "quellen" not in plan.operationen[0].parameter,
          str(plan.operationen[0].parameter))

    # Nicht auffindbare Quellen an einer neuen Notiz: weglassen, nicht scheitern
    plan2 = WritePlan(absicht="SBIOS", operationen=[
        Operation("notiz_erstellen", {"titel": "SBIOS",
                                      "quellen": ["Gibt es nicht", "Auch nicht"]})])
    meldungen.clear()
    fehler = planer.namen_aufloesen(plan2, melde)
    check("Unauffindbare Quellen lassen eine NEUE Notiz nicht scheitern",
          not fehler, str(fehler)[:120])
    check("Sie werden entfernt", "quellen" not in plan2.operationen[0].parameter)
    check("Und es wird gesagt, dass ohne sie angelegt wird",
          any("ohne sie angelegt" in m for m in meldungen), str(meldungen))

    # Wo Quellen der Zweck sind, gilt die Forderung weiter
    plan3 = WritePlan(absicht="Zusammenführen", operationen=[
        Operation("notizen_zusammenfuehren", {"titel": "Sammel",
                                              "quellen": ["Gibt es nicht"]})])
    fehler = planer.namen_aufloesen(plan3, melde)
    check("notizen_zusammenfuehren verlangt weiterhin zwei echte Notizen",
          bool(fehler), str(fehler)[:100])
    check("Und die Meldung nennt das Werkzeug",
          "notizen_zusammenfuehren" in str(fehler), str(fehler)[:120])

    # ============================== 3. Regelordner
    check("Der Regelordner steht standardmäßig unter Schutz",
          cfg.regel_ordner.strip("/") in cfg.protected_list, str(cfg.protected_list))

    werk = R.laden(cfg)
    check("Ohne Dateien gibt es keine Regeln", not werk.vorhanden)
    check("Und der Prompt bleibt unverändert", R.promptblock(cfg) == "")
    check("Die Übersicht sagt, wie man anfängt",
          "anlegen" in werk.uebersicht(), werk.uebersicht()[:90])

    geschrieben = R.vorlage_schreiben(cfg)
    check("Die Startfassung lässt sich anlegen", len(geschrieben) >= 3,
          str([p.name for p in geschrieben]))
    check("Ein zweiter Aufruf überschreibt nichts",
          R.vorlage_schreiben(cfg) == [])
    check("Mit ueberschreiben=True schon",
          len(R.vorlage_schreiben(cfg, ueberschreiben=True)) >= 3)

    werk = R.laden(cfg)
    check("Die Regeln werden gelesen", werk.vorhanden and len(werk.dateien) >= 3,
          f"{len(werk.dateien)} Dateien")
    check("Nach Dateinamen sortiert – die Nummern bestimmen die Reihenfolge",
          [p.name for p in werk.dateien] == sorted(p.name for p in werk.dateien),
          str([p.name for p in werk.dateien]))
    check("Das Frontmatter kommt nicht mit in den Prompt",
          "titel:" not in werk.text and "tags:" not in werk.text, werk.text[:80])

    block = R.promptblock(cfg)
    check("Der Promptblock kennzeichnet die Regeln als verbindlich",
          "VERBINDLICHE ARBEITSREGELN" in block and "gehen allem anderen" in block)
    check("Die Regeln zu Werkzeugen stehen drin",
          "buchstabengetreu" in block, "")
    check("Und die zum Anlegen von Daten",
          "Neu heißt neu" in block or "Der mitgelieferte Text" in block, "")

    # Sie müssen auch wirklich im Systemprompt landen
    prompt = agent.system_prompt("", with_tools=True)
    check("Der Systemprompt enthält die Regeln",
          "VERBINDLICHE ARBEITSREGELN" in prompt, "")

    # Kürzung
    eng = Settings()
    eng.regel_max_zeichen = 800
    kurz = R.laden(eng)
    check("Zu lange Regeln werden gekürzt", kurz.gekuerzt, "")
    check("Und bleiben im Rahmen", kurz.zeichen <= 800, str(kurz.zeichen))
    check("Die Kürzung wird gemeldet, nicht verschwiegen",
          "gekürzt" in kurz.uebersicht(), kurz.uebersicht()[-120:])

    # ============================== 4. Schreibschutz greift wirklich
    index.sync()
    tools = VaultTools(index, cfg)
    regeldatei = cfg.regel_pfad / "10_Werkzeuge.md"
    vorher = regeldatei.read_text(encoding="utf-8")

    versuche = [
        ("notiz_ergaenzen", {"name": "10_Werkzeuge", "inhalt": "Neue Regel"}),
        ("notiz_ueberschreiben", {"name": "10_Werkzeuge", "inhalt": "Alles anders"}),
        ("notiz_loeschen", {"name": "10_Werkzeuge"}),
        ("tags_setzen", {"name": "10_Werkzeuge", "tags": ["egal"]}),
    ]
    durchgekommen = []
    for werkzeug, parameter in versuche:
        antwort = tools.ausfuehren(werkzeug, parameter)
        if antwort.ok:
            durchgekommen.append(werkzeug)
    check("KEIN schreibendes Werkzeug kommt in den Regelordner",
          not durchgekommen, str(durchgekommen))
    check("Die Regeldatei ist unverändert",
          regeldatei.read_text(encoding="utf-8") == vorher)

    neu_dort = tools.notiz_erstellen(titel="Eigene Regel", inhalt="x" * 200,
                                     ordner=cfg.regel_ordner)
    check("Auch eine neue Notiz im Regelordner wird abgelehnt",
          not neu_dort.ok, neu_dort.meldung[:90])
    check("Und die Meldung nennt den Grund",
          "geschützt" in neu_dort.meldung, neu_dort.meldung[:110])

    # Lesen muss weiter gehen
    gelesen = tools.notiz_lesen(name="10_Werkzeuge")
    check("Gelesen werden darf der Ordner uneingeschränkt",
          gelesen.ok and "buchstabengetreu" in str(gelesen.meldung) + str(gelesen.daten),
          gelesen.meldung[:60])

    # Woanders bleibt Schreiben erlaubt
    frei = tools.notiz_erstellen(titel="Irgendwo anders", inhalt="y" * 200,
                                 ordner="IT Probleme")
    check("Außerhalb des Regelordners wird weiterhin geschrieben",
          frei.ok, frei.meldung[:80])

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
