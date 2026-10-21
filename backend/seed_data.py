"""
DealBridge test-data seeder
===========================

Creates realistic fake users, listings (10+ per category), and supporting
records so every feature can be exercised end to end.

Usage (from the backend/ folder, with your venv active):

    python seed_data.py            # create test data
    python seed_data.py --clear    # remove ALL test data
    python seed_data.py --reset    # clear, then re-create

SAFETY
------
Every account this script creates uses the @dealbridge.test email domain,
and --clear only ever deletes users on that domain (plus the rows that
cascade from them). Your real accounts and listings are never touched.

Because listings/messages/EMI rows hang off those users via ON DELETE
CASCADE (and products.posted_by is SET NULL), --clear deletes the test
users' products explicitly first, so no orphaned listings are left behind.

NOTE ON IMAGES
--------------
Seeded listings have no photos, so they render with the category emoji
placeholder. That's expected — upload images manually on a few listings
if you want to test the gallery.
"""
import argparse
import random
import sys
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import (User, Product, SavedProduct, EmiApplication, Message,
                    Report, Financer, Purchase, PurchaseUser)

TEST_DOMAIN = "@dealbridge.test"
TEST_PASSWORD = "Test@1234"

# Deterministic output so re-running gives a comparable dataset.
random.seed(42)

# ---------------------------------------------------------------- users
# (first, last, phone, pincode, city label)
TEST_USERS = [
    ("Aarav",   "Mehta",     "9812345601", "160055", "Mohali, Punjab"),
    ("Diya",    "Kapoor",    "9812345602", "110024", "New Delhi, Delhi"),
    ("Rohan",   "Iyer",      "9812345603", "560034", "Bengaluru, Karnataka"),
    ("Sneha",   "Nair",      "9812345604", "400058", "Mumbai, Maharashtra"),
    ("Vikram",  "Singh",     "9812345605", "141001", "Ludhiana, Punjab"),
    ("Ananya",  "Reddy",     "9812345606", "500081", "Hyderabad, Telangana"),
    ("Karan",   "Malhotra",  "9812345607", "160017", "Chandigarh, Chandigarh"),
    ("Priya",   "Desai",     "9812345608", "380015", "Ahmedabad, Gujarat"),
    ("Imran",   "Sheikh",    "9812345609", "700019", "Kolkata, West Bengal"),
    ("Meera",   "Joshi",     "9812345610", "411004", "Pune, Maharashtra"),
]

