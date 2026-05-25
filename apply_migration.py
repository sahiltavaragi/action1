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

if not url or not key:
    raise RuntimeError('Supabase credentials not set in .env')

sb = create_client(url, key)

# Columns we expect in the users table
needed = ['bank_account', 'ifsc_code', 'bank_name', 'upi_id']

# Build ALTER statements for missing columns
for col in needed:
    stmt = f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} TEXT;"
    try:
        sb.rpc('execute_sql', {"sql": stmt}).execute()
        print(f'Executed: {stmt}')
    except Exception as e:
        print(f'Failed to execute {stmt}: {e}')

print('Migration complete.')
