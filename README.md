# memnode

> Personal **social graph** for engineers. Track your network, build context automatically.

memnode is a context-aware social graph where you track people, projects, and their relationships. Tag entities inline using `type:name` syntax and the graph builds itself from your notes. With **smart NLP indexing**, it understands "Sarah" means `person:sarah-chen` without you having to tag everything explicitly.

**Why "social graph" not "knowledge graph"?** You're not storing knowledge - you're storing *context about people* and their relationships to topics and projects. The knowledge lives in the people. memnode helps you find the right person to talk to.

```
┌────────────────────────────────────────────────────────────────────┐
│  You write naturally:                                              │
│                                                                    │
│    Had a meeting with Sarah about the platform rewrite.            │
│    She knows Kubernetes well. Need to sync with the                │
│    platform team about the blockers.                               │
│                                                                    │
│  memnode automatically infers:                                     │
│                                                                    │
│    "Sarah" ──► person:sarah-chen                                   │
│    "platform rewrite" ──► project:platform-v2                      │
│    "Kubernetes" ──► topic:kubernetes                               │
│    "platform team" ──► team:platform                               │
│                                                                    │
│  And builds the graph:                                             │
│                                                                    │
│    person:sarah-chen ───[mentioned_with]───► project:platform-v2   │
│           │                                         ▲              │
│           └──────[knows]──► topic:kubernetes        │              │
│                                                     │              │
│                              team:platform ─────────┘              │
└────────────────────────────────────────────────────────────────────┘
```

## Philosophy

1. **Tag, don't organize** - Just write `person:sarah` or `project:memnode` inline. No folders, no hierarchy.
2. **References ARE relationships** - Mentioning an entity in context creates a discoverable link.
3. **Smart by default** - NLP extracts entities and infers relationships automatically.
4. **Plain text, portable** - Markdown files + SQLite index. Works with any editor.
5. **AI-ready** - MCP server for intelligent synthesis ("who knows about X?", "what's blocking Y?")

## What's New: Smart Indexing

memnode now includes an intelligent indexer that runs automatically on every file edit:

- **Alias Generation**: `person:sarah-chen` becomes searchable as "Sarah", "Sarah Chen", "Sarah C"
- **Fuzzy Entity Matching**: Write "Sarah" in your notes, memnode links it to `person:sarah-chen`
- **Relationship Inference**: Entities mentioned together get automatically linked
- **Zero config**: Just use memnode normally - intelligence is built in

```bash
# Add a person, edit in your editor, close it
memnode add person:sarah-chen
# Indexing person:sarah-chen... done
# ✓ Aliases generated: "Sarah Chen", "Sarah", "Sarah C", "SC"
# ✓ Found 3 inferred references
# ✓ Computed 2 co-occurrence relationships
```

## Installation

```bash
git clone https://github.com/bashketchum02/memnode.git
cd memnode
uv sync
uv run python setup.py

# Download the spaCy model for NLP (one-time, ~15MB)
uv run python -m spacy download en_core_web_sm
```

## Quick Start

```bash
# Initialize your notes directory
memnode init

# Add entities (opens editor, indexes on close)
memnode add person:sarah-chen
memnode add project:memnode  
memnode add topic:kubernetes

# Create explicit relationships
memnode link person:sarah topic:kubernetes --as knows
memnode link person:sarah project:memnode --as owns

# Or just write notes - relationships are auto-discovered!
memnode journal
# Write: "Talked to Sarah about the auth migration for Platform V2"
# memnode automatically links sarah-chen, auth, and platform-v2
```

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

**Pro tip**: You can still write naturally! The explicit `type:slug` syntax is for precision when you need it. For casual writing, just use names - memnode's NLP will match them.

## Writing Notes

Tag entities inline, or just write naturally:

```markdown
# Meeting Notes - Feb 21

Discussed the platform rewrite with Sarah.
She suggested involving Mike since he knows auth systems well.

Blockers:
- The API gateway project might impact our timeline
- Need approval from Acme Corp leadership

## Action Items
- [ ] Follow up with Sarah about the RFC #high @2025-03-01
- [ ] Schedule sync with the platform team
```

