import sqlite3
import os
import re
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get('DATABASE_URL')

class PostgresCursorWrapper:
    def __init__(self, pg_cursor, connection_wrapper):
        self.cursor = pg_cursor
        self.connection = connection_wrapper

    def execute(self, query, params=None):
        # 1. Translate SQLite '?' parameter placeholders to PostgreSQL '%s'
        query_pg = query.replace('?', '%s')

        # 2. Translate SQLite 'INTEGER PRIMARY KEY AUTOINCREMENT' to PostgreSQL 'SERIAL PRIMARY KEY'
        query_pg = re.sub(r'(?i)\binteger\s+primary\s+key\s+autoincrement\b', 'SERIAL PRIMARY KEY', query_pg)

        # 3. Translate SQLite 'PRAGMA table_info(table)' to PostgreSQL information_schema query
        pragma_match = re.match(r'(?i)^\s*PRAGMA\s+table_info\((.*?)\)', query_pg)
        if pragma_match:
            table_name = pragma_match.group(1).strip('"`\'')
            query_pg = """
                SELECT 0 as cid, column_name as name, data_type as type, 0 as notnull, NULL as dflt_value, 0 as pk 
                FROM information_schema.columns 
                WHERE table_name = %s
            """
            params = (table_name.lower(),)

        # 4. Handle SELECT last_insert_rowid() or lastval()
        if 'last_insert_rowid()' in query_pg or 'lastval()' in query_pg:
            val = self.connection._lastrowid or 0
            query_pg = f"SELECT {val}"
            params = None

        # 5. Append RETURNING id to INSERT queries to capture lastrowid automatically
        is_insert = query_pg.strip().upper().startswith('INSERT')
        has_returning = 'RETURNING' in query_pg.upper()

        if is_insert and not has_returning:
            # Safely append RETURNING id
            query_pg = query_pg.rstrip(';') + " RETURNING id"
            if params is not None:
                self.cursor.execute(query_pg, params)
            else:
                self.cursor.execute(query_pg)
            # Store the generated ID
            try:
                row = self.cursor.fetchone()
                if row:
                    self.connection._lastrowid = row[0]
            except Exception:
                pass
        else:
            if params is not None:
                self.cursor.execute(query_pg, params)
            else:
                self.cursor.execute(query_pg)

        return self

    def executemany(self, query, seq_of_params):
        query_pg = query.replace('?', '%s')
        self.cursor.executemany(query_pg, seq_of_params)
        return self

    @property
    def lastrowid(self):
        return self.connection._lastrowid

    def fetchone(self):
        try:
            return self.cursor.fetchone()
        except Exception:
            return None

    def fetchall(self):
        try:
            return self.cursor.fetchall()
        except Exception:
            return []

    def close(self):
        self.cursor.close()

    def __iter__(self):
        return iter(self.cursor)

class PostgresConnectionWrapper:
    def __init__(self, pg_conn):
        self.conn = pg_conn
        self._lastrowid = None

    def cursor(self):
        return PostgresCursorWrapper(
            self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor),
            self
        )

    def execute(self, query, params=None):
        cursor = self.cursor()
        cursor.execute(query, params)
        return cursor

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

def get_db_connection():
    if DATABASE_URL:
        # Connect to PostgreSQL
        conn = psycopg2.connect(DATABASE_URL)
        return PostgresConnectionWrapper(conn)
    else:
        # Fall back to SQLite locally
        DB_PATH = os.path.join(os.path.dirname(__file__), 'pablo.db')
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

    # Reviews Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            content TEXT NOT NULL,
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

    # Insert initial reviews if none exist
    c.execute("SELECT count(*) FROM reviews")
    if c.fetchone()[0] == 0:
        import random
        from werkzeug.security import generate_password_hash
        
        # Create 6 dummy reviewers
        reviewers = [
            ("Rajesh Kumar", "rajesh@example.com", "9876543211"),
            ("Priya Sharma", "priya@example.com", "9876543212"),
            ("Arun Prakash", "arun@example.com", "9876543213"),
            ("Meera Nair", "meera@example.com", "9876543214"),
            ("Vikram Singh", "vikram@example.com", "9876543215"),
            ("Neha Gupta", "neha@example.com", "9876543216")
        ]
        
        user_ids = []
        for r_name, r_email, r_phone in reviewers:
            c.execute("SELECT id FROM users WHERE email = ?", (r_email,))
            u = c.fetchone()
            if not u:
                c.execute("INSERT INTO users (role, name, email, phone, username, password) VALUES (?, ?, ?, ?, ?, ?)",
                          ('customer', r_name, r_email, r_phone, r_email, generate_password_hash('pass')))
                user_ids.append(c.lastrowid)
            else:
                user_ids.append(u['id'])
                
        reviews_data = [
            (user_ids[0], 5, "Excellent service! The car was perfectly clean and the drop-off was right on time. Highly recommended for airport transfers."),
            (user_ids[1], 5, "I rented the Innova for a family trip. The rate was very reasonable and the car condition was superb. Will book again!"),
            (user_ids[2], 4, "Smooth booking process and great customer support. The live chat feature helped me clear my doubts instantly."),
            (user_ids[3], 5, "Very professional behavior. They delivered the car to my site without any hassle. The vehicle was well sanitized."),
            (user_ids[4], 5, "Hands down the best car rental experience I've had. No hidden charges, transparent pricing, and wonderful cars."),
            (user_ids[5], 4, "Good experience overall. The car drove perfectly. The only thing was a slight delay in pickup, but the admin compensated for it.")
        ]
        
        c.executemany("INSERT INTO reviews (user_id, rating, content) VALUES (?, ?, ?)", reviews_data)

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Database initialized successfully.")
