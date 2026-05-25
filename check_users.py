import database as db
from dotenv import load_dotenv
load_dotenv()

users = db.list_users_by_role()
print("Current Users in Database:")
for u in users:
    print(f"Role: {u['role']} | Email: {u['email']} | Login ID: {u['login_id']} | Name: {u['full_name']}")
