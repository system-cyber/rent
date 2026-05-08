from flask import Flask, render_template, request, redirect, url_for, session, flash, g, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db_connection
from datetime import datetime
import os, uuid, base64
import socket, struct, time, threading

ntp_offset = 0.0

def sync_ntp():
    global ntp_offset
    server = 'time.windows.com'
    port = 123
    
    while True:
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client.settimeout(5.0)
            data = b'\x1b' + 47 * b'\0'
            t1 = time.time()
            client.sendto(data, (server, port))
            data, address = client.recvfrom(1024)
            t2 = time.time()
            
            if data:
                s = struct.unpack('!12I', data)
                # NTP timestamp is seconds since 1900-01-01
                # Unix timestamp is seconds since 1970-01-01
                # Difference is 2208988800 seconds
                ntp_time = s[10] + float(s[11]) / 2**32 - 2208988800
                network_delay = (t2 - t1) / 2
                ntp_offset = (ntp_time - network_delay) - time.time()
        except Exception as e:
            print(f"NTP sync failed: {e}")
        
        # Interval: 1 minute
        time.sleep(60)

ntp_thread = threading.Thread(target=sync_ntp, daemon=True)
ntp_thread.start()

app = Flask(__name__)
app.secret_key = 'super_secret_pablo_key'

@app.route('/api/time')
def api_time():
    # Return the current correct time in seconds since epoch
    return jsonify({'correct_time_ms': (time.time() + ntp_offset) * 1000})

@app.before_request
def before_request():
    g.db = get_db_connection()
    g.user = None
    if 'user_id' in session:
        user = g.db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        if user:
            g.user = user

@app.teardown_request
def teardown_request(exception):
    if hasattr(g, 'db'):
        g.db.close()

# --- Customer Routes ---

@app.route('/')
def index():
    cars = g.db.execute('SELECT * FROM cars').fetchall()
    return render_template('index.html', cars=cars)

@app.route('/car/<int:car_id>')
def car_details(car_id):
    car = g.db.execute('SELECT * FROM cars WHERE id = ?', (car_id,)).fetchone()
    if not car:
        return "Car not found", 404
    return render_template('car.html', car=car)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = g.db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            if user['role'] == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('customer_dashboard'))
        else:
            flash('Invalid username or password', 'error')
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        phone = request.form['phone']
        username = request.form['username']
        password = generate_password_hash(request.form['password'])
        
        try:
            g.db.execute(
                'INSERT INTO users (name, email, phone, username, password) VALUES (?, ?, ?, ?, ?)',
                (name, email, phone, username, password)
            )
            g.db.commit()
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            flash('Username or Email already exists.', 'error')
            
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))

@app.route('/dashboard')
def customer_dashboard():
    if not g.user or g.user['role'] != 'customer':
        return redirect(url_for('login'))
        
    bookings = g.db.execute('''
        SELECT b.*, c.name as car_name 
        FROM bookings b 
        JOIN cars c ON b.car_id = c.id 
        WHERE b.user_id = ?
        ORDER BY b.created_at DESC
    ''', (g.user['id'],)).fetchall()
    
    messages = g.db.execute('''
        SELECT * FROM messages WHERE user_id = ? ORDER BY timestamp ASC
    ''', (g.user['id'],)).fetchall()
    
    return render_template('customer_dashboard.html', bookings=bookings, messages=messages)

