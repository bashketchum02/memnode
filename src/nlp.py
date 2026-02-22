"""
NLP utilities for intelligent entity extraction and relationship inference.

Key features:
- Alias management (explicit aliases + auto-generated from names)
- Fuzzy entity matching using rapidfuzz
- Named Entity Recognition using spaCy
- TF-IDF vectorization for content similarity
- Co-occurrence analysis for relationship inference
"""

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process

# Lazy imports for optional heavy dependencies
_spacy_nlp = None
_tfidf_vectorizer = None


def get_spacy_nlp():
    """Lazy load spaCy model."""
    global _spacy_nlp
    if _spacy_nlp is None:
        try:
            import spacy
            try:
                _spacy_nlp = spacy.load("en_core_web_sm")
            except OSError:
                # Model not installed, download it
                import subprocess
                subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"], check=True)
                _spacy_nlp = spacy.load("en_core_web_sm")
        except ImportError:
            return None
    return _spacy_nlp


@dataclass
class EntityAlias:
    """An alias mapping to a canonical entity."""
    alias: str              # The alias text (e.g., "Sarah", "Platform v2")
    entity_id: str          # Canonical entity ID (e.g., "person:sarah-chen")
    alias_type: str         # "explicit" (user-defined) or "auto" (derived from name)
    confidence: float = 1.0 # Confidence score (1.0 for explicit, lower for auto)


@dataclass 
class InferredEntity:
    """An entity mention found via NLP (not explicit type:slug)."""
    text: str               # The text that was matched
    entity_id: str          # Best matching entity ID
    confidence: float       # Match confidence (0-1)
    start_pos: int          # Character position in content
    end_pos: int
    context: str            # Surrounding text
    match_type: str         # "alias", "fuzzy", "ner"


@dataclass
class InferredRelationship:
    """A relationship inferred from content analysis."""
    source_id: str
    target_id: str
    relation: str           # e.g., "mentioned_with", "discussed_together"
    confidence: float       # 0-1 confidence score
    evidence: list[str] = field(default_factory=list)  # Supporting context snippets
    inference_type: str = "co_occurrence"  # "co_occurrence", "semantic", "ner"


