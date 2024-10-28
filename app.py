from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import matplotlib.pyplot as plt
import io
import base64
import logging
import smtplib
from email.mime.text import MIMEText
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime
from sqlalchemy.exc import IntegrityError
import pytz
from werkzeug.utils import secure_filename
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///spare_parts.db'
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Configure logging
logging.basicConfig(level=logging.INFO)

# Set timezone to local timezone
local_tz = pytz.timezone('Asia/Jakarta')

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), nullable=False, default='user')

class SparePart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    part_number = db.Column(db.String(100), nullable=True)
    name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    min_stock = db.Column(db.Integer, nullable=False, default=0)
    location = db.Column(db.String(100), nullable=True)
    uom = db.Column(db.String(50), nullable=True)
    photo = db.Column(db.String(100), nullable=True)

class AuditTrail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(100), nullable=False)
    part_number = db.Column(db.String(100), nullable=True)
    part_name = db.Column(db.String(100), nullable=True)
    quantity = db.Column(db.Integer, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()
    inspector = db.inspect(db.engine)
    columns = [column['name'] for column in inspector.get_columns('spare_part')]
    if 'part_number' not in columns:
        with db.engine.connect() as connection:
            connection.execute(text('ALTER TABLE spare_part ADD COLUMN part_number VARCHAR(100)'))
    if 'location' not in columns:
        with db.engine.connect() as connection:
            connection.execute(text('ALTER TABLE spare_part ADD COLUMN location VARCHAR(100)'))
    if 'uom' not in columns:
        with db.engine.connect() as connection:
            connection.execute(text('ALTER TABLE spare_part ADD COLUMN uom VARCHAR(50)'))
    if 'photo' not in columns:
        with db.engine.connect() as connection:
            connection.execute(text('ALTER TABLE spare_part ADD COLUMN photo VARCHAR(100)'))

@login_manager.user_loader
def load_user(user_id):
    with Session(db.engine) as session:
        return session.get(User, int(user_id))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Login failed. Please check your username and password', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    if current_user.role != 'admin':
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash('Username already exists. Please choose another.', 'danger')
            return redirect(url_for('register'))
        role = 'admin' if username == 'yuliyanto' and password == '125' else 'user'
        new_user = User(username=username, password=password, role=role)
        try:
            db.session.add(new_user)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('An error occurred during registration. Please try again.', 'danger')
            return redirect(url_for('register'))
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/')
@login_required
def index():
    search_query = request.args.get('search')
    if search_query:
        spare_parts = SparePart.query.filter(
            (SparePart.name.contains(search_query)) | 
            (SparePart.part_number.contains(search_query))
        ).all()
    else:
        spare_parts = SparePart.query.all()
    return render_template('index.html', spare_parts=spare_parts)

@app.route('/add_part', methods=['POST'])
@login_required
def add_part():
    if current_user.role != 'admin':
        flash('You do not have permission to add spare parts.', 'danger')
        return redirect(url_for('index'))
    try:
        part_number = request.form['part_number']
        quantity = int(request.form['quantity'])
        photo = request.files['photo']
        filename = None
        if photo:
            filename = secure_filename(photo.filename)
            photo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        existing_part = SparePart.query.filter_by(part_number=part_number).first()
        if existing_part:
            part_name = existing_part.name
            min_stock = existing_part.min_stock
            location = existing_part.location
            uom = existing_part.uom
            existing_part.quantity += quantity
            if filename:
                existing_part.photo = filename
            action = "Updated"
        else:
            part_name = request.form['part_name']
            min_stock = request.form.get('min_stock')
            location = request.form.get('location')
            uom = request.form.get('uom')
            if not min_stock or not location or not uom:
                flash('Minimum stock, location, and UOM are required for new parts', 'danger')
                return redirect(url_for('index'))
            new_part = SparePart(part_number=part_number, name=part_name, quantity=quantity, min_stock=int(min_stock), location=location, uom=uom, photo=filename)
            db.session.add(new_part)
            action = "Added"
        db.session.commit()
        audit = AuditTrail(
            user_id=current_user.id,
            action=action,
            part_number=part_number,
            part_name=part_name,
            quantity=quantity,
            timestamp=datetime.now(local_tz)
        )
        db.session.add(audit)
        db.session.commit()
        return redirect(url_for('index'))
    except KeyError as e:
        flash(f'Missing required field: {e.args}', 'danger')
        return redirect(url_for('index'))

@app.route('/take_part/<int:part_id>', methods=['POST'])
@login_required
def take_part(part_id):
    part = SparePart.query.get(part_id)
    if part:
        data = request.get_json()
        quantity = data.get('quantity', 1)
        if part.quantity >= quantity:
            part.quantity -= quantity
            db.session.commit()
            flash(f'Spare part {part.name} taken successfully.', 'success')
            audit = AuditTrail(
                user_id=current_user.id,
                action="Taken",
                part_number=part.part_number,
                part_name=part.name,
                quantity=quantity,
                timestamp=datetime.now(local_tz)
            )
            db.session.add(audit)
            db.session.commit()
            if part.quantity < part.min_stock:
                try:
                    send_email(
                        subject="Low Stock Warning",
                        recipients=["yuliyanto@keplersignaltek.co.id"],
                        body=f"Stock for {part.name} (Part Number: {part.part_number}) is below minimum level. Current quantity: {part.quantity}"
                    )
                except Exception as e:
                    logging.error(f"Failed to send email: {e}")
            return jsonify({'message': 'Success'}), 200
        else:
            flash(f'Spare part {part.name} is out of stock.', 'danger')
            return jsonify({'message': 'Insufficient stock'}), 400
    else:
        flash('Spare part not found.', 'danger')
        return jsonify({'message': 'Part not found'}), 404

@app.route('/get_part_details/<part_number>', methods=['GET'])
@login_required
def get_part_details(part_number):
    part = SparePart.query.filter_by(part_number=part_number).first()
    if part:
        return jsonify({
            'name': part.name,
            'min_stock': part.min_stock,
            'location': part.location,
            'uom': part.uom
        })
    return jsonify({'name': '', 'min_stock': '', 'location': '', 'uom': ''})

@app.route('/report')
@login_required
def report():
    spare_parts = SparePart.query.all()
    part_names = [part.name for part in spare_parts]
    quantities = [part.quantity for part in spare_parts]
    plt.figure(figsize=(10, 5))
    plt.bar(part_names, quantities, color='blue')
    plt.xlabel('Spare Part')
    plt.ylabel('Quantity')
    plt.title('Inventory Levels')
    img = io.BytesIO()
    plt.savefig(img, format='png')
    img.seek(0)
    plot_url = base64.b64encode(img.getvalue()).decode()
    return render_template('report.html', plot_url=plot_url)

def send_email(subject, recipients, body):
    try:
        smtp_server = "smtp-relay.brevo.com"
        smtp_port = 587
        smtp_login = "7e29eb002@smtp-brevo.com"
        smtp_password = "WKX4Nw9y0Eg5TJnI"  # Updated SMTP password
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = "sparepartroom@yahoo.com"
        msg['To'] = ", ".join(recipients)
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_login, smtp_password)
            server.sendmail(msg['From'], recipients, msg.as_string())
        logging.info(f"Email sent to {recipients} with subject '{subject}'")
    except Exception as e:
        logging.error(f"Failed to send email to {recipients}: {e}")

if __name__ == "__main__":
    app.run(debug=false)

@app.errorhandler(500)
def internal_error(error):
    logging.error(f"Server Error: {error}")
    return "500 error", 500

@app.errorhandler(Exception)
def unhandled_exception(e):
    logging.error(f"Unhandled Exception: {e}")
    return "500 error", 500
