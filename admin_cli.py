
import requests
import argparse
import os
import time
from discovery import ServerListener

# --- CONFIGURATION ---
# The C2 server URL is now read from an environment variable
# The C2 server URL is now read from an environment variable
# C2_SERVER_URL = "http://localhost:5000"
API_SECRET_KEY = "a_fixed_c2_api_key"

# ---------------------

def get_c2_server_info():
    listener = ServerListener()
    listener.start()
    active_servers = []
    try:
        print("Searching for C2 servers...")
        # Wait a bit for discovery
        for _ in range(5): # Try for 5 seconds
            active_servers = listener.get_active_servers()
            if active_servers:
                break
            time.sleep(1)
        if not active_servers:
            print("No C2 servers found.")
    finally:
        listener.stop()
    return active_servers

def list_agents():
    """Fetches and displays the list of connected agents."""
    active_servers = get_c2_server_info()
    if not active_servers:
        return

    if not API_SECRET_KEY:
        print("ERROR: API_SECRET_KEY environment variable not set.")
        return

    headers = {'X-API-KEY': API_SECRET_KEY}
    
    for server_info in active_servers:
        c2_server_url = f"http://{server_info['ip']}:{server_info['port']}"
        print(f"Attempting to connect to {c2_server_url}...")
        try:
            response = requests.get(f"{c2_server_url}/api/admin/agents", headers=headers, timeout=10)
            response.raise_for_status()
            agents = response.json()
            if not agents:
                print("No agents have checked in yet.")
                return
            
            print("{:<38} {:<20} {:<18} {:<25}".format("Agent ID", "Hostname", "IP Address", "Last Seen (UTC)"))
            print("-" * 101)
            for agent in agents:
                print("{:<38} {:<20} {:<18} {:<25}".format(
                    agent['id'], 
                    agent['hostname'], 
                    agent['ip_address'], 
                    agent['last_seen']
                ))
            return # Successfully listed agents, exit
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to {c2_server_url}: {e}")
    
    print("Could not connect to any C2 server to list agents.")

def issue_command(agent_id, command):
    """Issues a command to a specific agent via the C2 server."""
    active_servers = get_c2_server_info()
    if not active_servers:
        return

    if not API_SECRET_KEY:
        print("ERROR: API_SECRET_KEY environment variable not set.")
        return

    headers = {'X-API-KEY': API_SECRET_KEY}
    payload = {"agent_id": agent_id, "command": command}
    
    for server_info in active_servers:
        c2_server_url = f"http://{server_info['ip']}:{server_info['port']}"
        print(f"Attempting to issue command via {c2_server_url}...")
        try:
            response = requests.post(f"{c2_server_url}/api/admin/command", headers=headers, json=payload, timeout=10)
            response.raise_for_status()
            print(response.json()['status'])
            return # Successfully issued command, exit
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to {c2_server_url}: {e}")
    
    print("Could not connect to any C2 server to issue command.")

def get_all_servers_cli():
    """Displays all discovered C2 servers and their roles."""
    active_servers = get_c2_server_info()
    if not active_servers:
        print("No C2 servers found.")
        return

    print("{:<18} {:<8} {:<15}".format("IP Address", "Port", "Role"))
    print("-"*41)
    for server in active_servers:
        print("{:<18} {:<8} {:<15}".format(server['ip'], server['port'], server['role']))

def set_server_role(ip_address, port, role):
    """Sets the role of a specific server."""
    c2_server_url = f"http://{ip_address}:{port}"

    if not API_SECRET_KEY:
        print("ERROR: API_SECRET_KEY environment variable not set.")
        return

    headers = {'X-API-KEY': API_SECRET_KEY}
    payload = {"ip_address": ip_address, "port": port, "role": role}
    try:
        response = requests.post(f"{c2_server_url}/api/admin/set_server_role", headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        print(response.json()['status'])
    except requests.exceptions.RequestException as e:
        print(f"Error setting server role: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Admin CLI for the Secure Remote Control C2 Server")
    subparsers = parser.add_subparsers(dest="action", required=True)

    # 'list' command
    parser_list = subparsers.add_parser("list", help="List all connected agents")

    # 'issue' command
    parser_issue = subparsers.add_parser("issue", help="Issue a command to an agent")
    parser_issue.add_argument("agent_id", help="The ID of the agent to command")
    parser_issue.add_argument("command", choices=["shutdown", "restart", "sleep"], help="The command to issue")

    # 'set-role' command
    parser_set_role = subparsers.add_parser("set-role", help="Set the role of a C2 server")
    parser_set_role.add_argument("ip_address", help="IP address of the server")
    parser_set_role.add_argument("port", type=int, help="Port of the server")
    parser_set_role.add_argument("role", choices=["primary", "secondary", "tertiary", "client"], help="Role to assign to the server")

    # 'get-servers' command
    parser_get_servers = subparsers.add_parser("get-servers", help="List all discovered C2 servers and their roles")

    args = parser.parse_args()

    if args.action == "list":
        list_agents()
    elif args.action == "issue":
        issue_command(args.agent_id, args.command)
    elif args.action == "set-role":
        set_server_role(args.ip_address, args.port, args.role)
    elif args.action == "get-servers":
        get_all_servers_cli()
