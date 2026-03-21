#!/bin/bash
# Shared prerequisite-checking helpers for SAM scripts.
# Source this file at the top of any script that needs dependency validation.
#
# Usage:
#   SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
#   source "$SCRIPT_DIR/../lib/prereqs.sh"   # adjust relative path as needed
#
#   require_cmd mysql mysql-client mysql-client
#   check_vpn "$DB_HOST" 3306
#   check_aws_cli
#   check_docker

_PREREQS_OS="$(uname -s)"

# --------------------------------------------------------------------------
# require_cmd <command> <brew_package> <apt_package> [description]
#
# Checks that <command> is on PATH. If missing, prints install instructions
# for macOS (brew) and Linux (apt), then offers to auto-install on macOS
# when brew is available. Exits 2 on failure.
# --------------------------------------------------------------------------
require_cmd() {
    local cmd="$1"
    local brew_pkg="${2:-$cmd}"
    local apt_pkg="${3:-$cmd}"
    local desc="${4:-$cmd}"

    if command -v "$cmd" &>/dev/null; then
        return 0
    fi

    echo "ERROR: $desc ($cmd) is not installed."
    echo ""

    if [ "$_PREREQS_OS" = "Darwin" ] && command -v brew &>/dev/null; then
        echo "  Installing via Homebrew: brew install $brew_pkg"
        echo ""
        brew install "$brew_pkg"

        if command -v "$cmd" &>/dev/null; then
            echo ""
            echo "  Installed successfully."
            return 0
        fi

        # Some brew packages install to a keg-only path (e.g. mysql-client)
        local brew_prefix
        brew_prefix="$(brew --prefix "$brew_pkg" 2>/dev/null)/bin"
        if [ -x "$brew_prefix/$cmd" ]; then
            export PATH="$brew_prefix:$PATH"
            echo ""
            echo "  Installed. Added $brew_prefix to PATH for this session."
            echo "  To make permanent, add to your shell profile:"
            echo "    export PATH=\"$brew_prefix:\$PATH\""
            return 0
        fi

        echo "  Installation may have failed. Check brew output above."
        exit 2
    fi

    # No brew available or not macOS -- print manual instructions
    if [ "$_PREREQS_OS" = "Darwin" ]; then
        echo "  macOS:  brew install $brew_pkg"
    else
        echo "  Linux:  sudo apt-get install $apt_pkg"
    fi
    echo ""
    exit 2
}

# --------------------------------------------------------------------------
# check_vpn <host> <port>
#
# Quick connectivity test to verify VPN access. Uses nc (netcat) if available,
# falls back to a bash /dev/tcp probe.
# --------------------------------------------------------------------------
check_vpn() {
    local host="$1"
    local port="${2:-3306}"

    if command -v nc &>/dev/null; then
        if nc -z -w 3 "$host" "$port" 2>/dev/null; then
            return 0
        fi
    elif (echo >/dev/tcp/"$host"/"$port") 2>/dev/null; then
        return 0
    fi

    echo "ERROR: Cannot reach $host:$port"
    echo ""
    echo "  Are you connected to the UCAR VPN (128.117.0.0/16)?"
    echo "  Verify with: nc -z $host $port"
    exit 1
}

# --------------------------------------------------------------------------
# check_aws_cli
#
# Validates that the AWS CLI is installed and credentials are configured.
# --------------------------------------------------------------------------
check_aws_cli() {
    require_cmd aws awscli awscli "AWS CLI"

    if ! aws sts get-caller-identity &>/dev/null; then
        echo "ERROR: AWS CLI is installed but credentials are not configured."
        echo ""
        echo "  Configure with:"
        echo "    aws configure"
        echo "  Or set environment variables:"
        echo "    export AWS_ACCESS_KEY_ID=..."
        echo "    export AWS_SECRET_ACCESS_KEY=..."
        exit 2
    fi
}

# --------------------------------------------------------------------------
# check_docker
#
# Validates that Docker is installed and the daemon is running.
# --------------------------------------------------------------------------
check_docker() {
    require_cmd docker docker docker "Docker"

    if ! docker info &>/dev/null; then
        echo "ERROR: Docker is installed but the daemon is not running."
        echo ""
        if [ "$_PREREQS_OS" = "Darwin" ]; then
            echo "  Start Docker Desktop from Applications, or:"
            echo "    open -a Docker"
        else
            echo "  Start the Docker daemon:"
            echo "    sudo systemctl start docker"
        fi
        exit 2
    fi
}
