"""Hybrid Agricultural Auction System — Flask + Supabase."""

import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_apscheduler import APScheduler
from werkzeug.security import check_password_hash, generate_password_hash

import httpx

import database as db

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

scheduler = APScheduler()


# --------------- Global error handler for Supabase timeouts ---------------
@app.errorhandler(httpx.ConnectTimeout)
@app.errorhandler(httpx.ReadTimeout)
@app.errorhandler(httpx.ConnectError)
@app.errorhandler(httpx.TimeoutException)
def handle_db_timeout(error):
    """Show a friendly retry page when Supabase is unreachable."""
    app.logger.warning("Supabase connection error: %s", error)
    return (
        """<!DOCTYPE html>
        <html><head><meta charset="UTF-8">
        <title>Connection Issue — AgriAuction</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
        <style>
          body{font-family:'Inter',sans-serif;background:#f9fafb;display:flex;
               align-items:center;justify-content:center;min-height:100vh;margin:0;}
          .card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;
                padding:2.5rem;max-width:460px;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.06);}
          h1{font-size:1.4rem;margin-bottom:.5rem;color:#111827;}
          p{color:#6b7280;font-size:.95rem;line-height:1.5;margin-bottom:1.25rem;}
          .btn{display:inline-block;padding:.65rem 1.5rem;background:#2563eb;color:#fff;
               text-decoration:none;border-radius:6px;font-weight:600;font-size:.9rem;
               transition:background .2s;}
          .btn:hover{background:#1d4ed8;}
          .icon{font-size:2.5rem;margin-bottom:.75rem;}
        </style></head>
        <body><div class="card">
          <div class="icon">🔌</div>
          <h1>Database Temporarily Unavailable</h1>
          <p>The database server is waking up from sleep mode (free-tier cold start).
             This usually resolves in a few seconds.</p>
          <a class="btn" href="javascript:location.reload()">🔄 Retry Now</a>
        </div></body></html>""",
        503,
    )


def format_dt(value):
    if not value:
        return "—"
    try:
        val_str = str(value)
        if val_str.endswith('Z'):
            val_str = val_str[:-1] + '+00:00'
        dt = datetime.fromisoformat(val_str)
        return dt.strftime("%Y-%m-%d, %I:%M:%S %p")
    except Exception:
        return value


@app.template_filter('format_datetime')
def format_datetime_filter(value):
    return format_dt(value)



