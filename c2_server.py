from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, session
import sqlite3
import uuid
import time
from datetime import datetime
import os
import logging
import socket
import json
import threading
import paramiko
from discovery import ServerAnnouncer, ServerListener
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# Global listener for discovered servers
discovery_listener = ServerListener()
announcer = None

# --- CONFIGURATION ---
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'a_fixed_flask_secret_key')
API_SECRET_KEY = os.getenv('API_SECRET_KEY', 'a_fixed_c2_api_key')
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
    """Initializes the database with the required schema."""
    db = get_db()
    cursor = db.cursor()

    # Check if tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    users_table_exists = cursor.fetchone()

    if not users_table_exists:
        print("Initializing database schema...")
        with open("schema.sql", "r") as f:
            schema_sql = f.read()
            # Run schema scripts directly
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
    return "unassigned"

# --- SSH Command Execution Engine ---
def execute_ssh_task(command_id, ip_address, username, command_obj):
    db = get_db()
    db.execute("UPDATE commands SET status = 'running' WHERE id = ?", (command_id,))
    db.commit()

    action = command_obj.get('action')
    ssh_cmd = ""
    if action == "shutdown":
        ssh_cmd = "sudo shutdown -h now || shutdown -h now || shutdown /s /t 1"
    elif action == "restart":
        ssh_cmd = "sudo shutdown -r now || reboot || shutdown /r /t 1"
    elif action == "sleep":
        ssh_cmd = "systemctl suspend || pm-suspend || rundll32.exe powrprof.dll,SetSuspendState 0,1,0"
    elif action == "execute":
        cmd = command_obj.get('cmd', '')
        args = command_obj.get('args', '')
        ssh_cmd = f"{cmd} {args}".strip()
    elif action == "install":
        path = command_obj.get('path', '')
        args = command_obj.get('args', '')
        ssh_cmd = f"{path} {args}".strip()

    output_str = ""
    status = "completed"
    agent_status = "offline"

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        logging.info(f"Connecting to {username}@{ip_address} via SSH...")
        client.connect(hostname=ip_address, username=username, timeout=15, look_for_keys=True)
        
        # Connection and authentication succeeded, target is definitely online
        agent_status = "online"
        db_conn = get_db()
        db_conn.execute(
            "UPDATE agents SET status = ?, last_seen = ? WHERE id = ?",
            (agent_status, datetime.utcnow(), ip_address)
        )
        db_conn.commit()
        
        logging.info(f"Executing SSH command: {ssh_cmd}")
        stdin, stdout, stderr = client.exec_command(ssh_cmd)
        
        stdout_str = stdout.read().decode('utf-8', errors='replace')
        stderr_str = stderr.read().decode('utf-8', errors='replace')
        
        output_str = stdout_str
        if stderr_str:
            output_str += "\n--- STDERR ---\n" + stderr_str
            
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            status = "failed"
            output_str += f"\nExit Status: {exit_status}"
            
        client.close()
    except Exception as e:
        logging.error(f"SSH execution failed: {e}")
        status = "failed"
        agent_status = "offline"
        output_str = f"Error: {str(e)}"

    db = get_db()
    db.execute(
        "UPDATE commands SET status = ?, output = ?, executed = 1, finished_at = ? WHERE id = ?",
        (status, output_str, datetime.utcnow(), command_id)
    )
    if agent_status == "offline":
        db.execute(
            "UPDATE agents SET status = ?, last_seen = ? WHERE id = ?",
            (agent_status, datetime.utcnow(), ip_address)
        )
    db.commit()

# --- API Endpoints (for CLI) ---
@app.before_request
def check_api_key():
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
        return jsonify({"error": "Invalid role"}), 400

    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO servers (ip_address, port, role) VALUES (?, ?, ?)",
        (ip_address, port, role)
    )
    db.commit()
    logging.info(f"Server {ip_address}:{port} set to role: {role}")

    if announcer and ip_address == announcer.server_ip and port == announcer.server_port:
        announcer.server_role = role
        logging.info(f"Updated announcer role to: {role}")

    return jsonify({"status": "Server role updated", "ip_address": ip_address, "port": port, "role": role})

