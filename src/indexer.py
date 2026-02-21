"""
memnode indexer - SQLite-based index for the knowledge graph.

Key features:
- Indexes all entities (person, project, topic, etc.)
- Extracts entity references from content (person:sarah, project:memnode)
- Maintains relationship graph
- Full-text search across all content
- Auto-discovers implicit relationships from references

Reference format: type:slug (e.g., person:sarah-chen, project:memnode)
"""

import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml


# Entity reference pattern: type:slug
ENTITY_REF_PATTERN = re.compile(
    r'\b(person|project|topic|org|team|decision|meeting):([a-z0-9-]+)\b'
)

# Known entity types and their directories
ENTITY_DIRS = {
    "person": "people",
    "project": "projects",
    "topic": "topics",
    "org": "orgs",
    "team": "teams",
    "decision": "decisions",
    "meeting": "meetings",
}


class MemnodeIndex:
    """SQLite-based index for memnode knowledge graph."""

    def __init__(self, notes_dir: Path, db_path: Optional[Path] = None):
        self.notes_dir = Path(notes_dir).expanduser().resolve()
        self.db_path = db_path or (self.notes_dir / ".memnode.db")
        self.relationships_file = self.notes_dir / ".relationships.yaml"
        self._init_db()

    def _init_db(self):
        """Initialize the SQLite database schema."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

        self.conn.executescript("""
            -- Entities table (unified: person, project, topic, etc.)
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,          -- type:slug (e.g., person:sarah)
                entity_type TEXT NOT NULL,
                slug TEXT NOT NULL,
                name TEXT,
                metadata TEXT,                -- JSON blob for type-specific fields
                content TEXT,                 -- Full markdown content
                path TEXT,                    -- File path relative to notes_dir
                created_at TEXT,
                updated_at TEXT,
                indexed_at TEXT
            );

            -- References table (entity mentions in content)
            CREATE TABLE IF NOT EXISTS refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,      -- Entity containing the reference
                target_id TEXT NOT NULL,      -- Entity being referenced
                context TEXT,                 -- Surrounding text
                line_number INTEGER,
                FOREIGN KEY (source_id) REFERENCES entities(id)
            );

            -- Relationships table (explicit relationships from .relationships.yaml)
            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                context TEXT,
                created_at TEXT,
                UNIQUE(source_id, target_id, relation)
            );

            -- Todos table
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT,               -- Which entity file contains this todo
                text TEXT,
                completed INTEGER DEFAULT 0,
                priority TEXT,
                due_date TEXT,
                tags TEXT,                    -- JSON array
                refs TEXT,                    -- JSON array of entity refs in this todo
                line_number INTEGER,
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            );

            -- Full-text search
            CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
                id,
                name,
                content,
                tokenize='porter'
            );

            -- Indexes
            CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
            CREATE INDEX IF NOT EXISTS idx_entities_slug ON entities(slug);
            CREATE INDEX IF NOT EXISTS idx_refs_source ON refs(source_id);
            CREATE INDEX IF NOT EXISTS idx_refs_target ON refs(target_id);
            CREATE INDEX IF NOT EXISTS idx_rels_source ON relationships(source_id);
            CREATE INDEX IF NOT EXISTS idx_rels_target ON relationships(target_id);
            CREATE INDEX IF NOT EXISTS idx_todos_due ON todos(due_date);
            CREATE INDEX IF NOT EXISTS idx_todos_priority ON todos(priority);
            CREATE INDEX IF NOT EXISTS idx_todos_completed ON todos(completed);
        """)
        self.conn.commit()

    def reindex_all(self):
        """Rebuild the entire index from scratch."""
        self.conn.executescript("""
            DELETE FROM todos;
            DELETE FROM refs;
            DELETE FROM relationships;
            DELETE FROM entities;
            DELETE FROM entities_fts;
        """)

        # Index all entity files
        for entity_type, dir_name in ENTITY_DIRS.items():
            type_dir = self.notes_dir / dir_name
            if type_dir.exists():
                for md_file in type_dir.glob("*.md"):
                    self._index_entity_file(entity_type, md_file)

        # Index special directories (todos, journal)
        self._index_todos_dir()
        self._index_journal_dir()

        # Index relationships from YAML
        self._index_relationships()

        self.conn.commit()

    def _index_entity_file(self, entity_type: str, path: Path):
        """Index a single entity file."""
        try:
            with open(path) as f:
                content = f.read()
        except Exception as e:
            print(f"Warning: Failed to read {path}: {e}")
            return

        slug = path.stem
        entity_id = f"{entity_type}:{slug}"
        rel_path = str(path.relative_to(self.notes_dir))
        now = datetime.now().isoformat()

        # Parse frontmatter
        metadata = {}
        name = slug.replace("-", " ").title()
        body = content

        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                try:
                    metadata = yaml.safe_load(content[3:end]) or {}
                    name = metadata.get("name", name)
                    body = content[end + 3:].strip()
                except yaml.YAMLError:
                    pass

        # Insert entity
        self.conn.execute(
            """
            INSERT OR REPLACE INTO entities 
            (id, entity_type, slug, name, metadata, content, path, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                entity_type,
                slug,
                name,
                json.dumps(metadata),
                content,
                rel_path,
                now,
            ),
        )

        # Insert into FTS
        self.conn.execute(
            "INSERT OR REPLACE INTO entities_fts (id, name, content) VALUES (?, ?, ?)",
            (entity_id, name, content),
        )

        # Extract and index entity references
        self._index_refs(entity_id, content)

        # Extract todos from content
        self._extract_todos(entity_id, content)

    def _index_refs(self, source_id: str, content: str):
        """Extract entity references from content and index them."""
        lines = content.split("\n")
        for line_num, line in enumerate(lines, 1):
            for match in ENTITY_REF_PATTERN.finditer(line):
                target_type = match.group(1)
                target_slug = match.group(2)
                target_id = f"{target_type}:{target_slug}"

                # Get surrounding context (the line)
                context = line.strip()[:200]

                self.conn.execute(
                    """
                    INSERT INTO refs (source_id, target_id, context, line_number)
                    VALUES (?, ?, ?, ?)
                    """,
                    (source_id, target_id, context, line_num),
                )

    def _extract_todos(self, entity_id: str, content: str):
        """Extract todo items from content."""
        todo_pattern = re.compile(
            r'^(\s*)-\s*\[([ xX])\]\s*(.+)$', re.MULTILINE
        )
        priority_pattern = re.compile(r'#(high|medium|low)\b', re.IGNORECASE)
        due_pattern = re.compile(r'@(\d{4}-\d{2}-\d{2})')
        tag_pattern = re.compile(r'#(\w+)')

        lines = content.split("\n")
        for line_num, line in enumerate(lines, 1):
            match = todo_pattern.match(line)
            if match:
                completed = match.group(2).lower() == 'x'
                text = match.group(3)

                # Extract priority
                priority = None
                priority_match = priority_pattern.search(text)
                if priority_match:
                    priority = priority_match.group(1).lower()

                # Extract due date
                due_date = None
                due_match = due_pattern.search(text)
                if due_match:
                    due_date = due_match.group(1)

                # Extract tags (excluding priority)
                tags = [
                    t for t in tag_pattern.findall(text)
                    if t.lower() not in ['high', 'medium', 'low']
                ]

                # Extract entity references in this todo
                refs = [
                    f"{m.group(1)}:{m.group(2)}"
                    for m in ENTITY_REF_PATTERN.finditer(text)
                ]

                self.conn.execute(
                    """
                    INSERT INTO todos 
                    (entity_id, text, completed, priority, due_date, tags, refs, line_number)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        text,
                        1 if completed else 0,
                        priority,
                        due_date,
                        json.dumps(tags),
                        json.dumps(refs),
                        line_num,
                    ),
                )

    def _index_todos_dir(self):
        """Index standalone todo files."""
        todos_dir = self.notes_dir / "todos"
        if not todos_dir.exists():
            return

        for md_file in todos_dir.glob("*.md"):
            # Treat as a pseudo-entity
            slug = md_file.stem
            entity_id = f"todolist:{slug}"

            try:
                with open(md_file) as f:
                    content = f.read()
            except Exception:
                continue

            rel_path = str(md_file.relative_to(self.notes_dir))
            now = datetime.now().isoformat()

            self.conn.execute(
                """
                INSERT OR REPLACE INTO entities 
                (id, entity_type, slug, name, metadata, content, path, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (entity_id, "todolist", slug, slug.replace("-", " ").title(),
                 "{}", content, rel_path, now),
            )

            self._extract_todos(entity_id, content)
            self._index_refs(entity_id, content)

    def _index_journal_dir(self):
        """Index journal entries."""
        journal_dir = self.notes_dir / "journal"
        if not journal_dir.exists():
            return

        for md_file in journal_dir.glob("*.md"):
            slug = md_file.stem  # YYYY-MM-DD
            entity_id = f"journal:{slug}"

            try:
                with open(md_file) as f:
                    content = f.read()
            except Exception:
                continue

            rel_path = str(md_file.relative_to(self.notes_dir))
            now = datetime.now().isoformat()

            self.conn.execute(
                """
                INSERT OR REPLACE INTO entities 
                (id, entity_type, slug, name, metadata, content, path, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (entity_id, "journal", slug, slug, "{}", content, rel_path, now),
            )

            self._extract_todos(entity_id, content)
            self._index_refs(entity_id, content)

    def _index_relationships(self):
        """Index relationships from YAML file."""
        if not self.relationships_file.exists():
            return

        try:
            with open(self.relationships_file) as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            return

        for rel in data.get("relationships", []):
            source_id = f"{rel['source_type']}:{rel['source']}"
            target_id = f"{rel['target_type']}:{rel['target']}"

            self.conn.execute(
                """
                INSERT OR IGNORE INTO relationships 
                (source_id, target_id, relation, context, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    target_id,
                    rel.get("relation", "related_to"),
                    rel.get("context", ""),
                    rel.get("created_at"),
                ),
            )

    # =========================================================================
    # Query Methods
    # =========================================================================

    def get_entity(self, entity_id: str) -> Optional[dict]:
        """Get an entity by ID (type:slug)."""
        cursor = self.conn.execute(
            "SELECT * FROM entities WHERE id = ?", (entity_id,)
        )
        row = cursor.fetchone()
        if row:
            result = dict(row)
            result["metadata"] = json.loads(result["metadata"] or "{}")
            return result
        return None

    def get_entities_by_type(self, entity_type: str) -> list[dict]:
        """Get all entities of a specific type."""
        cursor = self.conn.execute(
            "SELECT * FROM entities WHERE entity_type = ? ORDER BY name",
            (entity_type,),
        )
        results = []
        for row in cursor.fetchall():
            result = dict(row)
            result["metadata"] = json.loads(result["metadata"] or "{}")
            results.append(result)
        return results

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search across all entities."""
        safe_query = '"' + query.replace('"', '""') + '"'
        try:
            cursor = self.conn.execute(
                """
                SELECT e.*, snippet(entities_fts, 2, '>>>', '<<<', '...', 32) as snippet
                FROM entities_fts
                JOIN entities e ON entities_fts.id = e.id
                WHERE entities_fts MATCH ?
                LIMIT ?
                """,
                (safe_query, limit),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            # Fallback to LIKE search
            cursor = self.conn.execute(
                """
                SELECT * FROM entities 
                WHERE content LIKE ? OR name LIKE ?
                LIMIT ?
                """,
                (f"%{query}%", f"%{query}%", limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_references_to(self, entity_id: str) -> list[dict]:
        """Get all references TO an entity (who mentions it)."""
        cursor = self.conn.execute(
            """
            SELECT r.*, e.name as source_name, e.entity_type as source_type
            FROM refs r
            JOIN entities e ON r.source_id = e.id
            WHERE r.target_id = ?
            ORDER BY r.source_id
            """,
            (entity_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_references_from(self, entity_id: str) -> list[dict]:
        """Get all references FROM an entity (what it mentions)."""
        cursor = self.conn.execute(
            """
            SELECT r.*, e.name as target_name, e.entity_type as target_type
            FROM refs r
            LEFT JOIN entities e ON r.target_id = e.id
            WHERE r.source_id = ?
            ORDER BY r.line_number
            """,
            (entity_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_relationships(self, entity_id: str) -> dict:
        """Get all relationships for an entity."""
        outgoing = self.conn.execute(
            """
            SELECT r.*, e.name as target_name
            FROM relationships r
            LEFT JOIN entities e ON r.target_id = e.id
            WHERE r.source_id = ?
            """,
            (entity_id,),
        ).fetchall()

        incoming = self.conn.execute(
            """
            SELECT r.*, e.name as source_name
            FROM relationships r
            LEFT JOIN entities e ON r.source_id = e.id
            WHERE r.target_id = ?
            """,
            (entity_id,),
        ).fetchall()

        return {
            "outgoing": [dict(r) for r in outgoing],
            "incoming": [dict(r) for r in incoming],
        }

    def get_todos(
        self,
        completed: Optional[bool] = None,
        priority: Optional[str] = None,
        entity_id: Optional[str] = None,
        due_before: Optional[date] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query todos with filters."""
        query = "SELECT * FROM todos WHERE 1=1"
        params = []

        if completed is not None:
            query += " AND completed = ?"
            params.append(1 if completed else 0)

        if priority:
            query += " AND priority = ?"
            params.append(priority)

        if entity_id:
            query += " AND entity_id = ?"
            params.append(entity_id)

        if due_before:
            query += " AND due_date IS NOT NULL AND due_date <= ?"
            params.append(due_before.isoformat())

        query += " ORDER BY due_date ASC NULLS LAST, priority ASC LIMIT ?"
        params.append(limit)

        cursor = self.conn.execute(query, params)
        results = []
        for row in cursor.fetchall():
            result = dict(row)
            result["tags"] = json.loads(result["tags"] or "[]")
            result["refs"] = json.loads(result["refs"] or "[]")
            results.append(result)
        return results

    def get_overdue_todos(self) -> list[dict]:
        """Get all overdue, incomplete todos."""
        today = date.today().isoformat()
        return self.get_todos(completed=False, due_before=date.today())

    def who_knows_about(self, topic: str) -> list[dict]:
        """Find people who know about a topic."""
        topic_id = f"topic:{topic}" if ":" not in topic else topic

        # Check explicit relationships (knows, knows_about)
        cursor = self.conn.execute(
            """
            SELECT DISTINCT e.*
            FROM entities e
            JOIN relationships r ON e.id = r.source_id
            WHERE r.target_id = ? AND r.relation IN ('knows', 'knows_about')
            AND e.entity_type = 'person'
            """,
            (topic_id,),
        )
        results = {row["id"]: dict(row) for row in cursor.fetchall()}

        # Also check references (people who mention this topic)
        cursor = self.conn.execute(
            """
            SELECT DISTINCT e.*
            FROM entities e
            JOIN refs r ON e.id = r.source_id
            WHERE r.target_id = ? AND e.entity_type = 'person'
            """,
            (topic_id,),
        )
        for row in cursor.fetchall():
            if row["id"] not in results:
                results[row["id"]] = dict(row)

        return list(results.values())

    def get_entity_context(self, entity_id: str) -> dict:
        """Get full context for an entity: info, relationships, references, todos."""
        entity = self.get_entity(entity_id)
        if not entity:
            return {"error": f"Entity not found: {entity_id}"}

        relationships = self.get_relationships(entity_id)
        refs_to = self.get_references_to(entity_id)
        refs_from = self.get_references_from(entity_id)
        
        # Get todos that reference this entity
        cursor = self.conn.execute(
            "SELECT * FROM todos WHERE refs LIKE ?",
            (f'%"{entity_id}"%',),
        )
        related_todos = [dict(row) for row in cursor.fetchall()]

        return {
            "entity": entity,
            "relationships": relationships,
            "referenced_by": refs_to,
            "references": refs_from,
            "related_todos": related_todos,
        }

    def get_daily_summary(self) -> dict:
        """Get summary for today: overdue, high priority, due soon."""
        from datetime import timedelta

        today = date.today()
        week_out = today + timedelta(days=7)

        overdue = self.get_todos(completed=False, due_before=today)
        high_priority = self.get_todos(completed=False, priority="high", limit=10)
        due_this_week = self.get_todos(completed=False, due_before=week_out, limit=20)

        # Filter out overdue from due_this_week
        overdue_ids = {t["id"] for t in overdue}
        due_this_week = [t for t in due_this_week if t["id"] not in overdue_ids]

        return {
            "date": today.isoformat(),
            "overdue": overdue,
            "high_priority": high_priority,
            "due_this_week": due_this_week,
        }

    def find_path(self, from_id: str, to_id: str, max_depth: int = 4) -> list[dict]:
        """Find relationship path between two entities (BFS)."""
        if from_id == to_id:
            return []

        visited = {from_id}
        queue = [(from_id, [])]

        while queue:
            current, path = queue.pop(0)
            if len(path) >= max_depth:
                continue

            # Check relationships
            rels = self.get_relationships(current)
            for rel in rels["outgoing"]:
                next_id = rel["target_id"]
                step = {"from": current, "to": next_id, "relation": rel["relation"], "direction": "->"}
                if next_id == to_id:
                    return path + [step]
                if next_id not in visited:
                    visited.add(next_id)
                    queue.append((next_id, path + [step]))

            for rel in rels["incoming"]:
                next_id = rel["source_id"]
                step = {"from": next_id, "to": current, "relation": rel["relation"], "direction": "<-"}
                if next_id == to_id:
                    return path + [step]
                if next_id not in visited:
                    visited.add(next_id)
                    queue.append((next_id, path + [step]))

        return []

    def close(self):
        """Close database connection."""
        self.conn.close()


# Alias for backwards compatibility
NotesIndex = MemnodeIndex