class AliasManager:
    """Manages entity aliases for fuzzy matching."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.aliases: dict[str, EntityAlias] = {}  # alias_lower -> EntityAlias
        self.entity_aliases: dict[str, list[str]] = defaultdict(list)  # entity_id -> [aliases]
        self._init_db()
    
    def _init_db(self):
        """Ensure aliases table exists."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS aliases (
                alias TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                alias_type TEXT NOT NULL,  -- 'explicit' or 'auto'
                confidence REAL DEFAULT 1.0,
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            );
            CREATE INDEX IF NOT EXISTS idx_aliases_entity ON aliases(entity_id);
        """)
        conn.commit()
        conn.close()
        self._load_aliases()
    
    def _load_aliases(self):
        """Load aliases from database into memory."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT alias, entity_id, alias_type, confidence FROM aliases")
        for row in cursor.fetchall():
            alias_lower = row[0].lower()
            self.aliases[alias_lower] = EntityAlias(
                alias=row[0],
                entity_id=row[1],
                alias_type=row[2],
                confidence=row[3]
            )
            self.entity_aliases[row[1]].append(row[0])
        conn.close()
    
    def add_alias(self, alias: str, entity_id: str, alias_type: str = "explicit", 
                  confidence: float = 1.0):
        """Add an alias for an entity."""
        alias_lower = alias.lower()
        
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO aliases (alias, entity_id, alias_type, confidence)
            VALUES (?, ?, ?, ?)
        """, (alias, entity_id, alias_type, confidence))
        conn.commit()
        conn.close()
        
        self.aliases[alias_lower] = EntityAlias(alias, entity_id, alias_type, confidence)
        if alias not in self.entity_aliases[entity_id]:
            self.entity_aliases[entity_id].append(alias)
    
    def remove_alias(self, alias: str):
        """Remove an alias."""
        alias_lower = alias.lower()
        if alias_lower in self.aliases:
            entity_id = self.aliases[alias_lower].entity_id
            del self.aliases[alias_lower]
            if alias in self.entity_aliases[entity_id]:
                self.entity_aliases[entity_id].remove(alias)
        
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM aliases WHERE LOWER(alias) = ?", (alias_lower,))
        conn.commit()
        conn.close()
    
    def get_entity_for_alias(self, alias: str) -> Optional[EntityAlias]:
        """Look up entity by exact alias match."""
        return self.aliases.get(alias.lower())
    
    def get_aliases_for_entity(self, entity_id: str) -> list[str]:
        """Get all aliases for an entity."""
        return self.entity_aliases.get(entity_id, [])
    
    def generate_auto_aliases(self, entity_id: str, name: str, entity_type: str):
        """Generate automatic aliases from entity name."""
        aliases_to_add = []
        
        # Full name
        aliases_to_add.append((name, 0.95))
        
        if entity_type == "person":
            # First name
            parts = name.split()
            if parts:
                aliases_to_add.append((parts[0], 0.85))
            # First + Last initial (e.g., "Sarah C")
            if len(parts) >= 2:
                aliases_to_add.append((f"{parts[0]} {parts[-1][0]}", 0.80))
            # Initials (e.g., "SC")
            if len(parts) >= 2:
                initials = "".join(p[0].upper() for p in parts)
                aliases_to_add.append((initials, 0.60))  # Lower confidence for initials
        
        elif entity_type == "project":
            # Common variations
            # "Platform V2" -> "platform v2", "platform-v2"
            slug_form = name.lower().replace(" ", "-")
            aliases_to_add.append((name.lower(), 0.90))
            # Without hyphens for matching
            aliases_to_add.append((name.lower().replace("-", " "), 0.90))
        
        # Add all generated aliases
        for alias, conf in aliases_to_add:
            # Don't add if it's too short or too common
            if len(alias) >= 2:
                self.add_alias(alias, entity_id, "auto", conf)
    
    def clear_auto_aliases(self, entity_id: str):
        """Clear auto-generated aliases for an entity (before regenerating)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "DELETE FROM aliases WHERE entity_id = ? AND alias_type = 'auto'",
            (entity_id,)
        )
        conn.commit()
        conn.close()
        
        # Update in-memory cache
        self.entity_aliases[entity_id] = [
            a for a in self.entity_aliases.get(entity_id, [])
            if self.aliases.get(a.lower(), EntityAlias("", "", "auto")).alias_type == "explicit"
        ]
        # Reload from DB
        self._load_aliases()


