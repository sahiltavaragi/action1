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
    print(f"Notifications count: {len(notifications)}")
    for n in notifications:
        msg = n['message'].replace('\u20b9', 'Rs.')
        print(f"ID: {n['id']} | Title: {n['title']} | Is Read: {n['is_read']} | Msg: {msg}")
else:
    print("User not found")
