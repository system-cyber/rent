import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'pablo.db')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()

    # Users Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL DEFAULT 'customer',
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')

    # Cars Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS cars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            daily_rate INTEGER NOT NULL,
            monthly_rate INTEGER NOT NULL,
            extra_km_rate INTEGER NOT NULL,
            image1 TEXT,
            image2 TEXT,
            image_front TEXT,
            image_back TEXT,
            image_right TEXT,
            image_left TEXT,
            quantity INTEGER NOT NULL DEFAULT 1,
            description TEXT
        )
    ''')

    # Bookings Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            car_id INTEGER NOT NULL,
            pickup_date TEXT NOT NULL,
            drop_date TEXT NOT NULL,
            total_days INTEGER NOT NULL,
            total_amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            pickup_km INTEGER DEFAULT 0,
            drop_km INTEGER DEFAULT 0,
            km_run INTEGER DEFAULT 0,
            extra_km INTEGER DEFAULT 0,
            return_verified INTEGER NOT NULL DEFAULT 0,
            return_notes TEXT,
            return_km TEXT,
            return_fuel TEXT,
            return_exterior TEXT,
            return_interior TEXT,
            return_photo_front TEXT,
            return_photo_back TEXT,
            return_photo_right TEXT,
            return_photo_left TEXT,
            return_verified_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(car_id) REFERENCES cars(id)
        )
    ''')

    # Messages Table (for live chat)
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            sender TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')

    # ─── Migrations: add new columns if they don't already exist ────────────
    existing_car_cols = {row[1] for row in c.execute("PRAGMA table_info(cars)")}
    for col in ['image_front', 'image_back', 'image_right', 'image_left']:
        if col not in existing_car_cols:
            c.execute(f"ALTER TABLE cars ADD COLUMN {col} TEXT")

    existing_booking_cols = {row[1] for row in c.execute("PRAGMA table_info(bookings)")}
    booking_new_cols = {
        'pickup_km':          'INTEGER DEFAULT 0',
        'drop_km':            'INTEGER DEFAULT 0',
        'km_run':             'INTEGER DEFAULT 0',
        'extra_km':           'INTEGER DEFAULT 0',
        'return_verified':    'INTEGER NOT NULL DEFAULT 0',
        'return_notes':       'TEXT',
        'return_km':          'TEXT',
        'return_fuel':        'TEXT',
        'return_exterior':    'TEXT',
        'return_interior':    'TEXT',
        'return_photo_front': 'TEXT',
        'return_photo_back':  'TEXT',
        'return_photo_right': 'TEXT',
        'return_photo_left':  'TEXT',
        'return_verified_at': 'TIMESTAMP',
    }
    for col, definition in booking_new_cols.items():
        if col not in existing_booking_cols:
            c.execute(f"ALTER TABLE bookings ADD COLUMN {col} {definition}")
    # ─────────────────────────────────────────────────────────────────────────

    # Insert default admin if not exists
    c.execute("SELECT * FROM users WHERE username = 'admin'")
    if not c.fetchone():
        from werkzeug.security import generate_password_hash
        hashed_pw = generate_password_hash('admin123')
        c.execute("INSERT INTO users (role, name, email, phone, username, password) VALUES (?, ?, ?, ?, ?, ?)",
                  ('admin', 'Admin', 'admin@pablosrentals.com', '7025328233', 'admin', hashed_pw))

    # Insert initial cars if none exist
    c.execute("SELECT count(*) FROM cars")
    if c.fetchone()[0] == 0:
        initial_cars = [
            ('Innova', 2500, 60000, 15, 'innova1.jpg', 'innova2.jpg', 2, 'Spacious and comfortable MUV.'),
            ('Baleno', 1500, 35000, 10, 'baleno1.jpg', 'baleno2.jpg', 2, 'Premium hatchback for smooth rides.'),
            ('XL6', 2200, 55000, 14, 'xl61.jpg', 'xl62.jpg', 1, 'Luxury 6-seater MUV.'),
            ('Fronx', 1800, 45000, 12, 'fronx1.jpg', 'fronx2.jpg', 1, 'Stylish and sporty crossover.')
        ]
        c.executemany(
            "INSERT INTO cars (name, daily_rate, monthly_rate, extra_km_rate, image1, image2, quantity, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            initial_cars
        )

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Database initialized successfully.")
