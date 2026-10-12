"""Inkrementeller FAISS-Index + hybride Suche (Vektor + Stichwort + Struktur)."""
from __future__ import annotations

import json
import math
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .config import settings
from .llm import OllamaEmbedding
from .sprache import cjk_zerlegen, ist_cjk_wort
from .vault import (
    Note,
    VaultStructure,
    chunk_note,
    fold,
    folder_summary_chunks,
    scan_vault,
)

MANIFEST_VERSION = 3
STOPWORDS = {
    "der", "die", "das", "und", "oder", "ein", "eine", "einen", "ist", "sind", "was", "wie",
    "wer", "wo", "welche", "welcher", "welches", "mir", "mich", "ich", "du", "wir", "von",
    "mit", "auf", "fuer", "für", "im", "in", "am", "an", "zu", "zum", "zur", "den", "dem",
    "des", "es", "sich", "auch", "noch", "nur", "the", "a", "of", "to", "is", "are", "what",
    "bitte", "mal", "gibt", "hast", "hab", "habe", "kannst", "kann", "sag", "zeig", "erklaer",
}


@dataclass
class Hit:
    text: str
    metadata: dict
    score: float
    origin: str = "vector"

    @property
    def label(self) -> str:
        folder = self.metadata.get("folder") or ""
        stem = self.metadata.get("stem") or self.metadata.get("filename", "?")
        head = self.metadata.get("headings") or ""
        base = f"{folder}/{stem}" if folder else stem
        return f"{base} › {head}" if head else base


@dataclass
class SyncReport:
    added: int = 0
    updated: int = 0
    removed: int = 0
    total_notes: int = 0
    total_chunks: int = 0
    rebuilt: bool = False
    messages: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.rebuilt:
            head = f"Index neu aufgebaut: {self.total_chunks} Abschnitte aus {self.total_notes} Notizen."
        elif self.added or self.updated or self.removed:
            head = (
                f"Index aktualisiert: +{self.added} neu, ~{self.updated} geändert, "
                f"-{self.removed} entfernt ({self.total_notes} Notizen)."
            )
        else:
            head = f"Index ist aktuell ({self.total_notes} Notizen)."
        return " ".join([head, *self.messages])


def tokens(text: str) -> List[str]:
    """Stichwörter für den Abgleich - gefaltet, ohne Füllwörter.

    Chinesisch/Japanisch/Koreanisch kennt keine Leerzeichen zwischen Wörtern;
    ein ganzer Satz wäre sonst EIN Stichwort, das nie wieder trifft. Dort wird
    in Zeichenpaare zerlegt (sprache.cjk_zerlegen), und die Mindestlänge
    von drei Zeichen gilt nicht - zwei Schriftzeichen sind oft ein ganzes Wort.
    """
    aus: List[str] = []
    for wort in fold(text).split():
        teile = cjk_zerlegen(wort)
        for teil in ([wort] if teile is None else teile):
            if ist_cjk_wort(teil) or (len(teil) > 2 and teil not in STOPWORDS):
                aus.append(teil)
    return aus


