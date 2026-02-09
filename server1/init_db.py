"""
Database initialization script for AGOUTIC v24 with multi-user support.

Creates a fresh database with all tables:
- users: User accounts with Google OAuth support
- sessions: Session tokens for authentication
- projects: Project metadata with ownership
- project_blocks: Block architecture (conversation history)
- dogme_jobs: Job execution tracking (from Server 3)
- job_logs: Job execution logs (from Server 3)

Run this script to create a clean database.
"""

import os
import sys
import uuid
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from server1.config import DB_FOLDER, AGOUTIC_DATA

# New database name
DB_FILE = DB_FOLDER / "agoutic_v24.sqlite"


def enable_wal_mode(db_path: Path):
    """Enable WAL mode for better concurrency"""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()
    print(f"✅ Enabled WAL mode on {db_path}")


def create_tables(db_path: Path):
    """Create all tables for v24 schema"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            google_sub_id TEXT UNIQUE,
            display_name TEXT,
            role TEXT DEFAULT 'user' CHECK(role IN ('user', 'admin')),
            is_active BOOLEAN DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_login DATETIME
        )
    """)
    
    # 2. Sessions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL,
            is_valid BOOLEAN DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    
    # Create index on user_id for faster session lookups
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)
    """)
    
    # 3. Projects table (NEW - projects are now first-class entities)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            is_public BOOLEAN DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(owner_id) REFERENCES users(id)
        )
    """)
    
    # Create index on owner_id for faster project listings
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_projects_owner_id ON projects(owner_id)
    """)
    
    # 4. Project blocks table (updated with owner_id)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS project_blocks (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            status TEXT DEFAULT 'NEW',
            payload_json TEXT NOT NULL DEFAULT '{}',
            parent_id TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id),
            FOREIGN KEY(owner_id) REFERENCES users(id)
        )
    """)
    
    # Create compound index for efficient block queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_blocks_project_seq 
        ON project_blocks(project_id, seq)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_blocks_owner 
        ON project_blocks(owner_id)
    """)
    
    # 5. Dogme jobs table (updated with owner_id)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dogme_jobs (
            run_uuid TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            sample_name TEXT NOT NULL,
            mode TEXT NOT NULL,
            input_directory TEXT NOT NULL,
            reference_genome TEXT,
            modifications TEXT,
            nextflow_work_dir TEXT,
            nextflow_config_path TEXT,
            nextflow_pid INTEGER,
            status TEXT DEFAULT 'PENDING',
            progress_percent INTEGER DEFAULT 0,
            submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME,
            completed_at DATETIME,
            error_message TEXT,
            nextflow_stdout TEXT,
            nextflow_stderr TEXT,
            input_type TEXT,
            entry_point TEXT,
            config_data TEXT,
            FOREIGN KEY(project_id) REFERENCES projects(id),
            FOREIGN KEY(owner_id) REFERENCES users(id)
        )
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_project_id ON dogme_jobs(project_id)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_owner_id ON dogme_jobs(owner_id)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON dogme_jobs(status)
    """)
    
    # 6. Job logs table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            task TEXT,
            FOREIGN KEY(job_id) REFERENCES dogme_jobs(run_uuid)
        )
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_logs_job_id ON job_logs(job_id)
    """)
    
    conn.commit()
    conn.close()
    print("✅ Created all tables")


def create_super_admin(db_path: Path):
    """Create the super admin user from environment variable"""
    super_admin_email = os.getenv("SUPER_ADMIN_EMAIL")
    
    if not super_admin_email:
        print("⚠️  SUPER_ADMIN_EMAIL not set - skipping admin creation")
        print("   Set SUPER_ADMIN_EMAIL environment variable and run again")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if admin already exists
    cursor.execute("SELECT id FROM users WHERE email = ?", (super_admin_email,))
    existing = cursor.fetchone()
    
    if existing:
        print(f"✅ Super admin already exists: {super_admin_email}")
        conn.close()
        return
    
    # Create super admin
    admin_id = str(uuid.uuid4())
    cursor.execute("""
        INSERT INTO users (id, email, display_name, role, is_active, created_at)
        VALUES (?, ?, ?, 'admin', 1, ?)
    """, (admin_id, super_admin_email, "Super Admin", datetime.utcnow().isoformat()))
    
    conn.commit()
    conn.close()
    print(f"✅ Created super admin: {super_admin_email}")


def main():
    """Main initialization routine"""
    print(f"🗄️  Initializing AGOUTIC v24 database")
    print(f"   Database location: {DB_FILE}")
    
    # Ensure data directory exists
    DB_FOLDER.mkdir(parents=True, exist_ok=True)
    
    # Check if database already exists
    if DB_FILE.exists():
        response = input(f"⚠️  Database already exists at {DB_FILE}. Overwrite? (yes/no): ")
        if response.lower() != "yes":
            print("❌ Aborted")
            return
        
        # Back up existing database
        backup_path = DB_FILE.with_suffix(f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite")
        import shutil
        shutil.copy(DB_FILE, backup_path)
        print(f"📦 Backed up existing database to {backup_path}")
        
        # Remove old database
        DB_FILE.unlink()
    
    # Create tables
    create_tables(DB_FILE)
    
    # Enable WAL mode for concurrency
    enable_wal_mode(DB_FILE)
    
    # Create super admin
    create_super_admin(DB_FILE)
    
    print("\n✅ Database initialization complete!")
    print(f"\n📝 Next steps:")
    print(f"   1. Set environment variables:")
    print(f"      export SUPER_ADMIN_EMAIL=your@email.com")
    print(f"      export GOOGLE_CLIENT_ID=your_client_id")
    print(f"      export GOOGLE_CLIENT_SECRET=your_client_secret")
    print(f"      export INTERNAL_API_SECRET=$(openssl rand -hex 32)")
    print(f"   2. Update config.py to use agoutic_v24.sqlite")
    print(f"   3. Restart Server 1 and Server 3")


if __name__ == "__main__":
    main()
