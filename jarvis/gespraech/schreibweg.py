"""Der deterministische Schreibweg und die Notizerzeugung.

Die Bruecke zum Planer: Absicht erkennen, Plan anfordern,
ausfuehren lassen, Ergebnis als Antwort formen.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from ..index import Hit
from ..planner import PlanErgebnis
from ..tools import (
    ToolResult,
)

from .antwort import Answer, FolderDecision, ToolCallRecord


class SchreibwegTeil:
    """Wird in JarvisAgent eingemischt - siehe agent.py."""


    # --------------------------------------------------------- Planer-Pfad
    def _plan_melder(self, on_tool) -> Callable[[str, str], None]:
        """Übersetzt Planer-Ereignisse in dieselben Anzeigezeilen wie Werkzeuge."""
        def melder(art: str, text: str) -> None:
            if on_tool:
                on_tool(ToolCallRecord(
                    werkzeug="plan" if art == "arbeit" else art,
                    parameter={},
                    ergebnis=ToolResult(art != "fehler", text),
                ))
        return melder

    def _plan_durchfuehren(self, question, context, hits, folder_filter, on_tool) -> Optional[Answer]:
        melder = self._plan_melder(on_tool)
        melder("arbeit", "Schreibauftrag erkannt – erstelle Plan …")
        # Der Verlauf muss mit: "erstelle die neue Notiz so wie besprochen"
        # nennt weder Namen noch Thema. Ohne die letzten Züge plant der
        # Planer ins Blaue und erfindet einen Titel.
        plan = self.planner.plan_erstellen(
            question, context, verlauf=self.history[-2 * self.cfg.history_turns:])
        # Nennt der Auftrag einen Ordner, gilt er als Schranke für den ganzen
        # Plan. Ohne das verknüpfte er auf "alle Code dateien" fröhlich
        # Deep-Learning-Notizen - alles echte Notizen, nur eben die falschen.
        if plan is not None:
            bereich = folder_filter or self._ordner_aus_text(question)
            if bereich:
                plan.geltungsbereich = bereich
                melder("arbeit", f"Geltungsbereich: {bereich}")
        if not plan:
            # Nicht mehr nur "kein verwertbarer Plan" - der Grund gehört dazu,
            # sonst ist die Meldung für niemanden zu gebrauchen.
            gruende = self.planner.diagnose[-3:] or ["kein Grund ermittelbar"]
            for grund in gruende:
                melder("fehler", grund)
            melder("fehler", "Kein ausführbarer Plan – arbeite als normale Antwort weiter.")
            self.letzte_plandiagnose = list(self.planner.diagnose)
            return None

        arten = sorted({op.aktion for op in plan.operationen})
        melder("arbeit", f"Plan steht: {len(plan.operationen)} Schritte "
                         f"({', '.join(arten)})")
        ergebnis = self.planner.plan_ausfuehren(plan, melder)
        self.last_plan = ergebnis
        return self._plan_antwort(ergebnis, question, hits, folder_filter)

    def _plan_antwort(self, ergebnis: PlanErgebnis, question: str, hits, folder_filter) -> Answer:
        self.last_plan = ergebnis
        text = ergebnis.bericht(self.cfg.assistant_name, self.cfg.user_title)
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": text})
        self.history = self.history[-2 * self.cfg.history_turns :]
        calls = [
            ToolCallRecord(e.get("aktion", "notiz_erstellen"), {"titel": e["titel"]},
                           ToolResult(True, e.get("meldung", ""), {"pfad": e.get("pfad", "")}))
            for e in ergebnis.erstellt
        ] + [
            ToolCallRecord(e.get("aktion", "notiz_erstellen"), {"titel": e["titel"]},
                           ToolResult(False, e["fehler"]))
            for e in ergebnis.fehlgeschlagen
        ]
        return Answer(text=text, hits=list(hits), folder_filter=folder_filter,
                      kind="plan", tool_calls=calls)


    # ------------------------------------------------------ Notizen schreiben
    def write_note(
        self,
        topic: str,
        content: str,
        hits: Sequence[Hit] = (),
        folder: Optional[str] = None,
        extra_tags: Sequence[str] = (),
    ) -> Tuple[Path, FolderDecision]:
        """Für /notiz und /merke - nutzt dieselbe Werkzeugebene wie der Agent."""
        quellen = [
            h.metadata.get("stem", "") for h in hits
            if not h.metadata.get("rel_path", "").startswith("__struktur__")
        ]
        tags = list(dict.fromkeys(["ai-update", *extra_tags]))
        ergebnis = self.tools.notiz_erstellen(
            titel=topic, inhalt=content, ordner=folder or "", tags=tags,
            quellen=list(dict.fromkeys([q for q in quellen if q]))[:6],
        )
        if not ergebnis.ok:
            raise RuntimeError(ergebnis.meldung)
        rel = ergebnis.daten["pfad"]
        decision = FolderDecision(
            folder=ergebnis.daten.get("ordner", "").replace("Wurzel", ""),
            reason=ergebnis.daten.get("begruendung", ""),
            confidence=0.8,
        )
        return self.structure.vault_root / rel, decision


    # ------------------------------------------------------------ Zusammenfassen
    def summarize_for_note(self, topic: str) -> Tuple[str, List[Hit]]:
        hits = self.index.search(topic, k=self.cfg.top_k)
        context = self._build_context(hits, structural=False, frage=topic)
        prompt = f"""Fasse zusammen, was in den Notizen zum Thema "{topic}" steht.
Schreibe eine eigenständige Notiz in Markdown: kurze Einordnung, dann die Kernpunkte als
Stichpunkte, am Ende offene Fragen. Verweise auf Notizen als [[Name]].
Erfinde nichts, was nicht im Kontext steht.

{context}"""
        text = self.llm.chat([
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": prompt},
        ])
        return text, hits
