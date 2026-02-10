"""
Database migration script to add conversation history tables.
Run this to add Conversation, ConversationMessage, and JobResult tables.
"""

import sys
from pathlib import Path

# Add parent directory to path so we can import server1
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from server1.models import Base
from server1.config import DATABASE_URL

def migrate():
    """Create new conversation history tables and add last_project_id column."""
    # Convert async URL to sync for migration
    db_url = DATABASE_URL
    if "+aiosqlite" in db_url:
        db_url = db_url.replace("+aiosqlite", "")
    
    print(f"Connecting to database: {db_url}")
    
    # Create sync engine
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
        echo=False
    )
    
    # Add last_project_id column to users table if it doesn't exist
    print("Checking users table...")
    with engine.connect() as conn:
        try:
            # Try to add the column
            conn.execute(text("ALTER TABLE users ADD COLUMN last_project_id TEXT"))
            conn.commit()
            print("  ✓ Added last_project_id column to users table")
        except Exception as e:
            if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                print("  ✓ Column last_project_id already exists")
            else:
                print(f"  ! Error adding column: {e}")
    
    print("Creating new tables...")
    # This will create only tables that don't exist yet
    Base.metadata.create_all(engine)
    
    print("\n✅ Migration complete!")
    print("\nNew tables created:")
    print("  - conversations")
    print("  - conversation_messages")
    print("  - job_results")
    print("  - project_access")
    print("\nNew columns added:")
    print("  - users.last_project_id")
    print("\nRestart server1 and the UI to enable conversation history.")

if __name__ == "__main__":
    migrate()