# ------------------------------------------------------------- listings
# category -> list of (title, brand, model, price, original_price)
CATALOG = {
    "cars": [
        ("Maruti Suzuki Swift VXi 2019 - Single Owner", "Maruti Suzuki", "Swift VXi", 545000, 720000),
        ("Hyundai i20 Sportz 2021, 28k km", "Hyundai", "i20 Sportz", 785000, 920000),
        ("Honda City ZX CVT 2020 Petrol", "Honda", "City ZX", 1050000, 1350000),
        ("Tata Nexon XZ+ 2022 Diesel", "Tata", "Nexon XZ+", 1120000, 1340000),
        ("Mahindra Scorpio S11 2018 4x4", "Mahindra", "Scorpio S11", 1180000, 1650000),
        ("Toyota Innova Crysta 2.4 GX 2019", "Toyota", "Innova Crysta GX", 1690000, 2100000),
        ("Renault Kwid RXT 2020, low usage", "Renault", "Kwid RXT", 335000, 450000),
        ("Kia Seltos HTK+ 2021 Petrol", "Kia", "Seltos HTK+", 1240000, 1480000),
        ("Volkswagen Polo Highline 2017", "Volkswagen", "Polo Highline", 465000, 720000),
        ("Ford EcoSport Titanium 2018 Diesel", "Ford", "EcoSport Titanium", 625000, 950000),
        ("Skoda Rapid Rider Plus 2019", "Skoda", "Rapid Rider Plus", 690000, 980000),
    ],
    "phones": [
        ("iPhone 13 128GB Midnight, box included", "Apple", "iPhone 13", 42000, 69900),
        ("Samsung Galaxy S22 Ultra 256GB", "Samsung", "Galaxy S22 Ultra", 54000, 109999),
        ("OnePlus 11R 5G 128GB Sonic Black", "OnePlus", "11R 5G", 27500, 39999),
        ("Google Pixel 7a, 8/128, warranty left", "Google", "Pixel 7a", 24000, 43999),
        ("Redmi Note 12 Pro 5G 128GB", "Xiaomi", "Redmi Note 12 Pro", 13500, 24999),
        ("iPhone 12 64GB Blue - good condition", "Apple", "iPhone 12", 29000, 65900),
        ("Realme GT Neo 3 150W 256GB", "Realme", "GT Neo 3", 19000, 36999),
        ("Vivo V27 Pro 5G 256GB", "Vivo", "V27 Pro", 21000, 37999),
        ("Motorola Edge 40 Neo 5G", "Motorola", "Edge 40 Neo", 17500, 24999),
        ("Nothing Phone (2) 256GB White", "Nothing", "Phone (2)", 31000, 44999),
        ("iQOO Neo 7 Pro 5G 128GB", "iQOO", "Neo 7 Pro", 23000, 34999),
    ],
    "bikes": [
        ("Royal Enfield Classic 350 2021, 12k km", "Royal Enfield", "Classic 350", 138000, 193000),
        ("Bajaj Pulsar NS200 2020 ABS", "Bajaj", "Pulsar NS200", 82000, 128000),
        ("Honda Activa 6G 2022, single owner", "Honda", "Activa 6G", 58000, 76000),
        ("TVS Apache RTR 160 4V 2021", "TVS", "Apache RTR 160 4V", 79000, 118000),
        ("Yamaha MT-15 V2 2022 Cyan", "Yamaha", "MT-15 V2", 118000, 168000),
        ("KTM Duke 250 2019 - well maintained", "KTM", "Duke 250", 132000, 215000),
        ("Hero Splendor Plus 2020", "Hero", "Splendor Plus", 42000, 62000),
        ("Suzuki Access 125 2021 Disc", "Suzuki", "Access 125", 61000, 82000),
        ("Jawa 42 2020 Halley's Teal", "Jawa", "42", 108000, 168000),
        ("Ola S1 Pro Gen 2 2023 electric", "Ola", "S1 Pro Gen 2", 92000, 147000),
        ("Bajaj Chetak Premium 2022 electric", "Bajaj", "Chetak Premium", 88000, 144000),
    ],
    "electronics": [
        ("MacBook Air M2 8/256 - warranty till 2026", "Apple", "MacBook Air M2", 72000, 114900),
        ("Dell Inspiron 15 i5 12th Gen 16GB", "Dell", "Inspiron 15", 38000, 62000),
        ("Sony WH-1000XM5 headphones", "Sony", "WH-1000XM5", 18500, 29990),
        ("iPad 9th Gen 64GB WiFi", "Apple", "iPad 9th Gen", 19000, 30900),
        ("HP Pavilion Gaming Ryzen 5 GTX1650", "HP", "Pavilion Gaming", 41000, 71000),
        ("Canon EOS 200D II DSLR with 18-55mm", "Canon", "EOS 200D II", 34000, 58990),
        ("LG 55 inch 4K Smart TV 2021", "LG", "55UP7500", 33000, 62990),
        ("PlayStation 5 Disc Edition + 2 games", "Sony", "PlayStation 5", 39000, 54990),
        ("Asus TUF A15 Ryzen 7 RTX 3050", "Asus", "TUF A15", 62000, 98000),
        ("Samsung Galaxy Tab S8 128GB", "Samsung", "Galaxy Tab S8", 36000, 58999),
        ("Boat Stone 1200 Bluetooth speaker", "boAt", "Stone 1200", 3200, 6990),
    ],
    "furniture": [
        ("6-seater teak dining table with chairs", "Custom", "Teak 6-seater", 28000, 52000),
        ("L-shaped fabric sofa, grey", "Urban Ladder", "L-Sofa Grey", 24000, 48000),
        ("Queen size bed with hydraulic storage", "Wakefit", "Hydraulic Queen", 18500, 32000),
        ("Study table with bookshelf, sheesham", "Nilkamal", "Study Combo", 7500, 14000),
        ("Recliner single-seater, brown leatherette", "Godrej", "Recliner Single", 14000, 27000),
        ("4-door steel almirah with locker", "Godrej", "Storwel 4D", 12500, 24000),
        ("Ergonomic office chair, mesh back", "Green Soul", "Beast Mesh", 6800, 13500),
        ("Wooden coffee table, walnut finish", "Pepperfry", "Walnut CT", 4200, 8900),
        ("Kids bunk bed with ladder", "Custom", "Bunk Bed", 15000, 26000),
        ("Shoe rack 3-tier with seating", "Nilkamal", "Shoe Bench", 3100, 6200),
        ("TV unit with drawers, 5 ft", "Urban Ladder", "TV Unit 5ft", 8900, 17500),
    ],
    "appliances": [
        ("LG 7kg front load washing machine", "LG", "FHM1207ZDL", 19500, 34990),
        ("Samsung 253L double door fridge", "Samsung", "RT28", 17000, 31990),
        ("Voltas 1.5 ton 3-star split AC", "Voltas", "183V Vectra", 21000, 38990),
        ("IFB 30L convection microwave", "IFB", "30SC4", 8500, 17990),
        ("Bosch 12-place dishwasher", "Bosch", "SMS66GI01I", 27000, 47990),
        ("Prestige 3-burner gas stove", "Prestige", "Royale 3B", 2400, 4800),
        ("Kent RO+UV water purifier", "Kent", "Grand Plus", 7200, 17500),
        ("Havells 1200mm ceiling fan (pair)", "Havells", "Stealth Air", 3600, 7200),
        ("Philips air fryer 4.1L", "Philips", "HD9252", 6400, 12995),
        ("Crompton 25L storage geyser", "Crompton", "Solarium Neo", 4900, 9600),
        ("Eureka Forbes vacuum cleaner 1400W", "Eureka Forbes", "Quick Clean", 3800, 8500),
    ],
    "fashion": [
        ("Levi's 511 slim jeans, 32W (new)", "Levi's", "511 Slim", 1400, 3499),
        ("Nike Air Force 1 White UK9", "Nike", "Air Force 1", 5200, 8695),
        ("Titan Neo analog watch, steel strap", "Titan", "Neo Steel", 2100, 4295),
        ("Adidas Ultraboost 22 UK8", "Adidas", "Ultraboost 22", 6800, 16999),
        ("Fossil leather wallet, brown (unused)", "Fossil", "Bifold Leather", 1600, 3495),
        ("Raymond wool blazer, 40R", "Raymond", "Wool Blazer", 3400, 8999),
        ("Banarasi silk saree, red & gold", "Handloom", "Banarasi Silk", 4500, 11000),
        ("Ray-Ban Aviator sunglasses (original)", "Ray-Ban", "Aviator 3025", 4200, 7990),
        ("Puma running shoes UK10", "Puma", "Softride", 1900, 4499),
        ("Wildcraft 45L trekking rucksack", "Wildcraft", "Rucksack 45L", 1800, 3799),
        ("Zara wool overcoat, size M", "Zara", "Wool Overcoat", 3200, 8990),
    ],
    "property": [
        ("2 BHK flat for sale, 1050 sqft", "—", "2 BHK", 4800000, 5400000),
        ("3 BHK builder floor, semi-furnished", "—", "3 BHK", 7200000, 8100000),
        ("1 BHK studio apartment, ready to move", "—", "1 BHK", 2650000, 2950000),
        ("Commercial shop 400 sqft, main road", "—", "Shop 400sqft", 5500000, 6200000),
        ("Residential plot 200 sq yards", "—", "Plot 200sqyd", 3900000, 4400000),
        ("4 BHK independent house with lawn", "—", "4 BHK House", 12500000, 14000000),
        ("2 BHK flat, corner unit, 3rd floor", "—", "2 BHK Corner", 4350000, 4900000),
        ("Office space 900 sqft, IT park", "—", "Office 900sqft", 8900000, 9800000),
        ("Farmhouse plot 1 acre, highway facing", "—", "Farm 1 acre", 15000000, 17500000),
        ("3 BHK flat with covered parking", "—", "3 BHK Parking", 6800000, 7500000),
        ("Warehouse 2500 sqft, industrial area", "—", "Warehouse", 9500000, 11000000),
    ],
}

