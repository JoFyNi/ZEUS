"""Tests für listenpunkt_hinzufuegen/listenpunkt_erledigt.

Anlass: notiz_ergaenzen hängt neuen Inhalt immer ans Dateiende oder ans Ende
eines Abschnitts an - für eine Aufgabenliste ist das falsch. "Füge einen
Punkt hinzu" soll in die BESTEHENDE Liste, "Punkt X ist erledigt" soll den
bestehenden Punkt selbst verändern (abhaken/durchstreichen/entfernen), nicht
irgendwo einen Kommentar dazuschreiben. Diese Tests prüfen genau das - und
dass ein mehrdeutiger oder nicht vorhandener Treffer ehrlich scheitert statt
zu raten.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_listen_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.planner import PLANBARE_AKTIONEN  # noqa: E402
from jarvis.tools import SCHREIBENDE_WERKZEUGE, TOOL_NAMES  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def build_vault() -> None:
    VAULT.mkdir(parents=True)
    (VAULT / "Aufgaben.md").write_text(
        "# Aufgaben\n\n## Offen\n\n"
        "- [ ] Milch kaufen\n- [ ] Rasen mähen\n\n"
        "## Erledigt\n\n- [x] Steuererklärung abgeben\n",
        encoding="utf-8",
    )
    (VAULT / "Notizen.md").write_text("# Notizen\n\nFließtext ohne Liste.\n", encoding="utf-8")


def main() -> int:
    build_vault()
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.sync()
    tools = agent.tools

    check("Beide Werkzeuge registriert", {"listenpunkt_hinzufuegen", "listenpunkt_erledigt"} <= TOOL_NAMES)
    check("Beide gelten als schreibend", {"listenpunkt_hinzufuegen", "listenpunkt_erledigt"} <= SCHREIBENDE_WERKZEUGE)
    check("Beide sind für den Planer verfügbar", {"listenpunkt_hinzufuegen", "listenpunkt_erledigt"} <= set(PLANBARE_AKTIONEN))

    # ---------------------------------------- 1. In bestehende Liste einreihen
    r = tools.listenpunkt_hinzufuegen(name="Aufgaben", text="Auto waschen", abschnitt="Offen")
    inhalt = (VAULT / "Aufgaben.md").read_text(encoding="utf-8")
    check("Einfügen gelingt", r.ok, r.meldung)
    check("Neuer Punkt steht IN der Liste, nicht als Absatz am Dateiende",
          "- [ ] Rasen mähen\n- [ ] Auto waschen\n\n## Erledigt" in inhalt, inhalt)

    # -------------------------------------------------- 2. Position 'anfang'
    tools.listenpunkt_hinzufuegen(name="Aufgaben", text="Ganz zuerst", abschnitt="Offen", position="anfang")
    inhalt = (VAULT / "Aufgaben.md").read_text(encoding="utf-8")
    check("position=anfang setzt den Punkt vor die anderen",
          "- [ ] Ganz zuerst\n- [ ] Milch kaufen" in inhalt, inhalt)

    # --------------------------------------- 3. Ohne Liste: sauberer Fehler
    fehl = tools.listenpunkt_hinzufuegen(name="Notizen", text="Irgendwas")
    check("Keine Liste vorhanden -> Fehlschlag statt blindem Anhängen", not fehl.ok, fehl.meldung)
    check("Notiz bleibt unverändert",
          (VAULT / "Notizen.md").read_text(encoding="utf-8") == "# Notizen\n\nFließtext ohne Liste.\n")

    # -------------------------------------------------- 4. Checkbox=false
    tools.listenpunkt_hinzufuegen(name="Aufgaben", text="Reiner Punkt", abschnitt="Offen", checkbox=False)
    inhalt = (VAULT / "Aufgaben.md").read_text(encoding="utf-8")
    check("checkbox=false erzeugt reinen Bullet ohne '[ ]'",
          "- Reiner Punkt" in inhalt and "- [ ] Reiner Punkt" not in inhalt)

    # --------------------------------------------- 5. Abhaken (auto -> Checkbox)
    r = tools.listenpunkt_erledigt(name="Aufgaben", text="Milch kaufen")
    inhalt = (VAULT / "Aufgaben.md").read_text(encoding="utf-8")
    check("Abhaken gelingt", r.ok, r.meldung)
    check("Checkbox jetzt [x], Zeile bleibt erhalten", "- [x] Milch kaufen" in inhalt)

    # ------------------------------------------- 6. Explizit durchstreichen
    tools.listenpunkt_erledigt(name="Aufgaben", text="Rasen mähen", modus="durchstreichen")
    inhalt = (VAULT / "Aufgaben.md").read_text(encoding="utf-8")
    check("Durchstreichen wickelt Text in ~~...~~, Checkbox bleibt offen",
          "[ ] ~~Rasen mähen~~" in inhalt, inhalt)

    # ------------------------------------------------------ 7. Entfernen
    tools.listenpunkt_erledigt(name="Aufgaben", text="Auto waschen", modus="entfernen")
    check("entfernen löscht die Zeile ganz",
          "Auto waschen" not in (VAULT / "Aufgaben.md").read_text(encoding="utf-8"))

    # --------------------------------------- 8. Kein Treffer, kein Erfinden
    r = tools.listenpunkt_erledigt(name="Aufgaben", text="Voellig Erfundene Aufgabe 12345")
    check("Nicht vorhandener Punkt -> ehrlicher Fehlschlag", not r.ok, r.meldung)

    # ------------------------------------------- 9. Mehrdeutig wird abgelehnt
    (VAULT / "Mehrdeutig.md").write_text(
        "# M\n\n## L\n\n- [ ] Bericht schreiben\n- [ ] Bericht pruefen\n", encoding="utf-8")
    index.sync()
    r = tools.listenpunkt_erledigt(name="Mehrdeutig", text="Bericht")
    check("Mehrdeutiger Treffer -> Fehlschlag statt Rateentscheidung", not r.ok, r.meldung)
    check("Beide Kandidaten werden genannt", len(r.daten.get("treffer", [])) == 2, r.daten)

    # ==================================== 10. Planer: natürliche Sprache
    def plan_llm(plan_json):
        def chat(self, messages, stream_cb=None, temperature=None, format_json=False, **kwargs):
            if format_json:
                return json.dumps(plan_json, ensure_ascii=False)
            return "Erledigt."
        return chat

    agent.llm.chat = plan_llm({
        "absicht": "Aufgabe hinzufügen",
        "operationen": [{"aktion": "listenpunkt_hinzufuegen",
                         "parameter": {"name": "Aufgaben", "text": "Fenster putzen", "abschnitt": "Offen"}}],
    }).__get__(agent.llm)
    agent.ask("hake nichts ab, aber füge 'Fenster putzen' zur Aufgabenliste hinzu")
    check("Planer-Weg: neuer Punkt landet in der Liste",
          "- [ ] Fenster putzen" in (VAULT / "Aufgaben.md").read_text(encoding="utf-8"))

    agent.llm.chat = plan_llm({
        "absicht": "Aufgabe abhaken",
        "operationen": [{"aktion": "listenpunkt_erledigt",
                         "parameter": {"name": "Aufgaben", "text": "Fenster putzen"}}],
    }).__get__(agent.llm)
    agent.ask("hake 'Fenster putzen' in der Aufgabenliste ab")
    check("Planer-Weg: Punkt wird tatsächlich abgehakt",
          "- [x] Fenster putzen" in (VAULT / "Aufgaben.md").read_text(encoding="utf-8"))

    # -- Englische/alternative Aktionsnamen werden erkannt (Synonymtabelle) --
    plan = agent.planner._plan_parsen(json.dumps({
        "operationen": [{"aktion": "add_task", "parameter": {"name": "Aufgaben", "text": "X"}}]}))
    check("Synonym 'add_task' wird auf listenpunkt_hinzufuegen abgebildet",
          plan is not None and plan.operationen[0].aktion == "listenpunkt_hinzufuegen",
          str(plan.operationen[0].aktion if plan else None))

    plan2 = agent.planner._plan_parsen(json.dumps({
        "operationen": [{"aktion": "check_off", "parameter": {"name": "Aufgaben", "text": "X"}}]}))
    check("Synonym 'check_off' wird auf listenpunkt_erledigt abgebildet",
          plan2 is not None and plan2.operationen[0].aktion == "listenpunkt_erledigt",
          str(plan2.operationen[0].aktion if plan2 else None))

    # -- 'add' bleibt bewusst bei notiz_ergaenzen (zu generisch für Listen) --
    plan3 = agent.planner._plan_parsen(json.dumps({
        "operationen": [{"aktion": "add", "parameter": {"name": "Aufgaben", "inhalt": "X"}}]}))
    check("Generisches 'add' bleibt notiz_ergaenzen, nicht überladen",
          plan3 is not None and plan3.operationen[0].aktion == "notiz_ergaenzen",
          str(plan3.operationen[0].aktion if plan3 else None))

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