@app.route('/book/<int:car_id>', methods=['GET', 'POST'])
def book(car_id):
    car = g.db.execute('SELECT * FROM cars WHERE id = ?', (car_id,)).fetchone()
    
    if request.method == 'POST':
        pickup_date = request.form['pickup_date']
        drop_date = request.form['drop_date']
        
        if not g.user:
            cust_name = request.form.get('name', '').strip()
            cust_email = request.form.get('email', '').strip()
            cust_phone = request.form.get('phone', '').strip()
            
            user = g.db.execute('SELECT id FROM users WHERE phone = ? OR email = ?', (cust_phone, cust_email)).fetchone()
            if not user:
                import time
                unique_suffix = str(int(time.time()))
                guest_username = f"guest_{cust_phone}_{unique_suffix}"
                g.db.execute('INSERT INTO users (username, password, name, email, phone, role) VALUES (?, ?, ?, ?, ?, ?)',
                             (guest_username, "offline", cust_name, cust_email, cust_phone, "customer"))
                user_id = g.db.execute('SELECT last_insert_rowid()').fetchone()[0]
            else:
                user_id = user['id']
        else:
            user_id = g.user['id']
        
        # Calculate days
        pickup = datetime.strptime(pickup_date, '%Y-%m-%d')
        drop = datetime.strptime(drop_date, '%Y-%m-%d')
        days = (drop - pickup).days
        if days < 1: days = 1
        
        total_amount = 0
        if days > 15:
            # Using monthly rate logic: monthly_rate is for 30 days. Let's do prorated if > 15.
            total_amount = int((car['monthly_rate'] / 30) * days)
        else:
            total_amount = car['daily_rate'] * days
            
        g.db.execute('''
            INSERT INTO bookings (user_id, car_id, pickup_date, drop_date, total_days, total_amount)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, car_id, pickup_date, drop_date, days, total_amount))
        g.db.commit()
        
        flash('Booking placed successfully! Pay the advance via UPI to confirm.', 'success')
        if not g.user:
            return redirect(url_for('index'))
        return redirect(url_for('customer_dashboard'))
        
    return render_template('book.html', car=car)

from datetime import timedelta

def get_corrected_time_str():
    corrected = datetime.now() + timedelta(seconds=ntp_offset)
    return corrected.strftime('%Y-%m-%d %H:%M:%S')

@app.route('/send_message', methods=['POST'])
def send_message():
    if not g.user:
        return redirect(url_for('login'))
        
    content = request.form['content']
    sender = 'admin' if g.user['role'] == 'admin' else 'customer'
    target_user_id = request.form.get('target_user_id', g.user['id'])
    
    g.db.execute('''
        INSERT INTO messages (user_id, content, sender, timestamp) VALUES (?, ?, ?, ?)
    ''', (target_user_id, content, sender, get_corrected_time_str()))
    g.db.commit()
    
    if sender == 'admin':
        return redirect(url_for('admin_chat', user_id=target_user_id))
    return redirect(url_for('customer_dashboard'))

# --- Global Live Chat API ---
@app.route('/api/chat/init', methods=['POST'])
def api_chat_init():
    if g.user:
        return jsonify({'success': True, 'user_id': g.user['id']})
    
    data = request.json
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    
    if not name or not phone:
        return jsonify({'success': False, 'error': 'Name and phone are required'}), 400
        
    user = g.db.execute('SELECT id FROM users WHERE phone = ?', (phone,)).fetchone()
    if not user:
        import time
        unique_suffix = str(int(time.time()))
        guest_username = f"guest_{phone}_{unique_suffix}"
        dummy_email = f"guest_{phone}@chat.local"
        g.db.execute('INSERT INTO users (username, password, name, email, phone, role) VALUES (?, ?, ?, ?, ?, ?)',
                     (guest_username, "offline", name, dummy_email, phone, "customer"))
        user_id = g.db.execute('SELECT last_insert_rowid()').fetchone()[0]
        g.db.commit()
    else:
        user_id = user['id']
        
    session['guest_id'] = user_id
    return jsonify({'success': True, 'user_id': user_id})

@app.route('/api/chat/messages', methods=['GET'])
def api_chat_messages():
    user_id = None
    if g.user:
        user_id = g.user['id']
    elif 'guest_id' in session:
        user_id = session['guest_id']
        
    if not user_id:
        return jsonify({'messages': []})
        
    messages = g.db.execute('SELECT * FROM messages WHERE user_id = ? ORDER BY timestamp ASC', (user_id,)).fetchall()
    
    msg_list = []
    for msg in messages:
        # Format the timestamp exactly like the template does
        # msg.timestamp is '%Y-%m-%d %H:%M:%S'
        time_part = msg['timestamp'].split(' ')[1][:5] if ' ' in msg['timestamp'] else ''
        msg_list.append({
            'id': msg['id'],
            'content': msg['content'],
            'sender': msg['sender'],
            'time': time_part
        })
        
    return jsonify({'messages': msg_list})

@app.route('/api/chat/send', methods=['POST'])
def api_chat_send():
    user_id = None
    if g.user:
        user_id = g.user['id']
    elif 'guest_id' in session:
        user_id = session['guest_id']
        
    if not user_id:
        return jsonify({'success': False, 'error': 'Not initialized'}), 401
        
    data = request.json
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'success': False, 'error': 'Empty message'}), 400
        
    sender = 'admin' if (g.user and g.user['role'] == 'admin') else 'customer'
    target_user_id = data.get('target_user_id', user_id)
    
    g.db.execute('''
        INSERT INTO messages (user_id, content, sender, timestamp) VALUES (?, ?, ?, ?)
    ''', (target_user_id, content, sender, get_corrected_time_str()))
    g.db.commit()
    
    return jsonify({'success': True})

# --- Admin Routes ---

@app.route('/admin')
def admin_dashboard():
    if not g.user or g.user['role'] != 'admin':
        return redirect(url_for('login'))
        
    cars = g.db.execute('SELECT * FROM cars').fetchall()
    users = g.db.execute('SELECT * FROM users WHERE role = "customer"').fetchall()
    
    bookings = g.db.execute('''
        SELECT b.*, c.name as car_name, u.name as user_name 
        FROM bookings b 
        JOIN cars c ON b.car_id = c.id 
        JOIN users u ON b.user_id = u.id
        WHERE b.status != 'cancelled'
        ORDER BY b.created_at DESC
    ''').fetchall()

    cancelled_bookings = g.db.execute('''
        SELECT b.*, c.name as car_name, u.name as user_name 
        FROM bookings b 
        JOIN cars c ON b.car_id = c.id 
        JOIN users u ON b.user_id = u.id
        WHERE b.status = 'cancelled'
        ORDER BY b.created_at DESC
    ''').fetchall()

    # Bookings that are active/pending and past their drop date — need return verification
    pending_returns = g.db.execute('''
        SELECT b.*, c.name as car_name, u.name as user_name, u.phone as user_phone
        FROM bookings b
        JOIN cars c ON b.car_id = c.id
        JOIN users u ON b.user_id = u.id
        WHERE b.return_verified = 0 AND b.status = 'confirmed'
        ORDER BY b.drop_date ASC
    ''').fetchall()
    
    return render_template('admin_dashboard.html', cars=cars, bookings=bookings,
                           users=users, pending_returns=pending_returns,
                           cancelled_bookings=cancelled_bookings)

@app.route('/admin/car/add', methods=['GET', 'POST'])
def admin_add_car():
    if not g.user or g.user['role'] != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        name         = request.form['name'].strip()
        daily_rate   = int(request.form['daily_rate'])
        monthly_rate = int(request.form['monthly_rate'])
        extra_km     = int(request.form['extra_km_rate'])
        quantity     = int(request.form['quantity'])
        description  = request.form['description'].strip()
        image_front  = request.form.get('image_front', '').strip() or 'default.jpg'
        image_back   = request.form.get('image_back',  '').strip() or 'default.jpg'
        image_right  = request.form.get('image_right', '').strip() or 'default.jpg'
        image_left   = request.form.get('image_left',  '').strip() or 'default.jpg'

        g.db.execute(
            '''INSERT INTO cars (name, daily_rate, monthly_rate, extra_km_rate, quantity,
               description, image1, image2, image_front, image_back, image_right, image_left)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (name, daily_rate, monthly_rate, extra_km, quantity, description,
             image_front, image_back, image_front, image_back, image_right, image_left)
        )
        g.db.commit()
        flash(f'Car "{name}" added successfully.', 'success')
        return redirect(url_for('admin_dashboard'))

    return render_template('admin_car_form.html', car=None, action='Add')


