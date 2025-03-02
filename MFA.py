# Flask MFA Implementation using Flask-TOTP

from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import pyotp
import qrcode
from io import BytesIO
import base64

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'  # Use a strong random key in production
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# User model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    totp_secret = db.Column(db.String(32), nullable=True)
    mfa_enabled = db.Column(db.Boolean, default=False)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def get_totp_uri(self):
        return f'otpauth://totp/Flask-MFA:{self.username}?secret={self.totp_secret}&issuer=Flask-MFA'
    
    def verify_totp(self, token):
        totp = pyotp.TOTP(self.totp_secret)
        return totp.verify(token)

# Create database tables
with app.app_context():
    db.create_all()

# Routes
@app.route('/')
def index():
    if 'user_id' in session:
        return render_template('dashboard.html')
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Check if user exists
        user = User.query.filter_by(username=username).first()
        if user:
            flash('Username already exists')
            return redirect(url_for('register'))
        
        # Create new user
        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        
        flash('Registration successful! Please log in.')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if not user or not user.check_password(password):
            flash('Invalid username or password')
            return redirect(url_for('login'))
        
        if user.mfa_enabled:
            # MFA is enabled, redirect to MFA verification
            session['login_user_id'] = user.id  # Store temporarily for MFA
            return redirect(url_for('verify_mfa'))
        else:
            # MFA not enabled, log user in directly
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
    
    return render_template('login.html')

@app.route('/verify-mfa', methods=['GET', 'POST'])
def verify_mfa():
    if 'login_user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['login_user_id'])
    
    if request.method == 'POST':
        token = request.form.get('token')
        if user.verify_totp(token):
            # MFA verification successful
            session.pop('login_user_id', None)
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid token')
    
    return render_template('verify_mfa.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    return render_template('dashboard.html', user=user)

@app.route('/setup-mfa', methods=['GET', 'POST'])
def setup_mfa():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    
    if user.mfa_enabled:
        flash('MFA is already enabled')
        return redirect(url_for('dashboard'))
    
    if not user.totp_secret:
        # Generate a new TOTP secret
        user.totp_secret = pyotp.random_base32()
        db.session.commit()
    
    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(user.get_totp_uri())
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered)
    img_str = base64.b64encode(buffered.getvalue()).decode()
    
    if request.method == 'POST':
        token = request.form.get('token')
        if user.verify_totp(token):
            user.mfa_enabled = True
            db.session.commit()
            flash('MFA has been enabled')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid token')
    
    return render_template('setup_mfa.html', secret=user.totp_secret, qr_code=img_str)

@app.route('/disable-mfa', methods=['GET', 'POST'])
def disable_mfa():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    
    if not user.mfa_enabled:
        flash('MFA is not enabled')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        token = request.form.get('token')
        if user.verify_totp(token):
            user.mfa_enabled = False
            db.session.commit()
            flash('MFA has been disabled')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid token')
    
    return render_template('disable_mfa.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('login_user_id', None)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