class KnowledgeIndex:
    """Hält Vault-Struktur und Vektorspeicher synchron."""

    def __init__(self, cfg=settings, embeddings=None):
        self.cfg = cfg
        self.embeddings = embeddings or OllamaEmbedding(cfg=cfg)
        self.store = None
        self.notes: List[Note] = []
        self.structure: Optional[VaultStructure] = None
        self.manifest: Dict = {"version": MANIFEST_VERSION, "files": {}, "structure_ids": []}

    # ---------------------------------------------------------------- Manifest
    def _load_manifest(self) -> Dict:
        path = self.cfg.manifest_path
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") == MANIFEST_VERSION:
                    return data
            except (OSError, ValueError):
                pass
        return {"version": MANIFEST_VERSION, "files": {}, "structure_ids": []}

    def _save_manifest(self) -> None:
        self.cfg.store_path.mkdir(parents=True, exist_ok=True)
        self.manifest["embedding_model"] = self.cfg.embedding_model
        self.manifest["chunk_size"] = self.cfg.chunk_size
        self.cfg.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # ------------------------------------------------------------------- Store
    def _load_store(self):
        from langchain_community.vectorstores import FAISS

        faiss_file = self.cfg.store_path / "index.faiss"
        if not faiss_file.exists():
            return None
        try:
            return FAISS.load_local(
                folder_path=str(self.cfg.store_path),
                embeddings=self.embeddings,
                allow_dangerous_deserialization=True,
            )
        except Exception:
            return None

    def _new_store(self, chunks: Sequence[dict], ids: Sequence[str]):
        from langchain_community.vectorstores import FAISS

        return FAISS.from_texts(
            texts=[c["text"] for c in chunks],
            embedding=self.embeddings,
            metadatas=[c["metadata"] for c in chunks],
            ids=list(ids),
        )

    # -------------------------------------------------------------------- Sync
    def refresh_structure(self) -> VaultStructure:
        self.notes = scan_vault(self.cfg.vault_path)
        self.structure = VaultStructure(self.cfg.vault_path, self.notes)
        return self.structure

    def sync(self, force: bool = False, progress: Optional[Callable[[str], None]] = None) -> SyncReport:
        say = progress or (lambda _msg: None)
        report = SyncReport()

        say("Scanne Vault …")
        structure = self.refresh_structure()
        report.total_notes = len(self.notes)

        self.manifest = self._load_manifest()
        config_changed = (
            self.manifest.get("embedding_model") not in (None, self.cfg.embedding_model)
            or self.manifest.get("chunk_size") not in (None, self.cfg.chunk_size)
        )
        if config_changed:
            report.messages.append("Embedding-Modell/Chunking geändert → kompletter Neuaufbau.")

        self.store = None if (force or config_changed) else self._load_store()
        if self.store is None:
            force = True

        known = {} if force else dict(self.manifest.get("files", {}))
        current = {n.rel_path: n for n in self.notes}

        changed = [n for rel, n in current.items() if known.get(rel, {}).get("checksum") != n.checksum]
        removed = [rel for rel in known if rel not in current]
        report.added = sum(1 for n in changed if n.rel_path not in known)
        report.updated = len(changed) - report.added
        report.removed = len(removed)

        if force:
            say(f"Baue Index neu ({len(self.notes)} Notizen) …")
            if self.cfg.store_path.exists():
                shutil.rmtree(self.cfg.store_path, ignore_errors=True)
            self.manifest = {"version": MANIFEST_VERSION, "files": {}, "structure_ids": []}
            all_chunks, file_ids = [], {}
            for note in self.notes:
                pieces = chunk_note(note, self.cfg.chunk_size, self.cfg.chunk_overlap)
                ids = [uuid.uuid4().hex for _ in pieces]
                file_ids[note.rel_path] = {"checksum": note.checksum, "ids": ids}
                all_chunks.extend(zip(pieces, ids))
            struct_chunks = folder_summary_chunks(structure)
            struct_ids = [uuid.uuid4().hex for _ in struct_chunks]
            all_chunks.extend(zip(struct_chunks, struct_ids))

            if not all_chunks:
                report.messages.append("Vault enthält keine lesbaren Notizen.")
                return report

            say(f"Erzeuge {len(all_chunks)} Embeddings (das dauert beim ersten Mal) …")
            self.store = self._new_store([c for c, _ in all_chunks], [i for _, i in all_chunks])
            self.manifest["files"] = file_ids
            self.manifest["structure_ids"] = struct_ids
            report.rebuilt = True
            report.total_chunks = len(all_chunks)
        else:
            stale_ids: List[str] = []
            for rel in removed:
                stale_ids.extend(known[rel].get("ids", []))
                self.manifest["files"].pop(rel, None)
            for note in changed:
                stale_ids.extend(known.get(note.rel_path, {}).get("ids", []))
            stale_ids.extend(self.manifest.get("structure_ids", []))
            self._delete_ids(stale_ids)

            new_chunks: List[dict] = []
            new_ids: List[str] = []
            for note in changed:
                pieces = chunk_note(note, self.cfg.chunk_size, self.cfg.chunk_overlap)
                ids = [uuid.uuid4().hex for _ in pieces]
                self.manifest["files"][note.rel_path] = {"checksum": note.checksum, "ids": ids}
                new_chunks.extend(pieces)
                new_ids.extend(ids)

            struct_chunks = folder_summary_chunks(structure)
            struct_ids = [uuid.uuid4().hex for _ in struct_chunks]
            new_chunks.extend(struct_chunks)
            new_ids.extend(struct_ids)
            self.manifest["structure_ids"] = struct_ids

            if new_chunks:
                say(f"Aktualisiere {len(new_chunks)} Abschnitte …")
                self.store.add_texts(
                    texts=[c["text"] for c in new_chunks],
                    metadatas=[c["metadata"] for c in new_chunks],
                    ids=new_ids,
                )
            report.total_chunks = sum(
                len(v.get("ids", [])) for v in self.manifest.get("files", {}).values()
            )

        self.cfg.store_path.mkdir(parents=True, exist_ok=True)
        self.store.save_local(str(self.cfg.store_path))
        self._save_manifest()
        return report

    def _delete_ids(self, ids: Sequence[str]) -> None:
        if not ids or self.store is None:
            return
        docstore = getattr(self.store, "docstore", None)
        known_ids = getattr(docstore, "_dict", {}) if docstore is not None else None
        valid = [i for i in ids if known_ids is None or i in known_ids]
        if valid:
            try:
                self.store.delete(valid)
            except Exception:
                pass

    def add_note_to_index(self, note: Note) -> None:
        """Neue/geänderte Einzelnotiz sofort einindexieren (ohne Vollscan)."""
        if self.store is None:
            self.sync()
            return
        old = self.manifest.get("files", {}).get(note.rel_path, {}).get("ids", [])
        self._delete_ids(old)
        pieces = chunk_note(note, self.cfg.chunk_size, self.cfg.chunk_overlap)
        ids = [uuid.uuid4().hex for _ in pieces]
        if pieces:
            self.store.add_texts(
                texts=[c["text"] for c in pieces],
                metadatas=[c["metadata"] for c in pieces],
                ids=ids,
            )
        self.manifest.setdefault("files", {})[note.rel_path] = {"checksum": note.checksum, "ids": ids}
        self.store.save_local(str(self.cfg.store_path))
        self._save_manifest()

    # ------------------------------------------------------------------ Suche
    def search(
        self,
        query: str,
        k: Optional[int] = None,
        folder: Optional[str] = None,
        include_structure: bool = True,
    ) -> List[Hit]:
        """Hybrid: Vektortreffer + Stichworttreffer, zusammengeführt und entdoppelt."""
        k = k or self.cfg.top_k
        hits: List[Hit] = []

        if self.store is not None:
            try:
                vector = self.embeddings.embed_query(query)
                raw = self.store.similarity_search_with_score_by_vector(vector, k=self.cfg.fetch_k)
            except Exception:
                raw = []
            for doc, distance in raw:
                meta = dict(doc.metadata)
                if meta.get("kind") == "structure" and not include_structure:
                    continue
                if folder and not _folder_matches(meta.get("folder", ""), folder):
                    continue
                hits.append(Hit(doc.page_content, meta, _distance_to_score(distance), "vector"))

        hits.extend(self._lexical_hits(query, folder=folder, limit=max(3, k // 2)))
        return _merge_hits(hits, k)

    def _lexical_hits(self, query: str, folder: Optional[str], limit: int) -> List[Hit]:
        """Fängt das ab, was Embeddings gern verfehlen: exakte Namen, Tags, Dateinamen."""
        q = tokens(query)
        if not q or not self.notes:
            return []
        scored: List[Tuple[float, Note]] = []
        for note in self.notes:
            if folder and not _folder_matches(note.folder, folder):
                continue
            haystack_strong = tokens(" ".join([note.stem, note.title, note.folder, *note.tags]))
            haystack_body = tokens(note.body[:20000])
            if not haystack_strong and not haystack_body:
                continue
            strong = sum(1 for t in q if t in haystack_strong)
            weak = sum(1 for t in q if t in haystack_body)
            score = strong * 1.4 + weak * 0.35 * math.log1p(weak)
            if score > 0:
                scored.append((score, note))
        scored.sort(key=lambda kv: -kv[0])

        out: List[Hit] = []
        best = scored[0][0] if scored else 1.0
        for score, note in scored[:limit]:
            passage = _best_passage(note.body, q)
            header = f"[Vault] Ordner: {note.folder or 'Wurzel'} | Notiz: {note.title} ({note.stem})"
            out.append(
                Hit(
                    text=f"{header}\n\n{passage}",
                    metadata={
                        "source": str(note.path),
                        "rel_path": note.rel_path,
                        "folder": note.folder,
                        "filename": note.path.name,
                        "stem": note.stem,
                        "title": note.title,
                        "headings": "",
                        "tags": ", ".join(note.tags),
                    },
                    score=0.45 + 0.35 * (score / best if best else 0),
                    origin="lexical",
                )
            )
        return out

    # ------------------------------------------------------------------ Extras
    def note_by_rel(self, rel_path: str) -> Optional[Note]:
        return next((n for n in self.notes if n.rel_path == rel_path), None)

    def expand_hit(self, hit: Hit, query: str = "") -> Tuple[str, str]:
        """Liefert statt des Fragments den ganzen Zusammenhang – plus dessen Kapitel.

        Kleine Notizen kommen komplett, große abschnittsweise, aber immer als
        vollständiges Kapitel inklusive Unterabschnitten.

        Wichtig für Stichworttreffer: Die haben keinen Abschnittspfad in den
        Metadaten. Früher wurde deshalb einfach der ANFANG der Notiz geliefert –
        bei einem langen Skript also fast immer die falsche Stelle. Jetzt wird
        der Abschnitt mit der höchsten Stichwortdichte gesucht.
        """
        from .vault import extract_section, split_sections, trim_at_boundary

        rel = hit.metadata.get("rel_path", "")
        if rel.startswith("__struktur__"):
            return hit.text, ""
        note = self.note_by_rel(rel)
        if note is None:
            return hit.text, hit.metadata.get("headings", "")
        if len(note.body) <= self.cfg.full_note_chars:
            return note.body.strip(), "(ganze Notiz)"

        breadcrumb = hit.metadata.get("headings") or ""
        if breadcrumb:
            return extract_section(note.body, breadcrumb, self.cfg.section_max_chars), breadcrumb

        gefragt = set(tokens(query)) if query else set()
        if gefragt:
            bester, bestwert, bester_pfad = "", -1, ""
            for headings, text in split_sections(note.body):
                treffer = sum(1 for t in gefragt if t in set(tokens(text)))
                if treffer > bestwert:
                    bester, bestwert, bester_pfad = text, treffer, " > ".join(headings)
            if bester and bestwert > 0:
                return trim_at_boundary(bester, self.cfg.section_max_chars), bester_pfad
        return trim_at_boundary(note.body, self.cfg.section_max_chars), ""


def _folder_matches(note_folder: str, wanted: str) -> bool:
    if not wanted:
        return True
    nf, w = fold(note_folder), fold(wanted)
    return nf == w or nf.startswith(w + " ") or w in nf


def _distance_to_score(distance: float) -> float:
    """FAISS liefert L2-Distanz; kleiner ist besser -> auf 0..1 abbilden."""
    try:
        return 1.0 / (1.0 + max(0.0, float(distance)))
    except (TypeError, ValueError):
        return 0.0


def _merge_hits(hits: Sequence[Hit], k: int) -> List[Hit]:
    """Beste Treffer je Notiz+Abschnitt behalten, Vielfalt über Notizen erzwingen."""
    best: Dict[Tuple[str, str], Hit] = {}
    for hit in hits:
        key = (hit.metadata.get("rel_path", ""), hit.metadata.get("headings", ""))
        if key not in best or hit.score > best[key].score:
            best[key] = hit

    ordered = sorted(best.values(), key=lambda h: -h.score)
    selected: List[Hit] = []
    per_note: Dict[str, int] = {}
    max_per_note = 2 if k >= 4 else 1
    for hit in ordered:
        rel = hit.metadata.get("rel_path", "")
        if per_note.get(rel, 0) >= max_per_note:
            continue
        selected.append(hit)
        per_note[rel] = per_note.get(rel, 0) + 1
        if len(selected) >= k:
            break
    if len(selected) < k:  # auffüllen, falls zu stark gefiltert
        for hit in ordered:
            if hit not in selected:
                selected.append(hit)
            if len(selected) >= k:
                break
    return selected


def _best_passage(body: str, query_tokens: Sequence[str], window: int = 900) -> str:
    """Fensterausschnitt mit der höchsten Stichwortdichte."""
    if len(body) <= window:
        return body.strip()
    step = max(150, window // 3)
    best_text, best_score = body[:window], -1
    for start in range(0, max(1, len(body) - window), step):
        piece = body[start : start + window]
        toks = set(tokens(piece))
        score = sum(1 for t in query_tokens if t in toks)
        if score > best_score:
            best_text, best_score = piece, score
    return best_text.strip()
