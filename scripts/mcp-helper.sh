#!/bin/bash

# LibreOffice MCP Server Helper Script
# This script checks the prerequisites the LibreOffice extension needs.
#
# Installing, building and testing the extension itself is plugin/install.sh.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}  LibreOffice MCP Server Helper${NC}"
    echo -e "${BLUE}========================================${NC}"
}

print_status() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

check_dependencies() {
    print_header
    echo "Checking system prerequisites..."
    echo ""
    
    local all_ok=true
    
    # Check LibreOffice (Required)
    echo "LibreOffice (Required - Version 24.2+):"
    if command -v libreoffice >/dev/null 2>&1; then
        local version=$(libreoffice --version 2>/dev/null | head -1)
        print_status "LibreOffice is installed: $version"
        
        # Test headless mode
        if libreoffice --headless --help >/dev/null 2>&1; then
            print_status "LibreOffice headless mode is working"
        else
            print_warning "LibreOffice headless mode may have issues"
        fi
    else
        print_error "LibreOffice not found. Please install LibreOffice 24.2+"
        echo "  Ubuntu/Debian: sudo apt install libreoffice"
        echo "  macOS: brew install --cask libreoffice"
        echo "  Windows: Download from https://www.libreoffice.org/download/"
        all_ok=false
    fi
    echo ""
    
    # Check Python (Required)
    echo "Python (Required - Version 3.12+):"
    if command -v python3 >/dev/null 2>&1; then
        local python_version=$(python3 --version 2>&1)
        print_status "Python is installed: $python_version"
        
        # Check if version is 3.12+
        local version_check=$(python3 -c "import sys; print('ok' if sys.version_info >= (3, 12) else 'old')" 2>/dev/null || echo "error")
        if [ "$version_check" = "ok" ]; then
            print_status "Python version meets requirements (3.12+)"
        elif [ "$version_check" = "old" ]; then
            print_error "Python version is too old. Need Python 3.12+"
            all_ok=false
        else
            print_warning "Could not verify Python version"
        fi
    else
        print_error "Python3 not found. Please install Python 3.12+"
        echo "  Ubuntu/Debian: sudo apt install python3.12"
        echo "  macOS: brew install python@3.12"
        echo "  Windows: Download from https://www.python.org/downloads/"
        all_ok=false
    fi
    echo ""
    
    # Check UV Package Manager (Required)
    echo "UV Package Manager (Required - Latest):"
    if command -v uv >/dev/null 2>&1; then
        local uv_version=$(uv --version 2>/dev/null | head -1)
        print_status "UV package manager is available: $uv_version"
    else
        print_error "UV package manager not found. Please install uv."
        echo "  Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
        echo "  Or: pip install uv"
        all_ok=false
    fi
    echo ""
    
    # Check Java (Optional - for advanced LibreOffice features)
    echo "Java (Optional - for advanced LibreOffice features like PDF generation):"
    if command -v java >/dev/null 2>&1; then
        local java_version=$(java -version 2>&1 | head -1)
        print_status "Java is installed: $java_version"
        print_status "Advanced LibreOffice features available"
    else
        print_warning "Java not found. Some LibreOffice features may be limited."
        echo "  Ubuntu/Debian: sudo apt install default-jre"
        echo "  macOS: brew install openjdk"
        echo "  Windows: Download from https://adoptium.net/"
    fi
    echo ""
    
    # Summary
    if [ "$all_ok" = true ]; then
        print_status "All required dependencies are satisfied!"
        echo "Ready to run LibreOffice MCP Server."
    else
        print_error "Some required dependencies are missing."
        echo "Please install the missing components and run '$0 check' again."
        exit 1
    fi
    
    echo ""
}

test_mcp_server() {
    echo "Testing the plugin's tool registry..."
    cd "$PROJECT_ROOT"

    if uv run pytest tests/ -q; then
        print_status "Tests passed"
    else
        print_error "Tests failed"
        exit 1
    fi

    echo ""
}

