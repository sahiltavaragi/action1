import sys
import os
sys.path.append(os.getcwd())

import database as db
from dotenv import load_dotenv
load_dotenv()

user = db.get_user_by_login("9113215200")
if user:
    print(f"User ID: {user['id']} | Name: {user['full_name']}")
    notifications = db.list_notifications(user["id"])
    if notifications:
        target_id = notifications[0]["id"]
        # Update is_read to False for the first notification
        db.get_supabase().table("notifications").update({"is_read": False}).eq("id", target_id).execute()
        print(f"Notification {target_id} marked as UNREAD.")
    else:
        print("No notifications found to mark unread.")
else:
    print("User not found")
