# -*- coding: utf-8 -*-
"""Check if trading_wallet_balance column exists, if not guide user."""
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.environ.get('SUPABASE_URL')
key = (
    os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    or os.environ.get('SUPABASE_ANON_KEY')
    or os.environ.get('SUPABASE_PUBLISHABLE_KEY')
)

sb = create_client(url, key)

# Try to read the column - if it exists, we're good
try:
    res = sb.table("users").select("id, trading_wallet_balance").limit(1).execute()
    print("SUCCESS: trading_wallet_balance column already exists!")
    if res.data:
        print(f"Sample row: {res.data[0]}")
except Exception as e:
    err_str = str(e)
    if "trading_wallet_balance" in err_str or "column" in err_str.lower():
        print("Column does NOT exist yet.")
        print("")
        print("Please run this SQL in Supabase SQL Editor:")
        print("  https://supabase.com/dashboard/project/gnqewafabdictfbxrztu/sql/new")
        print("")
        print("  ALTER TABLE users ADD COLUMN IF NOT EXISTS trading_wallet_balance NUMERIC(12, 2) NOT NULL DEFAULT 0;")
    else:
        print(f"Error: {e}")