show_requirements() {
    print_header
    echo "LibreOffice MCP Server - System Requirements"
    echo ""
    
    echo -e "${BLUE}Required Components:${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    echo "1. LibreOffice (Version 24.2 or higher)"
    echo "   Purpose: Document processing engine"
    echo "   Features: Create, read, convert documents"
    echo "   Install:"
    echo "     • Ubuntu/Debian: sudo apt install libreoffice"
    echo "     • CentOS/RHEL: sudo yum install libreoffice"
    echo "     • macOS: brew install --cask libreoffice"
    echo "     • Windows: https://www.libreoffice.org/download/"
    echo ""
    
    echo "2. Python (Version 3.12 or higher)"
    echo "   Purpose: Runs the extension's tests against faked UNO modules"
    echo "   Features: The extension itself runs under LibreOffice's bundled Python"
    echo "   Install:"
    echo "     • Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"
    echo "     • macOS: brew install python@3.12"
    echo "     • Windows: https://www.python.org/downloads/"
    echo ""

    echo "3. UV Package Manager (Latest version)"
    echo "   Purpose: Fast Python package management"
    echo "   Features: Dependency resolution, virtual environments"
    echo "   Install:"
    echo "     • All platforms: curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "     • Alternative: pip install uv"
    echo ""

    echo -e "${YELLOW}Optional Components:${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    echo "4. Java Runtime Environment (JRE 11+)"
    echo "   Purpose: Advanced LibreOffice features"
    echo "   Features: PDF generation, complex document processing"
    echo "   Install:"
    echo "     • Ubuntu/Debian: sudo apt install default-jre"
    echo "     • macOS: brew install openjdk"
    echo "     • Windows: https://adoptium.net/"
    echo ""

    echo -e "${BLUE}Integration Targets:${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    echo "• Any MCP client speaking SSE"
    echo "  - The extension serves http://localhost:8765/sse"
    echo "  - Point the client at that URL once the server is started"
    echo ""

    echo "• LibreOffice itself"
    echo "  - Tools > MCP Server starts and stops the server"
    echo ""

    echo "• scripts/mcp_standalone.py"
    echo "  - The same tools over a UNO socket, when the extension"
    echo "    cannot be registered"
    echo ""
    echo -e "${GREEN}Minimum System Requirements:${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "• Operating System: Linux, macOS, Windows"
    echo "• Memory: 2GB RAM (4GB recommended)"
    echo "• Storage: 2GB free space for LibreOffice + documents"
    echo "• Network: Internet access for initial setup only"
    echo ""
    
    echo "Run '$0 check' to verify your system meets these requirements."
    echo ""
}

show_info() {
    print_header
    echo "LibreOffice MCP Server - Project Information"
    echo ""
    
    echo -e "${BLUE}Project Details:${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "• Name: LibreOffice MCP Server (mcp-libre)"
    echo "• Version: 0.1.0"
    echo "• License: MIT License"
    echo "• Purpose: Model Context Protocol server for LibreOffice document processing"
    echo ""
    
    echo -e "${GREEN}Key Features:${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "• Live editing of open Writer documents over UNO"
    echo "• Formatting, comments, tracked changes, fields, tables, images"
    echo "• Guarded rewrites that refuse to flatten what they cannot restore"
    echo "• Batched tool calls as a single undo step"
    echo "• Served to any MCP client as SSE on localhost:8765"
    echo ""

    echo -e "${YELLOW}License Information:${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "• License Type: MIT License (Permissive)"
    echo "• Commercial Use: ✅ Allowed"
    echo "• Modification: ✅ Allowed"
    echo "• Distribution: ✅ Allowed"
    echo "• Private Use: ✅ Allowed"
    echo ""
    echo "For full license text, see: LICENSE"
    echo "For other license options, see: LICENSE_OPTIONS.md"
    echo ""

    echo -e "${BLUE}File Structure:${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "• plugin/pythonpath/ - the extension: UNO bridge, MCP server, SSE transport"
    echo "• plugin/install.sh - build, install and test the extension"
    echo "• scripts/mcp_standalone.py - the same tools over a UNO socket"
    echo "• tests/ - asserting tests against faked UNO modules"
    echo "• mcp-helper.sh - prerequisite checks"
    echo "• Documentation: README.md, CLAUDE.md, docs/"
    echo ""
}

show_help() {
    print_header
    echo "Usage: $0 [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  requirements Show detailed system requirements and installation guides"
    echo "  check        Check dependencies and system requirements"
    echo "  test         Run the extension's tests (uv run pytest tests/)"
    echo "  info         Show project information and license details"
    echo "  help         Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 requirements             # Show detailed system requirements"
    echo "  $0 check                    # Check if everything is set up correctly"
    echo "  $0 test                     # Run the tests"
    echo ""
    echo "Setup Workflow:"
    echo "  1. Run '$0 requirements' to see what you need"
    echo "  2. Install required components"
    echo "  3. Run '$0 check' to verify setup"
    echo "  4. Run 'cd plugin && ./install.sh install' to build and install"
    echo "     the extension, then restart LibreOffice"
    echo "  5. Start the server from Tools > MCP Server and point your MCP"
    echo "     client at http://localhost:8765/sse"
    echo ""
}

# Main command handling
case "${1:-help}" in
    "requirements"|"req")
        show_requirements
        ;;
    "check")
        check_dependencies
        ;;
    "test")
        check_dependencies
        test_mcp_server
        ;;
    "info"|"information")
        show_info
        ;;
    "help"|"--help"|"-h")
        show_help
        ;;
    *)
        print_error "Unknown command: $1"
        echo ""
        show_help
        exit 1
        ;;
esac
