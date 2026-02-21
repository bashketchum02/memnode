"""
Parser for markdown notes with YAML frontmatter.

Handles:
- YAML frontmatter extraction
- Todo item parsing from checkboxes
- Person and project parsing
"""

import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import frontmatter

from .models import Note, NoteType, Person, Priority, Project, TodoItem


class NoteParser:
    """Parse markdown files into structured Note objects."""

    # Regex for todo items: - [ ] or - [x] followed by text
    TODO_PATTERN = re.compile(
        r"^(?P<indent>\s*)-\s*\[(?P<done>[xX ])\]\s*(?P<text>.+)$", re.MULTILINE
    )

    # Patterns within todo text
    PRIORITY_PATTERN = re.compile(r"#(high|medium|low)\b", re.IGNORECASE)
    DUE_DATE_PATTERN = re.compile(r"@(\d{4}-\d{2}-\d{2})")
    TAG_PATTERN = re.compile(r"#(\w+)")
    LINK_LINE_PATTERN = re.compile(r"^\s+link:(.+)$", re.MULTILINE)
    PERSON_LINE_PATTERN = re.compile(r"^\s+person:(.+)$", re.MULTILINE)

    def parse_file(self, path: Path) -> Note:
        """Parse a markdown file into a Note object."""
        post = frontmatter.load(path)

        fm = post.metadata or {}
        content = post.content

        note_type = NoteType(fm.get("type", "note"))
        title = fm.get("title") or self._title_from_path(path)

        note = Note(
            path=path,
            note_type=note_type,
            title=title,
            content=content,
            tags=fm.get("tags", []),
            project=fm.get("project"),
            created_at=self._parse_datetime(fm.get("created_at")),
            updated_at=self._parse_datetime(fm.get("updated_at")),
            frontmatter=fm,
        )

        # Parse todos if this is a todo file or has checkboxes
        if note_type == NoteType.TODO or "- [" in content:
            note.todos = self._parse_todos(content)

        return note

    def parse_person(self, path: Path) -> Person:
        """Parse a person markdown file."""
        post = frontmatter.load(path)
        fm = post.metadata or {}

        slug = path.stem  # filename without extension

        person = Person(
            slug=slug,
            name=fm.get("name", slug.replace("-", " ").title()),
            role=fm.get("role"),
            team=fm.get("team"),
            reports_to=fm.get("reports_to"),
            direct_reports=fm.get("direct_reports", []),
            influence=fm.get("influence"),
            communication_style=fm.get("communication_style"),
            topics=fm.get("topics", []),
            notes=post.content,
            path=path,
        )

        # Parse 1:1 notes from content
        person.one_on_one_notes = self._parse_one_on_ones(post.content)

        return person

    def parse_project(self, path: Path) -> Project:
        """Parse a project markdown file."""
        post = frontmatter.load(path)
        fm = post.metadata or {}

        slug = path.stem

        return Project(
            slug=slug,
            name=fm.get("name", slug.replace("-", " ").title()),
            status=fm.get("status"),
            description=post.content,
            stakeholders=fm.get("stakeholders", []),
            links=fm.get("links", []),
            tags=fm.get("tags", []),
            path=path,
        )

    def _parse_todos(self, content: str) -> list[TodoItem]:
        """Extract todo items from markdown content."""
        todos = []
        lines = content.split("\n")

        for i, line in enumerate(lines):
            match = self.TODO_PATTERN.match(line)
            if match:
                text = match.group("text")
                completed = match.group("done").lower() == "x"

                # Extract metadata from text
                priority = None
                priority_match = self.PRIORITY_PATTERN.search(text)
                if priority_match:
                    priority = Priority(priority_match.group(1).lower())

                due_date = None
                due_match = self.DUE_DATE_PATTERN.search(text)
                if due_match:
                    due_date = date.fromisoformat(due_match.group(1))

                # Get all tags (excluding priority which we already captured)
                tags = [
                    t
                    for t in self.TAG_PATTERN.findall(text)
                    if t.lower() not in ["high", "medium", "low"]
                ]

                # Clean text by removing metadata markers
                clean_text = self.PRIORITY_PATTERN.sub("", text)
                clean_text = self.DUE_DATE_PATTERN.sub("", clean_text)
                clean_text = self.TAG_PATTERN.sub("", clean_text)
                clean_text = clean_text.strip()

                # Look for link: and person: on following indented lines
                links = []
                person_ref = None
                for j in range(i + 1, min(i + 5, len(lines))):  # Check next few lines
                    next_line = lines[j]
                    if not next_line.startswith(" ") and not next_line.startswith("\t"):
                        break
                    link_match = self.LINK_LINE_PATTERN.match(next_line)
                    if link_match:
                        links.append(link_match.group(1).strip())
                    person_match = self.PERSON_LINE_PATTERN.match(next_line)
                    if person_match:
                        person_ref = person_match.group(1).strip()

                todos.append(
                    TodoItem(
                        text=clean_text,
                        completed=completed,
                        priority=priority,
                        due_date=due_date,
                        tags=tags,
                        links=links,
                        person_ref=person_ref,
                        line_number=i + 1,
                    )
                )

        return todos

    def _parse_one_on_ones(self, content: str) -> list[dict]:
        """Extract 1:1 notes from person file content."""
        notes = []
        # Look for ### YYYY-MM-DD headers
        pattern = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
        matches = list(pattern.finditer(content))

        for i, match in enumerate(matches):
            date_str = match.group(1)
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            note_content = content[start:end].strip()

            notes.append({"date": date_str, "content": note_content})

        return notes

    def _title_from_path(self, path: Path) -> str:
        """Generate title from filename."""
        return path.stem.replace("-", " ").replace("_", " ").title()

    def _parse_datetime(self, value) -> Optional[datetime]:
        """Parse datetime from various formats."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None
