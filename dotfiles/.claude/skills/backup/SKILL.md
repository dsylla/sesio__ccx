---
name: backup
description: Design, implement, and verify backup strategies for databases, files, and infrastructure. Includes disaster recovery planning. Use when user says "backup", "setup backups", "disaster recovery", "DR plan", or needs backup/restore guidance.
tools: Bash, Read, Write, Edit
args: plan | restore <backup> | verify | dr | (none for analysis)
---

# Backup & Disaster Recovery

Design, implement, and verify backup strategies for databases, files, and infrastructure.

## Arguments
- `plan` - Design backup strategy for current project
- `restore <backup>` - Guide through restoration process
- `verify` - Test backup integrity and restorability
- `dr` - Disaster recovery planning
- (none) - Analyze current backup setup or help implement

## Instructions

### Phase 1: Discovery

1. **Identify what needs backup:**
   - Databases: PostgreSQL, MySQL, MongoDB, Redis
   - Files: uploads, configs, certificates, secrets
   - Infrastructure state: Terraform, Kubernetes configs
   - Application data: logs, audit trails

2. **Check existing backups:**
   ```bash
   # Cron jobs
   crontab -l
   ls /etc/cron.d/

   # Systemd timers
   systemctl list-timers

   # Cloud-native
   # AWS RDS snapshots, S3 versioning, etc.
   ```

3. **Determine requirements:**
   - RPO (Recovery Point Objective): How much data loss is acceptable?
   - RTO (Recovery Time Objective): How fast must recovery be?
   - Retention: How long to keep backups?
   - Compliance: Any regulatory requirements?

### Phase 2: Strategy Design

**Database Backups:**

| Database | Tool | Type | Recommended |
|----------|------|------|-------------|
| PostgreSQL | pg_dump / pg_basebackup | Logical / Physical | Daily logical + continuous WAL |
| MySQL | mysqldump / xtrabackup | Logical / Physical | Daily + binlog shipping |
| MongoDB | mongodump / oplog | Logical / Oplog | Daily + oplog tailing |
| Redis | RDB / AOF | Snapshot / Log | RDB snapshots + AOF |

**File Backups:**

| Tool | Best For | Features |
|------|----------|----------|
| restic | General purpose | Dedup, encryption, multiple backends |
| rclone | Cloud sync | 40+ cloud providers |
| borgbackup | Large datasets | Dedup, compression, encryption |
| rsync | Simple sync | Fast, incremental |

**Destinations (3-2-1 rule: 3 copies, 2 media types, 1 offsite):**
- Local: NAS, separate disk
- Cloud: S3, GCS, Backblaze B2, Wasabi
- Offsite: Different region/provider

### Phase 3: Implementation

**Generate backup script template:**

```bash
#!/bin/bash
set -euo pipefail

# Configuration
BACKUP_NAME="[project]-$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/var/backups/[project]"
RETENTION_DAYS=30
S3_BUCKET="s3://[bucket]/[project]"

# Notification (customize)
notify() { echo "$1"; }  # Replace with Slack/email

# Database backup
backup_database() {
    pg_dump -Fc "$DATABASE_URL" > "$BACKUP_DIR/$BACKUP_NAME.dump"
    # Or: mongodump --uri="$MONGO_URL" --archive="$BACKUP_DIR/$BACKUP_NAME.archive"
}

# Files backup
backup_files() {
    restic -r "$S3_BUCKET" backup /path/to/data --tag "$BACKUP_NAME"
}

# Upload to remote
upload_backup() {
    aws s3 cp "$BACKUP_DIR/$BACKUP_NAME.dump" "$S3_BUCKET/"
}

# Cleanup old backups
cleanup() {
    find "$BACKUP_DIR" -type f -mtime +$RETENTION_DAYS -delete
    restic -r "$S3_BUCKET" forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6
}

# Main
main() {
    notify "Backup started: $BACKUP_NAME"
    backup_database
    backup_files
    upload_backup
    cleanup
    notify "Backup completed: $BACKUP_NAME"
}

main "$@"
```

**Systemd timer (preferred over cron):**

```ini
# /etc/systemd/system/backup.timer
[Unit]
Description=Daily backup timer

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true
RandomizedDelaySec=1800

[Install]
WantedBy=timers.target
```

```ini
# /etc/systemd/system/backup.service
[Unit]
Description=Backup service

[Service]
Type=oneshot
ExecStart=/opt/scripts/backup.sh
User=backup
StandardOutput=journal
StandardError=journal
```

### Phase 4: Verification

**Backup integrity checks:**
```bash
# PostgreSQL
pg_restore --list backup.dump > /dev/null

# Restic
restic -r "$REPO" check
restic -r "$REPO" snapshots

# MongoDB
mongorestore --archive=backup.archive --dryRun
```

**Test restore procedure:**
1. Create isolated test environment
2. Restore from backup
3. Verify data integrity
4. Document restore time (for RTO validation)
5. Clean up test environment

**Monitoring alerts:**
- Backup job failed
- Backup older than expected
- Backup size anomaly (too small/large)
- Storage quota warnings

### Phase 5: Disaster Recovery

**DR Plan Template:**

```markdown
# Disaster Recovery Plan: [Project]

## Scenarios

### 1. Database Corruption
- **Detection:** [How to detect]
- **Impact:** [What's affected]
- **Recovery:**
  1. Stop application
  2. Identify last good backup
  3. Restore: `pg_restore -d dbname backup.dump`
  4. Apply WAL if using PITR
  5. Verify data integrity
  6. Restart application
- **RTO:** [Expected time]

### 2. Complete Infrastructure Loss
- **Recovery:**
  1. Provision new infrastructure (Terraform)
  2. Restore databases
  3. Restore application configs
  4. Update DNS
  5. Verify all services
- **RTO:** [Expected time]

### 3. Ransomware / Security Breach
- **Recovery:**
  1. Isolate affected systems
  2. Assess scope
  3. Restore from offline/immutable backups
  4. Rotate all credentials
  5. Security audit before going live
```

**PITR (Point-in-Time Recovery):**

```bash
# PostgreSQL
pg_restore -d newdb backup.dump
# Then replay WAL to specific time

# MongoDB
mongorestore --oplogReplay --oplogLimit="2024-01-15T10:30:00"
```

## Output

Based on the request:
- `plan`: Detailed backup strategy document
- `restore`: Step-by-step restore commands
- `verify`: Verification script and checklist
- `dr`: Disaster recovery runbook
- Default: Analysis and recommendations