@app.route('/admin/car/edit/<int:car_id>', methods=['GET', 'POST'])
def admin_edit_car(car_id):
    if not g.user or g.user['role'] != 'admin':
        return redirect(url_for('login'))

    car = g.db.execute('SELECT * FROM cars WHERE id = ?', (car_id,)).fetchone()
    if not car:
        flash('Car not found.', 'error')
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        name         = request.form['name'].strip()
        daily_rate   = int(request.form['daily_rate'])
        monthly_rate = int(request.form['monthly_rate'])
        extra_km     = int(request.form['extra_km_rate'])
        quantity     = int(request.form['quantity'])
        description  = request.form['description'].strip()
        image_front  = request.form.get('image_front', '').strip() or (car['image_front'] or car['image1'] or 'default.jpg')
        image_back   = request.form.get('image_back',  '').strip() or (car['image_back']  or car['image2'] or 'default.jpg')
        image_right  = request.form.get('image_right', '').strip() or (car['image_right'] or 'default.jpg')
        image_left   = request.form.get('image_left',  '').strip() or (car['image_left']  or 'default.jpg')

        g.db.execute(
            '''UPDATE cars SET name=?, daily_rate=?, monthly_rate=?, extra_km_rate=?,
               quantity=?, description=?,
               image1=?, image2=?,
               image_front=?, image_back=?, image_right=?, image_left=?
               WHERE id=?''',
            (name, daily_rate, monthly_rate, extra_km, quantity, description,
             image_front, image_back, image_front, image_back, image_right, image_left, car_id)
        )
        g.db.commit()
        flash(f'Car "{name}" updated successfully.', 'success')
        return redirect(url_for('admin_dashboard'))

    return render_template('admin_car_form.html', car=car, action='Edit')


