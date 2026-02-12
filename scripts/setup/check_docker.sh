#!/bin/bash
# Check Docker status and permissions

echo "Checking Docker status..."
echo ""

# Check if Docker Desktop is running
if pgrep -f "Docker Desktop" > /dev/null 2>&1 || pgrep -f "com.docker.backend" > /dev/null 2>&1; then
    echo "✅ Docker Desktop process is running"
else
    echo "❌ Docker Desktop is not running"
    echo "   Please start Docker Desktop application"
    exit 1
fi

echo ""

# Try different Docker socket paths
SOCKETS=(
    "/var/run/docker.sock"
    "$HOME/.docker/run/docker.sock"
    "/Users/$USER/.docker/run/docker.sock"
)

FOUND_SOCKET=""
for socket in "${SOCKETS[@]}"; do
    if [ -S "$socket" ] 2>/dev/null; then
        echo "✅ Found Docker socket: $socket"
        FOUND_SOCKET="$socket"
        # Check permissions
        if [ -r "$socket" ] && [ -w "$socket" ]; then
            echo "   ✅ Socket is readable and writable"
        else
            echo "   ⚠️  Socket permissions issue"
            ls -l "$socket" 2>/dev/null || true
        fi
        break
    fi
done

if [ -z "$FOUND_SOCKET" ]; then
    echo "⚠️  Docker socket not found in common locations"
fi

echo ""

# Test Docker connection
echo "Testing Docker connection..."
if docker info > /dev/null 2>&1; then
    echo "✅ Docker connection successful!"
    echo ""
    echo "Docker version:"
    docker --version
    echo ""
    echo "Container status:"
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "Cannot list containers"
else
    echo "❌ Docker connection failed"
    echo ""
    echo "Troubleshooting steps:"
    echo "  1. Ensure Docker Desktop is fully started (whale icon in menu bar)"
    echo "  2. Try restarting Docker Desktop"
    echo "  3. Check Docker Desktop settings → Resources → Advanced"
    echo "  4. Try: docker ps (in a regular terminal, not Cursor)"
    echo ""
    echo "If Docker works in regular terminal but not Cursor:"
    echo "  - This may be a Cursor-specific permission issue"
    echo "  - Try restarting Cursor"
    echo "  - Check Cursor's terminal settings"
fi

echo ""

# Test docker compose
echo "Testing docker compose..."
if docker compose version > /dev/null 2>&1; then
    echo "✅ docker compose is available"
    echo "   Version: $(docker compose version)"
else
    echo "❌ docker compose not available"
fi
