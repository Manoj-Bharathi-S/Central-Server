DROP TABLE IF EXISTS agents;
DROP TABLE IF EXISTS commands;
DROP TABLE IF EXISTS servers;

CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    last_seen TIMESTAMP NOT NULL,
    current_user TEXT
);

CREATE TABLE commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    command TEXT NOT NULL,
    executed BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (agent_id) REFERENCES agents (id)
);

CREATE TABLE servers (
    ip_address TEXT NOT NULL,
    port INTEGER NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (ip_address, port)
);