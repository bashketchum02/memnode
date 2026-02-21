"""
memnode MCP Server - READ ONLY + SYNTHESIS.

Provides AI agents with intelligent querying and synthesis over your knowledge graph.
All writes happen via CLI. This server only reads and synthesizes.

Entity format: type:slug (e.g., person:sarah-chen, project:memnode)
"""

import os
from datetime import date
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .indexer import MemnodeIndex

# Get notes directory from environment
NOTES_DIR = Path(
    os.environ.get("MEMNODE_DIR", os.environ.get("NOTES_DIR", "~/memnode"))
).expanduser().resolve()

server = Server("memnode")
index: Optional[MemnodeIndex] = None


def get_index() -> MemnodeIndex:
    """Get or create the index."""
    global index
    if index is None:
        index = MemnodeIndex(NOTES_DIR)
        index.reindex_all()
    return index


# =============================================================================
# Tool Definitions
# =============================================================================


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="search",
            description="Full-text search across all entities and notes. Returns matching entities with context snippets.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results (default 20)"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_entity",
            description="Get full details for an entity including metadata, relationships, and who references it. Use entity format type:slug (e.g., person:sarah-chen, project:memnode).",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "Entity ID as type:slug (e.g., person:sarah-chen)",
                    },
                },
                "required": ["entity_id"],
            },
        ),
        Tool(
            name="list_entities",
            description="List all entities, optionally filtered by type (person, project, topic, team, org, decision).",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "description": "Filter by type: person, project, topic, team, org, decision",
                    },
                },
            },
        ),
        Tool(
            name="get_references",
            description="Get all places where an entity is mentioned/referenced in notes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "Entity ID as type:slug",
                    },
                },
                "required": ["entity_id"],
            },
        ),
        Tool(
            name="who_knows_about",
            description="Find people who have expertise or context on a topic. Searches both explicit relationships and references.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Topic to find experts on (e.g., kubernetes, auth, ml-pipeline)",
                    },
                },
                "required": ["topic"],
            },
        ),
        Tool(
            name="whats_on_my_plate",
            description="Get prioritized summary: overdue todos, high priority items, upcoming deadlines.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_todos",
            description="List todos with optional filters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "completed": {"type": "boolean", "description": "Filter by completion status"},
                    "priority": {"type": "string", "description": "Filter by priority: high, medium, low"},
                    "entity_id": {"type": "string", "description": "Filter by containing entity"},
                    "limit": {"type": "integer", "description": "Max results (default 30)"},
                },
            },
        ),
        Tool(
            name="find_connection",
            description="Find how two entities are connected through relationships.",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_entity": {"type": "string", "description": "Starting entity (type:slug)"},
                    "to_entity": {"type": "string", "description": "Target entity (type:slug)"},
                },
                "required": ["from_entity", "to_entity"],
            },
        ),
        Tool(
            name="reindex",
            description="Rebuild the search index. Use after manually editing files.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


# =============================================================================
# Tool Implementations
# =============================================================================


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    idx = get_index()

    try:
        if name == "search":
            result = _search(idx, arguments["query"], arguments.get("limit", 20))
        elif name == "get_entity":
            result = _get_entity(idx, arguments["entity_id"])
        elif name == "list_entities":
            result = _list_entities(idx, arguments.get("entity_type"))
        elif name == "get_references":
            result = _get_references(idx, arguments["entity_id"])
        elif name == "who_knows_about":
            result = _who_knows_about(idx, arguments["topic"])
        elif name == "whats_on_my_plate":
            result = _whats_on_my_plate(idx)
        elif name == "get_todos":
            result = _get_todos(
                idx,
                completed=arguments.get("completed"),
                priority=arguments.get("priority"),
                entity_id=arguments.get("entity_id"),
                limit=arguments.get("limit", 30),
            )
        elif name == "find_connection":
            result = _find_connection(idx, arguments["from_entity"], arguments["to_entity"])
        elif name == "reindex":
            idx.reindex_all()
            result = "Index rebuilt successfully."
        else:
            result = f"Unknown tool: {name}"

        return [TextContent(type="text", text=str(result))]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


def _search(idx: MemnodeIndex, query: str, limit: int = 20) -> str:
    """Full-text search."""
    results = idx.search(query, limit)

    if not results:
        return f"No results found for: {query}"

    lines = [f"## Search Results for '{query}'\n"]
    for r in results:
        lines.append(f"### {r['id']}")
        lines.append(f"**Name:** {r['name']}")
        if r.get("snippet"):
            lines.append(f"**Snippet:** ...{r['snippet']}...")
        lines.append(f"**Path:** {r.get('path', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


def _get_entity(idx: MemnodeIndex, entity_id: str) -> str:
    """Get full entity context."""
    ctx = idx.get_entity_context(entity_id)

    if "error" in ctx:
        return ctx["error"]

    entity = ctx["entity"]
    rels = ctx["relationships"]
    refs_to = ctx["referenced_by"]

    lines = [f"# {entity_id}"]
    lines.append(f"**Name:** {entity['name']}")
    lines.append(f"**Type:** {entity['entity_type']}")
    
    # Metadata
    import json
    metadata = entity.get("metadata", {})
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    for key, value in metadata.items():
        if key not in ["type", "name"] and value:
            lines.append(f"**{key}:** {value}")

    # Outgoing relationships
    if rels["outgoing"]:
        lines.append("\n## Relationships (outgoing)")
        for r in rels["outgoing"]:
            lines.append(f"- --[{r['relation']}]--> {r['target_id']}")

    # Incoming relationships
    if rels["incoming"]:
        lines.append("\n## Relationships (incoming)")
        for r in rels["incoming"]:
            lines.append(f"- <--[{r['relation']}]-- {r['source_id']}")

    # Referenced by
    if refs_to:
        lines.append(f"\n## Referenced by ({len(refs_to)} mentions)")
        for ref in refs_to[:10]:
            lines.append(f"- **{ref['source_id']}**: \"{ref['context'][:80]}...\"")
        if len(refs_to) > 10:
            lines.append(f"  ... and {len(refs_to) - 10} more")

    # Related todos
    if ctx.get("related_todos"):
        lines.append(f"\n## Related Todos ({len(ctx['related_todos'])})")
        for t in ctx["related_todos"][:5]:
            status = "[x]" if t["completed"] else "[ ]"
            lines.append(f"- {status} {t['text']}")

    return "\n".join(lines)


def _list_entities(idx: MemnodeIndex, entity_type: Optional[str] = None) -> str:
    """List entities."""
    if entity_type:
        entities = idx.get_entities_by_type(entity_type)
        if not entities:
            return f"No {entity_type}s found."
        
        lines = [f"# {entity_type.title()}s ({len(entities)})\n"]
        for e in entities:
            lines.append(f"- **{e['id']}**: {e['name']}")
        return "\n".join(lines)
    
    else:
        # List all types
        lines = ["# All Entities\n"]
        for etype in ["person", "project", "topic", "team", "org", "decision"]:
            entities = idx.get_entities_by_type(etype)
            if entities:
                lines.append(f"## {etype.title()}s ({len(entities)})")
                for e in entities[:10]:
                    lines.append(f"- {e['id']}: {e['name']}")
                if len(entities) > 10:
                    lines.append(f"  ... and {len(entities) - 10} more")
                lines.append("")
        return "\n".join(lines)


def _get_references(idx: MemnodeIndex, entity_id: str) -> str:
    """Get all references to an entity."""
    refs = idx.get_references_to(entity_id)

    if not refs:
        return f"No references found to {entity_id}"

    lines = [f"# References to {entity_id} ({len(refs)} mentions)\n"]
    
    # Group by source
    by_source = {}
    for ref in refs:
        source = ref["source_id"]
        if source not in by_source:
            by_source[source] = []
        by_source[source].append(ref)

    for source, source_refs in by_source.items():
        lines.append(f"## {source}")
        for ref in source_refs:
            lines.append(f"- Line {ref['line_number']}: \"{ref['context']}\"")
        lines.append("")

    return "\n".join(lines)


def _who_knows_about(idx: MemnodeIndex, topic: str) -> str:
    """Find people who know about a topic."""
    # Normalize topic
    if ":" not in topic:
        topic = f"topic:{topic}"

    people = idx.who_knows_about(topic)

    if not people:
        return f"No one found with expertise on: {topic}"

    lines = [f"# Who knows about {topic}\n"]
    for p in people:
        import json
        metadata = p.get("metadata", {})
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        
        line = f"- **{p['id']}**: {p['name']}"
        if metadata.get("role"):
            line += f" ({metadata['role']})"
        lines.append(line)

    return "\n".join(lines)


def _whats_on_my_plate(idx: MemnodeIndex) -> str:
    """Get prioritized summary."""
    summary = idx.get_daily_summary()

    lines = [f"# What's On Your Plate ({summary['date']})\n"]

    # Overdue
    if summary["overdue"]:
        lines.append(f"## OVERDUE ({len(summary['overdue'])} items)")
        for t in summary["overdue"][:10]:
            lines.append(f"- [ ] {t['text']}")
        lines.append("")

    # High priority
    if summary["high_priority"]:
        lines.append(f"## High Priority ({len(summary['high_priority'])} items)")
        for t in summary["high_priority"][:10]:
            lines.append(f"- [ ] {t['text']}")
        lines.append("")

    # Due this week
    if summary["due_this_week"]:
        lines.append(f"## Due This Week ({len(summary['due_this_week'])} items)")
        for t in summary["due_this_week"][:10]:
            lines.append(f"- [ ] {t['text']}")
        lines.append("")

    if len(lines) == 1:
        lines.append("Nothing pressing! You're all caught up.")

    return "\n".join(lines)


def _get_todos(
    idx: MemnodeIndex,
    completed: Optional[bool] = None,
    priority: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = 30,
) -> str:
    """Get todos with filters."""
    todos = idx.get_todos(
        completed=completed,
        priority=priority,
        entity_id=entity_id,
        limit=limit,
    )

    if not todos:
        return "No todos found matching criteria."

    lines = ["# Todos\n"]
    for t in todos:
        status = "[x]" if t["completed"] else "[ ]"
        line = f"- {status} {t['text']}"
        if t.get("priority"):
            line += f" #{t['priority']}"
        if t.get("due_date"):
            line += f" @{t['due_date']}"
        if t.get("refs"):
            refs = t["refs"] if isinstance(t["refs"], list) else []
            if refs:
                line += f" (refs: {', '.join(refs)})"
        lines.append(line)

    return "\n".join(lines)


def _find_connection(idx: MemnodeIndex, from_entity: str, to_entity: str) -> str:
    """Find connection path between entities."""
    path = idx.find_path(from_entity, to_entity)

    if not path:
        return f"No connection found between {from_entity} and {to_entity}"

    lines = [f"# Connection: {from_entity} → {to_entity}\n"]
    
    current = from_entity
    for step in path:
        if step["direction"] == "->":
            lines.append(f"  {step['from']} --[{step['relation']}]--> {step['to']}")
        else:
            lines.append(f"  {step['to']} <--[{step['relation']}]-- {step['from']}")
        current = step["to"] if step["direction"] == "->" else step["from"]

    return "\n".join(lines)


def main():
    """Run the MCP server."""
    import asyncio

    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
