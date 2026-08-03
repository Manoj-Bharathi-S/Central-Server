# Central Server (C2)

A Command and Control (C2) server and agent system for managing multiple devices, issuing commands, and discovering nodes over the network. It features a Flask-based web dashboard, a polling Windows agent, and a CLI for administration.

## Features

- **Web Dashboard**: View agents, issue commands, and manage users.
- **Agent Discovery**: Uses UDP broadcasting for agents to dynamically discover available C2 servers.
- **Command Execution**:
  - **SSH-based execution**: Push commands to Linux/macOS nodes directly from the C2 server.
  - **Polling execution**: Windows agents (`win_agent.py`) poll the server for queued commands.
- **CLI Administration**: Manage servers and agents via a convenient CLI (`admin_cli.py`).

## Requirements

- Python 3.x
- Requirements listed in `requirements.txt`

## Setup

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment Variables**:
   Copy `.env.example` to `.env` and fill in your secrets.
   ```bash
   copy .env.example .env
   ```
   * Ensure `FLASK_SECRET_KEY` and `API_SECRET_KEY` are securely set.

3. **Initialize the Database**:
   The database (`c2_server.db`) will be automatically initialized using `schema.sql` the first time you run the server.

## Usage

### 1. Start the C2 Server
Run the Flask server:
```bash
python c2_server.py
```
- The server will broadcast its presence on the network (UDP port 5001).
- The web interface is available at `http://<your-ip>:5000` (default port).
- Login with the default credentials (`admin` / `admin`). **Please change these!**

### 2. Run the Windows Agent
On the client machines, execute:
```bash
python win_agent.py
```
- The agent will discover the server via UDP and automatically check in, saving an `agent_id.txt`.
- It will periodically poll the `/agent/checkin` endpoint for commands like shutdown, restart, sleep, or generic execute/install.

### 3. Use the Admin CLI
You can use the CLI to interact with the server programmatically. Make sure `.env` is configured so the CLI can authenticate with `API_SECRET_KEY`.

- **List all connected agents**:
  ```bash
  python admin_cli.py list
  ```
- **List discovered C2 servers**:
  ```bash
  python admin_cli.py get-servers
  ```
- **Issue a command** (e.g., shutdown, restart, sleep):
  ```bash
  python admin_cli.py issue <agent_id> <command>
  ```
