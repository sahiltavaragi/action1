"""Check which columns are missing from the live users table."""
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_ANON_KEY")
    or os.environ.get("SUPABASE_PUBLISHABLE_KEY")
)

sb = create_client(url, key)

print("Checking current users table columns...")
res = sb.table("users").select("*").limit(1).execute()
if res.data:
    cols = list(res.data[0].keys())
    print(f"Existing columns: {cols}")
else:
    print("No rows in users table — trying to insert a dummy select to see error")
    cols = []

needed = ["bank_account", "ifsc_code", "bank_name", "upi_id", "password_changed"]
missing = [c for c in needed if c not in cols]

if not missing:
    print("\nAll columns already exist! No migration needed.")
else:
    print(f"\nMissing columns: {missing}")
    print("\n>>> Please run this SQL in the Supabase SQL Editor (Dashboard > SQL Editor):\n")
    print("-- Migration: add missing columns to users table")
    for col in missing:
        if col == "password_changed":
            print(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} BOOLEAN NOT NULL DEFAULT FALSE;")
        else:
            print(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} TEXT;")
    print("\n-- After running, restart the Flask server.")