CONDITIONS = ["like_new", "good", "fair", "for_parts"]
# for_parts is rare and never used for property
CONDITION_WEIGHTS = [30, 45, 20, 5]

DESCRIPTIONS = [
    "Well maintained and used with care. Everything works as expected. "
    "Serious buyers only, price slightly negotiable.",
    "Selling because I've upgraded. No major scratches or issues. "
    "Happy to demonstrate before purchase.",
    "Bought new and barely used. Original bill available. "
    "Pickup preferred, can help with local delivery.",
    "Good working condition with normal signs of use. "
    "Inspection welcome — meet at a public place.",
    "Genuine sale, no time wasters please. Item is exactly as described "
    "in the photos and title.",
]


def emi_for(principal, annual_rate, months):
    """Standard reducing-balance EMI. Returns (monthly, total)."""
    r = annual_rate / 12 / 100
    if r == 0:
        monthly = principal / months
    else:
        f = (1 + r) ** months
        monthly = principal * r * f / (f - 1)
    return round(monthly, 2), round(monthly * months, 2)


def clear_test_data():
    """Delete every @dealbridge.test account and everything hanging off it."""
    users = User.query.filter(User.email.like(f"%{TEST_DOMAIN}")).all()
    if not users:
        print("No test data found — nothing to clear.")
        return 0

    ids = [u.id for u in users]

    # products.posted_by is ON DELETE SET NULL, so deleting the users alone
    # would leave their listings behind as orphans. Remove them explicitly,
    # along with the rows that reference those products.
    product_ids = [p.id for p in Product.query.filter(Product.posted_by.in_(ids)).all()]
    if product_ids:
        purchase_ids = [p.purchase_id for p in Purchase.query.filter(Purchase.product_id.in_(product_ids)).all()]
        if purchase_ids:
            PurchaseUser.query.filter(PurchaseUser.purchase_id.in_(purchase_ids)).delete(synchronize_session=False)
            Purchase.query.filter(Purchase.purchase_id.in_(purchase_ids)).delete(synchronize_session=False)
        Report.query.filter(Report.product_id.in_(product_ids)).delete(synchronize_session=False)
        EmiApplication.query.filter(EmiApplication.product_id.in_(product_ids)).delete(synchronize_session=False)
        SavedProduct.query.filter(SavedProduct.product_id.in_(product_ids)).delete(synchronize_session=False)
        Message.query.filter(Message.product_id.in_(product_ids)).delete(synchronize_session=False)
        Product.query.filter(Product.id.in_(product_ids)).delete(synchronize_session=False)

    Report.query.filter(Report.reported_user_id.in_(ids)).delete(synchronize_session=False)
    Report.query.filter(Report.reporter_id.in_(ids)).delete(synchronize_session=False)
    EmiApplication.query.filter(EmiApplication.user_id.in_(ids)).delete(synchronize_session=False)
    SavedProduct.query.filter(SavedProduct.user_id.in_(ids)).delete(synchronize_session=False)
    Message.query.filter(Message.sender_id.in_(ids)).delete(synchronize_session=False)
    Message.query.filter(Message.receiver_id.in_(ids)).delete(synchronize_session=False)
    User.query.filter(User.id.in_(ids)).delete(synchronize_session=False)

    db.session.commit()
    print(f"Cleared {len(users)} test users, {len(product_ids)} listings, and related records.")
    return len(users)