def login_required(role: str | None = None, roles: tuple[str, ...] | None = None):
    allowed = roles or ((role,) if role else None)

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            if allowed and session.get("role") not in allowed:
                flash("You do not have permission for that action.", "danger")
                return redirect(url_for("index"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def process_bid_wallet(auction: dict, bidder_id: str, amount: float) -> bool:
    """Hold bid amount from Trading Wallet: refund previous high bidder, deduct from new bidder."""
    prev_id = auction.get("highest_bidder_id")
    prev_amount = float(auction.get("current_price") or 0)
    qty = float(auction.get("crops", {}).get("quantity") or 1)
    
    prev_total = prev_amount * qty
    total_bid = amount * qty

    # Refund previous bidder's trading wallet
    if prev_id and prev_id != bidder_id and prev_total > 0:
        db.add_trading_wallet(prev_id, prev_total)
    # Deduct from new bidder's trading wallet
    if not db.deduct_trading_wallet(bidder_id, total_bid):
        # Rollback the refund if deduction fails
        if prev_id and prev_id != bidder_id and prev_total > 0:
            db.deduct_trading_wallet(prev_id, prev_total)
        return False
    return True


def close_expired_auctions():
    try:
        expired = db.list_expired_active_auctions()
        for auction in expired:
            winner = auction.get("highest_bidder_id")
            reason = "time_expired_with_winner" if winner else "time_expired_no_bids"
            db.close_auction(
                auction["id"],
                auction["crop_id"],
                winner,
                reason,
            )
        if expired:
            app.logger.info("Closed %d expired auction(s)", len(expired))
    except Exception:
        app.logger.exception("Error closing expired auctions")


@scheduler.task("interval", id="close_auctions", seconds=60)
def scheduled_close_auctions():
    with app.app_context():
        close_expired_auctions()


@app.route("/")
def index():
    # If user is already logged in, send them to dashboard
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    # Otherwise show the signup page
    return redirect(url_for("register"))


@app.route("/board")
def auction_board():
    auctions = db.list_auctions()
    live = [a for a in auctions if a.get("status") == "active"]
    closed = [a for a in auctions if a.get("status") == "closed"]
    wallet = None
    if session.get("user_id") and session.get("role") == "buyer":
        wallet = db.get_security_deposit_balance(session["user_id"])
    return render_template(
        "index.html",
        live_auctions=live,
        closed_auctions=closed,
        total_lots=len(auctions),
        live_count=len(live),
        wallet=wallet,
        deposit_min=db.DEPOSIT_MINIMUM,
    )


@app.route("/browse")
def browse():
    return render_template("browse.html", auctions=db.list_auctions())


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip() or None
        city = request.form.get("city", "").strip()
        pincode = request.form.get("pincode", "").strip()
        location = f"{city}, {pincode}" if city and pincode else (city or pincode or None)

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template(
                "register.html", initial_wallet=db.BUYER_INITIAL_WALLET
            )

        if db.get_user_by_email(email):
            flash("Email already registered.", "danger")
            return render_template(
                "register.html", initial_wallet=db.BUYER_INITIAL_WALLET
            )

        user = db.create_user(
            email=email,
            password_hash=generate_password_hash(password),
            full_name=full_name,
            role="buyer",
            phone=phone,
            location=location,
            wallet_balance=db.BUYER_INITIAL_WALLET,
        )
        session["user_id"] = user["id"]
        session["role"] = user["role"]
        session["full_name"] = user["full_name"]
        flash("Account created! Please add a security deposit to start bidding.", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "register.html", initial_wallet=db.BUYER_INITIAL_WALLET
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        login_id = request.form.get("login_id", "").strip()
        password = request.form.get("password", "")
        user = db.get_user_by_login(login_id)
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid login ID / email or password.", "danger")
            return render_template("login.html")

        session["user_id"] = user["id"]
        session["role"] = user["role"]
        session["full_name"] = user["full_name"]

        if user.get("password_changed") is False:
            flash("First login detected. Please change your password to continue.", "info")
            return redirect(url_for("change_password"))

        flash(f"Welcome back, {user['full_name']}!", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/change-password", methods=["GET", "POST"])
@login_required()
def change_password():
    if request.method == "POST":
        new_password = request.form.get("password", "")
        if len(new_password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("change_password.html")

        db.get_supabase().table("users").update(
            {
                "password_hash": generate_password_hash(new_password),
                "password_changed": True,
            }
        ).eq("id", session["user_id"]).execute()

        flash("Password updated successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("change_password.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required()
def dashboard():
    role = session.get("role")
    notifications = db.list_notifications(session["user_id"])
    if role == "admin":
        return redirect(url_for("admin_dashboard"))
    if role == "farmer":
        crops = db.list_farmer_crops(session["user_id"])
        wallet = db.get_wallet_balance(session["user_id"])
        user = db.get_user_by_id(session["user_id"])
        txs = db.list_farmer_transactions(session["user_id"])
        
        total_earned = sum(float(t.get("farmer_payout") or 0) for t in txs if t.get("status") == "released")
        pending_payout = sum(float(t.get("farmer_payout") or 0) for t in txs if t.get("status") == "held")
        
        has_unread = any(not n.get("is_read") for n in notifications)
        
        return render_template(
            "farmer_dashboard.html", 
            crops=crops, 
            wallet=wallet, 
            user=user, 
            notifications=notifications,
            has_unread=has_unread,
            transactions=txs,
            total_earned=total_earned,
            pending_payout=pending_payout
        )
    wallet = db.get_security_deposit_balance(session["user_id"])
    trading_wallet = db.get_trading_wallet_balance(session["user_id"])
    won_auctions = db.list_buyer_won_auctions(session["user_id"])
    pending_payments = db.list_buyer_pending_payments(session["user_id"])
    buyer_bids = db.list_buyer_bids(session["user_id"])
    transactions = db.list_buyer_transactions(session["user_id"])
    
    total_bids = len(buyer_bids)
    total_bid_value = sum(float(b.get("amount") or 0) for b in buyer_bids)
    payment_count = len(transactions)

    return render_template(
        "buyer_dashboard.html",
        wallet=wallet,
        trading_wallet=trading_wallet,
        eligible=wallet >= db.DEPOSIT_MINIMUM,
        deposit_min=db.DEPOSIT_MINIMUM,
        notifications=notifications,
        won_auctions=won_auctions,
        pending_payments=pending_payments,
        buyer_bids=buyer_bids,
        transactions=transactions,
        total_bids=total_bids,
        total_bid_value=total_bid_value,
        payment_count=payment_count
    )


@app.route("/dashboard/deposit", methods=["POST"])
@login_required(role="buyer")
def add_deposit():
    amount = request.form.get("amount", type=float)
    if not amount or amount <= 0:
        flash("Please enter a valid deposit amount.", "danger")
        return redirect(url_for("dashboard"))
    
    db.add_wallet(session["user_id"], amount)
    flash(f"₹{amount:,.2f} added to your Security Deposit.", "success")
    return redirect(url_for("dashboard"))


@app.route("/dashboard/trading-deposit", methods=["POST"])
@login_required(role="buyer")
def add_trading_deposit():
    amount = request.form.get("amount", type=float)
    if not amount or amount <= 0:
        flash("Please enter a valid amount.", "danger")
        return redirect(url_for("dashboard"))
    
    db.add_trading_wallet(session["user_id"], amount)
    flash(f"₹{amount:,.2f} added to your Trading Wallet.", "success")
    return redirect(url_for("dashboard"))


@app.route("/notifications/read", methods=["POST"])
@login_required(role="farmer")
def mark_read():
    db.mark_notifications_read(session["user_id"])
    return redirect(request.referrer or url_for("dashboard"))


# ——— Admin ———

@app.route("/admin")
@login_required(role="admin")
def admin_dashboard():
    return redirect(url_for("admin_overview"))


@app.route("/admin/overview")
@login_required(role="admin")
def admin_overview():
    return render_template(
        "admin/overview.html",
        stats=db.get_admin_stats(),
        activity=db.list_recent_activity(),
    )


@app.route("/admin/farmers", methods=["GET", "POST"])
@login_required(role="admin")
def admin_farmers():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        location = request.form.get("location", "").strip() or None
        bank_account = request.form.get("bank_account", "").strip() or None
        ifsc_code = request.form.get("ifsc_code", "").strip() or None
        bank_name = request.form.get("bank_name", "").strip() or None
        upi_id = request.form.get("upi_id", "").strip() or None

        if not full_name or not phone:
            flash("Farmer name and phone number are required.", "danger")
            return redirect(url_for("admin_farmers"))
            
        if not bank_account or not ifsc_code or not bank_name:
            flash("Bank Account, IFSC Code, and Bank Name are compulsory for farmer registration.", "danger")
            return redirect(url_for("admin_farmers"))

        login_id = phone
        password = phone
        email = f"{phone}@farmauction.local"

        if db.get_user_by_login(login_id):
            flash(f"A farmer with phone number {phone} already exists.", "danger")
            return redirect(url_for("admin_farmers"))

        user = db.create_user(
            email=email,
            password_hash=generate_password_hash(password),
            full_name=full_name,
            role="farmer",
            phone=phone,
            location=location,
            login_id=login_id,
            wallet_balance=0,
            bank_account=bank_account,
            ifsc_code=ifsc_code,
            bank_name=bank_name,
            upi_id=upi_id
        )
        db.add_notification(user["id"], "Account Created", f"Welcome {full_name}! Your account has been created by the admin. Please change your password.")
        flash(f"Farmer {full_name} registered successfully.", "success")
        return redirect(url_for("admin_farmers"))

    farmers = db.list_farmers()
    return render_template("admin/farmers.html", farmers=farmers)


@app.route("/admin/crop-lots")
@login_required(role="admin")
def admin_crop_lots():
    return render_template("admin/crop_lots.html", lots=db.list_auctions())


@app.route("/admin/deposits")
@login_required(role="admin")
def admin_deposits():
    return render_template(
        "admin/deposits.html",
        buyers=db.list_users_by_role("buyer"),
        stats=db.get_admin_stats(),
        deposit_min=db.DEPOSIT_MINIMUM,
    )


@app.route("/admin/payments")
@login_required(role="admin")
def admin_payments():
    txs = db.list_transactions()
    total_value = sum(float(t.get("bid_amount") or 0) for t in txs if t.get("status") == "released")
    total_commission = sum(float(t.get("commission") or 0) for t in txs if t.get("status") == "released")
    total_payout = sum(float(t.get("farmer_payout") or 0) for t in txs if t.get("status") == "released")
    return render_template(
        "admin/payments.html",
        transactions=txs,
        total_value=total_value,
        total_commission=total_commission,
        total_payout=total_payout,
    )


@app.route("/admin/payment/<tx_id>/<action>", methods=["POST"])
@login_required(role="admin")
def admin_payment_action(tx_id, action):
    if action == "release":
        if db.release_payment(tx_id):
            flash("Payment released to farmer.", "success")
        else:
            flash("Could not release payment.", "danger")
    elif action == "refund":
        if db.refund_payment(tx_id):
            flash("Payment refunded to buyer.", "info")
        else:
            flash("Could not refund payment.", "danger")
    return redirect(url_for("admin_payments"))


@app.route("/admin/farmer/new", methods=["GET", "POST"])
@login_required(role="admin")
def admin_create_farmer():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        location = request.form.get("location", "").strip() or None
        bank_account = request.form.get("bank_account", "").strip() or None
        ifsc_code = request.form.get("ifsc_code", "").strip() or None
        bank_name = request.form.get("bank_name", "").strip() or None
        upi_id = request.form.get("upi_id", "").strip() or None

        if not full_name or not phone:
            flash("Farmer name and phone number are required.", "danger")
            return render_template("admin_create_farmer.html")

        if not bank_account or not ifsc_code or not bank_name:
            flash("Bank Account, IFSC Code, and Bank Name are compulsory for farmer registration.", "danger")
            return render_template("admin_create_farmer.html")

        # Flow says: Username = Phone Number, Default Password = Phone Number
        login_id = phone
        password = phone
        # Using a dummy email if none provided, or generate one from phone
        email = request.form.get("email", "").strip() or f"{phone}@farmauction.local"

        if db.get_user_by_login(login_id):
            flash(f"A farmer with phone number {phone} already exists.", "danger")
            return render_template("admin_create_farmer.html")

        user = db.create_user(
            email=email,
            password_hash=generate_password_hash(password),
            full_name=full_name,
            role="farmer",
            phone=phone,
            location=location,
            login_id=login_id,
            wallet_balance=0,
            bank_account=bank_account,
            ifsc_code=ifsc_code,
            bank_name=bank_name,
            upi_id=upi_id
        )
        db.add_notification(user["id"], "Account Created", f"Welcome {full_name}! Your account has been created by the admin. Please change your password.")
        flash(
            f"Farmer created — Login ID: {login_id} | Password: {password}",
            "success",
        )
        return redirect(url_for("admin_farmers"))

    return render_template("admin_create_farmer.html")


@app.route("/admin/crop/new", methods=["GET", "POST"])
@login_required(role="admin")
def admin_add_crop():
    farmers = db.list_farmers()
    if request.method == "POST":
        farmer_id = request.form.get("farmer_id", "").strip()
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "").strip()
        quantity = request.form.get("quantity", type=float)
        unit = request.form.get("unit", "kg").strip()
        quality_grade = request.form.get("quality_grade", "B").strip()
        location = request.form.get("location", "").strip() or None
        start_price = request.form.get("start_price", type=float)
        starts_at = request.form.get("starts_at")
        ends_at = request.form.get("ends_at")
        
        # Browser datetime-local sends YYYY-MM-DDTHH:MM or YYYY-MM-DDTHH:MM:SS
        if starts_at:
            if len(starts_at) == 16:
                starts_at += ":00+05:30"
            elif len(starts_at) == 19:
                starts_at += "+05:30"
        if ends_at:
            if len(ends_at) == 16:
                ends_at += ":00+05:30"
            elif len(ends_at) == 19:
                ends_at += "+05:30"

        image_url = None
        image_file = request.files.get("image")
        if image_file and image_file.filename:
            try:
                image_url = db.upload_crop_image(
                    image_file.read(),
                    image_file.filename,
                    image_file.content_type or "image/jpeg",
                )
            except Exception:
                flash("Image upload failed; crop saved without photo.", "warning")

        if not farmer_id or not name or not category or not quantity or start_price is None or not starts_at or not ends_at:
            flash("Please fill all required fields and select a farmer.", "danger")
            return render_template("admin_add_crop.html", farmers=farmers)

        farmer = db.get_user_by_id(farmer_id)

        db.create_crop_and_auction(
            farmer_id,
            {
                "farmer_id": farmer_id,
                "name": name,
                "description": description,
                "category": category,
                "quantity": quantity,
                "unit": unit,
                "quality_grade": f"Grade {quality_grade}",
                "location": location,
                "image_url": image_url,
                "status": "active",
            },
            {
                "start_price": start_price,
                "current_price": start_price,
                "base_price": start_price,
                "buy_now_price": None,
                "min_increment": 1.0,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "status": "active",
            },
        )
        flash(f"Crop lot listed for {farmer['full_name']}.", "success")
        return redirect(url_for("admin_crop_lots"))

    return render_template("admin_add_crop.html", farmers=farmers)


@app.route("/admin/wallet", methods=["GET", "POST"])
@login_required(role="admin")
def admin_add_money():
    users = db.list_users_by_role("buyer")
    if request.method == "POST":
        user_id = request.form.get("user_id", "").strip()
        amount = request.form.get("amount", type=float)
        if not user_id or amount is None or amount <= 0:
            flash("Select a buyer and enter a positive amount.", "danger")
            return render_template("admin_add_money.html", users=users)

        user = db.get_user_by_id(user_id)
        new_bal = db.add_wallet(user_id, amount)
        flash(f"Added ₹{amount:,.2f} to {user['full_name']}. New balance: ₹{new_bal:,.2f}", "success")
        return redirect(url_for("admin_deposits"))

    return render_template("admin_add_money.html", users=users)


# ——— Auctions ———

@app.route("/auction/<auction_id>")
def auction_detail(auction_id):
    auction = db.get_auction_detail(auction_id)
    if not auction:
        flash("Auction not found.", "danger")
        return redirect(url_for("index"))

    bids = db.list_bids_for_auction(auction_id)
    ends = db.parse_ts(auction.get("ends_at"))
    now = datetime.now(timezone.utc)
    seconds_left = max(0, int((ends - now).total_seconds())) if ends else 0
    is_active = auction.get("status") == "active" and seconds_left > 0
    wallet = None
    trading_wallet = None
    has_paid = False
    if session.get("user_id") and session.get("role") == "buyer":
        wallet = db.get_security_deposit_balance(session["user_id"])
        trading_wallet = db.get_trading_wallet_balance(session["user_id"])
        txs = db.list_buyer_transactions(session["user_id"])
        has_paid = any(t.get("auction_id") == auction_id for t in txs)

    return render_template(
        "auction_detail.html",
        auction=auction,
        bids=bids,
        seconds_left=seconds_left,
        is_active=is_active,
        wallet=wallet,
        trading_wallet=trading_wallet,
        deposit_min=db.DEPOSIT_MINIMUM,
        has_paid=has_paid,
    )


@app.route("/api/auction/<auction_id>")
def api_auction_detail(auction_id):
    auction = db.get_auction_detail(auction_id)
    if not auction:
        return {"error": "Not found"}, 404

    bids = db.list_bids_for_auction(auction_id, limit=5)
    ends = db.parse_ts(auction.get("ends_at"))
    now = datetime.now(timezone.utc)
    seconds_left = max(0, int((ends - now).total_seconds())) if ends else 0

    return {
        "current_price": float(auction["current_price"]),
        "highest_bidder": auction.get("users", {}).get("full_name", "—") if auction.get("highest_bidder_id") else "—",
        "seconds_left": seconds_left,
        "status": auction.get("status"),
        "bids": [
            {
                "bidder": b.get("users", {}).get("full_name", "Bidder"),
                "amount": float(b["amount"]),
                "time": format_dt(b.get("created_at"))
            } for b in bids
        ]
    }


@app.route("/auction/<auction_id>/bid", methods=["POST"])
@login_required(role="buyer")
def place_bid(auction_id):
    amount = request.form.get("amount", type=float)
    auction = db.get_auction_detail(auction_id)

    if not auction or auction.get("status") != "active":
        flash("This auction is not active.", "danger")
        return redirect(url_for("auction_detail", auction_id=auction_id))

    ends = db.parse_ts(auction.get("ends_at"))
    if ends and ends <= datetime.now(timezone.utc):
        flash("Auction has ended.", "warning")
        return redirect(url_for("auction_detail", auction_id=auction_id))

    current = float(auction["current_price"])

    if amount is None or amount <= current:
        flash(f"Bid must be higher than ₹{current:,.2f}.", "danger")
        return redirect(url_for("auction_detail", auction_id=auction_id))

    security_deposit = db.get_security_deposit_balance(session["user_id"])
    if security_deposit < db.DEPOSIT_MINIMUM:
        flash(f"Bidding Disabled: You must maintain a minimum Security Deposit of ₹{db.DEPOSIT_MINIMUM:,.2f} to place bids. Current deposit: ₹{security_deposit:,.2f}.", "danger")
        return redirect(url_for("auction_detail", auction_id=auction_id))

    # Notify previous bidder if they exist
    prev_bidder_id = auction.get("highest_bidder_id")
    if prev_bidder_id and prev_bidder_id != session["user_id"]:
        crop_name = (auction.get("crops") or {}).get("name", "crop")
        db.add_notification(
            prev_bidder_id,
            "Outbid!",
            f"You've been outbid on {crop_name}. The new highest bid is ₹{amount:,.2f}."
        )

    sb = db.get_supabase()
    sb.table("bids").insert(
        {
            "auction_id": auction_id,
            "bidder_id": session["user_id"],
            "amount": amount,
        }
    ).execute()
    sb.table("auctions").update(
        {"current_price": amount, "highest_bidder_id": session["user_id"]}
    ).eq("id", auction_id).execute()

    flash(f"Bid ₹{amount:,.2f} placed successfully!", "success")
    return redirect(url_for("auction_detail", auction_id=auction_id))


@app.route("/auction/<auction_id>/buy-now", methods=["POST"])
@login_required(role="buyer")
def buy_now(auction_id):
    auction = db.get_auction_detail(auction_id)

    if not auction or auction.get("status") != "active":
        flash("This auction is not active.", "danger")
        return redirect(url_for("auction_detail", auction_id=auction_id))

    price = float(auction.get("buy_now_price") or 0)
    if not price:
        flash("Buy now is not available.", "danger")
        return redirect(url_for("auction_detail", auction_id=auction_id))

    security_deposit = db.get_security_deposit_balance(session["user_id"])
    if security_deposit < db.DEPOSIT_MINIMUM:
        flash(f"Purchase Disabled: You must maintain a minimum Security Deposit of ₹{db.DEPOSIT_MINIMUM:,.2f} to make purchases. Current deposit: ₹{security_deposit:,.2f}.", "danger")
        return redirect(url_for("auction_detail", auction_id=auction_id))

    crop = auction.get("crops") or {}
    farmer_id = crop.get("farmer_id")

    sb = db.get_supabase()
    sb.table("auctions").update(
        {
            "status": "closed",
            "current_price": price,
            "highest_bidder_id": session["user_id"],
            "winner_id": session["user_id"],
            "closed_reason": "buy_now",
        }
    ).eq("id", auction_id).execute()
    sb.table("crops").update(
        {"status": "sold", "updated_at": db.utc_now_iso()}
    ).eq("id", auction["crop_id"]).execute()

    if farmer_id:
        db.add_notification(
            session["user_id"],
            "Auction Won (Buy Now)",
            f"You won {crop.get('name')} via Buy Now! Please initiate payment of ₹{price:,.2f} to complete your purchase."
        )
        db.add_notification(
            farmer_id,
            "Crop Sold (Buy Now)",
            f"Your crop {crop.get('name')} was purchased via Buy Now. Waiting for buyer to initiate payment."
        )

    flash("Instant purchase successful! Please initiate payment to complete the transaction.", "success")
    return redirect(url_for("pay_auction", auction_id=auction_id))


@app.route("/auction/<auction_id>/pay", methods=["GET", "POST"])
@login_required(role="buyer")
def pay_auction(auction_id):
    auction = db.get_auction_detail(auction_id)
    if not auction or auction.get("status") != "closed":
        flash("This auction is not closed or settled yet.", "danger")
        return redirect(url_for("dashboard"))

    if auction.get("winner_id") != session["user_id"]:
        flash("You are not the winner of this auction.", "danger")
        return redirect(url_for("dashboard"))

    # Check if transaction already exists
    txs = db.list_buyer_transactions(session["user_id"])
    existing_tx = next((t for t in txs if t.get("auction_id") == auction_id), None)
    if existing_tx:
        flash("You have already initiated payment for this auction.", "info")
        return redirect(url_for("dashboard"))

    crop = auction.get("crops") or {}
    farmer_id = crop.get("farmer_id")
    farmer = db.get_user_by_id(farmer_id) if farmer_id else {}

    unit_price = float(auction.get("current_price") or 0)
    qty = float(crop.get("quantity") or 1)
    total_price = unit_price * qty

    trading_balance = db.get_trading_wallet_balance(session["user_id"])
    needed = max(0.0, total_price - trading_balance)

    if request.method == "POST":
        if needed > 0:
            # Auto top up
            db.add_trading_wallet(session["user_id"], needed)
            flash(f"Auto-deposited ₹{needed:,.2f} to your Trading Wallet.", "success")
        
        # Deduct
        if db.deduct_trading_wallet(session["user_id"], total_price):
            if farmer_id:
                db.record_transaction(auction, session["user_id"], farmer_id, total_price, "held")
                db.add_notification(
                    session["user_id"],
                    "Payment Completed",
                    f"Escrow payment of ₹{total_price:,.2f} completed for {crop.get('name')}. Payout is held in escrow."
                )
                db.add_notification(
                    farmer_id,
                    "Payment Completed",
                    f"Payment of ₹{total_price:,.2f} for crop {crop.get('name')} has been completed by the buyer and is held in escrow."
                )
            flash(f"Payment of ₹{total_price:,.2f} completed successfully! The crop is now secured in escrow.", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Payment failed. Please try again or check your balance.", "danger")
            return redirect(url_for("pay_auction", auction_id=auction_id))

    return render_template(
        "pay_auction.html",
        auction=auction,
        crop=crop,
        farmer=farmer,
        unit_price=unit_price,
        total_price=total_price,
        trading_balance=trading_balance,
        needed=needed
    )


def _start_scheduler() -> None:
    if not scheduler.running:
        scheduler.init_app(app)
        scheduler.start()


if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or __name__ != "__main__":
    _start_scheduler()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
