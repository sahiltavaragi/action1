"""Supabase client and data-access helpers for the auction system."""

import os
import time
import httpx

# Force HTTP/1.1 to avoid Windows-specific socket read errors (WinError 10035 / WSAEWOULDBLOCK) with HTTP/2
# Also enforce a longer timeout to prevent ReadTimeout/ConnectTimeout errors on Supabase free-tier cold starts.
original_client_init = httpx.Client.__init__
def patched_client_init(self, *args, **kwargs):
    if "http2" in kwargs:
        kwargs["http2"] = False
    # Give 60s for connection (covers TLS handshake + free-tier cold start) and 90s to read
    if "timeout" not in kwargs:
        kwargs["timeout"] = httpx.Timeout(90.0, connect=60.0)
    original_client_init(self, *args, **kwargs)
httpx.Client.__init__ = patched_client_init

original_async_client_init = httpx.AsyncClient.__init__
def patched_async_client_init(self, *args, **kwargs):
    if "http2" in kwargs:
        kwargs["http2"] = False
    if "timeout" not in kwargs:
        kwargs["timeout"] = httpx.Timeout(90.0, connect=60.0)
    original_async_client_init(self, *args, **kwargs)
httpx.AsyncClient.__init__ = patched_async_client_init


original_send = httpx.Client.send
def patched_send(self, request, *args, **kwargs):
    last_exc = None
    retries = 2
    delay = 2.0
    for attempt in range(retries):
        try:
            return original_send(self, request, *args, **kwargs)
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError, httpx.TimeoutException) as exc:
            last_exc = exc
            print(f"[DB] Timeout attempt {attempt + 1}/{retries} — {request.url}: {exc}")
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    raise last_exc
httpx.Client.send = patched_send


original_async_send = httpx.AsyncClient.send
async def patched_async_send(self, request, *args, **kwargs):
    last_exc = None
    retries = 3
    delay = 1.5
    import asyncio
    for attempt in range(retries):
        try:
            return await original_async_send(self, request, *args, **kwargs)
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as exc:
            last_exc = exc
            print(f"HTTPX AsyncClient.send timeout on request {request.url} (attempt {attempt + 1}/{retries}): {exc}")
            if attempt < retries - 1:
                await asyncio.sleep(delay * (attempt + 1))
    raise last_exc
httpx.AsyncClient.send = patched_async_send


from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()


CROP_IMAGES_BUCKET = "crop-images"
BUYER_INITIAL_WALLET = float(os.environ.get("BUYER_INITIAL_WALLET", "0"))
COMMISSION_RATE = float(os.environ.get("COMMISSION_RATE", "0.03"))
DEPOSIT_MINIMUM = float(os.environ.get("DEPOSIT_MINIMUM", "10000"))


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_PUBLISHABLE_KEY")
    )
    if not url or not key:
        raise RuntimeError(
            "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or "
            "SUPABASE_PUBLISHABLE_KEY) in .env"
        )
    return create_client(url, key)


_TIMEOUT_ERRORS = (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException)


def _supabase_call(fn, retries: int = 3, delay: float = 2.0):
    """Execute a lambda that makes a Supabase call, retrying on timeout errors."""
    last_exc = None
    for attempt in range(retries):
        try:
            return fn()
        except _TIMEOUT_ERRORS as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    raise last_exc