def seed():
    if User.query.filter(User.email.like(f"%{TEST_DOMAIN}")).first():
        print("Test data already exists. Use --reset to rebuild it, or --clear to remove it.")
        return

    now = datetime.utcnow()
    pw = generate_password_hash(TEST_PASSWORD)

    # ---- users ----
    users = []
    for i, (first, last, phone, pincode, _city) in enumerate(TEST_USERS):
        u = User(
            email=f"{first.lower()}.{last.lower()}{TEST_DOMAIN}",
            password_hash=pw,
            first_name=first, last_name=last,
            phone=phone, city=pincode,
            is_admin=False,
            created_at=now - timedelta(days=random.randint(30, 400)),
        )
        db.session.add(u)
        users.append(u)
    db.session.flush()
    print(f"Created {len(users)} test users.")

    # ---- listings ----
    products = []
    for category, items in CATALOG.items():
        for idx, (title, brand, model, price, original) in enumerate(items):
            seller = users[(idx + hash(category)) % len(users)]
            _f, _l, phone, pincode, city_label = TEST_USERS[users.index(seller)]

            condition = "good" if category == "property" else random.choices(CONDITIONS, CONDITION_WEIGHTS)[0]

            # Give the data some realistic spread: most listings active, a few
            # sold or removed, so status filters and My Listings can be tested.
            roll = random.random()
            status = "active" if roll < 0.82 else ("sold" if roll < 0.93 else "removed")

            p = Product(
                title=title, category=category, condition=condition,
                price=price, original_price=original,
                negotiable=random.random() < 0.6,
                # High-value categories are the realistic EMI candidates.
                emi_eligible=(category in ("cars", "bikes", "phones", "electronics", "appliances", "furniture")
                              and random.random() < 0.85),
                description=random.choice(DESCRIPTIONS),
                image_url=None, extra_images=None,
                posted_by=seller.id,
                seller_name=f"{seller.first_name} {seller.last_name}",
                seller_avatar=None, seller_phone=phone,
                show_phone=random.choice(["yes", "chat_only"]),
                location=city_label, city=pincode,
                landmark=f"Near {city_label.split(',')[0]} main market",
                brand=brand, model=model,
                views=random.randint(0, 480),
                status=status,
                created_at=now - timedelta(days=random.randint(0, 90), hours=random.randint(0, 23)),
            )
            db.session.add(p)
            products.append(p)
    db.session.flush()

    per_cat = {}
    for p in products:
        per_cat[p.category] = per_cat.get(p.category, 0) + 1
    print(f"Created {len(products)} listings: " + ", ".join(f"{k} {v}" for k, v in sorted(per_cat.items())))

    active = [p for p in products if p.status == "active"]

    # ---- saved items ----
    saved_count = 0
    for u in users:
        for p in random.sample(active, k=min(5, len(active))):
            if p.posted_by == u.id:
                continue
            db.session.add(SavedProduct(user_id=u.id, product_id=p.id))
            saved_count += 1
    print(f"Created {saved_count} saved items.")

    # ---- chat threads ----
    msg_count = 0
    for p in random.sample(active, k=min(12, len(active))):
        buyer = random.choice([u for u in users if u.id != p.posted_by])
        base = now - timedelta(days=random.randint(1, 20))
        thread = [
            (buyer.id, p.posted_by, "Hi, is this still available?"),
            (p.posted_by, buyer.id, "Yes, it is. Let me know if you'd like to see it."),
            (buyer.id, p.posted_by, "Would you take a slightly lower price?"),
            (p.posted_by, buyer.id, "Price is a bit negotiable for a serious buyer."),
        ]
        for n, (sender, receiver, body) in enumerate(thread):
            db.session.add(Message(
                product_id=p.id, sender_id=sender, receiver_id=receiver, body=body,
                is_read=(n < 2), created_at=base + timedelta(minutes=n * 7),
            ))
            msg_count += 1
    print(f"Created {msg_count} chat messages.")

    # ---- EMI applications across the whole lifecycle ----
    emi_pool = [p for p in active if p.emi_eligible and p.price >= 20000]
    # (status, was_disbursed, is_archived)
    lifecycle = [
        ("pending", False, False), ("pending", False, False),
        ("review", False, False),
        ("approved", False, False), ("approved", False, False),
        ("disbursed", True, False), ("disbursed", True, False),
        ("completed", True, True),
        ("rejected", False, False),
        ("cancelled", False, True),
    ]
    emi_count = 0
    for i, (status, disbursed, archived) in enumerate(lifecycle):
        if i >= len(emi_pool):
            break
        p = emi_pool[i]
        applicant = random.choice([u for u in users if u.id != p.posted_by])
        down = round(float(p.price) * random.choice([0.1, 0.15, 0.2]), 2)
        principal = round(float(p.price) - down, 2)
        tenure = random.choice([6, 12, 18, 24, 36])
        rate = random.choice([11.0, 12.5, 14.0, 16.0])
        monthly, total = emi_for(principal, rate, tenure)

        applied = now - timedelta(days=random.randint(20, 120))
        reviewed = applied + timedelta(days=random.randint(1, 6)) if status != "pending" else None
        disbursed_at = reviewed + timedelta(days=random.randint(1, 5)) if disbursed and reviewed else None
        closed = (disbursed_at or reviewed) + timedelta(days=random.randint(20, 90)) if status in ("completed", "cancelled") else None

        db.session.add(EmiApplication(
            user_id=applicant.id, product_id=p.id,
            product_title_snapshot=p.title,
            product_image_snapshot=None,
            product_price_snapshot=p.price,
            down_payment=down, tenure_months=tenure, interest_rate=rate,
            loan_amount=principal, monthly_emi=monthly, total_payable=total,
            applicant_name=f"{applicant.first_name} {applicant.last_name}",
            applicant_phone=applicant.phone,
            applicant_income=random.choice([25000, 40000, 55000, 80000, 120000]),
            status=status, is_archived=archived,
            applied_at=applied, reviewed_at=reviewed,
            disbursed_at=disbursed_at, closed_at=closed,
            decision_notes=("Documents verified, income adequate." if status in ("approved", "disbursed", "completed")
                            else "Income proof insufficient for requested tenure." if status == "rejected"
                            else None),
        ))
        emi_count += 1
    print(f"Created {emi_count} EMI applications across the lifecycle.")

    # ---- seller reports ----
    report_reasons = ["fraud_scam", "fake_listing", "counterfeit", "harassment", "prohibited_item", "payment_issue"]
    report_count = 0
    # One seller gets several reports so the "repeat offender" flag is visible.
    flagged = users[8]
    for i, reason in enumerate(report_reasons[:3]):
        reporter = users[i]
        db.session.add(Report(
            reported_user_id=flagged.id, reporter_id=reporter.id,
            product_id=next((p.id for p in products if p.posted_by == flagged.id), None),
            reason=reason,
            details="Seller asked for advance payment over UPI and then stopped replying.",
            status="open", created_at=now - timedelta(days=random.randint(1, 15)),
        ))
        report_count += 1

    for i, reason in enumerate(report_reasons[3:]):
        reporter = users[i + 3]
        target = users[(i + 5) % len(users)]
        if target.id == reporter.id:
            continue
        db.session.add(Report(
            reported_user_id=target.id, reporter_id=reporter.id,
            product_id=next((p.id for p in products if p.posted_by == target.id), None),
            reason=reason, details="Listing photos don't match the actual item.",
            status=random.choice(["investigating", "dismissed", "action_taken"]),
            reviewed_at=now - timedelta(days=random.randint(1, 5)),
            resolution_notes="Reviewed by moderation team.",
            created_at=now - timedelta(days=random.randint(5, 30)),
        ))
        report_count += 1
    print(f"Created {report_count} seller reports.")

    # ---- financers (only if none exist) ----
    if not Financer.query.first():
        for name, lo, hi, maxamt in [
            ("Bajaj Finserv", 11.0, 18.0, 500000),
            ("HDFC Bank Consumer Finance", 10.5, 16.0, 1000000),
            ("IDFC First Bank", 12.0, 20.0, 750000),
        ]:
            db.session.add(Financer(
                name=name, contact_email=f"partners@{name.split()[0].lower()}.example",
                contact_phone="1800-000-000",
                min_interest_rate=lo, max_interest_rate=hi,
                min_loan_amount=5000, max_loan_amount=maxamt, status="active",
            ))
        print("Created 3 financers.")

    db.session.commit()

    print("\n" + "=" * 60)
    print("SEED COMPLETE")
    print("=" * 60)
    print(f"Login with any of these accounts (password: {TEST_PASSWORD})")
    for first, last, *_ in TEST_USERS:
        print(f"   {first.lower()}.{last.lower()}{TEST_DOMAIN}")
    print("\nListings have no photos — they show the category placeholder.")
    print("Run 'python seed_data.py --clear' to remove all of this.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed or clear DealBridge test data.")
    parser.add_argument("--clear", action="store_true", help="Remove all test data")
    parser.add_argument("--reset", action="store_true", help="Clear then re-create")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        if args.clear:
            clear_test_data()
        elif args.reset:
            clear_test_data()
            seed()
        else:
            seed()