@app.route('/admin/car/delete/<int:car_id>', methods=['POST'])
def admin_delete_car(car_id):
    if not g.user or g.user['role'] != 'admin':
        return redirect(url_for('login'))

    car = g.db.execute('SELECT * FROM cars WHERE id = ?', (car_id,)).fetchone()
    if car:
        g.db.execute('DELETE FROM cars WHERE id = ?', (car_id,))
        g.db.commit()
        flash(f'Car "{car["name"]}" deleted.', 'success')
    else:
        flash('Car not found.', 'error')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/chat/<int:user_id>')
def admin_chat(user_id):
    if not g.user or g.user['role'] != 'admin':
        return redirect(url_for('login'))
        
    target_user = g.db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    messages = g.db.execute('SELECT * FROM messages WHERE user_id = ? ORDER BY timestamp ASC', (user_id,)).fetchall()
    
    return render_template('admin_chat.html', target_user=target_user, messages=messages)

@app.route('/admin/booking/verify-return/<int:booking_id>', methods=['GET', 'POST'])
def admin_verify_return(booking_id):
    if not g.user or g.user['role'] != 'admin':
        return redirect(url_for('login'))

    booking = g.db.execute('''
        SELECT b.*, c.name as car_name, c.daily_rate, c.monthly_rate, c.extra_km_rate, u.name as user_name, u.phone as user_phone
        FROM bookings b
        JOIN cars c ON b.car_id = c.id
        JOIN users u ON b.user_id = u.id
        WHERE b.id = ?
    ''', (booking_id,)).fetchone()

    if not booking:
        flash('Booking not found.', 'error')
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        pickup_km       = int(request.form.get('pickup_km', 0) or 0)
        drop_km         = int(request.form.get('drop_km', 0) or 0)
        return_fuel     = request.form.get('return_fuel', '')
        return_exterior = request.form.get('return_exterior', '')
        return_interior = request.form.get('return_interior', '')
        return_notes    = request.form.get('return_notes', '').strip()
        round_off       = int(request.form.get('round_off', 0) or 0)

        # Read (possibly edited) dates
        pickup_date = request.form.get('pickup_date', booking['pickup_date'])
        drop_date   = request.form.get('drop_date', booking['drop_date'])

        # Read the final amount (bargained/negotiated in the UI)
        total_amount = int(request.form.get('final_total_input', 0) or 0)

        # Recalculate days from the (possibly extended) dates
        d1 = datetime.strptime(pickup_date, '%Y-%m-%d')
        d2 = datetime.strptime(drop_date, '%Y-%m-%d')
        days_used = max((d2 - d1).days, 1)

        # KM run and extra KM val (for records)
        km_run = max(drop_km - pickup_km, 0)
        free_km = days_used * 100
        extra_km_val = max(km_run - free_km, 0)

        # Save camera photos (base64 from JS)
        photo_filenames = {}
        upload_dir = os.path.join(os.path.dirname(__file__), 'static', 'images', 'returns')
        os.makedirs(upload_dir, exist_ok=True)

        for side in ['front', 'back', 'right', 'left']:
            data = request.form.get(f'photo_{side}', '')
            if data and data.startswith('data:image'):
                header, encoded = data.split(',', 1)
                ext = 'png'
                fname = f"return_{booking_id}_{side}_{uuid.uuid4().hex[:8]}.{ext}"
                fpath = os.path.join(upload_dir, fname)
                with open(fpath, 'wb') as f:
                    f.write(base64.b64decode(encoded))
                photo_filenames[side] = f"returns/{fname}"
            else:
                photo_filenames[side] = ''

        g.db.execute('''
            UPDATE bookings SET
                status = 'returned',
                return_verified = 1,
                pickup_date = ?,
                drop_date = ?,
                total_days = ?,
                total_amount = ?,
                pickup_km = ?,
                drop_km = ?,
                km_run = ?,
                extra_km = ?,
                return_fuel = ?,
                return_exterior = ?,
                return_interior = ?,
                return_notes = ?,
                return_photo_front = ?,
                return_photo_back = ?,
                return_photo_right = ?,
                return_photo_left = ?,
                return_verified_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (pickup_date, drop_date, days_used, total_amount,
              pickup_km, drop_km, km_run, extra_km_val,
              return_fuel, return_exterior, return_interior, return_notes,
              photo_filenames.get('front', ''), photo_filenames.get('back', ''),
              photo_filenames.get('right', ''), photo_filenames.get('left', ''),
              booking_id))
        g.db.commit()
        flash(f'Return verified. Final bargained amount: ₹{total_amount}', 'success')
        return redirect(url_for('admin_dashboard'))

    return render_template('admin_verify_return.html', booking=booking)


@app.route('/admin/booking/offline', methods=['GET', 'POST'])
def admin_offline_booking():
    if not g.user or g.user['role'] != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        cust_name   = request.form['customer_name'].strip()
        cust_phone  = request.form['customer_phone'].strip()
        car_id      = request.form['car_id']
        pickup_date = request.form['pickup_date']
        drop_date   = request.form['drop_date']
        pickup_km   = int(request.form.get('pickup_km', 0) or 0)
        agreed_rate = int(request.form.get('total_amount', 0) or 0)

        # Find or create a guest user for this offline booking
        user = g.db.execute('SELECT id FROM users WHERE phone = ?', (cust_phone,)).fetchone()
        if not user:
            # Create a simple placeholder user
            dummy_email = f"guest_{cust_phone}@offline.local"
            g.db.execute('INSERT INTO users (username, password, name, email, phone, role) VALUES (?, ?, ?, ?, ?, ?)',
                         (f"guest_{cust_phone}", "offline", cust_name, dummy_email, cust_phone, "customer"))
            user_id = g.db.execute('SELECT last_insert_rowid()').fetchone()[0]
        else:
            user_id = user['id']

        # Calculate days
        d1 = datetime.strptime(pickup_date, '%Y-%m-%d')
        d2 = datetime.strptime(drop_date, '%Y-%m-%d')
        days = max((d2 - d1).days, 1)

        g.db.execute('''
            INSERT INTO bookings (user_id, car_id, pickup_date, drop_date, total_days, total_amount, status, pickup_km)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, car_id, pickup_date, drop_date, days, agreed_rate, 'confirmed', pickup_km))
        
        g.db.commit()
        flash('Offline booking created successfully.', 'success')
        return redirect(url_for('admin_dashboard'))

    cars = g.db.execute('SELECT * FROM cars').fetchall()
    return render_template('admin_offline_booking.html', cars=cars)


