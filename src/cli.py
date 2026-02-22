"""
memnode CLI - Personal knowledge graph for engineers.

This is the PRIMARY interface for creating and modifying data.
The MCP server is read-only and uses this data for synthesis.

Syntax:
    memnode add <type>:<name>           # Add an entity
    memnode link <from> <to> [--as X]   # Create relationship
    memnode list [type]                 # List entities
    memnode show <type>:<name>          # Show entity details
"""

import os
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Optional
import re

import typer
import yaml
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

app = typer.Typer(
    name="memnode",
    help="Personal knowledge graph for engineers",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()

# =============================================================================
# Entity Types and Templates
# =============================================================================

ENTITY_TYPES = {
    "person": {
        "dir": "people",
        "template": """---
type: person
name: {name}
role: {role}
team: {team}
---

# {name}

## Context


## Working Style


## 1:1 Notes

""",
        "fields": ["role", "team"],
    },
    "project": {
        "dir": "projects",
        "template": """---
type: project
name: {name}
status: {status}
---

# {name}

## Overview


## Goals


## Risks


## Key Decisions

""",
        "fields": ["status"],
        "defaults": {"status": "active"},
    },
    "topic": {
        "dir": "topics",
        "template": """---
type: topic
name: {name}
---

# {name}

## Overview


## Key Resources


## Related Topics

""",
        "fields": [],
    },
    "org": {
        "dir": "orgs",
        "template": """---
type: org
name: {name}
---

# {name}

## Overview


## Key Contacts


## Notes

""",
        "fields": [],
    },
    "team": {
        "dir": "teams",
        "template": """---
type: team
name: {name}
---

# {name}

## Overview


## Members


## Responsibilities

""",
        "fields": [],
    },
    "decision": {
        "dir": "decisions",
        "template": """---
type: decision
name: {name}
date: {date}
status: {status}
---

# {name}

## Context
Why was this decision needed?

## Decision
What was decided?

## Consequences
What are the implications?

""",
        "fields": ["status"],
        "defaults": {"status": "accepted"},
        "auto_fields": {"date": lambda: date.today().isoformat()},
    },
    "meeting": {
        "dir": "meetings",
        "template": """---
type: meeting
name: {name}
date: {date}
---

# {name}

## Attendees


## Agenda


## Notes


## Action Items

- [ ] 

""",
        "fields": [],
        "auto_fields": {"date": lambda: date.today().isoformat()},
    },
}

# Default relationship types based on source->target
DEFAULT_RELATIONS = {
    ("person", "topic"): "knows",
    ("person", "project"): "stakeholder",
    ("person", "team"): "member_of",
    ("person", "org"): "works_at",
    ("person", "person"): "works_with",
    ("project", "project"): "related_to",
    ("project", "team"): "owned_by",
}

VALID_RELATIONS = [
    # Person relations
    "knows",           # person -> topic
    "owns",            # person -> project
    "stakeholder",     # person -> project
    "contributes_to",  # person -> project
    "reports_to",      # person -> person
    "works_with",      # person -> person
    "member_of",       # person -> team/org
    "works_at",        # person -> org
    "leads",           # person -> team/project
    # Project relations
    "blocks",          # project -> project
    "depends_on",      # project -> project
    "related_to",      # anything -> anything
    "owned_by",        # project -> team
    "part_of",         # anything -> anything
]


# =============================================================================
# Utility Functions
# =============================================================================


def get_notes_dir() -> Path:
    """Get the notes directory from env or default."""
    dir_path = os.environ.get("MEMNODE_DIR") or os.environ.get("NOTES_DIR") or "~/memnode"
    return Path(dir_path).expanduser().resolve()


def get_index():
    """Get the memnode index (lazy-loaded singleton)."""
    from .indexer import MemnodeIndex
    notes_dir = get_notes_dir()
    db_path = notes_dir / ".memnode.db"
    return MemnodeIndex(notes_dir, db_path)


def smart_index_entity(entity_type: str, slug: str, show_progress: bool = True):
    """
    Index a single entity with NLP processing.
    
    This is the inline post-edit hook - runs NLP indexing immediately
    after any file edit. Keeps the graph smart without a background daemon.
    """
    notes_dir = get_notes_dir()
    entity_id = f"{entity_type}:{slug}"
    path = get_entity_path(entity_type, slug)
    
    if not path.exists():
        return
    
    try:
        from .watcher import SmartIndexer
        
        if show_progress:
            rprint(f"[dim]Indexing {entity_id}...[/dim]", end=" ")
        
        smart_indexer = SmartIndexer(notes_dir, enable_nlp=True)
        smart_indexer.process_file(path)
        
        if show_progress:
            rprint(f"[green]done[/green]")
            
    except ImportError:
        # Fall back to basic indexing if NLP deps not installed
        if show_progress:
            rprint(f"[dim]Indexing {entity_id} (basic)...[/dim]", end=" ")
        try:
            index = get_index()
            index.index_entity(entity_type, slug)
            index.close()
            if show_progress:
                rprint(f"[green]done[/green]")
        except Exception:
            if show_progress:
                rprint(f"[yellow]skipped[/yellow]")
    except Exception as e:
        if show_progress:
            rprint(f"[yellow]error: {e}[/yellow]")


def update_index(entity_type: str, slug: str):
    """Update the index for a single entity (legacy, uses smart_index_entity)."""
    smart_index_entity(entity_type, slug, show_progress=False)


def update_relationships_index():
    """Update the relationships in the index."""
    try:
        index = get_index()
        index.index_relationships()
        index.close()
    except Exception:
        pass  # Silently ignore indexing errors in CLI


def get_relationships_file() -> Path:
    """Get the relationships YAML file path."""
    return get_notes_dir() / ".relationships.yaml"


def load_relationships() -> list[dict]:
    """Load relationships from YAML file."""
    path = get_relationships_file()
    if not path.exists():
        return []
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("relationships", [])


def save_relationships(relationships: list[dict]):
    """Save relationships to YAML file."""
    path = get_relationships_file()
    with open(path, "w") as f:
        yaml.dump({"relationships": relationships}, f, default_flow_style=False)


def slugify(text: str) -> str:
    """Convert text to a slug."""
    # Handle already slugified text
    if re.match(r'^[a-z0-9-]+$', text):
        return text
    return text.lower().replace(" ", "-").replace("_", "-")


def parse_entity(entity: str) -> tuple[str, str]:
    """Parse entity string like 'person:sarah-chen' into (type, name)."""
    if ":" not in entity:
        raise typer.BadParameter(
            f"Invalid entity format: '{entity}'. Use type:name (e.g., person:sarah)"
        )
    
    entity_type, name = entity.split(":", 1)
    entity_type = entity_type.lower()
    
    if entity_type not in ENTITY_TYPES:
        valid = ", ".join(ENTITY_TYPES.keys())
        raise typer.BadParameter(
            f"Unknown entity type: '{entity_type}'. Valid types: {valid}"
        )
    
    return entity_type, slugify(name)


def get_entity_path(entity_type: str, slug: str) -> Path:
    """Get the file path for an entity."""
    notes_dir = get_notes_dir()
    type_info = ENTITY_TYPES[entity_type]
    return notes_dir / type_info["dir"] / f"{slug}.md"


def entity_exists(entity_type: str, slug: str) -> bool:
    """Check if an entity exists."""
    return get_entity_path(entity_type, slug).exists()


def open_in_editor(path: Path):
    """Open a file in the user's preferred editor."""
    editor = os.environ.get("EDITOR", "vim")
    subprocess.run([editor, str(path)])


def get_existing_entities(entity_type: str) -> list[str]:
    """Get existing slugs for an entity type."""
    notes_dir = get_notes_dir()
    type_info = ENTITY_TYPES.get(entity_type)
    if not type_info:
        return []
    type_dir = notes_dir / type_info["dir"]
    if not type_dir.exists():
        return []
    return [f.stem for f in type_dir.glob("*.md")]


# =============================================================================
# Main Commands
# =============================================================================


@app.command()
def add(
    entity: str = typer.Argument(
        ...,
        help="Entity to add as type:name (e.g., person:sarah, project:memnode)",
    ),
    edit: bool = typer.Option(True, "--edit/--no-edit", "-e/-E", help="Open in editor after creating"),
):
    """
    Add a new entity to your knowledge graph.
    
    Examples:
        memnode add person:sarah-chen
        memnode add project:memnode
        memnode add topic:kubernetes
        memnode add team:platform
        memnode add org:acme-corp
        memnode add decision:use-postgres
    """
    entity_type, slug = parse_entity(entity)
    type_info = ENTITY_TYPES[entity_type]
    
    # Get the path
    path = get_entity_path(entity_type, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Check if exists
    if path.exists():
        if not Confirm.ask(f"[yellow]{entity} already exists. Overwrite?[/yellow]"):
            raise typer.Exit(0)
    
    # Build template data
    name = slug.replace("-", " ").title()
    template_data = {"name": name}
    
    # Add defaults
    if "defaults" in type_info:
        template_data.update(type_info["defaults"])
    
    # Add auto-generated fields
    if "auto_fields" in type_info:
        for field, generator in type_info["auto_fields"].items():
            template_data[field] = generator()
    
    # Interactive prompts for additional fields
    if type_info.get("fields"):
        rprint(Panel(f"Adding {entity_type}: [cyan]{name}[/cyan]", style="blue"))
        
        # Allow editing the display name
        new_name = Prompt.ask("Name", default=name)
        if new_name != name:
            template_data["name"] = new_name
        
        for field in type_info["fields"]:
            if field not in template_data:
                default = type_info.get("defaults", {}).get(field, "")
                value = Prompt.ask(field.replace("_", " ").title(), default=default)
                template_data[field] = value
    
    # Generate content
    content = type_info["template"].format(**template_data)
    
    # Write file
    with open(path, "w") as f:
        f.write(content)
    
    rprint(f"[green]✓[/green] Created {entity_type}:{slug}")
    
    if edit:
        open_in_editor(path)
        # Smart index after editor closes (NLP processing)
        smart_index_entity(entity_type, slug)
    else:
        # Still index even without editing
        smart_index_entity(entity_type, slug)


@app.command()
def link(
    source: str = typer.Argument(..., help="Source entity (e.g., person:sarah)"),
    target: str = typer.Argument(..., help="Target entity (e.g., project:memnode)"),
    relation: Optional[str] = typer.Option(
        None, "--as", "-a",
        help="Relationship type (e.g., owns, knows, blocks). Auto-detected if not specified.",
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c",
        help="Additional context for the relationship",
    ),
):
    """
    Create a relationship between two entities.
    
    Examples:
        memnode link person:sarah project:memnode --as owns
        memnode link person:sarah topic:kubernetes
        memnode link project:api project:frontend --as blocks
        memnode link person:alice person:bob --as reports_to
    """
    source_type, source_slug = parse_entity(source)
    target_type, target_slug = parse_entity(target)
    
    # Auto-detect relation if not specified
    if not relation:
        relation = DEFAULT_RELATIONS.get(
            (source_type, target_type),
            "related_to"
        )
    
    # Validate relation
    if relation not in VALID_RELATIONS:
        rprint(f"[yellow]Warning:[/yellow] '{relation}' is not a standard relation type")
        rprint(f"[dim]Standard types: {', '.join(VALID_RELATIONS)}[/dim]")
        if not Confirm.ask("Use anyway?"):
            raise typer.Exit(0)
    
    # Load existing relationships
    relationships = load_relationships()
    
    # Check for duplicates
    for r in relationships:
        if (r["source"] == source_slug and 
            r["source_type"] == source_type and
            r["target"] == target_slug and
            r["target_type"] == target_type and
            r["relation"] == relation):
            rprint(f"[yellow]Relationship already exists[/yellow]")
            return
    
    # Add relationship
    relationships.append({
        "source": source_slug,
        "source_type": source_type,
        "relation": relation,
        "target": target_slug,
        "target_type": target_type,
        "context": context or "",
        "created_at": date.today().isoformat(),
    })
    
    save_relationships(relationships)
    
    # Update index
    update_relationships_index()
    
    rprint(f"[green]✓[/green] {source_type}:{source_slug} --[{relation}]--> {target_type}:{target_slug}")


@app.command()
def unlink(
    source: str = typer.Argument(..., help="Source entity"),
    target: str = typer.Argument(..., help="Target entity"),
    relation: Optional[str] = typer.Option(None, "--as", "-a", help="Specific relation to remove"),
):
    """
    Remove a relationship between two entities.
    
    Examples:
        memnode unlink person:sarah project:memnode
        memnode unlink person:sarah project:memnode --as owns
    """
    source_type, source_slug = parse_entity(source)
    target_type, target_slug = parse_entity(target)
    
    relationships = load_relationships()
    original_count = len(relationships)
    
    relationships = [
        r for r in relationships
        if not (
            r["source"] == source_slug and
            r["source_type"] == source_type and
            r["target"] == target_slug and
            r["target_type"] == target_type and
            (relation is None or r["relation"] == relation)
        )
    ]
    
    removed = original_count - len(relationships)
    if removed > 0:
        save_relationships(relationships)
        update_relationships_index()
        rprint(f"[green]✓[/green] Removed {removed} relationship(s)")
    else:
        rprint("[yellow]No matching relationships found[/yellow]")


@app.command("list")
def list_entities(
    entity_type: Optional[str] = typer.Argument(
        None,
        help="Entity type to list (person, project, topic, etc.). Lists all if not specified.",
    ),
):
    """
    List entities in your knowledge graph.
    
    Examples:
        memnode list              # List all entities
        memnode list person       # List all people
        memnode list project      # List all projects
    """
    notes_dir = get_notes_dir()
    
    if entity_type:
        # List specific type
        if entity_type not in ENTITY_TYPES:
            valid = ", ".join(ENTITY_TYPES.keys())
            rprint(f"[red]Error:[/red] Unknown type '{entity_type}'. Valid: {valid}")
            raise typer.Exit(1)
        
        type_info = ENTITY_TYPES[entity_type]
        type_dir = notes_dir / type_info["dir"]
        
        if not type_dir.exists() or not list(type_dir.glob("*.md")):
            rprint(f"[dim]No {entity_type}s yet[/dim]")
            return
        
        table = Table(title=f"{entity_type.title()}s")
        table.add_column("Entity", style="cyan")
        table.add_column("Name")
        
        for path in sorted(type_dir.glob("*.md")):
            with open(path) as f:
                content = f.read()
            
            name = path.stem.replace("-", " ").title()
            if content.startswith("---"):
                end = content.find("---", 3)
                if end > 0:
                    fm = yaml.safe_load(content[3:end]) or {}
                    name = fm.get("name", name)
            
            table.add_row(f"{entity_type}:{path.stem}", name)
        
        console.print(table)
    
    else:
        # List all types with counts
        table = Table(title="Knowledge Graph")
        table.add_column("Type", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Directory", style="dim")
        
        total = 0
        for etype, info in ENTITY_TYPES.items():
            type_dir = notes_dir / info["dir"]
            count = len(list(type_dir.glob("*.md"))) if type_dir.exists() else 0
            total += count
            if count > 0:
                table.add_row(etype, str(count), info["dir"] + "/")
        
        # Add relationships count
        rels = load_relationships()
        table.add_row("relationships", str(len(rels)), ".relationships.yaml")
        
        console.print(table)
        rprint(f"\n[dim]Total entities: {total}[/dim]")


@app.command()
def show(
    entity: str = typer.Argument(..., help="Entity to show (e.g., person:sarah)"),
):
    """
    Show details and relationships for an entity.
    
    Examples:
        memnode show person:sarah
        memnode show project:memnode
    """
    entity_type, slug = parse_entity(entity)
    entity_id = f"{entity_type}:{slug}"
    path = get_entity_path(entity_type, slug)
    
    if not path.exists():
        rprint(f"[red]Error:[/red] {entity} not found")
        raise typer.Exit(1)
    
    # Read file content
    with open(path) as f:
        content = f.read()
    
    # Parse frontmatter
    name = slug.replace("-", " ").title()
    metadata = {}
    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            metadata = yaml.safe_load(content[3:end]) or {}
            name = metadata.get("name", name)
    
    rprint(Panel(f"{entity_type}: [bold]{name}[/bold]", style="blue"))
    
    # Show metadata
    for key, value in metadata.items():
        if key not in ["type", "name"] and value:
            rprint(f"[dim]{key}:[/dim] {value}")
    
    # Show explicit relationships
    relationships = load_relationships()
    outgoing = [r for r in relationships if r["source"] == slug and r["source_type"] == entity_type]
    incoming = [r for r in relationships if r["target"] == slug and r["target_type"] == entity_type]
    
    if outgoing or incoming:
        rprint("\n[bold]Relationships:[/bold]")
        for r in outgoing:
            rprint(f"  --\\[{r['relation']}]--> {r['target_type']}:{r['target']}")
        for r in incoming:
            rprint(f"  <--\\[{r['relation']}]-- {r['source_type']}:{r['source']}")
    
    # Show inferred relationships
    try:
        from .nlp import RelationshipInferrer
        notes_dir = get_notes_dir()
        db_path = notes_dir / ".memnode.db"
        
        if db_path.exists():
            inferrer = RelationshipInferrer(db_path)
            inferred = inferrer.get_inferred_relationships(entity_id, min_confidence=0.5)
            
            if inferred:
                rprint("\n[bold]Inferred relationships:[/bold] [dim](from mentions & co-occurrence)[/dim]")
                for r in inferred[:10]:  # Limit to top 10
                    if r["source_id"] == entity_id:
                        rprint(f"  [dim]--\\[{r['relation']}]-->[/dim] {r['target_id']} [dim]({r['confidence']:.0%})[/dim]")
                    else:
                        rprint(f"  [dim]<--\\[{r['relation']}]--[/dim] {r['source_id']} [dim]({r['confidence']:.0%})[/dim]")
                if len(inferred) > 10:
                    rprint(f"  [dim]... and {len(inferred) - 10} more[/dim]")
    except Exception:
        pass  # Silently skip if NLP components not available
    
    rprint(f"\n[dim]File: {path}[/dim]")


@app.command()
def edit(
    entity: str = typer.Argument(..., help="Entity to edit (e.g., person:sarah)"),
):
    """
    Open an entity in your editor.
    
    Examples:
        memnode edit person:sarah
        memnode edit project:memnode
    """
    entity_type, slug = parse_entity(entity)
    path = get_entity_path(entity_type, slug)
    
    if not path.exists():
        rprint(f"[red]Error:[/red] {entity} not found")
        rprint(f"[dim]Create with: memnode add {entity}[/dim]")
        raise typer.Exit(1)
    
    open_in_editor(path)
    # Smart index after editor closes (NLP processing)
    smart_index_entity(entity_type, slug)


@app.command()
def rm(
    entity: str = typer.Argument(..., help="Entity to remove (e.g., person:sarah)"),
    force: bool = typer.Option(False, "--force", "-f", help="Don't ask for confirmation"),
):
    """
    Remove an entity and its relationships.
    
    Examples:
        memnode rm person:sarah
        memnode rm project:old-project --force
    """
    entity_type, slug = parse_entity(entity)
    path = get_entity_path(entity_type, slug)
    
    if not path.exists():
        rprint(f"[red]Error:[/red] {entity} not found")
        raise typer.Exit(1)
    
    # Count relationships that will be removed
    relationships = load_relationships()
    related = [
        r for r in relationships
        if (r["source"] == slug and r["source_type"] == entity_type) or
           (r["target"] == slug and r["target_type"] == entity_type)
    ]
    
    if not force:
        rprint(f"Will remove: {entity}")
        if related:
            rprint(f"Will also remove {len(related)} relationship(s)")
        if not Confirm.ask("Continue?"):
            raise typer.Exit(0)
    
    # Remove relationships
    if related:
        relationships = [r for r in relationships if r not in related]
        save_relationships(relationships)
        update_relationships_index()
    
    # Remove file
    path.unlink()
    
    # Update index (remove entity)
    update_index(entity_type, slug)
    
    rprint(f"[green]✓[/green] Removed {entity}")
    if related:
        rprint(f"[green]✓[/green] Removed {len(related)} relationship(s)")


# =============================================================================
# Special Commands
# =============================================================================


@app.command()
def capture(
    text: str = typer.Argument(..., help="Text to capture to inbox"),
):
    """
    Quickly capture a thought to the inbox.
    
    Examples:
        memnode capture "Look into that tool Sarah mentioned"
        memnode capture "Review RFC for auth system"
    """
    notes_dir = get_notes_dir()
    inbox_path = notes_dir / "todos" / "inbox.md"
    inbox_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not inbox_path.exists():
        content = f"""---
type: todo
---

# Inbox

- [ ] {text}  <!-- {timestamp} -->
"""
    else:
        with open(inbox_path) as f:
            content = f.read()
        content += f"\n- [ ] {text}  <!-- {timestamp} -->"

    with open(inbox_path, "w") as f:
        f.write(content)

    # Smart index (NLP processing for entity extraction)
    smart_index_entity("todolist", "inbox")
    
    rprint(f"[green]✓[/green] Captured: {text}")


@app.command("1on1")
def one_on_one(
    person: str = typer.Argument(..., help="Person entity (e.g., person:sarah or just sarah)"),
):
    """
    Add a 1:1 note for a person.
    
    Examples:
        memnode 1on1 person:sarah
        memnode 1on1 sarah
    """
    # Allow shorthand without type prefix
    if ":" not in person:
        person = f"person:{person}"
    
    entity_type, slug = parse_entity(person)
    
    if entity_type != "person":
        rprint(f"[red]Error:[/red] 1on1 is only for people, not {entity_type}")
        raise typer.Exit(1)
    
    path = get_entity_path(entity_type, slug)
    
    if not path.exists():
        rprint(f"[red]Error:[/red] {person} not found")
        rprint(f"[dim]Create with: memnode add {person}[/dim]")
        raise typer.Exit(1)
    
    today = date.today().isoformat()
    
    template = f"""
### {today}

**Topics discussed:**
- 

**Their concerns/interests:**
- 

**Action items:**
- [ ] 

**Follow-ups:**
- 

"""
    
    with open(path) as f:
        content = f.read()
    
    if "## 1:1 Notes" in content:
        pos = content.find("## 1:1 Notes") + len("## 1:1 Notes")
        content = content[:pos] + "\n" + template + content[pos:]
    else:
        content += "\n## 1:1 Notes\n" + template
    
    with open(path, "w") as f:
        f.write(content)
    
    rprint(f"[green]✓[/green] Added 1:1 entry for {slug}")
    open_in_editor(path)
    # Smart index after editor closes (NLP processing)
    smart_index_entity(entity_type, slug)


@app.command()
def todo(
    text: str = typer.Argument(..., help="Todo text"),
    priority: Optional[str] = typer.Option(None, "--priority", "-p", help="high/medium/low"),
    due: Optional[str] = typer.Option(None, "--due", "-d", help="Due date YYYY-MM-DD"),
    project: Optional[str] = typer.Option(None, "--project", "-P", help="Project (e.g., project:memnode or just memnode)"),
):
    """
    Add a todo item.
    
    Examples:
        memnode todo "Review the RFC" --priority high --due 2025-03-01
        memnode todo "Update docs" --project memnode
    """
    notes_dir = get_notes_dir()
    todos_dir = notes_dir / "todos"
    todos_dir.mkdir(parents=True, exist_ok=True)
    
    # Parse project if specified
    project_slug = None
    if project:
        if ":" in project:
            _, project_slug = parse_entity(project)
        else:
            project_slug = slugify(project)
    
    # Determine which file to add to
    if project_slug:
        todo_path = todos_dir / f"{project_slug}.md"
    else:
        todo_path = todos_dir / "work.md"
    
    # Build the todo line
    todo_line = f"- [ ] {text}"
    if priority:
        todo_line += f" #{priority}"
    if due:
        todo_line += f" @{due}"
    
    if not todo_path.exists():
        content = f"""---
type: todo
project: {project_slug or 'general'}
---

# Todos

{todo_line}
"""
    else:
        with open(todo_path) as f:
            content = f.read()
        content += f"\n{todo_line}"
    
    with open(todo_path, "w") as f:
        f.write(content)
    
    # Smart index (NLP processing for entity extraction)
    smart_index_entity("todolist", todo_path.stem)
    
    rprint(f"[green]✓[/green] Added todo to {todo_path.name}")


@app.command()
def journal():
    """
    Create or open today's journal entry.
    """
    notes_dir = get_notes_dir()
    journal_dir = notes_dir / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    
    today = date.today().isoformat()
    path = journal_dir / f"{today}.md"
    
    if path.exists():
        rprint(f"[dim]Opening existing journal for {today}[/dim]")
    else:
        content = f"""---
type: journal
date: {today}
---

# {today}

## Morning


## Afternoon


## Reflections


## Action Items

- [ ] 

"""
        with open(path, "w") as f:
            f.write(content)
        
        rprint(f"[green]✓[/green] Created journal for {today}")
    
    open_in_editor(path)
    # Smart index after editor closes (NLP processing)
    smart_index_entity("journal", today)


# =============================================================================
# Config Commands
# =============================================================================


@app.command()
def init():
    """Initialize notes directory structure."""
    notes_dir = get_notes_dir()
    
    # Create all entity type directories
    dirs = [info["dir"] for info in ENTITY_TYPES.values()]
    dirs.extend(["todos", "journal"])  # Special directories
    dirs = list(set(dirs))  # Remove duplicates
    
    for d in dirs:
        (notes_dir / d).mkdir(parents=True, exist_ok=True)
    
    # Create relationships file
    rel_file = get_relationships_file()
    if not rel_file.exists():
        save_relationships([])
    
    # Create inbox
    inbox = notes_dir / "todos" / "inbox.md"
    if not inbox.exists():
        with open(inbox, "w") as f:
            f.write("""---
type: todo
---

# Inbox

""")
    
    rprint(f"[green]✓[/green] Initialized memnode at {notes_dir}")
    rprint("\nDirectories:")
    for d in sorted(dirs):
        rprint(f"  [dim]└──[/dim] {d}/")
    
    # Build initial index
    from .indexer import MemnodeIndex
    rprint("\n[dim]Building search index...[/dim]")
    index = MemnodeIndex(notes_dir)
    index.reindex_all()
    index.close()
    rprint(f"[green]✓[/green] Index ready")


@app.command()
def config():
    """Show current configuration."""
    notes_dir = get_notes_dir()
    env_var = os.environ.get("MEMNODE_DIR") or os.environ.get("NOTES_DIR")
    
    rprint(Panel("memnode configuration", style="blue"))
    rprint()
    
    rprint(f"[bold]Notes directory:[/bold] {notes_dir}")
    if env_var:
        rprint(f"[dim]  (from environment variable)[/dim]")
    else:
        rprint(f"[dim]  (default - set MEMNODE_DIR to change)[/dim]")
    rprint()
    
    if notes_dir.exists():
        rprint(f"[green]✓[/green] Directory exists")
        
        # Count by type
        total = 0
        for etype, info in ENTITY_TYPES.items():
            type_dir = notes_dir / info["dir"]
            count = len(list(type_dir.glob("*.md"))) if type_dir.exists() else 0
            if count > 0:
                rprint(f"  {etype}: {count}")
                total += count
        
        rels = load_relationships()
        rprint(f"  relationships: {len(rels)}")
        rprint(f"\n[dim]Total entities: {total}[/dim]")
    else:
        rprint(f"[yellow]![/yellow] Directory does not exist")
        rprint(f"[dim]  Run 'memnode init' to create it[/dim]")
    
    rprint()
    rprint("[bold]To change directory:[/bold]")
    rprint("  export MEMNODE_DIR=/path/to/your/notes")


@app.command()
def reindex(
    no_nlp: bool = typer.Option(
        False, "--no-nlp",
        help="Skip NLP processing (faster, but no fuzzy matching or inferred relationships)"
    ),
):
    """Rebuild the search index from scratch (with NLP by default)."""
    from .indexer import MemnodeIndex
    
    notes_dir = get_notes_dir()
    if not notes_dir.exists():
        rprint(f"[red]Error:[/red] Notes directory does not exist: {notes_dir}")
        rprint(f"[dim]Run 'memnode init' first[/dim]")
        raise typer.Exit(1)
    
    if no_nlp:
        rprint(f"[dim]Reindexing {notes_dir} (basic mode)...[/dim]")
        index = MemnodeIndex(notes_dir)
        index.reindex_all()
        index.close()
        rprint(f"[green]✓[/green] Index rebuilt (basic)")
    else:
        rprint(f"[dim]Reindexing {notes_dir} with NLP processing...[/dim]")
        try:
            from .watcher import SmartIndexer
            smart_indexer = SmartIndexer(notes_dir, enable_nlp=True)
            smart_indexer.full_reindex_with_nlp()
            rprint(f"[green]✓[/green] Index rebuilt (aliases + inferred relationships)")
        except ImportError as e:
            rprint(f"[yellow]Warning:[/yellow] NLP dependencies not available: {e}")
            rprint(f"[dim]Falling back to basic indexing...[/dim]")
            index = MemnodeIndex(notes_dir)
            index.reindex_all()
            index.close()
            rprint(f"[green]✓[/green] Index rebuilt (basic)")
            rprint(f"[dim]For smart indexing: pip install spacy rapidfuzz scikit-learn[/dim]")


@app.command()
def watch(
    no_nlp: bool = typer.Option(
        False, "--no-nlp",
        help="Disable NLP processing (faster, but no fuzzy matching)"
    ),
    debounce: float = typer.Option(
        2.0, "--debounce", "-d",
        help="Debounce delay in seconds"
    ),
    reindex_first: bool = typer.Option(
        False, "--reindex", "-r",
        help="Do a full reindex before starting the watcher"
    ),
):
    """
    Watch for file changes and auto-index.
    
    Runs a daemon that watches your notes directory and automatically
    re-indexes files when they change. Like a web crawler for your knowledge graph.
    
    Press Ctrl+C to stop.
    """
    notes_dir = get_notes_dir()
    if not notes_dir.exists():
        rprint(f"[red]Error:[/red] Notes directory does not exist: {notes_dir}")
        rprint(f"[dim]Run 'memnode init' first[/dim]")
        raise typer.Exit(1)
    
    try:
        from .watcher import MemnodeWatcher
    except ImportError as e:
        rprint(f"[red]Error:[/red] Watcher dependencies not installed: {e}")
        rprint(f"[dim]Run: pip install watchdog spacy rapidfuzz scikit-learn[/dim]")
        raise typer.Exit(1)
    
    rprint(Panel(
        f"[bold]Watching:[/bold] {notes_dir}\n"
        f"[bold]NLP:[/bold] {'disabled' if no_nlp else 'enabled'}\n"
        f"[bold]Debounce:[/bold] {debounce}s\n\n"
        "[dim]Press Ctrl+C to stop[/dim]",
        title="memnode watcher",
        style="blue"
    ))
    
    watcher = MemnodeWatcher(
        notes_dir=notes_dir,
        enable_nlp=not no_nlp,
        debounce_delay=debounce
    )
    
    if reindex_first:
        rprint("[dim]Running initial reindex...[/dim]")
        watcher.smart_indexer.full_reindex_with_nlp()
        rprint("[green]✓[/green] Initial reindex complete")
    
    rprint("\n[green]Watcher started.[/green] Editing files will trigger re-indexing.\n")
    
    try:
        watcher.start(blocking=True)
    except KeyboardInterrupt:
        pass
    
    rprint("\n[dim]Watcher stopped[/dim]")


def main():
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
