#!/bin/bash
# Setup script for Antigravity OAuth MCP Server

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "Setting up Antigravity OAuth MCP Server..."

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate and install dependencies
echo "Installing dependencies..."
source "$VENV_DIR/bin/activate"
pip install -q -r "$SCRIPT_DIR/requirements.txt"

echo "Setup complete!"
echo ""
echo "To test the server, run:"
echo "  $VENV_DIR/bin/python $SCRIPT_DIR/server.py"
echo ""
echo "The MCP server config has been added to ~/.claude.json"
