"""Die Verifikationsschicht - erkennt sie eine falsche Erfolgsmeldung?

Der Befund, der seit dem 01.09. in der eigenen BESTANDSAUFNAHME als
wichtigster offener Punkt steht:

    "Es gibt eine Rücklesekontrolle auf Byte-Ebene. Es gibt KEINE Prüfung
     auf Sach-Ebene. notiz_verschieben meldet ok=True, wenn os.replace nicht
     geworfen hat. Niemand prüft danach, ob die Quelle wirklich verschwunden
     und das Ziel wirklich vorhanden ist."

Hier wird genau das geprueft, und zwar mit FEHLERINJEKTION (Master-Prompt
§34): Es werden Erfolgsmeldungen gebaut, die nachweislich nicht stimmen. Die
Verifikation muss sie zu Fall bringen.

Ein Test, der nur bestaetigt, dass richtige Ergebnisse durchgehen, prueft die
Verifikation nicht - er prueft nur, dass sie nicht stoert.
"""
from __future__ import annotations

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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_verif_")).resolve()
VAULT = TMP / "Zeus"
(VAULT / "00_Inbox").mkdir(parents=True)
(VAULT / "Wissen").mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["VAULT_GIT"] = "false"
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

import stubs  # noqa: E402,F401

from jarvis.config import settings as cfg  # noqa: E402
from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.tools import ToolResult  # noqa: E402
from jarvis.kern.verifikation import (  # noqa: E402
    BESTANDEN, DURCHGEFALLEN, NICHT_PRUEFBAR, Verifikation,
)

FAILURES = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


