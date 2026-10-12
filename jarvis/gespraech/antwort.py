"""Die Datentypen einer Antwort: Werkzeugaufruf, Antwort, Ordnerentscheidung.

Klein und ohne Abhaengigkeit ausser den Werkzeugen - damit jede andere
Datei sie importieren kann, ohne einen Ring zu bauen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..index import Hit
from ..tools import SCHREIBENDE_WERKZEUGE, ToolResult


@dataclass
class ToolCallRecord:
    werkzeug: str
    parameter: dict
    ergebnis: ToolResult

    @property
    def kurz(self) -> str:
        return f"{self.werkzeug}({', '.join(f'{k}={str(v)[:40]}' for k, v in self.parameter.items())})"


@dataclass
class Answer:
    text: str
    hits: List[Hit] = field(default_factory=list)
    folder_filter: Optional[str] = None
    kind: str = "rag"
    tool_calls: List[ToolCallRecord] = field(default_factory=list)

    @property
    def sources(self) -> List[str]:
        seen, out = set(), []
        for hit in self.hits:
            rel = hit.metadata.get("rel_path", "")
            if rel.startswith("__struktur__") or rel in seen:
                continue
            seen.add(rel)
            out.append(rel)
        return out

    @property
    def changed_files(self) -> List[str]:
        """Welche Dateien wurden GEÄNDERT - nicht, welche wurden angefasst.

        notiz_lesen gibt ebenfalls einen 'pfad' zurück. Der zählte hier mit,
        und die Oberfläche meldete nach einer reinen Leseanfrage "Geändert:
        Agenten.md". Eine falsche Änderungsmeldung ist schlimmer als keine:
        Sie schickt den Nutzer in eine Datei, in der nichts passiert ist.
        """
        out = []
        for call in self.tool_calls:
            if call.werkzeug not in SCHREIBENDE_WERKZEUGE:
                continue
            pfad = call.ergebnis.daten.get("pfad") or call.ergebnis.daten.get("nachher")
            if call.ergebnis.ok and pfad and pfad not in out:
                out.append(pfad)
        return out


@dataclass
class FolderDecision:
    folder: str
    reason: str
    confidence: float
    is_new: bool = False