**What happens automatically:**
- "Sarah" → linked to `person:sarah-chen` (fuzzy match)
- "platform rewrite" → linked to `project:platform-v2` (alias match)
- "Mike" → linked to `person:mike-johnson`
- Co-occurrence relationships inferred (Sarah ↔ platform-v2)
- Todos extracted with their entity references

**Query later:**
- "Who is mentioned with platform-v2?" → finds Sarah, Mike
- "What does Sarah know about?" → kubernetes, auth (from co-occurrence)

## CLI Commands

```bash
# Add entities (opens editor, NLP indexes on close)
memnode add person:name
memnode add project:name
memnode add topic:name

# Edit (NLP indexes on close)
memnode edit person:sarah

# Create explicit relationships  
memnode link person:sarah project:memnode --as owns
memnode link project:api project:web --as blocks

# View entities
memnode list                    # All entities
memnode list person             # All people
memnode show person:sarah       # Details + relationships + references

# Special commands
memnode capture "Quick thought"           # Add to inbox (indexes immediately)
memnode todo "Task" --priority high       # Add todo
memnode 1on1 sarah                        # Add 1:1 note (indexes on close)
memnode journal                           # Today's journal (indexes on close)

# Indexing
memnode reindex                           # Full reindex with NLP (default)
memnode reindex --no-nlp                  # Basic reindex (faster, no inference)

# Watch mode (for external editor changes)
memnode watch                             # Watch directory, index on any change
```

## MCP Server

The MCP server provides intelligent synthesis for AI agents:

```bash
# Start server (for Claude Desktop, Cursor, etc.)
memnode-server
```

### Tools

| Tool | Description |
|------|-------------|
| `search` | Full-text search across all entities |
| `fuzzy_find` | Find entity by name ("Sarah" → person:sarah-chen) |
| `get_entity` | Get entity details + relationships + inferred connections |
| `get_references` | All mentions (explicit + fuzzy-matched) |
| `get_related` | Discover related entities via co-occurrence/similarity |
| `who_knows_about` | Find people with expertise on a topic |
| `whats_on_my_plate` | Prioritized todos and blockers |
| `find_connection` | How are two entities connected? |
| `reindex` | Rebuild index with NLP processing |

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

**Cursor** (`.cursor/mcp.json`):

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

---

## Architecture

