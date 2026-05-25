"""Optional: create demo farmer and buyer accounts. Run once after schema is applied."""

import os

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

import database as db

load_dotenv()

DEMO_USERS = [
    {
        "email": "farmer@demo.local",
        "password": "demo1234",
        "full_name": "Demo Farmer",
        "role": "farmer",
        "location": "Punjab",
    },
    {
        "email": "buyer@demo.local",
        "password": "demo1234",
        "full_name": "Demo Buyer",
        "role": "buyer",
        "location": "Delhi",
    },
]


def main():
    for u in DEMO_USERS:
        if db.get_user_by_email(u["email"]):
            print(f"Skip (exists): {u['email']}")
            continue
        db.create_user(
            email=u["email"],
            password_hash=generate_password_hash(u["password"]),
            full_name=u["full_name"],
            role=u["role"],
            location=u.get("location"),
        )
        print(f"Created: {u['email']} / {u['password']} ({u['role']})")


if __name__ == "__main__":
    main()