@app.route("/api/admin/agents", methods=['GET'])
def api_admin_get_agents():
    db = get_db()
    agents = db.execute("SELECT id, hostname, ip_address, username, status, last_seen FROM agents").fetchall()
    return jsonify([dict(agent) for agent in agents])

@app.route("/api/admin/command", methods=['POST'])
def api_admin_issue_command():
    data = request.json
    agent_id = data.get('agent_id')
    command = data.get('command')
    if not all([agent_id, command]):
        return jsonify({"error": "Invalid request"}), 400
    
    db = get_db()
    agent = db.execute("SELECT ip_address, username FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if not agent:
        return jsonify({"error": "Target host not found"}), 404

    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO commands (agent_id, command, status) VALUES (?, ?, 'queued')",
        (agent_id, json.dumps(command))
    )
    command_id = cursor.lastrowid
    db.commit()

    threading.Thread(
        target=execute_ssh_task,
        args=(command_id, agent['ip_address'], agent['username'], command)
    ).start()

    return jsonify({"status": "Command triggered", "command_id": command_id, "agent_id": agent_id})

@app.route("/api/servers", methods=['GET'])
def api_get_servers():
    db = get_db()
    servers = db.execute("SELECT ip_address, port, role FROM servers").fetchall()
    return jsonify([dict(server) for server in servers])

@app.route("/api/discovered_servers", methods=['GET'])
def api_get_discovered_servers():
    return jsonify(discovery_listener.get_active_servers())

@app.route("/agent/checkin", methods=['POST'])
def agent_checkin():
    data = request.json
    agent_id = data.get('agent_id')
    hostname = data.get('hostname')
    current_user = data.get('current_user')
    ip_address = request.remote_addr

    db = get_db()
    
    if not agent_id:
        agent_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO agents (id, hostname, ip_address, username, status, last_seen, current_user) VALUES (?, ?, ?, ?, 'online', CURRENT_TIMESTAMP, ?)",
            (agent_id, hostname, ip_address, 'agent', current_user)
        )
    else:
        db.execute(
            "UPDATE agents SET hostname = ?, ip_address = ?, status = 'online', last_seen = CURRENT_TIMESTAMP, current_user = ? WHERE id = ?",
            (hostname, ip_address, current_user, agent_id)
        )
    db.commit()

    # Try to find a queued command
    command_row = db.execute(
        "SELECT id, command FROM commands WHERE agent_id = ? AND status = 'queued' ORDER BY created_at ASC LIMIT 1",
        (agent_id,)
    ).fetchone()

    command_to_send = "none"
    if command_row:
        command_to_send = command_row['command']
        # For polling agents, we assume success upon sending as they don't have a callback mechanism yet
        db.execute(
            "UPDATE commands SET status = 'completed', executed = 1, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
            (command_row['id'],)
        )
        db.commit()

    return jsonify({
        "agent_id": agent_id,
        "command": command_to_send
    })

@app.route("/servers")
def server_management():
    current_user = os.getlogin()
    return render_template("server_management.html", current_user=current_user)