memnode is built as a **local-first knowledge graph** with three layers:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            USER INTERFACE                               │
├─────────────────────────────┬───────────────────────────────────────────┤
│        CLI (typer)          │            MCP Server                     │
│        src/cli.py           │           src/server.py                   │
│                             │                                           │
│  Commands:                  │  Tools (read-only):                       │
│  - add, edit, rm            │  - search, fuzzy_find                     │
│  - link, unlink             │  - get_entity, get_references             │
│  - capture, todo, journal   │  - get_related, who_knows_about           │
│  - reindex, watch           │  - find_connection, reindex               │
│                             │                                           │
│  [Inline NLP indexing       │  [Serves inferred data to                 │
│   on every operation]       │   Claude, Cursor, etc.]                   │
├─────────────────────────────┴───────────────────────────────────────────┤
│                         SMART INDEXER                                   │
│                    src/watcher.py + src/nlp.py                          │
│                                                                         │
│  SmartIndexer:                    NLP Components:                       │
│  - Inline post-edit hook          - AliasManager (fuzzy matching)       │
│  - File watcher (optional)        - EntityMatcher (spaCy NER)           │
│  - Incremental reindexing         - RelationshipInferrer (TF-IDF)       │
│                                   - InferredRefManager                  │
├─────────────────────────────────────────────────────────────────────────┤
│                          CORE INDEXER                                   │
│                         src/indexer.py                                  │
│                                                                         │
│  MemnodeIndex:                                                          │
│  - Entity CRUD                    - Relationship graph traversal        │
│  - Reference extraction           - Full-text search (FTS5)             │
│  - Todo parsing                   - SQLite persistence                  │
├─────────────────────────────────────────────────────────────────────────┤
│                          DATA LAYER                                     │
├─────────────────────────────┬───────────────────────────────────────────┤
│     Markdown Files          │         SQLite Database                   │
│     (your notes)            │        (.memnode.db)                      │
│                             │                                           │
│  people/sarah-chen.md       │  Tables:                                  │
│  projects/platform-v2.md    │  - entities (id, type, content, meta)     │
│  journal/2025-02-21.md      │  - refs (explicit type:slug references)   │
│  todos/inbox.md             │  - relationships (explicit links)         │
│                             │  - aliases (name → entity mapping)        │
│  .relationships.yaml        │  - inferred_refs (NLP-matched mentions)   │
│  (explicit relationships)   │  - inferred_relationships (co-occurrence) │
│                             │  - entities_fts (full-text search)        │
└─────────────────────────────┴───────────────────────────────────────────┘
```

### How Smart Indexing Works

When you edit a file (via `memnode add`, `memnode edit`, etc.), the inline indexer runs automatically:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  1. FILE SAVED                                                          │
│     └─► Editor closes after `memnode add person:sarah-chen`             │
│                                                                         │
│  2. BASIC INDEXING (indexer.py)                                         │
│     ├─► Parse YAML frontmatter (name, role, team, etc.)                 │
│     ├─► Extract explicit references (type:slug patterns)                │
│     ├─► Parse todos (- [ ] format with #priority @due-date)             │
│     └─► Update FTS5 search index                                        │
│                                                                         │
│  3. ALIAS GENERATION (nlp.py:AliasManager)                              │
│     └─► "Sarah Chen" → ["Sarah Chen", "Sarah", "Sarah C", "SC"]         │
│         Stored in `aliases` table with confidence scores                │
│                                                                         │
│  4. ENTITY EXTRACTION (nlp.py:EntityMatcher)                            │
│     ├─► spaCy NER finds PERSON, ORG, PRODUCT entities                   │
│     ├─► Pattern matching finds capitalized phrases                      │
│     ├─► Fuzzy matching against known aliases (rapidfuzz)                │
│     └─► "Sarah" in text → matched to person:sarah-chen (0.85 conf)      │
│         Stored in `inferred_refs` table                                 │
│                                                                         │
│  5. RELATIONSHIP INFERENCE (nlp.py:RelationshipInferrer)                │
│     ├─► Co-occurrence: entities mentioned within 3 lines                │
│     │   "Sarah" + "platform-v2" nearby → mentioned_with relationship    │
│     ├─► TF-IDF similarity: documents with similar content               │
│     │   sarah-chen.md similar to platform-v2.md → similar_to            │
│     └─► Stored in `inferred_relationships` with confidence scores       │
│                                                                         │
│  6. DONE                                                                │
│     └─► "Indexing person:sarah-chen... done"                            │
└─────────────────────────────────────────────────────────────────────────┘
```

### Database Schema

```sql
-- Core tables (from basic indexer)
entities          -- All entities (person, project, topic, etc.)
refs              -- Explicit type:slug references found in content
relationships     -- Explicit relationships from .relationships.yaml
todos             -- Parsed todo items with priority, due date, tags
entities_fts      -- FTS5 full-text search index

-- Smart indexing tables (from NLP)
aliases           -- Entity aliases for fuzzy matching
                  -- (alias, entity_id, alias_type, confidence)
                  
inferred_refs     -- NLP-matched entity mentions
                  -- (source_id, target_id, matched_text, confidence, context)
                  
inferred_relationships  -- Co-occurrence and similarity relationships
                       -- (source_id, target_id, relation, confidence, evidence)
```

### Why No Background Daemon?

We chose **inline post-edit hooks** over a persistent file watcher daemon:

| Approach | Pros | Cons |
|----------|------|------|
| **Inline hook** (chosen) | Simple, no setup, works everywhere | Only catches memnode CLI edits |
| Background daemon | Catches all edits (vim, VSCode, etc.) | Complex setup, resource usage |

