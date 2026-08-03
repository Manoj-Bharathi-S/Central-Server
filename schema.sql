DROP TABLE IF EXISTS user_devices;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS commands;
DROP TABLE IF EXISTS agents;
DROP TABLE IF EXISTS servers;

CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT 'tech',
    status TEXT DEFAULT 'unknown',
    last_seen TIMESTAMP NOT NULL,
    current_user TEXT
);

CREATE TABLE commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    command TEXT NOT NULL,
    status TEXT DEFAULT 'queued', -- queued, running, completed, failed
    output TEXT,
    executed BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    FOREIGN KEY (agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE TABLE servers (
    ip_address TEXT NOT NULL,
    port INTEGER NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (ip_address, port)
);

CREATE TABLE users (
    username TEXT PRIMARY KEY,
    password TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user'
);

CREATE TABLE user_devices (
    username TEXT NOT NULL,
    device_id TEXT NOT NULL,
    PRIMARY KEY (username, device_id),
    FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES agents(id) ON DELETE CASCADE
);

-- Seed default admin user
INSERT INTO users (username, password, role) VALUES ('admin', 'admin', 'admin');

-- Pre-populate the database with the target SSH host
INSERT INTO agents (id, hostname, ip_address, username, status, last_seen, current_user)
VALUES ('172.20.22.225', 'Target SSH Host', '172.20.22.225', 'tech', 'unknown', CURRENT_TIMESTAMP, 'tech');