def parse_ts(value: str | None) -> datetime | None:
    """Parse Supabase timestamptz ISO strings."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upload_crop_image(file_bytes: bytes, filename: str, content_type: str) -> str:
    """Upload to Supabase Storage and return public URL."""
    sb = get_supabase()
    safe_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{filename}"
    file_options = {"content-type": content_type}
    try:
        sb.storage.from_(CROP_IMAGES_BUCKET).upload(
            safe_name,
            file_bytes,
            file_options,
        )
    except Exception as exc:
        if "already exists" not in str(exc).lower():
            raise
        sb.storage.from_(CROP_IMAGES_BUCKET).update(
            safe_name,
            file_bytes,
            file_options,
        )
    return sb.storage.from_(CROP_IMAGES_BUCKET).get_public_url(safe_name)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    res = (
        get_supabase()
        .table("users")
        .select("*")
        .eq("email", email.lower().strip())
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def get_user_by_login(login: str) -> dict[str, Any] | None:
    """Find user by email or login_id (for farmers)."""
    value = login.strip().lower()
    user = get_user_by_email(value)
    if user:
        return user
    res = (
        get_supabase()
        .table("users")
        .select("*")
        .eq("login_id", login.strip())
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def get_wallet_balance(user_id: str) -> float:
    user = get_user_by_id(user_id)
    if not user:
        return 0.0
    return float(user.get("wallet_balance") or 0)


def set_wallet_balance(user_id: str, amount: float) -> None:
    get_supabase().table("users").update(
        {"wallet_balance": round(max(0, amount), 2)}
    ).eq("id", user_id).execute()


def add_wallet(user_id: str, amount: float) -> float:
    balance = get_wallet_balance(user_id)
    new_balance = round(balance + amount, 2)
    set_wallet_balance(user_id, new_balance)
    return new_balance


def deduct_wallet(user_id: str, amount: float) -> bool:
    balance = get_wallet_balance(user_id)
    if balance < amount:
        return False
    set_wallet_balance(user_id, round(balance - amount, 2))
    return True


# ——— Trading Wallet & Security Deposit (dual-wallet system) ———

def get_security_deposit_balance(user_id: str) -> float:
    user = get_user_by_id(user_id)
    if not user:
        return 0.0
    if "trading_wallet_balance" in user:
        return float(user.get("wallet_balance") or 0)
    # Fallback to dynamic calculation
    total = float(user.get("wallet_balance") or 0)
    return min(total, DEPOSIT_MINIMUM)


def get_trading_wallet_balance(user_id: str) -> float:
    user = get_user_by_id(user_id)
    if not user:
        return 0.0
    if "trading_wallet_balance" in user:
        return float(user.get("trading_wallet_balance") or 0)
    # Fallback to dynamic calculation
    total = float(user.get("wallet_balance") or 0)
    return max(0.0, round(total - DEPOSIT_MINIMUM, 2))


def set_trading_wallet_balance(user_id: str, amount: float) -> None:
    user = get_user_by_id(user_id)
    if not user:
        return
    if "trading_wallet_balance" in user:
        get_supabase().table("users").update(
            {"trading_wallet_balance": round(max(0, amount), 2)}
        ).eq("id", user_id).execute()
    else:
        # Fallback to dynamic calculation
        deposit = min(float(user.get("wallet_balance") or 0), DEPOSIT_MINIMUM)
        if float(user.get("wallet_balance") or 0) >= DEPOSIT_MINIMUM:
            deposit = DEPOSIT_MINIMUM
        new_total = round(deposit + amount, 2)
        set_wallet_balance(user_id, new_total)


def add_trading_wallet(user_id: str, amount: float) -> float:
    user = get_user_by_id(user_id)
    if not user:
        return 0.0
    if "trading_wallet_balance" in user:
        balance = get_trading_wallet_balance(user_id)
        new_balance = round(balance + amount, 2)
        set_trading_wallet_balance(user_id, new_balance)
        return new_balance
    else:
        # Fallback to dynamic calculation
        total = float(user.get("wallet_balance") or 0)
        new_total = round(total + amount, 2)
        set_wallet_balance(user_id, new_total)
        return max(0.0, round(new_total - DEPOSIT_MINIMUM, 2))


def deduct_trading_wallet(user_id: str, amount: float) -> bool:
    balance = get_trading_wallet_balance(user_id)
    if balance < amount:
        return False
    set_trading_wallet_balance(user_id, round(balance - amount, 2))
    return True


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    res = (
        get_supabase()
        .table("users")
        .select("*")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def create_user(
    email: str,
    password_hash: str,
    full_name: str,
    role: str,
    phone: str | None = None,
    location: str | None = None,
    login_id: str | None = None,
    wallet_balance: float = 0,
    bank_account: str | None = None,
    ifsc_code: str | None = None,
    bank_name: str | None = None,
    upi_id: str | None = None,
    bank_details: str | None = None,
) -> dict[str, Any]:
    # Build bank_details from individual fields if not provided directly
    if not bank_details and any([bank_account, ifsc_code, bank_name]):
        parts = []
        if bank_name:
            parts.append(bank_name)
        if bank_account:
            parts.append(f"A/C: {bank_account}")
        if ifsc_code:
            parts.append(f"IFSC: {ifsc_code}")
        bank_details = " | ".join(parts)

    payload = {
        "email": email.lower().strip(),
        "password_hash": password_hash,
        "full_name": full_name,
        "role": role,
        "phone": phone,
        "location": location,
        "login_id": login_id,
        "wallet_balance": round(wallet_balance, 2),
        "bank_details": bank_details,
        "bank_account": bank_account,
        "ifsc_code": ifsc_code,
        "bank_name": bank_name,
        "upi_id": upi_id,
        "password_changed": False if role == "farmer" else True,
    }
    res = get_supabase().table("users").insert(payload).execute()
    return res.data[0]


def add_notification(user_id: str, title: str, message: str) -> None:
    get_supabase().table("notifications").insert(
        {"user_id": user_id, "title": title, "message": message}
    ).execute()


def list_notifications(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    res = (
        get_supabase()
        .table("notifications")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def mark_notifications_read(user_id: str) -> None:
    get_supabase().table("notifications").update({"is_read": True}).eq(
        "user_id", user_id
    ).execute()


def list_users_by_role(role: str | None = None) -> list[dict[str, Any]]:
    q = get_supabase().table("users").select(
        "id, email, login_id, full_name, role, wallet_balance, location, created_at"
    )
    if role:
        q = q.eq("role", role)
    res = q.order("created_at", desc=True).execute()
    return res.data or []


def list_farmers() -> list[dict[str, Any]]:
    return list_users_by_role("farmer")


def next_lot_number() -> str:
    sb = get_supabase()
    res = sb.table("crops").select("lot_number").order("created_at", desc=True).limit(1).execute()
    if res.data and res.data[0].get("lot_number"):
        num = int(res.data[0]["lot_number"].replace("LOT", "") or 0)
        return f"LOT{num + 1:03d}"
    count = sb.table("crops").select("id", count="exact").execute()
    n = (count.count or 0) + 1
    return f"LOT{n:03d}"


def _enrich_auction_with_users(auction: dict[str, Any], users_map: dict[str, Any]) -> dict[str, Any]:
    """Enrich a single auction using a pre-loaded users map (no DB calls)."""
    crop = auction.get("crops") or {}
    farmer_id = crop.get("farmer_id")
    farmer = users_map.get(farmer_id) if farmer_id else None
    crop["farmer_name"] = farmer["full_name"] if farmer else "—"
    winner_id = auction.get("winner_id") or auction.get("highest_bidder_id")
    if winner_id:
        w = users_map.get(winner_id)
        auction["winner_name"] = w["full_name"] if w else "—"
    else:
        auction["winner_name"] = "—"
    base = float(auction.get("base_price") or auction.get("start_price") or 0)
    current = float(auction.get("current_price") or 0)
    auction["base_price"] = base
    auction["above_base"] = max(0, round(current - base, 2))
    grade = crop.get("quality_grade") or "Grade B"
    if not str(grade).lower().startswith("grade"):
        grade = f"Grade {grade}"
    crop["grade"] = grade
    crop["lot_number"] = crop.get("lot_number") or "LOT000"
    return auction


def enrich_auction(auction: dict[str, Any]) -> dict[str, Any]:
    """Single-auction enrichment (used when loading one auction at a time)."""
    crop = auction.get("crops") or {}
    farmer_id = crop.get("farmer_id")
    farmer = get_user_by_id(farmer_id) if farmer_id else None
    crop["farmer_name"] = farmer["full_name"] if farmer else "—"
    winner_id = auction.get("winner_id") or auction.get("highest_bidder_id")
    if winner_id:
        w = get_user_by_id(winner_id)
        auction["winner_name"] = w["full_name"] if w else "—"
    else:
        auction["winner_name"] = "—"
    base = float(auction.get("base_price") or auction.get("start_price") or 0)
    current = float(auction.get("current_price") or 0)
    auction["base_price"] = base
    auction["above_base"] = max(0, round(current - base, 2))
    grade = crop.get("quality_grade") or "Grade B"
    if not str(grade).lower().startswith("grade"):
        grade = f"Grade {grade}"
    crop["grade"] = grade
    crop["lot_number"] = crop.get("lot_number") or "LOT000"
    return auction


def list_auctions(status: str | None = None) -> list[dict[str, Any]]:
    q = get_supabase().table("auctions").select("*, crops(*)")
    if status:
        q = q.eq("status", status)
    res = q.order("created_at", desc=True).execute()
    items = res.data or []
    if not items:
        return []

    # Collect all unique user IDs (farmers + winners) in one pass
    user_ids: set[str] = set()
    for a in items:
        crop = a.get("crops") or {}
        if crop.get("farmer_id"):
            user_ids.add(crop["farmer_id"])
        winner_id = a.get("winner_id") or a.get("highest_bidder_id")
        if winner_id:
            user_ids.add(winner_id)

    # Batch-load all required users in ONE query
    users_map: dict[str, Any] = {}
    if user_ids:
        users_res = (
            get_supabase()
            .table("users")
            .select("id, full_name")
            .in_("id", list(user_ids))
            .execute()
        )
        for u in (users_res.data or []):
            users_map[u["id"]] = u

    return [_enrich_auction_with_users(a, users_map) for a in items]


def list_closed_auctions() -> list[dict[str, Any]]:
    return list_auctions("closed")


def get_admin_stats() -> dict[str, Any]:
    all_auctions = list_auctions()
    active = [a for a in all_auctions if a.get("status") == "active"]
    closed = [a for a in all_auctions if a.get("status") == "closed"]
    farmers = list_farmers()
    buyers = list_users_by_role("buyer")
    # Use lightweight summary query - no per-transaction user lookups needed
    txs_res = get_supabase().table("transactions").select("bid_amount, commission, status").execute()
    txs_raw = txs_res.data or []
    total_revenue = sum(float(t.get("bid_amount") or 0) for t in txs_raw if t.get("status") == "released")
    total_commission = sum(float(t.get("commission") or 0) for t in txs_raw if t.get("status") == "released")
    bids_res = get_supabase().table("bids").select("id", count="exact").execute()
    eligible = sum(
        1 for b in buyers if float(b.get("wallet_balance") or 0) >= DEPOSIT_MINIMUM
    )
    total_deposits = sum(float(b.get("wallet_balance") or 0) for b in buyers)
    return {
        "total_lots": len(all_auctions),
        "live_count": len(active),
        "closed_count": len(closed),
        "total_revenue": total_revenue,
        "total_commission": total_commission,
        "farmer_count": len(farmers),
        "buyer_count": len(buyers),
        "total_bids": bids_res.count or 0,
        "eligible_bidders": eligible,
        "total_deposits": total_deposits,
        "depositor_count": len(buyers),
    }


def list_farmer_transactions(farmer_id: str) -> list[dict[str, Any]]:
    res = (
        get_supabase()
        .table("transactions")
        .select("*")
        .eq("farmer_id", farmer_id)
        .order("created_at", desc=True)
        .execute()
    )
    txs = res.data or []
    for t in txs:
        if t.get("buyer_id"):
            u = get_user_by_id(t["buyer_id"])
            t["buyer_name"] = u["full_name"] if u else "—"
    return txs


def list_transactions() -> list[dict[str, Any]]:
    res = (
        get_supabase()
        .table("transactions")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    txs = res.data or []
    if not txs:
        return txs

    # Collect all unique user IDs needed
    user_ids = set()
    for t in txs:
        if t.get("buyer_id"):
            user_ids.add(t["buyer_id"])
        if t.get("farmer_id"):
            user_ids.add(t["farmer_id"])

    # Batch-load all users in ONE query
    users_map: dict[str, Any] = {}
    if user_ids:
        users_res = (
            get_supabase()
            .table("users")
            .select("id, full_name, bank_account, ifsc_code, bank_name, upi_id")
            .in_("id", list(user_ids))
            .execute()
        )
        for u in (users_res.data or []):
            users_map[u["id"]] = u

    for t in txs:
        buyer = users_map.get(t.get("buyer_id") or "")
        t["buyer_name"] = buyer["full_name"] if buyer else "—"
        farmer = users_map.get(t.get("farmer_id") or "")
        if farmer:
            t["farmer_name"] = farmer["full_name"]
            t["farmer_bank_account"] = farmer.get("bank_account")
            t["farmer_ifsc_code"] = farmer.get("ifsc_code")
            t["farmer_bank_name"] = farmer.get("bank_name")
            t["farmer_upi_id"] = farmer.get("upi_id")
        else:
            t["farmer_name"] = "—"
    return txs


def record_transaction(
    auction: dict[str, Any],
    buyer_id: str,
    farmer_id: str,
    amount: float,
    status: str = "held",
) -> dict[str, Any]:
    commission = round(amount * COMMISSION_RATE, 2)
    payout = round(amount - commission, 2)
    crop = auction.get("crops") or {}
    res = get_supabase().table("transactions").insert(
        {
            "auction_id": auction.get("id"),
            "crop_id": crop.get("id"),
            "lot_number": crop.get("lot_number"),
            "crop_name": crop.get("name"),
            "buyer_id": buyer_id,
            "farmer_id": farmer_id,
            "bid_amount": amount,
            "commission": commission,
            "farmer_payout": payout,
            "status": status,
            "txn_ref": f"TXN{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        }
    ).execute()
    return res.data[0] if res.data else {}


def release_payment(transaction_id: str) -> bool:
    sb = get_supabase()
    res = sb.table("transactions").select("*").eq("id", transaction_id).limit(1).execute()
    if not res.data:
        return False
    tx = res.data[0]
    if tx["status"] != "held":
        return False

    # Funds go direct to farmer's external bank account, so we do NOT credit internal wallet.
    # add_wallet(tx["farmer_id"], tx["farmer_payout"])

    # Update status
    sb.table("transactions").update({"status": "released"}).eq("id", transaction_id).execute()

    # Update crop status
    sb.table("crops").update({"status": "Ready for Pickup"}).eq("id", tx["crop_id"]).execute()

    # Notify farmer
    add_notification(
        tx["farmer_id"],
        "Payment Released",
        f"Payment of ₹{tx['farmer_payout']:,.2f} for {tx['crop_name']} ({tx['lot_number']}) has been transferred to your Bank Account."
    )
    return True


def refund_payment(transaction_id: str) -> bool:
    sb = get_supabase()
    res = sb.table("transactions").select("*").eq("id", transaction_id).limit(1).execute()
    if not res.data:
        return False
    tx = res.data[0]
    if tx["status"] != "held":
        return False

    # Refund buyer's trading wallet (where the bid was deducted from)
    add_trading_wallet(tx["buyer_id"], tx["bid_amount"])

    # Update status
    sb.table("transactions").update({"status": "refunded"}).eq("id", transaction_id).execute()

    # Notify buyer
    add_notification(
        tx["buyer_id"],
        "Payment Refunded",
        f"Your payment of ₹{tx['bid_amount']:,.2f} for {tx['crop_name']} has been refunded to your Trading Wallet."
    )
    return True


def list_recent_activity(limit: int = 15) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    closed = list_closed_auctions()[:8]
    for a in closed:
        crop = a.get("crops") or {}
        activities.append(
            {
                "type": "closed",
                "text": f"Auction closed for {crop.get('name', 'crop')} ({crop.get('lot_number', '')}) — winning bid ₹{float(a.get('current_price', 0)):,.0f}",
                "time": a.get("created_at"),
                "ts": a.get("created_at"),
            }
        )
    bids = (
        get_supabase()
        .table("bids")
        .select("*")
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    for b in bids.data or []:
        bidder = get_user_by_id(b["bidder_id"])
        auction = get_auction_detail(b["auction_id"])
        crop_name = (auction.get("crops") or {}).get("name", "crop") if auction else "crop"
        activities.append(
            {
                "type": "bid",
                "text": f"{bidder['full_name'] if bidder else 'User'} bid ₹{float(b['amount']):,.0f} on {crop_name}".replace(",", ","),
                "time": b.get("created_at"),
                "ts": b.get("created_at"),
            }
        )
    activities.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return activities[:limit]


def create_crop_and_auction(
    farmer_id: str,
    crop_data: dict[str, Any],
    auction_data: dict[str, Any],
) -> dict[str, Any]:
    sb = get_supabase()
    crop_data.setdefault("lot_number", next_lot_number())
    if crop_data.get("quality_grade") and not str(crop_data["quality_grade"]).startswith("Grade"):
        crop_data["quality_grade"] = f"Grade {crop_data['quality_grade']}"
    crop_res = sb.table("crops").insert(crop_data).execute()
    crop = crop_res.data[0]
    auction_data["crop_id"] = crop["id"]
    auction_data.setdefault("base_price", auction_data.get("start_price"))
    auction_res = sb.table("auctions").insert(auction_data).execute()
    return {"crop": crop, "auction": auction_res.data[0]}


def list_buyer_bids(buyer_id: str, limit: int = 10) -> list[dict[str, Any]]:
    res = (
        get_supabase()
        .table("bids")
        .select("*, auctions(*, crops(*))")
        .eq("bidder_id", buyer_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def list_buyer_won_auctions(buyer_id: str) -> list[dict[str, Any]]:
    res = (
        get_supabase()
        .table("auctions")
        .select("*, crops(*)")
        .eq("winner_id", buyer_id)
        .order("created_at", desc=True)
        .execute()
    )
    return [enrich_auction(a) for a in (res.data or [])]


def list_buyer_pending_payments(buyer_id: str) -> list[dict[str, Any]]:
    """Return won auctions that do not have a transaction recorded yet."""
    won = list_buyer_won_auctions(buyer_id)
    txs = list_buyer_transactions(buyer_id)
    paid_auction_ids = {t["auction_id"] for t in txs if t.get("auction_id")}
    return [a for a in won if a["id"] not in paid_auction_ids]


def list_buyer_transactions(buyer_id: str) -> list[dict[str, Any]]:
    res = (
        get_supabase()
        .table("transactions")
        .select("*")
        .eq("buyer_id", buyer_id)
        .order("created_at", desc=True)
        .execute()
    )
    txs = res.data or []
    for t in txs:
        if t.get("farmer_id"):
            u = get_user_by_id(t["farmer_id"])
            t["farmer_name"] = u["full_name"] if u else "—"
    return txs



def list_active_auctions() -> list[dict[str, Any]]:
    res = (
        get_supabase()
        .table("auctions")
        .select("*, crops(*)")
        .eq("status", "active")
        .order("ends_at")
        .execute()
    )
    return [enrich_auction(a) for a in (res.data or [])]


def get_auction_detail(auction_id: str) -> dict[str, Any] | None:
    res = (
        get_supabase()
        .table("auctions")
        .select("*, crops(*)")
        .eq("id", auction_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        return None
    auction = rows[0]
    crop = auction.get("crops") or {}
    farmer_id = crop.get("farmer_id")
    if farmer_id:
        farmer = get_user_by_id(farmer_id)
        if farmer:
            crop["users"] = {
                "id": farmer["id"],
                "full_name": farmer["full_name"],
                "email": farmer.get("email"),
                "location": farmer.get("location"),
            }
    bidder_id = auction.get("highest_bidder_id") or auction.get("winner_id")
    if bidder_id:
        bidder = get_user_by_id(bidder_id)
        if bidder:
            auction["users"] = {
                "id": bidder["id"],
                "full_name": bidder["full_name"],
            }
    return enrich_auction(auction)


def list_bids_for_auction(auction_id: str, limit: int = 20) -> list[dict[str, Any]]:
    res = (
        get_supabase()
        .table("bids")
        .select("*")
        .eq("auction_id", auction_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    bids = res.data or []
    for bid in bids:
        bidder = get_user_by_id(bid["bidder_id"])
        if bidder:
            bid["users"] = {"id": bidder["id"], "full_name": bidder["full_name"]}
    return bids


def _normalize_auctions(crop: dict[str, Any]) -> None:
    """Supabase returns 1:1 auctions as dict; templates expect a list."""
    auctions = crop.get("auctions")
    if isinstance(auctions, dict):
        crop["auctions"] = [auctions]
    elif auctions is None:
        crop["auctions"] = []


def list_farmer_crops(farmer_id: str) -> list[dict[str, Any]]:
    res = (
        get_supabase()
        .table("crops")
        .select("*, auctions(*)")
        .eq("farmer_id", farmer_id)
        .order("created_at", desc=True)
        .execute()
    )
    crops = res.data or []
    for crop in crops:
        _normalize_auctions(crop)
    return crops


def list_expired_active_auctions() -> list[dict[str, Any]]:
    """Auctions still marked active but past ends_at."""
    now = utc_now_iso()
    res = (
        get_supabase()
        .table("auctions")
        .select("id, crop_id, highest_bidder_id, current_price, ends_at")
        .eq("status", "active")
        .lt("ends_at", now)
        .execute()
    )
    return res.data or []


def settle_auction_payment(auction: dict[str, Any]) -> None:
    """Send win/end notifications when auction closes, without recording transactions or moving funds yet."""
    winner_id = auction.get("winner_id") or auction.get("highest_bidder_id")
    unit_price = float(auction.get("current_price") or 0)
    crop = auction.get("crops") or {}
    qty = float(crop.get("quantity") or 1)
    total_amount = unit_price * qty

    farmer_id = crop.get("farmer_id")
    if not farmer_id and crop.get("id"):
        c = (
            get_supabase()
            .table("crops")
            .select("farmer_id")
            .eq("id", crop["id"])
            .limit(1)
            .execute()
        )
        if c.data:
            farmer_id = c.data[0]["farmer_id"]

    if winner_id and total_amount > 0 and farmer_id:
        add_notification(
            winner_id,
            "Auction Won!",
            f"You won the auction for {crop.get('name')}! Please go to your dashboard to initiate payment of ₹{total_amount:,.2f}.",
        )
        add_notification(
            farmer_id,
            "Crop Sold!",
            f"Your crop {crop.get('name')} has been sold. Waiting for the buyer to initiate payment.",
        )


def close_auction(
    auction_id: str,
    crop_id: str,
    winner_id: str | None,
    reason: str,
) -> None:
    sb = get_supabase()
    # Fetch full auction with crops join for the settlement notification/transaction
    auction_row = (
        sb.table("auctions")
        .select("*, crops(*)")
        .eq("id", auction_id)
        .limit(1)
        .execute()
    )
    if not auction_row.data:
        return
    
    auction = auction_row.data[0]
    auction["winner_id"] = winner_id

    sb.table("auctions").update(
        {
            "status": "closed",
            "winner_id": winner_id,
            "closed_reason": reason,
        }
    ).eq("id", auction_id).execute()

    crop_status = "sold" if winner_id else "expired"
    sb.table("crops").update(
        {"status": crop_status, "updated_at": utc_now_iso()}
    ).eq("id", crop_id).execute()

    # Use the unified settlement function
    settle_auction_payment(auction)
