#!/usr/bin/env python3
"""
memnode setup script.

Handles:
1. Fresh install: Creates notes directory structure
2. Existing notes repo: Rebuilds index, validates structure
3. New machine: Clone notes repo, point MEMNODE_DIR, rebuild index

Usage:
    uv run python setup.py [--notes-dir PATH]
    
Or after install:
    memnode-setup [--notes-dir PATH]
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# ANSI colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def print_step(msg: str):
    print(f"{BLUE}==>{RESET} {msg}")


def print_success(msg: str):
    print(f"{GREEN}✓{RESET} {msg}")


def print_warning(msg: str):
    print(f"{YELLOW}!{RESET} {msg}")


def print_error(msg: str):
    print(f"{RED}✗{RESET} {msg}")


def get_default_notes_dir() -> Path:
    """Get default notes directory."""
    # Check env first (MEMNODE_DIR takes precedence)
    if env_dir := os.environ.get("MEMNODE_DIR"):
        return Path(env_dir).expanduser().resolve()
    if env_dir := os.environ.get("NOTES_DIR"):
        return Path(env_dir).expanduser().resolve()
    # Default to ~/memnode
    return Path.home() / "memnode"


def init_notes_directory(notes_dir: Path) -> bool:
    """Initialize notes directory structure."""
    print_step(f"Initializing directory: {notes_dir}")
    
    # Create directories
    dirs = ["todos", "people", "projects", "decisions", "journal", "meetings"]
    for d in dirs:
        (notes_dir / d).mkdir(parents=True, exist_ok=True)
        print_success(f"Created {d}/")
    
    # Create .gitignore
    gitignore = notes_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("""# SQLite index (regenerated on each machine)
.memnode_index.db
.memnode_index.db-journal
.notes_index.db
.notes_index.db-journal

# OS files
.DS_Store
Thumbs.db
""")
        print_success("Created .gitignore")
    
    # Create relationships file
    relationships = notes_dir / ".relationships.yaml"
    if not relationships.exists():
        relationships.write_text("relationships: []\n")
        print_success("Created .relationships.yaml")
    
    # Create inbox
    inbox = notes_dir / "todos" / "inbox.md"
    if not inbox.exists():
        inbox.write_text("""---
type: todo
---

# Inbox

Capture quick thoughts here, organize later.

