import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import database as db
from dotenv import load_dotenv

load_dotenv()

buyer = db.get_user_by_email("buyer@demo.local")
if not buyer:
    print("Demo Buyer not found!")
    exit(1)

user_id = buyer["id"]
db.set_wallet_balance(user_id, 10000)

print(f"Initial buyer info: {buyer['full_name']}")

# Check starting balances
sec_dep = db.get_security_deposit_balance(user_id)
trading_bal = db.get_trading_wallet_balance(user_id)
print(f"Starting Security Deposit: Rs. {sec_dep:,.2f}")
print(f"Starting Trading Wallet: Rs. {trading_bal:,.2f}")

print("\n--- Depositing Rs. 15,000 to Trading Wallet ---")
new_trading = db.add_trading_wallet(user_id, 15000)
sec_dep = db.get_security_deposit_balance(user_id)
trading_bal = db.get_trading_wallet_balance(user_id)
print(f"New Security Deposit: Rs. {sec_dep:,.2f} (should remain Rs. 10,000)")
print(f"New Trading Wallet: Rs. {trading_bal:,.2f}")

print("\n--- Deducting Rs. 5,000 from Trading Wallet ---")
success = db.deduct_trading_wallet(user_id, 5000)
sec_dep = db.get_security_deposit_balance(user_id)
trading_bal = db.get_trading_wallet_balance(user_id)
print(f"Deduction success: {success}")
print(f"Post-deduction Security Deposit: Rs. {sec_dep:,.2f} (should remain untouched at Rs. 10,000)")
print(f"Post-deduction Trading Wallet: Rs. {trading_bal:,.2f}")
