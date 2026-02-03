"""
Compatibility shim for sam_search_cli.

This module maintains backward compatibility with code that imports from sam_search_cli.
The actual implementation has been refactored into the cli/ package.

For new code, import from cli.cmds.search instead.
"""

# Re-export the CLI from the new location
from cli.cmds.search import cli

if __name__ == '__main__':
    cli()
