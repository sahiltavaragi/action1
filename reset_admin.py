import database as db
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
load_dotenv()

def reset_admin():
    admin_email = "admin@farmauction.local"
    new_password = "admin123"
    
    admin = db.get_user_by_email(admin_email)
    if admin:
        db.get_supabase().table("users").update({
            "password_hash": generate_password_hash(new_password)
        }).eq("email", admin_email).execute()
        print(f"Admin password has been reset to: {new_password}")
    else:
        # Create admin if doesn't exist
        db.create_user(
            email=admin_email,
            password_hash=generate_password_hash(new_password),
            full_name="System Admin",
            role="admin",
            login_id="admin"
        )
        print(f"Admin account created with password: {new_password}")

if __name__ == "__main__":
    reset_admin()