class EntityMatcher:
    """Matches text to entities using multiple strategies."""
    
    def __init__(self, alias_manager: AliasManager, db_path: Path):
        self.alias_manager = alias_manager
        self.db_path = db_path
        self._entity_names: dict[str, str] = {}  # entity_id -> name
        self._load_entity_names()
    
    def _load_entity_names(self):
        """Load entity names for fuzzy matching."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT id, name FROM entities")
        for row in cursor.fetchall():
            self._entity_names[row[0]] = row[1]
        conn.close()
    
    def refresh(self):
        """Refresh entity names from database."""
        self._load_entity_names()
    
    def match_text(self, text: str, min_confidence: float = 0.7) -> Optional[tuple[str, float, str]]:
        """
        Try to match text to an entity.
        
        Returns: (entity_id, confidence, match_type) or None
        """
        text_lower = text.lower().strip()
        
        # 1. Exact alias match (highest confidence)
        alias = self.alias_manager.get_entity_for_alias(text)
        if alias:
            return (alias.entity_id, alias.confidence, "alias")
        
        # 2. Fuzzy match against all aliases
        all_aliases = list(self.alias_manager.aliases.keys())
        if all_aliases:
            result = process.extractOne(
                text_lower, 
                all_aliases,
                scorer=fuzz.WRatio,
                score_cutoff=min_confidence * 100
            )
            if result:
                matched_alias, score, _ = result
                alias_obj = self.alias_manager.aliases[matched_alias]
                # Combine fuzzy score with alias confidence
                combined_conf = (score / 100) * alias_obj.confidence
                if combined_conf >= min_confidence:
                    return (alias_obj.entity_id, combined_conf, "fuzzy_alias")
        
        # 3. Fuzzy match against entity names directly
        if self._entity_names:
            names_list = list(self._entity_names.values())
            ids_list = list(self._entity_names.keys())
            result = process.extractOne(
                text,
                names_list,
                scorer=fuzz.WRatio,
                score_cutoff=min_confidence * 100
            )
            if result:
                matched_name, score, idx = result
                entity_id = ids_list[idx]
                if score / 100 >= min_confidence:
                    return (entity_id, score / 100, "fuzzy_name")
        
        return None
    
    def find_entities_in_text(self, content: str, min_confidence: float = 0.75) -> list[InferredEntity]:
        """
        Find all entity mentions in text using NLP.
        
        Uses:
        1. spaCy NER for PERSON, ORG, PRODUCT entities
        2. Pattern matching for potential entity mentions
        3. Fuzzy matching against known entities
        """
        found_entities = []
        
        # Skip explicit type:slug patterns (those are already indexed)
        explicit_pattern = re.compile(r'\b(person|project|topic|org|team|decision|meeting):([a-z0-9-]+)\b')
        explicit_spans = [(m.start(), m.end()) for m in explicit_pattern.finditer(content)]
        
        def is_in_explicit_span(start: int, end: int) -> bool:
            for es, ee in explicit_spans:
                if start >= es and end <= ee:
                    return True
            return False
        
        # 1. Use spaCy NER
        nlp = get_spacy_nlp()
        if nlp:
            doc = nlp(content)
            for ent in doc.ents:
                if ent.label_ in ("PERSON", "ORG", "PRODUCT", "GPE", "WORK_OF_ART"):
                    if is_in_explicit_span(ent.start_char, ent.end_char):
                        continue
                    
                    match = self.match_text(ent.text, min_confidence)
                    if match:
                        entity_id, confidence, match_type = match
                        # Get surrounding context
                        ctx_start = max(0, ent.start_char - 50)
                        ctx_end = min(len(content), ent.end_char + 50)
                        context = content[ctx_start:ctx_end].strip()
                        
                        found_entities.append(InferredEntity(
                            text=ent.text,
                            entity_id=entity_id,
                            confidence=confidence,
                            start_pos=ent.start_char,
                            end_pos=ent.end_char,
                            context=context,
                            match_type=f"ner_{match_type}"
                        ))
        
        # 2. Scan for capitalized phrases (potential names/projects)
        # Pattern: 2-4 capitalized words together
        cap_pattern = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b')
        for match in cap_pattern.finditer(content):
            if is_in_explicit_span(match.start(), match.end()):
                continue
            
            # Skip if already found by NER
            already_found = any(
                e.start_pos == match.start() and e.end_pos == match.end()
                for e in found_entities
            )
            if already_found:
                continue
            
            text = match.group(1)
            entity_match = self.match_text(text, min_confidence)
            if entity_match:
                entity_id, confidence, match_type = entity_match
                ctx_start = max(0, match.start() - 50)
                ctx_end = min(len(content), match.end() + 50)
                context = content[ctx_start:ctx_end].strip()
                
                found_entities.append(InferredEntity(
                    text=text,
                    entity_id=entity_id,
                    confidence=confidence,
                    start_pos=match.start(),
                    end_pos=match.end(),
                    context=context,
                    match_type=f"pattern_{match_type}"
                ))
        
        return found_entities


class RelationshipInferrer:
    """Infers relationships from content using co-occurrence and TF-IDF."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Ensure inferred_relationships table exists."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS inferred_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                confidence REAL NOT NULL,
                evidence TEXT,              -- JSON array of context snippets
                inference_type TEXT,        -- 'co_occurrence', 'semantic', 'ner'
                last_updated TEXT,
                UNIQUE(source_id, target_id, relation)
            );
            CREATE INDEX IF NOT EXISTS idx_inferred_source ON inferred_relationships(source_id);
            CREATE INDEX IF NOT EXISTS idx_inferred_target ON inferred_relationships(target_id);
            CREATE INDEX IF NOT EXISTS idx_inferred_confidence ON inferred_relationships(confidence);
            
            -- TF-IDF vectors stored as JSON (entity_id -> vector)
            CREATE TABLE IF NOT EXISTS entity_vectors (
                entity_id TEXT PRIMARY KEY,
                tfidf_vector TEXT,          -- JSON sparse vector
                updated_at TEXT
            );
        """)
        conn.commit()
        conn.close()
    
    def compute_cooccurrence(self, source_id: str, window_size: int = 3) -> list[InferredRelationship]:
        """
        Compute co-occurrence relationships for an entity.
        
        Looks at which entities appear near each other in content.
        window_size: number of lines to consider as "co-occurring"
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        # Get all references from documents that mention source_id
        cursor = conn.execute("""
            SELECT DISTINCT r2.target_id, r2.line_number, r1.source_id as doc_id
            FROM refs r1
            JOIN refs r2 ON r1.source_id = r2.source_id
            WHERE r1.target_id = ?
            AND r2.target_id != ?
        """, (source_id, source_id))
        
        # Group by document and find co-occurrences within window
        doc_refs: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for row in cursor.fetchall():
            doc_refs[row["doc_id"]].append((row["target_id"], row["line_number"]))
        
        # Also get source's own line numbers in each doc
        cursor = conn.execute("""
            SELECT source_id, line_number FROM refs WHERE target_id = ?
        """, (source_id,))
        source_lines: dict[str, list[int]] = defaultdict(list)
        for row in cursor.fetchall():
            source_lines[row["source_id"]].append(row["line_number"])
        
        # Count co-occurrences
        cooccur_count: dict[str, int] = defaultdict(int)
        cooccur_evidence: dict[str, list[str]] = defaultdict(list)
        
        for doc_id, refs in doc_refs.items():
            src_lines = source_lines.get(doc_id, [])
            for target_id, target_line in refs:
                # Check if any source line is within window of target line
                for src_line in src_lines:
                    if abs(src_line - target_line) <= window_size:
                        cooccur_count[target_id] += 1
                        # Get context
                        cursor = conn.execute(
                            "SELECT context FROM refs WHERE source_id = ? AND target_id = ? AND line_number = ?",
                            (doc_id, target_id, target_line)
                        )
                        ctx_row = cursor.fetchone()
                        if ctx_row and ctx_row["context"]:
                            cooccur_evidence[target_id].append(ctx_row["context"][:100])
                        break
        
        conn.close()
        
        # Convert counts to relationships with confidence
        relationships = []
        max_count = max(cooccur_count.values()) if cooccur_count else 1
        
        for target_id, count in cooccur_count.items():
            # Confidence based on count (log scale to prevent outliers)
            import math
            confidence = min(0.95, 0.5 + 0.3 * math.log(count + 1) / math.log(max_count + 1))
            
            relationships.append(InferredRelationship(
                source_id=source_id,
                target_id=target_id,
                relation="mentioned_with",
                confidence=confidence,
                evidence=cooccur_evidence[target_id][:5],  # Keep top 5 evidence
                inference_type="co_occurrence"
            ))
        
        return relationships
    
    def compute_tfidf_similarity(self, entity_id: str, top_k: int = 10) -> list[tuple[str, float]]:
        """
        Find similar entities using TF-IDF on content.
        
        Returns: list of (entity_id, similarity_score)
        """
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np
        from datetime import datetime
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        # Get all entity content
        cursor = conn.execute("SELECT id, content FROM entities WHERE content IS NOT NULL")
        entities = {row["id"]: row["content"] for row in cursor.fetchall()}
        conn.close()
        
        if entity_id not in entities or len(entities) < 2:
            return []
        
        # Build TF-IDF matrix
        entity_ids = list(entities.keys())
        contents = [entities[eid] for eid in entity_ids]
        
        vectorizer = TfidfVectorizer(
            max_features=1000,
            stop_words='english',
            ngram_range=(1, 2),
            min_df=1
        )
        
        try:
            tfidf_matrix = vectorizer.fit_transform(contents)
        except ValueError:
            return []
        
        # Find index of target entity
        try:
            target_idx = entity_ids.index(entity_id)
        except ValueError:
            return []
        
        # Compute similarities
        target_vector = tfidf_matrix[target_idx]
        similarities = cosine_similarity(target_vector, tfidf_matrix).flatten()
        
        # Get top-k similar (excluding self)
        similar_indices = np.argsort(similarities)[::-1]
        results = []
        
        for idx in similar_indices:
            if idx != target_idx and similarities[idx] > 0.1:
                results.append((entity_ids[idx], float(similarities[idx])))
                if len(results) >= top_k:
                    break
        
        return results
    
    def save_inferred_relationship(self, rel: InferredRelationship):
        """Save an inferred relationship to the database."""
        from datetime import datetime
        
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO inferred_relationships
            (source_id, target_id, relation, confidence, evidence, inference_type, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            rel.source_id,
            rel.target_id,
            rel.relation,
            rel.confidence,
            json.dumps(rel.evidence),
            rel.inference_type,
            datetime.now().isoformat()
        ))
        conn.commit()
        conn.close()
    
    def get_inferred_relationships(self, entity_id: str, min_confidence: float = 0.5) -> list[dict]:
        """Get inferred relationships for an entity."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        cursor = conn.execute("""
            SELECT * FROM inferred_relationships
            WHERE (source_id = ? OR target_id = ?)
            AND confidence >= ?
            ORDER BY confidence DESC
        """, (entity_id, entity_id, min_confidence))
        
        results = []
        for row in cursor.fetchall():
            result = dict(row)
            result["evidence"] = json.loads(result["evidence"] or "[]")
            results.append(result)
        
        conn.close()
        return results
    
    def clear_inferred_for_entity(self, entity_id: str):
        """Clear inferred relationships involving an entity (for re-inference)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "DELETE FROM inferred_relationships WHERE source_id = ? OR target_id = ?",
            (entity_id, entity_id)
        )
        conn.commit()
        conn.close()


