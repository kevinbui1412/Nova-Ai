import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from google import genai
from google.genai import types
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = '12042014Aichatbot!'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

client = genai.Client(api_key="")

# Model User
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    chats = db.relationship('ChatSession', backref='user', lazy=True)

# Model Phiên chat (Chat Thread)
class ChatSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), default="Đoạn chat mới")
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    messages = db.relationship('Message', backref='session', lazy=True, cascade="all, delete-orphan")

# Model Chi tiết tin nhắn
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('chat_session.id'), nullable=False)
    sender = db.Column(db.String(20), nullable=False) # 'user' hoặc 'ai'
    content = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(300), nullable=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

with app.app_context():
    db.create_all()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        flash('Tên đăng nhập hoặc mật khẩu không chính xác!')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('Tên đăng nhập đã tồn tại!')
        else:
            hashed_password = generate_password_hash(password, method='scrypt')
            new_user = User(username=username, password=hashed_password)
            db.session.add(new_user)
            db.session.commit()
            flash('Đăng ký thành công! Hãy đăng nhập.')
            return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    user_chats = ChatSession.query.filter_by(user_id=current_user.id).order_by(ChatSession.id.desc()).all()
    active_chat_id = request.args.get('chat_id', type=int)
    
    active_messages = []
    if active_chat_id:
        chat_session = ChatSession.query.filter_by(id=active_chat_id, user_id=current_user.id).first()
        if chat_session:
            active_messages = chat_session.messages
        else:
            active_chat_id = None

    return render_template('index.html', 
                           username=current_user.username, 
                           chats=user_chats, 
                           active_chat_id=active_chat_id, 
                           active_messages=active_messages)

@app.route('/chat', methods=['POST'])
@login_required
def chat():
    user_message = request.form.get('message', '')
    image_file = request.files.get('image')
    chat_id = request.form.get('chat_id', type=int)
    
    # Nếu chưa có đoạn chat nào đang mở, tự động tạo phiên chat mới
    if not chat_id:
        title = user_message[:30] + ('...' if len(user_message) > 30 else '') if user_message else "Đoạn chat hình ảnh"
        new_session = ChatSession(title=title, user_id=current_user.id)
        db.session.add(new_session)
        db.session.commit()
        chat_id = new_session.id
    else:
        chat_session = ChatSession.query.filter_by(id=chat_id, user_id=current_user.id).first()
        # Cập nhật lại tiêu đề chat theo câu hỏi đầu tiên nếu chat đang trống
        if chat_session and not chat_session.messages and user_message:
            chat_session.title = user_message[:30] + ('...' if len(user_message) > 30 else '')
            db.session.commit()

    contents = []
    image_path = None
    relative_image_url = None

    if image_file and image_file.filename != '':
        filename = secure_filename(image_file.filename)
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image_file.save(image_path)
        relative_image_url = url_for('static', filename=f'uploads/{filename}')
        
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        
        contents.append(
            types.Part.from_bytes(
                data=image_bytes,
                mime_type=image_file.mimetype or "image/jpeg",
            )
        )

    if user_message:
        contents.append(user_message)

    if not contents:
        return jsonify({'response': 'Vui lòng nhập nội dung hoặc tải lên một hình ảnh.'})

    # Lưu tin nhắn của User vào DB
    user_msg_db = Message(session_id=chat_id, sender='user', content=user_message, image_url=relative_image_url)
    db.session.add(user_msg_db)
    db.session.commit()

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents,
        )
        ai_reply = response.text
    except Exception as e:
        ai_reply = f'Đã xảy ra lỗi hệ thống: {str(e)}'

    # Lưu câu trả lời của AI vào DB
    ai_msg_db = Message(session_id=chat_id, sender='ai', content=ai_reply)
    db.session.add(ai_msg_db)
    db.session.commit()

    return jsonify({
        'chat_id': chat_id,
        'response': ai_reply,
        'image_url': relative_image_url
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