The inline approach is sufficient because:
1. Most edits go through `memnode add/edit/journal`
2. `memnode watch` is available for power users who edit with external tools
3. `memnode reindex` can rebuild everything if needed

### NLP Stack

We use classical NLP techniques (no LLMs) to keep it fast and local:

| Component | Library | Purpose |
|-----------|---------|---------|
| Named Entity Recognition | spaCy `en_core_web_sm` | Find PERSON, ORG, etc. in text |
| Fuzzy String Matching | rapidfuzz | Match "Sarah" to "Sarah Chen" |
| TF-IDF Vectorization | scikit-learn | Document similarity |
| Pattern Matching | regex | Find capitalized phrases |

**Memory footprint:**
- spaCy model: ~15MB (loaded once per CLI call)
- TF-IDF: Computed on-demand, not persisted
- Total overhead: Minimal, subsecond indexing per file

### Token Savings for AI Agents

The smart indexing pays off when AI agents query the graph:

**Without smart indexing:**
```
Agent: "Who should I talk to about auth?"
→ Search "auth" → 50 results
→ Agent reads all 50 documents
→ Agent figures out Sarah knows auth
→ ~10,000 tokens consumed
```

**With smart indexing:**
```
Agent: "Who should I talk to about auth?"
→ who_knows_about("auth")
→ Returns: person:sarah-chen (knows auth via co-occurrence)
→ ~200 tokens consumed
```

The graph does the work upfront so the AI doesn't have to.

---

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
# Your notes are just files - use git
cd ~/memnode
git add -A && git commit -m "Updates" && git push

# On another machine
git pull
memnode reindex  # Rebuilds index with NLP
```

## Example Queries (via AI)

Once connected to Claude/Cursor:

- "Who knows about kubernetes?" → Uses `who_knows_about`, returns people with explicit/inferred expertise
- "Find Sarah" → Uses `fuzzy_find`, matches to `person:sarah-chen`
- "What's related to the platform project?" → Uses `get_related`, shows co-occurring entities
- "Show me everything about Sarah" → Uses `get_entity` with inferred relationships
- "How are Alice and the API project connected?" → Uses `find_connection`

## Why Social Graph?

**Knowledge graphs** store facts: `kubernetes --[is_a]--> container-orchestration`

**Social graphs** store relationships: `person:sarah --[knows]--> topic:kubernetes`

memnode is a social graph because the core question isn't "what is kubernetes?" - it's **"who should I talk to about kubernetes?"**

| Knowledge Graph | memnode (Social Graph) |
|-----------------|------------------------|
| Stores facts and concepts | Stores people and context |
| "What is X?" | "Who knows about X?" |
| Static information | Living relationships |
| Answer questions directly | Navigate to people who can answer |

The knowledge lives in your network. memnode helps you navigate it.

## Why This Approach?

| Traditional PKM | memnode |
|-----------------|---------|
| Organize into folders | Tag inline, search everything |
| Manual linking | Auto-discovered + inferred from NLP |
| Separate tools for todos/notes/people | Unified entity model |
| Complex hierarchies | Flat + graph |
| Requires explicit tagging | Understands natural language |

The `type:slug` syntax is:
- **Greppable** - `grep "person:sarah" ~/memnode/**/*.md`
- **Unambiguous** - Explicit when you need precision
- **Optional** - Write naturally, NLP handles the rest
- **AI-friendly** - Easy for LLMs to parse and reference

## Dependencies

**Core:**
- `mcp` - Model Context Protocol server
- `typer` + `rich` - CLI interface
- `pyyaml` - YAML parsing
- `sqlite3` - Database (built-in)

**Smart Indexing:**
- `spacy` - Named Entity Recognition (~15MB model)
- `rapidfuzz` - Fuzzy string matching
- `scikit-learn` - TF-IDF vectorization
- `watchdog` - File system monitoring (for `memnode watch`)

## License

MIT
