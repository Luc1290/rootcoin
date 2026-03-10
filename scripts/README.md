# RootCoin Scripts

A collection of utility scripts to automate deployment, maintenance, and management of the RootCoin platform on a VPS.

## Core Scripts

### 1. `setup_vps.sh`
The initial setup script for a new VPS. It installs necessary system dependencies (Python, Node.js, SQLite), configures the environment, and prepares the system for RootCoin.

### 2. `deploy.sh`
Automates the deployment process:
- Pulls the latest code from the repository.
- Updates the Python virtual environment.
- Compiles the Tailwind CSS.
- Restarts the `systemd` service.

### 3. `rootcoin.service`
A `systemd` unit file template to manage RootCoin as a background service. It ensures the application starts automatically on boot and restarts if it crashes.

### 4. `backup_db.sh`
Performs a safe backup of the SQLite database. It can be scheduled via `cron` to prevent data loss.

## Usage

### Deployment
To update the dashboard on your VPS:
```bash
./scripts/deploy.sh
```

### System Logs
To monitor real-time logs via systemd:
```bash
journalctl -u rootcoin -f
```

### Database Backup
Manual backup:
```bash
./scripts/backup_db.sh
```