# --- Web UI Endpoints ---
@app.route("/")
def index():
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    
    db = get_db()
    role = session.get('role', 'user')
    username = session.get('username')

    if role == 'admin':
        agents = db.execute("SELECT id, hostname, ip_address, username, status, last_seen, current_user FROM agents ORDER BY id ASC").fetchall()
        commands = db.execute("""
            SELECT c.id, c.agent_id, c.command, c.status, c.output, c.created_at, c.finished_at, a.hostname 
            FROM commands c 
            JOIN agents a ON c.agent_id = a.id 
            ORDER BY c.created_at DESC LIMIT 50
        """).fetchall()
    else:
        agents = db.execute("""
            SELECT a.id, a.hostname, a.ip_address, a.username, a.status, a.last_seen, a.current_user 
            FROM agents a
            JOIN user_devices ud ON a.id = ud.device_id
            WHERE ud.username = ?
            ORDER BY a.id ASC
        """, (username,)).fetchall()
        
        commands = db.execute("""
            SELECT c.id, c.agent_id, c.command, c.status, c.output, c.created_at, c.finished_at, a.hostname 
            FROM commands c 
            JOIN agents a ON c.agent_id = a.id 
            JOIN user_devices ud ON a.id = ud.device_id
            WHERE ud.username = ?
            ORDER BY c.created_at DESC LIMIT 50
        """, (username,)).fetchall()

    return render_template("dashboard.html", agents=agents, commands=commands)

@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        db = get_db()
        user = db.execute("SELECT username, password, role FROM users WHERE username = ? AND password = ?", (username, password)).fetchone()
        
        if user:
            session['logged_in'] = True
            session['username'] = user['username']
            session['role'] = user['role']
            flash('You were successfully logged in')
            return redirect(url_for('index'))
        else:
            flash('Invalid credentials')
    return render_template('login.html')

@app.route("/logout")
def logout():
    session.clear()
    flash('You were logged out')
    return redirect(url_for('login'))

@app.route("/admin/users", methods=['GET', 'POST'])
def admin_users():
    if 'logged_in' not in session or session.get('role') != 'admin':
        flash("Unauthorized")
        return redirect(url_for('index'))
    
    db = get_db()
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'create_user':
            username = request.form.get('username')
            password = request.form.get('password')
            role = request.form.get('role', 'user')
            if username and password:
                try:
                    db.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", (username, password, role))
                    db.commit()
                    flash(f"User '{username}' created successfully.")
                except sqlite3.IntegrityError:
                    flash("Username already exists.")
        
        elif action == 'delete_user':
            username = request.form.get('username')
            if username and username != 'admin':
                db.execute("DELETE FROM users WHERE username = ?", (username,))
                db.commit()
                flash(f"User '{username}' deleted.")
                
        elif action == 'assign_device':
            username = request.form.get('username')
            device_id = request.form.get('device_id')
            if username and device_id:
                try:
                    db.execute("INSERT INTO user_devices (username, device_id) VALUES (?, ?)", (username, device_id))
                    db.commit()
                    flash(f"Device '{device_id}' assigned to user '{username}'.")
                except sqlite3.IntegrityError:
                    pass
                    
        elif action == 'unassign_device':
            username = request.form.get('username')
            device_id = request.form.get('device_id')
            if username and device_id:
                db.execute("DELETE FROM user_devices WHERE username = ? AND device_id = ?", (username, device_id))
                db.commit()
                flash(f"Device '{device_id}' unassigned from '{username}'.")

    # Fetch users, devices and map assignments
    users_list = db.execute("SELECT username, role FROM users ORDER BY username ASC").fetchall()
    all_devices = db.execute("SELECT id, hostname FROM agents ORDER BY id ASC").fetchall()
    
    user_devices_map = {}
    for u in users_list:
        devs = db.execute("SELECT device_id FROM user_devices WHERE username = ?", (u['username'],)).fetchall()
        user_devices_map[u['username']] = [d['device_id'] for d in devs]

    return render_template("admin_users.html", users_list=users_list, all_devices=all_devices, user_devices_map=user_devices_map)

