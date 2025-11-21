---
description: Start the Flask development server with auto-login enabled
---

# WebUI Development Server

Start the Flask web application in development mode with authentication disabled for local testing.

## Prerequisites

- MySQL container must be running (use `/container-up` first)
- Conda environment should be activated

## Environment Variables

The server starts with these environment variables:
- `DISABLE_AUTH=1` - Disable authentication checks
- `DEV_AUTO_LOGIN_USER=tcraig` - Auto-login as test user
- `FLASK_DEBUG=1` - Enable debug mode with hot reload

## Execution

1. Ensure in project root: `/Users/benkirk/codes/sam-queries`
2. Run: `DISABLE_AUTH=1 DEV_AUTO_LOGIN_USER=tcraig FLASK_DEBUG=1 python3 -m webui.app`
3. Server starts on http://localhost:5000

## Available Endpoints

- `GET /api/v1/users/` - List users
- `GET /api/v1/users/<username>` - User details
- `GET /api/v1/projects/` - List projects
- `GET /api/v1/projects/<projcode>` - Project details
- `GET /api/v1/projects/<projcode>/allocations` - Allocation usage

## Stopping the Server

Press `Ctrl+C` to stop the development server.

## Troubleshooting

- **Connection refused**: Ensure MySQL container is running
- **Module not found**: Activate conda environment first
- **Port in use**: Kill existing process on port 5000
