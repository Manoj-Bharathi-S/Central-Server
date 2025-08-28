import socket
import threading
import time
import json

import os

DISCOVERY_PORT = 5001
BROADCAST_INTERVAL = 5 # seconds

class ServerAnnouncer:
    """
    Broadcasts the server's presence on the network.
    """
    def __init__(self, server_ip, server_port, server_role="unassigned"):
        self.server_ip = server_ip
        self.server_port = server_port
        self.server_role = server_role
        self.current_user = os.getlogin()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Bind to allow broadcasting from any interface, preventing "Cannot assign requested address"
        try:
            self.sock.bind(('', 0))
        except OSError as e:
            print(f"Warning: Could not bind announcer socket: {e}")
        self.running = False
        self.thread = None

    def _broadcast_loop(self):
        while self.running:
            try:
                message = json.dumps({"type": "server_announcement", "ip": self.server_ip, "port": self.server_port, "role": self.server_role, "user": self.current_user}).encode('utf-8')
                self.sock.sendto(message, ('<broadcast>', DISCOVERY_PORT))
            except Exception as e:
                print(f"Error broadcasting: {e}")
            time.sleep(BROADCAST_INTERVAL)

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._broadcast_loop)
            self.thread.daemon = True
            self.thread.start()
            print(f"Server announcer started for {self.server_ip}:{self.server_port}")

    def stop(self):
        if self.running:
            self.running = False
            if self.thread:
                self.thread.join(timeout=BROADCAST_INTERVAL + 1) # Give it a moment to finish
            print("Server announcer stopped.")

class ServerListener:
    """
    Listens for server announcements and maintains a list of active servers.
    """
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('', DISCOVERY_PORT))
        self.active_servers = {} # {ip: {"port": port, "last_seen": timestamp}}
        self.running = False
        self.thread = None
        self.cleanup_interval = BROADCAST_INTERVAL * 3 # Remove servers not seen for a while

    def _listen_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                message = json.loads(data.decode('utf-8'))
                if message.get("type") == "server_announcement":
                    server_ip = message.get("ip")
                    server_port = message.get("port")
                    server_role = message.get("role", "unassigned")
                    current_user = message.get("user", "Unknown")
                    if server_ip and server_port:
                        server_key = f"{server_ip}:{server_port}"
                        if server_key not in self.active_servers or self.active_servers[server_key].get('role') != server_role or self.active_servers[server_key].get('user') != current_user:
                            print(f"Discovered or role updated for server: {server_ip}:{server_port} (Role: {server_role}, User: {current_user})")
                        self.active_servers[server_key] = {"port": server_port, "last_seen": time.time(), "role": server_role, "user": current_user}
            except socket.timeout:
                pass # Expected when no data
            except Exception as e:
                print(f"Error listening for announcements: {e}")
            self._cleanup_old_servers()

    def _cleanup_old_servers(self):
        current_time = time.time()
        to_remove = [key for key, info in self.active_servers.items() if current_time - info["last_seen"] > self.cleanup_interval]
        for key in to_remove:
            print(f"Removing old server: {key}")
            del self.active_servers[key]

    def get_active_servers(self):
        """Returns a list of currently active servers."""
        self._cleanup_old_servers()  # Ensure list is fresh before returning
        server_list = []
        for server_key, info in self.active_servers.items():
            ip = server_key.split(':')[0]
            server_list.append({"ip": ip, "port": info["port"], "role": info["role"], "user": info["user"]})
        return server_list

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._listen_loop)
            self.thread.daemon = True
            self.thread.start()
            print("Server listener started.")

    def stop(self):
        if self.running:
            self.running = False
            if self.thread:
                self.thread.join(timeout=self.cleanup_interval + 1)
            self.sock.close()
            print("Server listener stopped.")

if __name__ == "__main__":
    # Example Usage:
    # To run a server announcer (e.g., on a machine with IP 192.168.1.100)
    # announcer = ServerAnnouncer("192.168.1.100", 5000)
    # announcer.start()

    # To run a server listener on another machine
    # listener = ServerListener()
    # listener.start()

    # Keep the main thread alive for demonstration
    # try:
    #     while True:
    #         print("Active Servers:", listener.get_active_servers())
    #         time.sleep(10)
    # except KeyboardInterrupt:
    #     print("Stopping example.")
    # finally:
    #     announcer.stop()
    #     listener.stop()
    pass
