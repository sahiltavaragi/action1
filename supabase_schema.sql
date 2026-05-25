-- Hybrid Agricultural Auction System — Supabase / PostgreSQL schema
-- Run this in the Supabase SQL Editor. RLS disabled for college demo (service role in Flask only).

-- Extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Users (Flask session auth — passwords hashed in app with Werkzeug)
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('farmer', 'buyer', 'admin')),
    phone TEXT,
    location TEXT,
    login_id TEXT UNIQUE,
    wallet_balance NUMERIC(12, 2) NOT NULL DEFAULT 0 CHECK (wallet_balance >= 0),
    trading_wallet_balance NUMERIC(12, 2) NOT NULL DEFAULT 0 CHECK (trading_wallet_balance >= 0),
    bank_account TEXT,
    ifsc_code TEXT,
    bank_name TEXT,
    upi_id TEXT,
    password_changed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_role ON users (role);

-- Crops listed by farmers
CREATE TABLE IF NOT EXISTS crops (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    farmer_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL,
    quantity NUMERIC(12, 2) NOT NULL CHECK (quantity > 0),
    unit TEXT NOT NULL DEFAULT 'kg',
    quality_grade TEXT,
    location TEXT,
    image_url TEXT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'active', 'sold', 'expired', 'cancelled', 'Ready for Pickup')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crops_farmer ON crops (farmer_id);
CREATE INDEX IF NOT EXISTS idx_crops_status ON crops (status);

-- Auctions (hybrid: bidding + optional buy-now)
CREATE TABLE IF NOT EXISTS auctions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    crop_id UUID NOT NULL UNIQUE REFERENCES crops (id) ON DELETE CASCADE,
    start_price NUMERIC(12, 2) NOT NULL CHECK (start_price >= 0),
    current_price NUMERIC(12, 2) NOT NULL CHECK (current_price >= 0),
    buy_now_price NUMERIC(12, 2) CHECK (buy_now_price IS NULL OR buy_now_price > 0),
    min_increment NUMERIC(12, 2) NOT NULL DEFAULT 10 CHECK (min_increment > 0),
    starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ends_at TIMESTAMPTZ NOT NULL,
    highest_bidder_id UUID REFERENCES users (id) ON DELETE SET NULL,
    winner_id UUID REFERENCES users (id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('scheduled', 'active', 'closed', 'cancelled')),
    closed_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auctions_status ON auctions (status);
CREATE INDEX IF NOT EXISTS idx_auctions_ends_at ON auctions (ends_at);
CREATE INDEX IF NOT EXISTS idx_auctions_crop ON auctions (crop_id);

-- Bids
CREATE TABLE IF NOT EXISTS bids (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    auction_id UUID NOT NULL REFERENCES auctions (id) ON DELETE CASCADE,
    bidder_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    amount NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bids_auction ON bids (auction_id);
CREATE INDEX IF NOT EXISTS idx_bids_bidder ON bids (bidder_id);

-- Transactions (Escrow and Payouts)
CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    auction_id UUID REFERENCES auctions (id) ON DELETE SET NULL,
    crop_id UUID REFERENCES crops (id) ON DELETE SET NULL,
    lot_number TEXT,
    crop_name TEXT,
    buyer_id UUID REFERENCES users (id) ON DELETE SET NULL,
    farmer_id UUID REFERENCES users (id) ON DELETE SET NULL,
    bid_amount NUMERIC(12, 2) NOT NULL,
    commission NUMERIC(12, 2) NOT NULL,
    farmer_payout NUMERIC(12, 2) NOT NULL,
    status TEXT NOT NULL DEFAULT 'held' 
        CHECK (status IN ('held', 'released', 'refunded')),
    txn_ref TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_buyer ON transactions (buyer_id);
CREATE INDEX IF NOT EXISTS idx_transactions_farmer ON transactions (farmer_id);
CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions (status);

-- Notifications
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications (user_id);

-- Storage bucket (run in Supabase Dashboard → Storage, or via API):
--   Bucket name: crop-images
--   Public: true (for demo image URLs)

-- Optional seed admin (password: admin123 — change in production)
-- INSERT INTO users (email, password_hash, full_name, role)
-- VALUES ('admin@farmauction.local', '<werkzeug_hash>', 'System Admin', 'admin');
