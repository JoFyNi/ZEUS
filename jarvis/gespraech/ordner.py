"""In welchen Ordner eine Notiz gehoert.

Vier gewichtete Signale und eine Begruendung, die im Frontmatter
landet. Die Begruendung ist kein Schmuck: Sie ist der einzige Weg,
eine falsche Einsortierung spaeter zu verstehen statt nur zu
aergern.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Sequence, Tuple

from ..index import Hit
from ..llm import OllamaError
from ..vault import fold, safe_filename, similarity

from .antwort import FolderDecision


class OrdnerTeil:
    """Wird in JarvisAgent eingemischt - siehe agent.py."""


    # ------------------------------------------- Ausdrückliche Werkzeugrufe
    def _ordner_aus_text(self, text: str) -> str:
        """Nennt der Satz einen Ordner der Vault? Sonst leer.

        "alle code dateien [auto_verlinken]" meint `_System/Code`, sagt es aber
        nicht mit @. Ohne diese Auflösung lief auto_verlinken über die GANZE
        Vault - und verknüpfte Deep-Learning-Notizen, obwohl von Quellcode die
        Rede war. Genau das steht im Protokoll vom 01.09.

        Geraten wird nicht: Passen zwei Ordner gleich gut, bleibt es leer.
        """
        if not text:
            return ""
        klein = fold(text)
        treffer: List[str] = []
        for pfad in self._structure_pfade():
            for kandidat in (pfad, pfad.rsplit("/", 1)[-1]):
                gefaltet = fold(kandidat)
                if len(gefaltet) < 3:
                    continue
                if re.search(rf"(?<![\w]){re.escape(gefaltet)}(?![\w])", klein):
                    treffer.append(pfad)
                    break
        if not treffer:
            return ""
        treffer.sort(key=len, reverse=True)
        # Mehrdeutig? Dann lieber nichts als das Falsche.
        if len(treffer) > 1 and len(treffer[0]) == len(treffer[1]):
            return ""
        return treffer[0]

    def _structure_pfade(self) -> List[str]:
        try:
            return list(self.structure.folder_paths)
        except Exception:                                # noqa: BLE001
            return []


    # ------------------------------------------------------- Ordner-Zuordnung
    def resolve_target_folder(
        self,
        topic: str,
        hits: Sequence[Hit] = (),
        content: str = "",
        allow_llm: bool = True,
        hint_folders: Sequence[str] = (),
    ) -> FolderDecision:
        """Wohin gehört eine neue Notiz? Fünf Signale, gewichtet."""
        structure = self.structure
        scores: Dict[str, float] = {}
        reasons: Dict[str, List[str]] = {}

        def bump(folder: str, amount: float, why: str) -> None:
            if not folder:
                return
            scores[folder] = scores.get(folder, 0.0) + amount
            reasons.setdefault(folder, []).append(why)

        # Ausdrücklich genannte Quellnotizen wiegen am schwersten
        for folder in hint_folders:
            bump(folder, 3.0, "Ordner der angegebenen Quellnotizen")

        for rank, hit in enumerate(hits):
            rel = hit.metadata.get("rel_path", "")
            if rel.startswith("__struktur__"):
                continue
            bump(hit.metadata.get("folder", ""), 2.4 / (1 + rank) * max(0.3, hit.score),
                 f"Quelle {rel}")

        for folder, score in structure.match_folder(topic, threshold=0.6):
            bump(folder, 3.2 * score, f"Ordnername ähnelt '{topic}' ({score:.0%})")

        for note, score in structure.match_note(topic, threshold=0.75)[:5]:
            bump(note.folder, 1.6 * score, f"ähnliche Notiz [[{note.stem}]]")

        topic_tokens = set(re.findall(r"\w+", topic.lower()))
        for folder, info in structure.folders.items():
            if not folder:
                continue
            for tag in info.tags:
                if tag.lower() in topic_tokens or similarity(tag, topic) > 0.85:
                    bump(folder, 1.1, f"Tag #{tag}")
                    break

        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        top = ranked[0] if ranked else None
        runner_up = ranked[1] if len(ranked) > 1 else None
        ambiguous = top is None or top[1] < 2.2 or (runner_up and top[1] - runner_up[1] < 0.8)

        if ambiguous and allow_llm and structure.folder_paths:
            choice = self._llm_pick_folder(topic, content, [f for f, _ in ranked[:5]])
            if choice:
                folder, is_new = choice
                return FolderDecision(folder, "Einschätzung des Modells anhand der Ordnerliste",
                                      0.6, is_new)

        if top:
            return FolderDecision(top[0], "; ".join(dict.fromkeys(reasons[top[0]]))[:180],
                                  min(1.0, top[1] / 5.0))
        return FolderDecision(self.cfg.inbox_folder, "kein passender Ordner gefunden", 0.2,
                              self.cfg.inbox_folder not in self.structure.folders)

    def _llm_pick_folder(self, topic: str, content: str,
                         favourites: Sequence[str]) -> Optional[Tuple[str, bool]]:
        folders = self.structure.folder_paths
        listing = "\n".join(
            f"- {f} ({self.structure.folders[f].note_count} Notizen; "
            f"{', '.join(self.structure.folders[f].titles[:4]) or 'leer'})"
            for f in folders
        )
        prompt = f"""Ordne eine neue Notiz dem passendsten vorhandenen Ordner zu.

VORHANDENE ORDNER:
{listing or '(keine)'}

THEMA DER NEUEN NOTIZ: {topic}
INHALT (Auszug): {content[:800]}

Wähle GENAU EINEN vorhandenen Ordnerpfad aus der Liste. Nur wenn wirklich keiner
inhaltlich passt, schlage einen neuen vor.
Antworte ausschließlich als JSON: {{"ordner": "<Pfad oder neuer Name>", "neu": true|false}}"""
        try:
            raw = self.llm.chat([{"role": "user", "content": prompt}], temperature=0.0,
                                format_json=True, schnell=True)
        except OllamaError:
            return None

        try:
            data = json.loads(raw)
        except ValueError:
            match = re.search(r'"ordner"\s*:\s*"([^"]+)"', raw)
            if not match:
                return None
            data = {"ordner": match.group(1), "neu": True}

        choice = str(data.get("ordner", "")).strip().strip("/")
        if not choice:
            return None
        if choice in self.structure.folders:
            return choice, False
        matches = self.structure.match_folder(choice, threshold=0.75)
        if matches:
            return matches[0][0], False
        if favourites:
            return favourites[0], False
        return safe_filename(choice), True
