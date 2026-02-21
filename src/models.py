"""
Data models for notes, todos, people, and relationships.

Schema Design:
- All notes are markdown files with YAML frontmatter
- Frontmatter contains structured metadata
- Relationships are stored in a separate YAML file for graph queries
- Body contains free-form markdown content
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class NoteType(str, Enum):
    TODO = "todo"
    PERSON = "person"
    PROJECT = "project"
    DECISION = "decision"
    JOURNAL = "journal"
    MEETING = "meeting"
    NOTE = "note"  # generic


class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RelationType(str, Enum):
    """Types of relationships between entities."""
    # Person relationships
    REPORTS_TO = "reports_to"           # person -> person
    WORKS_WITH = "works_with"           # person <-> person
    KNOWS_ABOUT = "knows_about"         # person -> topic
    
    # Project relationships  
    OWNS = "owns"                       # person -> project
    STAKEHOLDER = "stakeholder"         # person -> project
    CONTRIBUTES_TO = "contributes_to"   # person -> project
    
    # Project dependencies
    BLOCKS = "blocks"                   # project -> project
    DEPENDS_ON = "depends_on"           # project -> project
    RELATED_TO = "related_to"           # any <-> any
    
    # Content relationships
    DISCUSSED_IN = "discussed_in"       # topic -> meeting/1on1
    DECIDED_IN = "decided_in"           # topic -> decision
    MENTIONED_IN = "mentioned_in"       # person/project -> note


@dataclass
class Relationship:
    """A directional relationship between two entities."""
    source: str          # slug of source entity
    source_type: str     # person, project, topic, decision, etc.
    relation: RelationType
    target: str          # slug of target entity
    target_type: str
    context: Optional[str] = None  # Additional context
    created_at: Optional[date] = None
    
    def reverse(self) -> Optional["Relationship"]:
        """Get the reverse relationship if applicable."""
        reverse_map = {
            RelationType.REPORTS_TO: None,  # No reverse (hierarchy)
            RelationType.WORKS_WITH: RelationType.WORKS_WITH,
            RelationType.KNOWS_ABOUT: None,
            RelationType.OWNS: None,
            RelationType.STAKEHOLDER: None,
            RelationType.BLOCKS: RelationType.DEPENDS_ON,
            RelationType.DEPENDS_ON: RelationType.BLOCKS,
            RelationType.RELATED_TO: RelationType.RELATED_TO,
        }
        rev_type = reverse_map.get(self.relation)
        if rev_type:
            return Relationship(
                source=self.target,
                source_type=self.target_type,
                relation=rev_type,
                target=self.source,
                target_type=self.source_type,
                context=self.context,
                created_at=self.created_at,
            )
        return None


@dataclass
class TodoItem:
    """A single todo item parsed from markdown checkbox."""

    text: str
    completed: bool = False
    priority: Optional[Priority] = None
    due_date: Optional[date] = None
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    person_ref: Optional[str] = None
    project_ref: Optional[str] = None
    line_number: int = 0

    def to_markdown(self) -> str:
        """Convert back to markdown checkbox format."""
        checkbox = "[x]" if self.completed else "[ ]"
        parts = [f"- {checkbox} {self.text}"]

        if self.priority:
            parts[0] += f" #{self.priority.value}"
        if self.due_date:
            parts[0] += f" @{self.due_date.isoformat()}"
        for tag in self.tags:
            if tag not in [self.priority.value if self.priority else ""]:
                parts[0] += f" #{tag}"

        extras = []
        if self.links:
            for link in self.links:
                extras.append(f"      link:{link}")
        if self.person_ref:
            extras.append(f"      person:{self.person_ref}")
        if self.project_ref:
            extras.append(f"      project:{self.project_ref}")
            
        if extras:
            parts.extend(extras)

        return "\n".join(parts)


@dataclass
class Note:
    """A note file with frontmatter and content."""

    path: Path
    note_type: NoteType
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    project: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    todos: list[TodoItem] = field(default_factory=list)
    frontmatter: dict = field(default_factory=dict)


@dataclass
class Person:
    """A person in your network with context for collaboration."""

    slug: str
    name: str
    role: Optional[str] = None
    team: Optional[str] = None
    reports_to: Optional[str] = None
    direct_reports: list[str] = field(default_factory=list)
    influence: Optional[str] = None  # high/medium/low
    communication_style: Optional[str] = None
    notes: str = ""
    one_on_one_notes: list[dict] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    path: Optional[Path] = None


@dataclass
class Project:
    """A project or initiative you're tracking."""

    slug: str
    name: str
    status: Optional[str] = None  # active, paused, completed
    description: str = ""
    stakeholders: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    path: Optional[Path] = None


@dataclass 
class Decision:
    """A decision record."""
    
    slug: str
    title: str
    date: date
    status: str  # proposed, accepted, superseded
    project: Optional[str] = None
    participants: list[str] = field(default_factory=list)
    context: str = ""  # Why was this decision needed?
    decision: str = ""  # What was decided?
    consequences: str = ""  # What are the implications?
    path: Optional[Path] = None


@dataclass
class Meeting:
    """A meeting or 1:1 record."""
    
    slug: str
    date: date
    meeting_type: str  # 1on1, team, planning, etc.
    participants: list[str] = field(default_factory=list)
    topics_discussed: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    notes: str = ""
    path: Optional[Path] = None


# Templates for CLI to use when creating new entries
TEMPLATES = {
    "person": """---
type: person
name: {name}
role: {role}
team: {team}
reports_to: {reports_to}
influence: {influence}
topics: {topics}
---

# {name}

## Context


## Working Style


## 1:1 Notes

""",
    "project": """---
type: project
name: {name}
status: {status}
stakeholders: {stakeholders}
links: {links}
tags: {tags}
---

# {name}

## Overview


## Goals


## Risks


## Key Decisions

""",
    "decision": """---
type: decision
title: {title}
date: {date}
status: {status}
project: {project}
participants: {participants}
---

# {title}

## Context
Why was this decision needed?

## Decision
What was decided?

## Consequences
What are the implications?

## Alternatives Considered

""",
    "meeting": """---
type: meeting
date: {date}
meeting_type: {meeting_type}
participants: {participants}
---

# {title}

## Topics Discussed


## Action Items

- [ ] 

## Notes

""",
    "journal": """---
type: journal
date: {date}
---

# {date}

## Morning


## Afternoon


## Reflections


## Action Items

- [ ] 

""",
    "todo": """---
type: todo
project: {project}
---

# {title}

""",
}