@app.route('/booking/cancel/<int:booking_id>', methods=['GET', 'POST'])
def cancel_booking(booking_id):
    if not g.user:
        return redirect(url_for('login'))
    
    print(f"DEBUG: Customer {g.user['id']} attempting to cancel booking {booking_id}")
    booking = g.db.execute('SELECT * FROM bookings WHERE id = ? AND user_id = ?', 
                           (booking_id, g.user['id'])).fetchone()
    
    if not booking:
        print(f"DEBUG: Booking {booking_id} not found for user {g.user['id']}")
        flash('Booking not found or access denied.', 'error')
    elif booking['status'] in ['returned', 'cancelled']:
        print(f"DEBUG: Booking {booking_id} status is {booking['status']}, cannot cancel")
        flash('This booking cannot be cancelled.', 'error')
    else:
        g.db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
        g.db.commit()
        print(f"DEBUG: Booking {booking_id} cancelled successfully")
        flash('Booking cancelled successfully.', 'success')
    
    return redirect(url_for('customer_dashboard'))


@app.route('/admin/booking/cancel/<int:booking_id>', methods=['GET', 'POST'])
def admin_cancel_booking(booking_id):
    if not g.user or g.user['role'] != 'admin':
        return redirect(url_for('login'))
    
    print(f"DEBUG: Admin attempting to cancel booking {booking_id}")
    booking = g.db.execute('SELECT * FROM bookings WHERE id = ?', (booking_id,)).fetchone()
    if not booking:
        print(f"DEBUG: Booking {booking_id} not found")
        flash('Booking not found.', 'error')
    else:
        g.db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
        g.db.commit()
        print(f"DEBUG: Booking {booking_id} cancelled by admin")
        flash(f'Booking #{booking_id} has been cancelled by Admin.', 'success')
    
    return redirect(url_for('admin_dashboard'))


if __name__ == '__main__':
    from database import init_db
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
