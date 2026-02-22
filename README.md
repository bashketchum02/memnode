# memnode

> Personal knowledge graph for engineers. Tag entities, Build context automatically

memnode is an opinionated knowledge management system where you tag entities inline using `type:name` syntax. The graph builds itself from your notes.

```
┌────────────────────────────────────────────────────────────────────┐
│  You write naturally:                                              │
│                                                                    │
│    Had a meeting with person:sarah about project:memnode.          │
│    She knows topic:kubernetes well. Need to sync with              │
│    team:platform about the blockers.                               │
│                                                                    │
│  memnode automatically extracts:                                   │
│                                                                    │
│    person:sarah ──────┐                                            │
│           │           │                                            │
│           ▼           ▼                                            │
│    topic:kubernetes  project:memnode ◄─── team:platform            │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

## Philosophy

1. **Tag, don't organize** - Just write `person:sarah` or `project:memnode` inline. No folders, no hierarchy.
2. **References ARE relationships** - Mentioning an entity in context creates a discoverable link.
3. **Plain text, portable** - Markdown files + SQLite index. Works with any editor.
4. **AI-ready** - MCP server for intelligent synthesis ("who knows about X?", "what's blocking Y?")

## Entity Syntax

Everything is `type:slug`:

```
person:sarah-chen       # People
project:memnode         # Projects  
topic:kubernetes        # Topics/skills
team:platform           # Teams
org:acme-corp           # Organizations
decision:use-postgres   # Decisions
meeting:2025-02-21      # Meetings
```

## Installation

```bash
git clone https://github.com/bashketchum02/memnode.git
cd memnode
uv sync
uv run python setup.py
```

## Quick Start

```bash
# Add entities
memnode add person:sarah-chen
memnode add project:memnode  
memnode add topic:kubernetes

# Create explicit relationships
memnode link person:sarah topic:kubernetes --as knows
memnode link person:sarah project:memnode --as owns

# Or just write notes with inline tags - relationships are auto-discovered!
```

## Writing Notes

Just tag entities inline. The indexer finds them automatically:

```markdown
# Meeting Notes

Discussed project:platform-v2 with person:sarah-chen.
She suggested involving person:mike since he knows topic:auth well.

Blockers:
- project:api-gateway might impact our timeline
- Need approval from org:acme-corp

## Action Items
- [ ] Follow up with person:sarah-chen about RFC #high @2025-03-01
- [ ] Schedule sync with team:platform
```

**What happens:**
- `person:sarah-chen` is linked to this note with context
- `project:platform-v2`, `topic:auth`, `team:platform` all indexed
- Todos extracted with their entity references
- "Who is mentioned with `project:platform-v2`?" → finds sarah, mike

## CLI Commands

```bash
# Add entities
memnode add person:name
memnode add project:name
memnode add topic:name
memnode add team:name
memnode add org:name
memnode add decision:name

# Create relationships  
memnode link person:sarah project:memnode --as owns
memnode link project:api project:web --as blocks
memnode link person:alice person:bob --as reports_to

# Remove relationships
memnode unlink person:sarah project:memnode

# View entities
memnode list                    # All entities
memnode list person             # All people
memnode show person:sarah       # Details + relationships + references

# Edit
memnode edit person:sarah

# Remove
memnode rm person:old-contact

# Special commands
memnode capture "Quick thought"           # Add to inbox
memnode todo "Task" --priority high       # Add todo
memnode 1on1 sarah                         # Add 1:1 note
memnode journal                            # Today's journal
```

## Relationship Types

```bash
# Person relationships
--as knows          # person → topic
--as owns           # person → project  
--as stakeholder    # person → project
--as contributes_to # person → project
--as reports_to     # person → person
--as works_with     # person → person
--as member_of      # person → team
--as works_at       # person → org
--as leads          # person → team/project

# Project relationships
--as blocks         # project → project
--as depends_on     # project → project
--as related_to     # anything → anything
```

## MCP Server

The MCP server provides read-only synthesis for AI agents:

```bash
# Start server (for Claude Desktop, Cursor, etc.)
memnode-server
```

### Tools

| Tool | Description |
|------|-------------|
| `search` | Full-text search across all entities |
| `get_entity` | Get entity details + relationships + references |
| `list_entities` | List entities by type |
| `who_knows_about` | Find people with expertise on a topic |
| `whats_on_my_plate` | Prioritized todos and blockers |
| `get_context` | Full context for an entity |
| `find_path` | How are two entities connected? |

### Configuration

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "memnode": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/memnode", "python", "-m", "src.server"],
      "env": {
        "MEMNODE_DIR": "/path/to/your/notes"
      }
    }
  }
}
```

## Directory Structure

```
~/memnode/
├── .memnode.db           # SQLite index (auto-generated)
├── .relationships.yaml   # Explicit relationships
├── people/
│   └── sarah-chen.md
├── projects/
│   └── memnode.md
├── topics/
│   └── kubernetes.md
├── teams/
│   └── platform.md
├── decisions/
│   └── use-postgres.md
├── todos/
│   └── inbox.md
└── journal/
    └── 2025-02-21.md
```

## Multi-Machine Sync

```bash
# Your notes are a git repo
cd ~/memnode
git add -A && git commit -m "Updates" && git push

# On another machine
git pull
memnode-setup --rebuild-index
```

## Example Queries (via AI)

Once connected to Claude/Cursor:

- "Who knows about kubernetes?"
- "What's blocking project:platform-v2?"
- "Show me everything related to person:sarah"
- "What did I discuss with person:mike last week?"
- "Find the connection between person:alice and project:api"

## Why This Approach?

| Traditional PKM | memnode |
|-----------------|---------|
| Organize into folders | Tag inline, search everything |
| Manual linking | Auto-discovered from references |
| Separate tools for todos/notes/people | Unified entity model |
| Complex hierarchies | Flat + graph |

The `type:slug` syntax is:
- **Greppable** - `grep "person:sarah" ~/memnode/**/*.md`
- **Unambiguous** - No confusion between "Sarah" the person and "Sarah" the project
- **Portable** - Works in any editor, any tool
- **AI-friendly** - Easy for LLMs to parse and reference

## License

MIT
