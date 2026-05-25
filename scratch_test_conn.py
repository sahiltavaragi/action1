import database as db
try:
    print("Fetching active auctions...")
    res = db.list_active_auctions()
    print("Success! Active auctions count:", len(res))
except Exception as e:
    import traceback
    traceback.print_exc()
