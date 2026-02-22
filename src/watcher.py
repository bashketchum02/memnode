"""
File watcher for automatic incremental indexing.

Watches the notes directory and triggers smart re-indexing when files change.
Like a web crawler, but for your local knowledge graph.

Usage:
    # As a daemon (runs in background)
    memnode watch
    
    # Or programmatically
    from src.watcher import MemnodeWatcher
    watcher = MemnodeWatcher(notes_dir)
    watcher.start()  # Blocks, watching for changes
"""

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .indexer import ENTITY_DIRS, MemnodeIndex

logger = logging.getLogger(__name__)


# Debounce delay in seconds (avoid re-indexing on every keystroke during editing)
DEBOUNCE_DELAY = 2.0

# File patterns to watch
WATCH_PATTERNS = {".md", ".yaml", ".yml"}


class DebouncedHandler(FileSystemEventHandler):
    """
    File system event handler with debouncing.
    
    Collects file changes and processes them after a delay,
    batching multiple rapid changes to the same file.
    """
    
    def __init__(
        self, 
        callback: Callable[[set[Path]], None],
        debounce_delay: float = DEBOUNCE_DELAY
    ):
        self.callback = callback
        self.debounce_delay = debounce_delay
        self._pending_files: set[Path] = set()
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
    
    def _should_process(self, path: Path) -> bool:
        """Check if this file should trigger indexing."""
        # Only watch markdown and yaml files
        if path.suffix.lower() not in WATCH_PATTERNS:
            return False
        
        # Ignore hidden files (except .relationships.yaml)
        if path.name.startswith(".") and path.name != ".relationships.yaml":
            return False
        
        # Ignore temporary/backup files
        if path.name.endswith("~") or path.name.endswith(".swp"):
            return False
        
        return True
    
    def _schedule_callback(self):
        """Schedule the callback after debounce delay."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
            
            self._timer = threading.Timer(self.debounce_delay, self._execute_callback)
            self._timer.start()
    
    def _execute_callback(self):
        """Execute callback with accumulated files."""
        with self._lock:
            if not self._pending_files:
                return
            files = self._pending_files.copy()
            self._pending_files.clear()
        
        try:
            self.callback(files)
        except Exception as e:
            logger.error(f"Error in watcher callback: {e}")
    
    def on_modified(self, event: FileSystemEvent):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if self._should_process(path):
            logger.debug(f"File modified: {path}")
            with self._lock:
                self._pending_files.add(path)
            self._schedule_callback()
    
    def on_created(self, event: FileSystemEvent):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if self._should_process(path):
            logger.debug(f"File created: {path}")
            with self._lock:
                self._pending_files.add(path)
            self._schedule_callback()
    
    def on_deleted(self, event: FileSystemEvent):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if self._should_process(path):
            logger.debug(f"File deleted: {path}")
            with self._lock:
                self._pending_files.add(path)
            self._schedule_callback()
    
    def on_moved(self, event: FileSystemEvent):
        if event.is_directory:
            return
        # Handle both source and destination
        src_path = Path(event.src_path)
        dest_path = Path(event.dest_path)
        
        with self._lock:
            if self._should_process(src_path):
                self._pending_files.add(src_path)
            if self._should_process(dest_path):
                self._pending_files.add(dest_path)
        
        if self._pending_files:
            self._schedule_callback()


class SmartIndexer:
    """
    Smart incremental indexer that uses NLP to infer relationships.
    
    This is the "crawler" that processes files when they change.
    """
    
    def __init__(self, notes_dir: Path, enable_nlp: bool = True):
        self.notes_dir = Path(notes_dir).expanduser().resolve()
        self.enable_nlp = enable_nlp
        self.index = MemnodeIndex(self.notes_dir)
        
        # Lazy-load NLP components
        self._alias_manager = None
        self._entity_matcher = None
        self._relationship_inferrer = None
        self._inferred_ref_manager = None
    
    @property
    def alias_manager(self):
        if self._alias_manager is None:
            from .nlp import AliasManager
            self._alias_manager = AliasManager(self.index.db_path)
        return self._alias_manager
    
    @property
    def entity_matcher(self):
        if self._entity_matcher is None:
            from .nlp import EntityMatcher
            self._entity_matcher = EntityMatcher(self.alias_manager, self.index.db_path)
        return self._entity_matcher
    
    @property
    def relationship_inferrer(self):
        if self._relationship_inferrer is None:
            from .nlp import RelationshipInferrer
            self._relationship_inferrer = RelationshipInferrer(self.index.db_path)
        return self._relationship_inferrer
    
    @property
    def inferred_ref_manager(self):
        if self._inferred_ref_manager is None:
            from .nlp import InferredRefManager
            self._inferred_ref_manager = InferredRefManager(self.index.db_path)
        return self._inferred_ref_manager
    
    def _path_to_entity_info(self, path: Path) -> Optional[tuple[str, str]]:
        """
        Convert a file path to (entity_type, slug).
        
        Returns None if the path doesn't correspond to an entity.
        """
        try:
            rel_path = path.relative_to(self.notes_dir)
        except ValueError:
            return None
        
        parts = rel_path.parts
        if len(parts) < 2:
            return None
        
        dir_name = parts[0]
        filename = parts[-1]
        
        if not filename.endswith(".md"):
            return None
        
        slug = filename[:-3]  # Remove .md
        
        # Map directory to entity type
        dir_to_type = {v: k for k, v in ENTITY_DIRS.items()}
        dir_to_type["journal"] = "journal"
        dir_to_type["todos"] = "todolist"
        
        entity_type = dir_to_type.get(dir_name)
        if entity_type:
            return (entity_type, slug)
        
        return None
    
    def process_file(self, path: Path):
        """
        Process a single file: index it and run NLP inference.
        
        This is called when a file is created/modified/deleted.
        """
        entity_info = self._path_to_entity_info(path)
        
        if path.name == ".relationships.yaml":
            # Reindex explicit relationships
            logger.info("Reindexing relationships from .relationships.yaml")
            self.index.index_relationships()
            return
        
        if not entity_info:
            logger.debug(f"Skipping non-entity file: {path}")
            return
        
        entity_type, slug = entity_info
        entity_id = f"{entity_type}:{slug}"
        
        if not path.exists():
            # File was deleted
            logger.info(f"Entity deleted: {entity_id}")
            self.index._delete_entity_data(entity_id)
            self.index.conn.commit()
            
            # Clean up NLP data
            if self.enable_nlp:
                self.alias_manager.clear_auto_aliases(entity_id)
                self.relationship_inferrer.clear_inferred_for_entity(entity_id)
            return
        
        # Index the entity
        logger.info(f"Indexing entity: {entity_id}")
        self.index.index_entity(entity_type, slug)
        
        # Run NLP processing
        if self.enable_nlp:
            self._process_entity_nlp(entity_id, path)
    
    def _process_entity_nlp(self, entity_id: str, path: Path):
        """Run NLP processing on an entity."""
        try:
            with open(path) as f:
                content = f.read()
        except Exception as e:
            logger.error(f"Failed to read {path}: {e}")
            return
        
        # Get entity info
        entity = self.index.get_entity(entity_id)
        if not entity:
            return
        
        entity_type = entity["entity_type"]
        name = entity["name"]
        
        # 1. Generate/update aliases
        logger.debug(f"Generating aliases for {entity_id}")
        self.alias_manager.clear_auto_aliases(entity_id)
        self.alias_manager.generate_auto_aliases(entity_id, name, entity_type)
        
        # Also add explicit aliases from frontmatter if present
        metadata = entity.get("metadata", {})
        if isinstance(metadata, str):
            import json
            try:
                metadata = json.loads(metadata)
            except:
                metadata = {}
        
        explicit_aliases = metadata.get("aliases", [])
        for alias in explicit_aliases:
            self.alias_manager.add_alias(alias, entity_id, "explicit", 1.0)
        
        # Refresh entity matcher with new aliases
        self.entity_matcher.refresh()
        
        # 2. Find inferred entity references using NLP
        logger.debug(f"Finding inferred references in {entity_id}")
        inferred_refs = self.entity_matcher.find_entities_in_text(content)
        self.inferred_ref_manager.save_inferred_refs(entity_id, inferred_refs)
        
        if inferred_refs:
            logger.info(f"Found {len(inferred_refs)} inferred references in {entity_id}")
        
        # 3. Compute co-occurrence relationships
        logger.debug(f"Computing co-occurrence for {entity_id}")
        cooccur_rels = self.relationship_inferrer.compute_cooccurrence(entity_id)
        for rel in cooccur_rels:
            if rel.confidence >= 0.5:  # Only save reasonably confident relationships
                self.relationship_inferrer.save_inferred_relationship(rel)
        
        if cooccur_rels:
            logger.info(f"Found {len(cooccur_rels)} co-occurrence relationships for {entity_id}")
    
    def process_files(self, paths: set[Path]):
        """Process multiple files (batch processing)."""
        for path in paths:
            self.process_file(path)
    
    def full_reindex_with_nlp(self):
        """
        Full reindex with NLP processing.
        
        Use this for initial setup or to rebuild everything.
        """
        logger.info("Starting full reindex with NLP...")
        start_time = time.time()
        
        # First, do the basic reindex
        self.index.reindex_all()
        
        if not self.enable_nlp:
            logger.info(f"Basic reindex completed in {time.time() - start_time:.2f}s")
            return
        
        # Clear all NLP data
        conn = self.index.conn
        conn.executescript("""
            DELETE FROM aliases WHERE alias_type = 'auto';
            DELETE FROM inferred_refs;
            DELETE FROM inferred_relationships;
        """)
        conn.commit()
        
        # Process all entities
        cursor = conn.execute("SELECT id, path FROM entities WHERE path IS NOT NULL")
        entities = cursor.fetchall()
        
        for entity_id, rel_path in entities:
            path = self.notes_dir / rel_path
            if path.exists():
                self._process_entity_nlp(entity_id, path)
        
        # Compute TF-IDF similarities for all entities
        logger.info("Computing TF-IDF similarities...")
        cursor = conn.execute("SELECT id FROM entities")
        for (entity_id,) in cursor.fetchall():
            similar = self.relationship_inferrer.compute_tfidf_similarity(entity_id, top_k=5)
            for other_id, similarity in similar:
                if similarity >= 0.3:  # Threshold for "similar"
                    from .nlp import InferredRelationship
                    rel = InferredRelationship(
                        source_id=entity_id,
                        target_id=other_id,
                        relation="similar_to",
                        confidence=similarity,
                        evidence=[],
                        inference_type="tfidf"
                    )
                    self.relationship_inferrer.save_inferred_relationship(rel)
        
        elapsed = time.time() - start_time
        logger.info(f"Full reindex with NLP completed in {elapsed:.2f}s")


class MemnodeWatcher:
    """
    File system watcher for automatic indexing.
    
    Watches the notes directory and triggers smart re-indexing
    when files are created, modified, or deleted.
    """
    
    def __init__(
        self, 
        notes_dir: Path, 
        enable_nlp: bool = True,
        debounce_delay: float = DEBOUNCE_DELAY
    ):
        self.notes_dir = Path(notes_dir).expanduser().resolve()
        self.enable_nlp = enable_nlp
        self.debounce_delay = debounce_delay
        
        self.smart_indexer = SmartIndexer(self.notes_dir, enable_nlp)
        self.observer = Observer()
        self._running = False
    
    def _on_files_changed(self, files: set[Path]):
        """Callback when files change."""
        logger.info(f"Processing {len(files)} changed file(s)...")
        self.smart_indexer.process_files(files)
        logger.info("Done processing changes")
    
    def start(self, blocking: bool = True):
        """
        Start watching for file changes.
        
        Args:
            blocking: If True, blocks the current thread. If False, runs in background.
        """
        if self._running:
            logger.warning("Watcher is already running")
            return
        
        handler = DebouncedHandler(
            callback=self._on_files_changed,
            debounce_delay=self.debounce_delay
        )
        
        self.observer.schedule(handler, str(self.notes_dir), recursive=True)
        self.observer.start()
        self._running = True
        
        logger.info(f"Watching for changes in {self.notes_dir}")
        logger.info(f"NLP processing: {'enabled' if self.enable_nlp else 'disabled'}")
        logger.info(f"Debounce delay: {self.debounce_delay}s")
        
        if blocking:
            try:
                while self._running:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.stop()
    
    def stop(self):
        """Stop watching for file changes."""
        if not self._running:
            return
        
        logger.info("Stopping watcher...")
        self._running = False
        self.observer.stop()
        self.observer.join()
        logger.info("Watcher stopped")
    
    def is_running(self) -> bool:
        return self._running


def main():
    """CLI entry point for the watcher daemon."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Memnode file watcher daemon")
    parser.add_argument(
        "--notes-dir", "-d",
        type=Path,
        default=Path("~/notes"),
        help="Notes directory to watch (default: ~/notes)"
    )
    parser.add_argument(
        "--no-nlp",
        action="store_true",
        help="Disable NLP processing (faster, but no fuzzy matching)"
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=DEBOUNCE_DELAY,
        help=f"Debounce delay in seconds (default: {DEBOUNCE_DELAY})"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Do a full reindex before starting the watcher"
    )
    
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )
    
    notes_dir = args.notes_dir.expanduser().resolve()
    if not notes_dir.exists():
        logger.error(f"Notes directory does not exist: {notes_dir}")
        return 1
    
    watcher = MemnodeWatcher(
        notes_dir=notes_dir,
        enable_nlp=not args.no_nlp,
        debounce_delay=args.debounce
    )
    
    if args.reindex:
        logger.info("Running full reindex...")
        watcher.smart_indexer.full_reindex_with_nlp()
    
    watcher.start(blocking=True)
    return 0


if __name__ == "__main__":
    exit(main())
