#!/bin/bash

# ShopStream Supplier Sync - Virtual Environment Setup Script

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PROJECT_ROOT/venv"

echo "🚀 Setting up ShopStream Supplier Sync Python Environment..."
echo "Project Root: $PROJECT_ROOT"

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
else
    echo "✅ Virtual environment already exists"
fi

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Upgrade pip
echo "⬆️ Upgrading pip..."
pip install --upgrade pip

# Install requirements for each component
components=("backend" "supplier-api" "azure-function")

for component in "${components[@]}"; do
    if [ -f "$PROJECT_ROOT/$component/requirements.txt" ]; then
        echo "📋 Installing requirements for $component..."
        pip install -r "$PROJECT_ROOT/$component/requirements.txt"
    else
        echo "⚠️ No requirements.txt found for $component (will be created)"
    fi
done

echo "🎉 Virtual environment setup complete!"
echo ""
echo "To activate the environment, run:"
echo "  source $VENV_DIR/bin/activate"
echo ""
echo "To deactivate, run:"
echo "  deactivate"