def main() -> int:
    (VAULT / "Wissen" / "Backpropagation.md").write_text(
        "# Backpropagation\n\nDer Rueckwaertsdurchlauf berechnet die Gradienten "
        "schichtweise von hinten nach vorn.\n", encoding="utf-8")

    agent = JarvisAgent(cfg)
    agent.index.sync()
    pruefer = Verifikation(agent.tools, cfg)

    # =================================== 1. Echte Erfolge gehen durch
    e = agent.tools.ausfuehren("notiz_erstellen", {
        "titel": "Echte Notiz",
        "inhalt": "Ein hinreichend langer Satz, der sich spaeter wiederfinden laesst."})
    befund = pruefer.pruefen("notiz_erstellen", {"titel": "Echte Notiz",
                                                 "inhalt": "Ein hinreichend langer "
                                                           "Satz, der sich spaeter "
                                                           "wiederfinden laesst."}, e)
    check("Ein echter Erfolg besteht die Prüfung", befund.urteil == BESTANDEN,
          befund.bericht()[:70])
    check("Und die Prüfung sagt, WAS sie geprüft hat", len(befund.geprueft) >= 2,
          str(befund.geprueft))

    # ============== 2. FEHLERINJEKTION: Erfolg gemeldet, Datei gibt es nicht
    geloge = ToolResult(True, "Notiz 'Phantom' angelegt.",
                        {"pfad": "00_Inbox/Phantom_gibt_es_nicht.md"})
    befund = pruefer.pruefen("notiz_erstellen", {"titel": "Phantom"}, geloge)
    check("Eine erfundene Datei fällt auf", befund.urteil == DURCHGEFALLEN,
          befund.bericht()[:70])
    check("Und die Prüfung gilt als nicht bestanden", not befund.ok)

    # ============ 3. FEHLERINJEKTION: verschoben, aber Quelle liegt noch da
    quelle = VAULT / "Wissen" / "Backpropagation.md"
    (VAULT / "00_Inbox" / "Backpropagation.md").write_text(
        quelle.read_text(encoding="utf-8"), encoding="utf-8")
    agent.index.sync()
    kopiert = ToolResult(True, "Notiz verschoben.",
                         {"pfad": "00_Inbox/Backpropagation.md",
                          "vorher": "Wissen/Backpropagation.md"})
    befund = pruefer.pruefen("notiz_verschieben",
                             {"name": "Backpropagation"}, kopiert)
    check("Ein Verschieben, das in Wahrheit kopiert hat, fällt auf",
          befund.urteil == DURCHGEFALLEN, befund.bericht()[:80])
    check("Die Begründung nennt die liegengebliebene Quelle",
          any("Quelle" in b or "liegt noch" in b for b in befund.befunde),
          str(befund.befunde)[:70])

    # Aufraeumen, damit die naechsten Proben sauber laufen
    (VAULT / "00_Inbox" / "Backpropagation.md").unlink()
    agent.index.sync()

    # ========= 4. FEHLERINJEKTION: Verknüpfung gemeldet, Link steht nicht da
    agent.tools.ausfuehren("notiz_erstellen", {
        "titel": "Ohne Verweis",
        "inhalt": "In dieser Notiz steht absichtlich kein einziger Wikilink drin."})
    agent.index.sync()
    behauptet = ToolResult(True, "Verknüpfung gesetzt.", {})
    befund = pruefer.pruefen("notizen_verknuepfen",
                             {"von": "Ohne Verweis", "nach": "Backpropagation"},
                             behauptet)
    check("Eine behauptete Verknüpfung ohne Link fällt auf",
          befund.urteil == DURCHGEFALLEN, befund.bericht()[:80])

    # ================= 5. FEHLERINJEKTION: Verweis auf eine Notiz, die fehlt
    agent.tools.ausfuehren("notiz_erstellen", {
        "titel": "Mit totem Verweis",
        "inhalt": "Hier steht ein Verweis auf [[Gibt Es Gar Nicht]] im Text."})
    agent.index.sync()
    befund = pruefer.pruefen("notizen_verknuepfen",
                             {"von": "Mit totem Verweis", "nach": "Gibt Es Gar Nicht"},
                             ToolResult(True, "Verknüpft.", {}))
    check("Ein Verweis ins Leere fällt auf", befund.urteil == DURCHGEFALLEN,
          befund.bericht()[:80])
    check("Die Begründung nennt den toten Verweis",
          any("tot" in b.lower() or "existiert nicht" in b.lower()
              for b in befund.befunde), str(befund.befunde)[:70])

    # ============ 6. FEHLERINJEKTION: Tags gemeldet, stehen nicht in der Datei
    befund = pruefer.pruefen("tags_setzen",
                             {"name": "Ohne Verweis", "tags": "niemalsgesetzt"},
                             ToolResult(True, "Tags gesetzt.", {}))
    check("Ein nicht geschriebener Tag fällt auf", befund.urteil == DURCHGEFALLEN,
          befund.bericht()[:70])

    # ===================== 7. Zusammenführung einer Notiz mit sich selbst
    # Genau der Fall vom 18.09.: "## Aus [[Aufgabenliste_(konsolidiert)]]"
    e = agent.tools.ausfuehren("notiz_erstellen", {
        "titel": "Selbstbezug", "inhalt": "Inhalt der Notiz mit genügend Text darin."})
    agent.index.sync()
    befund = pruefer.pruefen("notizen_zusammenfuehren",
                             {"quellen": ["Selbstbezug"], "ziel_titel": "Selbstbezug"},
                             e)
    check("Eine Zusammenführung mit sich selbst fällt auf",
          befund.urteil == DURCHGEFALLEN, befund.bericht()[:80])

    # ================================= 8. Selbstschutz der Prüfung
    befund = pruefer.pruefen("notiz_erstellen", {"titel": "X"},
                             ToolResult(False, "hat nicht geklappt"))
    check("Ein gemeldeter Fehler wird nicht gegengeprüft",
          befund.urteil == NICHT_PRUEFBAR, befund.urteil)
    befund = pruefer.pruefen("notizen_suchen", {"frage": "x"},
                             ToolResult(True, "3 Treffer"))
    check("Ein Werkzeug ohne Prüfregel gilt als nicht prüfbar",
          befund.urteil == NICHT_PRUEFBAR, befund.urteil)
    check("Nicht prüfbar ist NICHT dasselbe wie bestanden",
          befund.ok and befund.urteil != BESTANDEN)

    # ============== 9. Der Durchgriff: kippt _run_tool ein falsches ok=True?
    echt = agent.tools.ausfuehren
    def luegt(name, parameter=None):
        if name == "notiz_erstellen":
            return ToolResult(True, "Notiz angelegt.",
                              {"pfad": "00_Inbox/Niemals_Geschrieben.md"})
        return echt(name, parameter)
    agent.tools.ausfuehren = luegt
    record = agent._run_tool("notiz_erstellen", {"titel": "Niemals Geschrieben"}, None)
    agent.tools.ausfuehren = echt
    check("Ein falsches ok=True wird in der Schleife zu ok=False",
          not record.ergebnis.ok, str(record.ergebnis.ok))
    check("Und die Meldung sagt ausdrücklich, dass die Gegenprobe scheitert",
          "GEGENPROBE" in record.ergebnis.meldung,
          record.ergebnis.meldung[:80])

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
