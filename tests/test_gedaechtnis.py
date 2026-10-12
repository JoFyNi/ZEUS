"""Tests für das Kurzzeitgedächtnis: kommt der Gesprächsverlauf beim Modell an?

Anlass ist Joshs Terminal-Mitschnitt vom 27.08.: Nach drei Runden über die
Notiz USB_Stick_Konfiguration antwortete Zeus auf "okay, dein Plan sieht gut
aus, passe die Datei so an" mit "das hier ist für mich der erste Satz unserer
Unterhaltung". Eine Runde später durchsuchte er die VAULT nach dem, worüber
gerade geredet worden war.

Vier Ursachen, jede hier abgesichert:

1. Der System-Prompt erwähnte den Gesprächsverlauf mit keinem Wort, nannte
   aber die Vault "dein Gedächtnis" und schrieb vor, "auf Basis der Notizen"
   zu antworten. Ein 14B-Modell nimmt das wörtlich und sucht Gesagtes in
   Notizen statt im Verlauf über der Frage.
2. Der Planer (jeder erkannte Schreibauftrag) bekam den Verlauf überhaupt
   nicht - "passe die Datei so an" benennt keine Datei, die vorige Runde tut es.
3. Das Prompt-Budget galt nur für den System-Prompt; der Verlauf wurde
   ungezählt angehängt. Gemessen am echten Bestand: 15 547 Zeichen
   System-Prompt bei einem Budget von 14 000 - der Rest fiel bei Ollama
   still aus dem Fenster.
4. notiz_lesen meldete "Geändert: …", weil es den Pfad zurückgibt - eine
   Änderungsmeldung für einen reinen Lesevorgang.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_gedaechtnis_"))
VAULT = TMP / "Zeus"
VAULT.mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.agent import Answer, JarvisAgent, ToolCallRecord  # noqa: E402
from jarvis.config import settings as cfg  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.planner import _verlauf_als_text  # noqa: E402
from jarvis.tools import ToolResult  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def main() -> int:
    (VAULT / "USB_Stick_Konfiguration.md").write_text(
        "# USB Stick Konfiguration\n\nTreiber, Boot-Stick, Tastatur-Setup.\n", encoding="utf-8")
    (VAULT / "Agenten.md").write_text("# Agenten\n\nLernfähige Agenten.\n", encoding="utf-8")

    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.sync()

    # ================================ 1. System-Prompt nennt den Verlauf
    sp = agent.system_prompt("Kontext", with_tools=True, frage="test")
    check("System-Prompt erklärt den Gesprächsverlauf", "GESPRÄCHSVERLAUF" in sp)
    check("System-Prompt verbietet die Vault-Suche nach eben Gesagtem",
          "NIEMALS in der Vault" in sp or "Suche NIEMALS" in sp)
    check("System-Prompt verbietet 'das ist die erste Nachricht'",
          "erste Nachricht" in sp)

    # ============================ 2. Verlauf erreicht das Modell ab Runde 2
    gesehen = []

    def fake_chat(self, messages, stream_cb=None, temperature=None,
                  format_json=False, **kw):
        if format_json:
            return "{}"
        gesehen.append(list(messages))
        return "Verstanden, hier meine Antwort dazu."

    agent.llm.chat = fake_chat.__get__(agent.llm)
    agent.llm.chat_message = (lambda self, messages, tools=None, stream_cb=None, **kw: (
        gesehen.append(list(messages)),
        {"role": "assistant", "content": "Verstanden, hier meine Antwort dazu."})[1]
    ).__get__(agent.llm)
    agent._native_tools = False

    agent.ask("ich hätte eine Idee zum USB Stick")
    gesehen.clear()
    agent.ask("und was hältst du davon?")
    erste = gesehen[0] if gesehen else []
    rollen = [m["role"] for m in erste]
    check("Runde 2 enthält den Verlauf", rollen.count("assistant") >= 1, str(rollen))
    check("Die erste Frage steht wörtlich im Prompt",
          any("Idee zum USB Stick" in (m.get("content") or "") for m in erste))

    gesehen.clear()
    agent.ask("okay, dein Plan sieht gut aus. Passe die Datei so an")
    dritte = gesehen[0] if gesehen else []
    check("Runde 3 enthält weiterhin beide Vorrunden",
          [m["role"] for m in dritte].count("assistant") >= 2,
          str([m["role"] for m in dritte]))

    # ============================== 3. Budget wird NIE gerissen
    lang = "Sehr lange Antwort. " * 400          # ~8000 Zeichen je Runde
    agent.llm.chat = (lambda self, messages, stream_cb=None, temperature=None,
                      format_json=False, **kw: "{}" if format_json else
                      (gesehen.append(list(messages)), lang)[1]).__get__(agent.llm)
    agent.llm.chat_message = (lambda self, messages, tools=None, stream_cb=None, **kw: (
        gesehen.append(list(messages)), {"role": "assistant", "content": lang})[1]
    ).__get__(agent.llm)

    ueberschritten = []
    letzte_antwort = None
    for i in range(5):
        gesehen.clear()
        letzte_antwort = agent.ask(f"Runde {i}: erzähl ausführlich")
        umfang = sum(len(m.get("content") or "") for m in (gesehen[0] if gesehen else []))
        if umfang > cfg.prompt_char_budget:
            ueberschritten.append(umfang)
    check("Das Prompt-Budget wird nie überschritten", not ueberschritten,
          f"Budget {cfg.prompt_char_budget}, gemessen: {ueberschritten}")

    # ================= 4. Bei Überlänge: jüngste Runde bleibt, Verlust gemeldet
    letzte = gesehen[0] if gesehen else []
    check("Auch bei Überlänge bleibt etwas vom Verlauf erhalten",
          len(letzte) > 2, f"{len(letzte)} Nachrichten")
    check("Der Gedächtnisverlust wird dem Nutzer gemeldet",
          "ausgelassen" in (letzte_antwort.text or ""),
          (letzte_antwort.text or "")[-90:])

    # ====================== 5. Kurzer Verlauf wird NICHT unnötig gekürzt
    agent.history.clear()
    agent.llm.chat = fake_chat.__get__(agent.llm)
    agent.llm.chat_message = (lambda self, messages, tools=None, stream_cb=None, **kw: (
        gesehen.append(list(messages)),
        {"role": "assistant", "content": "Kurze Antwort."})[1]).__get__(agent.llm)
    agent.ask("erste kurze Frage")
    antwort = agent.ask("zweite kurze Frage")
    check("Kurzer Verlauf erzeugt keine Kürzungsmeldung",
          "ausgelassen" not in (antwort.text or ""), (antwort.text or "")[-80:])

    # ============================== 6. Planer bekommt den Verlauf
    block = _verlauf_als_text([
        {"role": "user", "content": "teile die Datei USB_Stick_Konfiguration"},
        {"role": "assistant", "content": "Plan: neue Notiz USB_Stick_Rekonfiguration anlegen."},
    ])
    check("Verlaufsblock nennt die besprochene Datei",
          "USB_Stick_Konfiguration" in block and "GESPRÄCHSVERLAUF" in block)
    check("Leerer Verlauf erzeugt keinen leeren Block", _verlauf_als_text([]).strip() == "")

    erhalten = {}

    def fake_plan_erstellen(self, auftrag, kontext="", verlauf=None):
        erhalten["verlauf"] = verlauf
        return None            # kein Plan -> fällt auf normale Antwort zurück

    agent.planner.plan_erstellen = fake_plan_erstellen.__get__(agent.planner)
    agent.history.clear()
    agent.history.extend([
        {"role": "user", "content": "teile die Datei USB_Stick_Konfiguration bitte auf"},
        {"role": "assistant", "content": "Plan: Rekonfigurations-Teil in eigene Notiz."},
    ])
    agent.ask("okay, erstelle die neue Notiz so wie besprochen")
    check("Der Planer bekommt den Verlauf übergeben",
          bool(erhalten.get("verlauf")), str(erhalten.get("verlauf"))[:70])
    check("Im übergebenen Verlauf steht die besprochene Datei",
          any("USB_Stick_Konfiguration" in (m.get("content") or "")
              for m in (erhalten.get("verlauf") or [])))

    # ================== 7. Lesen ist keine Änderung (falsche "Geändert:"-Meldung)
    lese = ToolCallRecord("notiz_lesen", {"name": "Agenten"},
                          ToolResult(True, "gelesen", {"pfad": "Agenten.md"}))
    schreib = ToolCallRecord("notiz_ergaenzen", {"name": "Agenten"},
                             ToolResult(True, "ergänzt", {"pfad": "Agenten.md"}))
    check("notiz_lesen gilt nicht als Änderung",
          Answer(text="x", tool_calls=[lese]).changed_files == [],
          str(Answer(text="x", tool_calls=[lese]).changed_files))
    check("notiz_ergaenzen gilt weiterhin als Änderung",
          Answer(text="x", tool_calls=[schreib]).changed_files == ["Agenten.md"])

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