@app.route("/add_device", methods=['POST'])
def add_device():
    if 'logged_in' not in session or session.get('role') != 'admin':
        flash("Unauthorized")
        return redirect(url_for('index'))
    
    ip_address = request.form.get('ip_address')
    hostname = request.form.get('hostname')
    username = request.form.get('username', 'tech')
    
    if not all([ip_address, hostname]):
        flash("IP Address and Hostname are required.")
        return redirect(url_for('index'))
    
    db = get_db()
    try:
        db.execute(
            "INSERT INTO agents (id, hostname, ip_address, username, status, last_seen, current_user) VALUES (?, ?, ?, ?, 'unknown', CURRENT_TIMESTAMP, ?)",
            (ip_address, hostname, ip_address, username, username)
        )
        db.commit()
        flash(f"Device '{hostname}' ({ip_address}) added successfully.")
    except sqlite3.IntegrityError:
        flash(f"Device with ID/IP '{ip_address}' already exists.")
        
    return redirect(url_for('index'))

@app.route("/issue_command/<agent_id>/<command>", methods=['GET'])
def issue_command_from_ui(agent_id, command):
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    if command not in ["shutdown", "restart", "sleep"]:
        flash("Invalid command")
        return redirect(url_for('index'))
    
    db = get_db()
    role = session.get('role')
    username = session.get('username')

    # Security check for regular users
    if role != 'admin':
        assigned = db.execute("SELECT 1 FROM user_devices WHERE username = ? AND device_id = ?", (username, agent_id)).fetchone()
        if not assigned:
            flash("Unauthorized access to this device.")
            return redirect(url_for('index'))

    agent = db.execute("SELECT ip_address, username FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if not agent:
        flash("Target host not found")
        return redirect(url_for('index'))

    command_obj = {"action": command}
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO commands (agent_id, command, status) VALUES (?, ?, 'queued')",
        (agent_id, json.dumps(command_obj))
    )
    command_id = cursor.lastrowid
    db.commit()

    threading.Thread(
        target=execute_ssh_task,
        args=(command_id, agent['ip_address'], agent['username'], command_obj)
    ).start()

    flash(f"Command '{command}' execution started on {agent_id}")
    return redirect(url_for('index'))

@app.route("/batch_command", methods=['POST'])
def batch_command():
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    
    data = request.get_json()
    agent_ids = data.get('agent_ids')
    command = data.get('command')

    if not agent_ids or not command:
        return jsonify({"status": "error", "message": "Missing agent_ids or command"}), 400

    db = get_db()
    role = session.get('role')
    username = session.get('username')

    for agent_id in agent_ids:
        # Security check for regular users
        if role != 'admin':
            assigned = db.execute("SELECT 1 FROM user_devices WHERE username = ? AND device_id = ?", (username, agent_id)).fetchone()
            if not assigned:
                continue

        agent = db.execute("SELECT ip_address, username FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if not agent:
            continue
        
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO commands (agent_id, command, status) VALUES (?, ?, 'queued')",
            (agent_id, json.dumps(command))
        )
        command_id = cursor.lastrowid
        db.commit()

        threading.Thread(
            target=execute_ssh_task,
            args=(command_id, agent['ip_address'], agent['username'], command)
        ).start()

    return jsonify({"status": "success"})

@app.route("/api/command/<int:command_id>")
def get_command_details(command_id):
    if 'logged_in' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    db = get_db()
    role = session.get('role')
    username = session.get('username')
    
    command = db.execute("SELECT id, agent_id, command, status, output, created_at, finished_at FROM commands WHERE id = ?", (command_id,)).fetchone()
    if not command:
        return jsonify({"error": "Command not found"}), 404
        
    # Security check for regular users
    if role != 'admin':
        assigned = db.execute("SELECT 1 FROM user_devices WHERE username = ? AND device_id = ?", (username, command['agent_id'])).fetchone()
        if not assigned:
            return jsonify({"error": "Unauthorized"}), 403

    return jsonify(dict(command))

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

if __name__ == "__main__":
    init_db()

    server_ip = get_local_ip()
    server_port = 5000

    current_server_role = get_server_role(server_ip, server_port)
    announcer = ServerAnnouncer(server_ip, server_port, current_server_role)
    announcer.start()
    discovery_listener.start()

    try:
        app.run(host='0.0.0.0', port=server_port, debug=False)
    finally:
        announcer.stop()
        discovery_listener.stop()