""")
        print_success("Created todos/inbox.md")
    
    # Initialize git if not already a repo
    if not (notes_dir / ".git").exists():
        print_step("Initializing git repository...")
        subprocess.run(["git", "init"], cwd=notes_dir, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=notes_dir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial memnode structure"],
            cwd=notes_dir,
            capture_output=True,
        )
        print_success("Git repository initialized")
    else:
        print_success("Git repository already exists")
    
    return True


def rebuild_index(notes_dir: Path) -> bool:
    """Rebuild the SQLite index."""
    print_step("Rebuilding search index...")
    
    # Remove old index
    for db_name in [".memnode_index.db", ".notes_index.db"]:
        db_path = notes_dir / db_name
        if db_path.exists():
            db_path.unlink()
    
    # Import and rebuild
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from src.indexer import NotesIndex
        
        index = NotesIndex(notes_dir)
        index.reindex_all()
        index.close()
        
        print_success("Search index rebuilt")
        return True
    except Exception as e:
        print_error(f"Failed to rebuild index: {e}")
        return False


def validate_structure(notes_dir: Path) -> list[str]:
    """Validate notes directory structure."""
    issues = []
    
    required_dirs = ["todos", "people", "projects"]
    for d in required_dirs:
        if not (notes_dir / d).exists():
            issues.append(f"Missing directory: {d}/")
    
    if not (notes_dir / ".relationships.yaml").exists():
        issues.append("Missing .relationships.yaml")
    
    return issues


def print_shell_config(notes_dir: Path):
    """Print shell configuration instructions."""
    print()
    print(f"{BOLD}Add to your shell config (~/.zshrc or ~/.bashrc):{RESET}")
    print()
    print(f'  export MEMNODE_DIR="{notes_dir}"')
    print()
    

def print_mcp_config(notes_dir: Path):
    """Print MCP configuration."""
    project_dir = Path(__file__).parent.resolve()
    
    print(f"{BOLD}MCP Configuration (Claude Desktop / OpenCode / Cursor):{RESET}")
    print()
    print("""{
  "mcpServers": {
    "memnode": {
      "command": "uv",
      "args": ["run", "--directory", "%s", "python", "-m", "src.server"],
      "env": {
        "MEMNODE_DIR": "%s"
      }
    }
  }
}""" % (project_dir, notes_dir))
    print()


def print_completion_instructions():
    """Print shell completion setup instructions."""
    print(f"{BOLD}Shell Completions:{RESET}")
    print()
    print(f"  {DIM}# Bash{RESET}")
    print("  memnode --install-completion bash")
    print()
    print(f"  {DIM}# Zsh{RESET}")
    print("  memnode --install-completion zsh")
    print()
    print(f"  {DIM}# Fish{RESET}")
    print("  memnode --install-completion fish")
    print()


def count_notes(notes_dir: Path) -> dict:
    """Count notes by type."""
    counts = {}
    for subdir in ["todos", "people", "projects", "decisions", "journal"]:
        path = notes_dir / subdir
        if path.exists():
            counts[subdir] = len(list(path.glob("*.md")))
        else:
            counts[subdir] = 0
    return counts


def main():
    parser = argparse.ArgumentParser(
        description="Setup memnode for a fresh install or new machine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive setup (prompts for directory)
  uv run python setup.py

  # Specify directory directly
  uv run python setup.py --notes-dir ~/my-notes

  # Use existing notes repo (e.g., after git clone)
  uv run python setup.py --notes-dir ~/work-notes

  # Rebuild index after syncing
  uv run python setup.py --rebuild-index

  # Show config for copy/paste
  uv run python setup.py --show-config --notes-dir ~/my-notes
"""
    )
    parser.add_argument(
        "--notes-dir", "-d",
        type=Path,
        default=None,
        help="Path to notes directory (will prompt if not specified)",
    )
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="Only rebuild the search index",
    )
    parser.add_argument(
        "--show-config",
        action="store_true", 
        help="Show MCP and shell configuration",
    )
    
    args = parser.parse_args()
    
    print()
    print(f"{BOLD}memnode setup{RESET}")
    print(f"{'=' * 50}")
    print()
    
    # Determine notes directory
    if args.notes_dir:
        notes_dir = args.notes_dir.expanduser().resolve()
    else:
        # Check environment variables
        env_dir = os.environ.get("MEMNODE_DIR") or os.environ.get("NOTES_DIR")
        if env_dir:
            default_dir = Path(env_dir).expanduser().resolve()
            print(f"{DIM}Found MEMNODE_DIR={env_dir}{RESET}")
        else:
            default_dir = Path.home() / "memnode"
        
        # Interactive prompt for directory
        print("Where would you like to store your notes?")
        print(f"{DIM}(This should be a git repository you can sync across machines){RESET}")
        print()
        user_input = input(f"Notes directory [{default_dir}]: ").strip()
        
        if user_input:
            notes_dir = Path(user_input).expanduser().resolve()
        else:
            notes_dir = default_dir
        print()
    
    # Show config only
    if args.show_config:
        print_shell_config(notes_dir)
        print_mcp_config(notes_dir)
        print_completion_instructions()
        return 0
    
    # Rebuild index only
    if args.rebuild_index:
        if not notes_dir.exists():
            print_error(f"Directory not found: {notes_dir}")
            return 1
        rebuild_index(notes_dir)
        return 0
    
    # Full setup
    if notes_dir.exists():
        print_step(f"Found existing directory: {notes_dir}")
        
        # Validate structure
        issues = validate_structure(notes_dir)
        if issues:
            print_warning("Structure issues found:")
            for issue in issues:
                print(f"  - {issue}")
            print()
            response = input("Fix issues and continue? [Y/n] ").strip().lower()
            if response and response != "y":
                return 1
            init_notes_directory(notes_dir)
        else:
            print_success("Structure validated")
        
        # Show stats
        counts = count_notes(notes_dir)
        print()
        print_step("Current data:")
        for category, count in counts.items():
            if count > 0:
                print(f"  {category}: {count} files")
    else:
        print_step(f"Creating new directory: {notes_dir}")
        response = input("Continue? [Y/n] ").strip().lower()
        if response and response != "y":
            return 1
        init_notes_directory(notes_dir)
    
    # Rebuild index
    print()
    rebuild_index(notes_dir)
    
    # Print next steps
    print()
    print(f"{BOLD}{'=' * 50}{RESET}")
    print(f"{GREEN}Setup complete!{RESET}")
    print()
    
    print_shell_config(notes_dir)
    print_mcp_config(notes_dir)
    print_completion_instructions()
    
    print(f"{BOLD}Quick start:{RESET}")
    print()
    print("  # Add a person to your network")
    print("  memnode add person")
    print()
    print("  # Quick capture a thought")
    print("  memnode capture 'Look into that thing'")
    print()
    print("  # Record a 1:1")
    print("  memnode add 1on1 <person-slug>")
    print()
    print("  # See all commands")
    print("  memnode --help")
    print()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
