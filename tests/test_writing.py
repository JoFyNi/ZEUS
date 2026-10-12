"""Härtetest für den Schreibpfad - stellt genau den gemeldeten Fehlerfall nach.

Szenario: llama3 ohne Werkzeug-Unterstützung, Auftrag "erstelle eine Struktur mit
Dateien für alle Punkte". Vorher: 0 Dateien und die Behauptung, es sei erledigt.
Nachher: Dateien müssen wirklich existieren - oder der Fehler muss gemeldet werden.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_write_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.llm import OllamaClient  # noqa: E402
from jarvis.planner import looks_like_write_request  # noqa: E402
from jarvis.tools import WriteError  # noqa: E402

FAILURES = []
THEMEN = [
    "Data Augmentation", "Regularisierung", "Modellarchitektur", "Hyperparameter-Tuning",
    "Pre-Training", "Transfer Learning", "Ensemble-Methoden", "Datenqualität",
    "Evaluationsmetriken", "Monitoring und Anpassung",
]


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def vault_dateien() -> list:
    """Nur echte Notizen - Backups und Protokolle liegen bewusst in .jarvis/."""
    return sorted(
        p.relative_to(VAULT).as_posix()
        for p in VAULT.rglob("*.md")
        if ".jarvis" not in p.parts and "_Papierkorb" not in p.parts
    )


def build_vault() -> None:
    (VAULT / "Deep Learning").mkdir(parents=True)
    (VAULT / "Deep Learning" / "01_Skript_Deep_Learning.md").write_text(
        "---\ntags: [dl]\n---\n\n# Skript\n\n## Nichtlineare Modelle\nAktivierungen sind nötig.\n",
        encoding="utf-8",
    )


class Llama3Attrappe:
    """Modell OHNE Werkzeug-Unterstützung - ignoriert 'tools' stillschweigend.

    Genau dieses Verhalten hat den ursprünglichen Fehler ausgelöst.
    """

    def __init__(self):
        self.tools_gesehen = 0
        self.inhalt_aufrufe = 0

    def capabilities(self, model=None):
        return ["completion"]          # kein "tools"

    def chat_message(self, messages, tools=None, temperature=None, format_json=False, **kwargs):
        if tools:
            self.tools_gesehen += 1    # bekommt Werkzeuge, kann sie aber nicht
        return {"role": "assistant",
                "content": "Here are the files created: data_augmentation.md, ..."}

    def chat(self, messages, stream_cb=None, temperature=None, format_json=False, **kwargs):
        inhalt = " ".join(str(m.get("content", "")) for m in messages)
        if format_json and "Plan" in inhalt or format_json and "notizen" in inhalt:
            return json.dumps({
                "absicht": "Struktur zu nichtlinearen Modellen anlegen",
                "ordner": "Deep Learning",
                "notizen": [{"titel": t, "auftrag": f"Erkläre {t} vollständig.",
                             "tags": ["dl"]} for t in THEMEN],
            }, ensure_ascii=False)
        if format_json:
            return '{"ordner": "Deep Learning", "neu": false}'
        self.inhalt_aufrufe += 1
        thema = "Thema"
        for t in THEMEN:
            if t in inhalt:
                thema = t
                break
        return (f"## Kurzfassung\n{thema} kurz erklärt. " + "Fachtext. " * 40 +
                f"\n\n## Einordnung und Struktur\nEinordnung von {thema}. " + "Mehr Text. " * 40 +
                "\n\n## Funktionsweise / Vorgehen\nSo funktioniert es. " + "Details. " * 40 +
                "\n\n## Typische Probleme\nProbleme. " + "Konkret. " * 30 +
                "\n\n## Lösungen und Gegenmaßnahmen\nLösungen. " + "Konkret. " * 30 +
                "\n\n## Offene Fragen\nWas noch offen ist.")


def install(agent: JarvisAgent, attrappe) -> None:
    OllamaClient.model_capabilities = lambda self, model=None: attrappe.capabilities(model)
    agent.llm.chat_message = attrappe.chat_message
    agent.llm.chat = attrappe.chat
    agent._capabilities = None
    agent._native_tools = None


def main() -> int:
    build_vault()
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.sync()
    tools = agent.tools

    # ------------------------------------------------- 1. Absichtserkennung
    check("Schreibauftrag wird erkannt",
          looks_like_write_request("erstelle eine neue Struktur mit dateien für alle punkte"))
    check("Auch 'erstelle die dateien'", looks_like_write_request("erstelle die dateien"))
    check("Reine Frage ist kein Schreibauftrag",
          not looks_like_write_request("Wie erstelle ich ein neuronales Netz?"))
    check("Wissensfrage ist kein Schreibauftrag",
          not looks_like_write_request("Was steht in meinen Notizen zu Backpropagation?"))

    # ------------------------------------- 2. Fähigkeitserkennung ohne Fehler
    attrappe = Llama3Attrappe()
    install(agent, attrappe)
    check("Fehlender Werkzeug-Support wird erkannt",
          agent.native_tools is False and agent.tool_mode == "json-protokoll", agent.tool_mode)

    # ------------------------------------------- 3. Der gemeldete Fehlerfall
    vorher = len(vault_dateien())
    antwort = agent.ask(
        "erstelle eine neue Struktur mit datein für alle punkte. Jede Datei soll den vollen "
        "Inhalt des Themas mit Ansatz, Struktur, Vorgehen, System, Probleme, Lösungen enthalten."
    )
    dateien = vault_dateien()
    neu = len(dateien) - vorher
    check("Dateien existieren wirklich", neu == len(THEMEN), f"{neu} neue Dateien")
    check("Antwort ist der Ausführungsbericht, keine Behauptung",
          "angelegt" in antwort.text and "Here are the files" not in antwort.text,
          antwort.text.splitlines()[0] if antwort.text else "")
    check("Alle im richtigen Ordner",
          all(d.startswith("Deep Learning/") for d in dateien), str(dateien[:3]))

    beispiel = VAULT / "Deep Learning" / "Data_Augmentation.md"
    check("Erwarteter Dateiname", beispiel.exists(), str(dateien))
    if beispiel.exists():
        inhalt = beispiel.read_text(encoding="utf-8")
        for abschnitt in ("## Kurzfassung", "## Funktionsweise / Vorgehen",
                          "## Typische Probleme", "## Lösungen und Gegenmaßnahmen"):
            check(f"Abschnitt '{abschnitt}' vorhanden", abschnitt in inhalt)
        check("Vollständiger Inhalt, nicht abgeschnitten", len(inhalt) > 1500, f"{len(inhalt)} Zeichen")
        check("Frontmatter mit Tags", "tags:" in inhalt and "- dl" in inhalt)
        check("Notizen sind untereinander verknüpft", "[[" in inhalt, "")

    check("Alle neuen Notizen sofort im Index",
          all(any(h.metadata.get("stem", "").startswith("Data_Aug")
                  for h in index.search("Data Augmentation")) for _ in [0]), "")

    # ------------------------------------------ 4. Schreibfehler wird gemeldet
    echtes_write = type(tools)._write

    def kaputtes_write(self, path, content, neue_datei=False):
        raise WriteError("Simulierter Plattenfehler")

    type(tools)._write = kaputtes_write
    fehler = tools.notiz_erstellen(titel="Darf nicht entstehen", inhalt="x" * 200)
    type(tools)._write = echtes_write
    check("Fehlschlag wird als Fehler gemeldet, nicht als Erfolg",
          not fehler.ok and "Plattenfehler" in fehler.meldung, fehler.meldung)
    check("Keine Geisterdatei zurückgeblieben",
          not [d for d in vault_dateien() if "Darf_nicht" in d])

    # ------------------------------------- 5. Verifikation erkennt Abweichung
    versuche = {"n": 0}
    echtes_atomic = type(tools)._atomic_write

    def wackliges_atomic(self, path, content):
        versuche["n"] += 1
        if versuche["n"] == 1:      # erster Versuch schreibt kaputt
            echtes_atomic(self, path, content[: len(content) // 2])
        else:
            echtes_atomic(self, path, content)

    type(tools)._atomic_write = wackliges_atomic
    ergebnis = tools.notiz_erstellen(titel="Wiederholung", inhalt="Inhalt " * 60)
    type(tools)._atomic_write = echtes_atomic
    check("Abweichung erkannt und erfolgreich wiederholt",
          ergebnis.ok and versuche["n"] == 2, f"{versuche['n']} Versuche, {ergebnis.meldung}")
    ziel = VAULT / ergebnis.daten.get("pfad", "x")
    check("Datei am Ende korrekt", ziel.exists() and ziel.read_text(encoding="utf-8").count("Inhalt") >= 60)

    # -------------------------------------------- 6. Transaktion und Rollback
    stand = vault_dateien()
    try:
        with tools.transaktion("Stapel mit Fehler", "test"):
            tools.notiz_erstellen(titel="Stapel A", inhalt="A" * 200)
            tools.notiz_erstellen(titel="Stapel B", inhalt="B" * 200)
            raise RuntimeError("Abbruch mitten im Stapel")
    except RuntimeError:
        pass
    danach = vault_dateien()
    check("Rollback entfernt den halben Stapel wieder", stand == danach,
          f"{set(danach) - set(stand)}")

    # ---------------------------------------------------- 7. Trockenlauf
    agent.cfg.dry_run = True
    trocken = tools.notiz_erstellen(titel="Trocken", inhalt="Nur ein Test." * 20)
    agent.cfg.dry_run = False
    check("Trockenlauf schreibt nichts",
          not [d for d in vault_dateien() if "Trocken" in d] and trocken.ok, trocken.meldung)

    # ------------------------------------------------------- 8. Guardrails
    agent.cfg.max_note_chars = 100
    zu_gross = tools.notiz_erstellen(titel="Zu groß", inhalt="x" * 5000)
    agent.cfg.max_note_chars = 60000
    check("Größenlimit greift", not zu_gross.ok, zu_gross.meldung)

    agent.cfg.protected_folders = "Deep Learning"
    geschuetzt = tools.notiz_loeschen("01_Skript_Deep_Learning")
    agent.cfg.protected_folders = ""
    check("Geschützter Ordner blockiert Löschen", not geschuetzt.ok, geschuetzt.meldung)

    # ------------------------------------------------- 9. Wiederholungsbefehl
    from jarvis.planner import Operation

    agent.last_plan.fehlgeschlagen = [{"titel": "Nachzügler", "fehler": "Test"}]
    agent.last_plan.plan.operationen.append(
        Operation(aktion="notiz_erstellen",
                  parameter={"titel": "Nachzügler", "ordner": "Deep Learning"},
                  auftrag="Erkläre Nachzügler.")
    )
    antwort2 = agent.ask("versuch es nochmal")
    check("Wiederholung legt die fehlende Notiz an",
          any("Nachz" in d for d in vault_dateien()), antwort2.text[:80])

    # ------------------------------------------------------- 10. Wartungslauf
    (VAULT / "Deep Learning" / "Kaputt.md").write_text(
        "# Kaputt\n\n[[Gibt es nicht]]\n", encoding="utf-8")
    index.sync()
    wartung = tools.vault_wartung(reparieren=True)
    check("Wartungslauf liefert Bericht",
          wartung.ok and "verwaiste_notizen" in wartung.daten and "index" in wartung.daten,
          wartung.daten.get("index", ""))

    # ----------------------------------------------------------- 11. Protokoll
    logs = list((VAULT / ".jarvis" / "logs").glob("*.jsonl"))
    check("Strukturiertes Protokoll geschrieben", bool(logs), str([p.name for p in logs]))
    if logs:
        zeilen = [json.loads(z) for z in logs[0].read_text(encoding="utf-8").splitlines() if z.strip()]
        check("Protokoll enthält Schreib- und Fehlerereignisse",
              any(z["ereignis"] == "write_ok" for z in zeilen)
              and any(z["ereignis"] in ("write_retry", "write_failed") for z in zeilen),
              f"{len(zeilen)} Einträge")
    check("Protokollordner wird nicht indexiert",
          all(".jarvis" not in n.rel_path for n in index.notes))

    # -------------------------------------------- 12. Erdung des Notizinhalts
    # Der Inhalt wurde früher ohne jeden Bezug zur Vault erzeugt. Bei dem
    # mehrdeutigen Wort "Vault" schrieb das Modell daraufhin über HashiCorp
    # Vault - Secret Engines, TLS-Zertifikate, Redis-Cache. Nichts davon stand
    # in den Notizen. Jetzt muss der eigene Bestand im Prompt stehen.
    from jarvis.planner import Operation, WritePlan  # noqa: E402

    mitgeschnitten = {}

    def mitschneiden(self, messages, **kwargs):
        mitgeschnitten["system"] = messages[0]["content"]
        mitgeschnitten["user"] = messages[-1]["content"]
        return "## Kurzfassung\nText."

    agent.llm.chat = mitschneiden.__get__(agent.llm)
    plan = WritePlan(absicht="Vault dokumentieren", ordner="Deep Learning",
                     operationen=[Operation(aktion="notiz_erstellen",
                                            parameter={"titel": "Vault"})])
    agent.planner._inhalt_erzeugen(plan.operationen[0], plan)

    check("Der Begriff 'Vault' wird im Systemprompt festgenagelt",
          "HashiCorp" in mitgeschnitten.get("system", ""),
          mitgeschnitten.get("system", "")[:80])
    check("Der Inhaltsprompt enthält den eigenen Bestand",
          "DAS STEHT BEREITS IN DER VAULT" in mitgeschnitten.get("user", "")
          or "steht noch nichts in der Vault" in mitgeschnitten.get("user", ""),
          mitgeschnitten.get("user", "")[:100])
    check("Mehrdeutigkeit wird ausdrücklich zugunsten des Kontexts entschieden",
          "nicht die bekanntere aus deinem Weltwissen" in mitgeschnitten.get("user", ""))

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
