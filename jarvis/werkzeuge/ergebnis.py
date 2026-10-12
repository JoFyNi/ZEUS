"""Was ein Werkzeug zurueckgibt - und was schiefgehen kann.

Drei kleine Typen, die ueberall gebraucht werden und deshalb keine
Abhaengigkeit haben duerfen. Sie stehen in einer eigenen Datei, damit
jede andere sie importieren kann, ohne einen Ring zu bauen.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List


class WriteError(RuntimeError):
    """Schreiben fehlgeschlagen oder Verifikation nicht bestanden."""


@dataclass
class _Transaktion:
    aktion: str
    beschreibung: str
    dateien: List[dict] = field(default_factory=list)


@dataclass
class ToolResult:
    ok: bool
    meldung: str
    daten: dict = field(default_factory=dict)

    def to_model(self) -> str:
        """Das, was das Modell als Werkzeug-Ergebnis zu sehen bekommt."""
        payload = {"ok": self.ok, "meldung": self.meldung}
        payload.update(self.daten)
        return json.dumps(payload, ensure_ascii=False)[:4000]