class InferredRefManager:
    """Manages inferred entity references (from NLP, not explicit type:slug)."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Ensure inferred_refs table exists."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS inferred_refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                matched_text TEXT,
                confidence REAL NOT NULL,
                context TEXT,
                start_pos INTEGER,
                end_pos INTEGER,
                match_type TEXT,
                FOREIGN KEY (source_id) REFERENCES entities(id)
            );
            CREATE INDEX IF NOT EXISTS idx_inferred_refs_source ON inferred_refs(source_id);
            CREATE INDEX IF NOT EXISTS idx_inferred_refs_target ON inferred_refs(target_id);
        """)
        conn.commit()
        conn.close()
    
    def save_inferred_refs(self, source_id: str, refs: list[InferredEntity]):
        """Save inferred references for a source entity."""
        conn = sqlite3.connect(self.db_path)
        
        # Clear existing inferred refs for this source
        conn.execute("DELETE FROM inferred_refs WHERE source_id = ?", (source_id,))
        
        # Insert new refs
        for ref in refs:
            conn.execute("""
                INSERT INTO inferred_refs 
                (source_id, target_id, matched_text, confidence, context, start_pos, end_pos, match_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                source_id,
                ref.entity_id,
                ref.text,
                ref.confidence,
                ref.context,
                ref.start_pos,
                ref.end_pos,
                ref.match_type
            ))
        
        conn.commit()
        conn.close()
    
    def get_inferred_refs_to(self, entity_id: str, min_confidence: float = 0.7) -> list[dict]:
        """Get inferred references TO an entity."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        cursor = conn.execute("""
            SELECT ir.*, e.name as source_name, e.entity_type as source_type
            FROM inferred_refs ir
            JOIN entities e ON ir.source_id = e.id
            WHERE ir.target_id = ? AND ir.confidence >= ?
            ORDER BY ir.confidence DESC
        """, (entity_id, min_confidence))
        
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results
    
    def get_inferred_refs_from(self, source_id: str, min_confidence: float = 0.7) -> list[dict]:
        """Get inferred references FROM an entity."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        cursor = conn.execute("""
            SELECT ir.*, e.name as target_name, e.entity_type as target_type
            FROM inferred_refs ir
            LEFT JOIN entities e ON ir.target_id = e.id
            WHERE ir.source_id = ? AND ir.confidence >= ?
            ORDER BY ir.confidence DESC
        """, (source_id, min_confidence))
        
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results
