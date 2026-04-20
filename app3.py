import os
import json
import threading
from flask import Flask, render_template, request, send_from_directory, jsonify, redirect, url_for
from flask_socketio import SocketIO, emit

APP_DIR = os.path.dirname(os.path.abspath(__file__))
GIFS_DIR = os.path.join(APP_DIR, 'gifs')
COUNT_FILE = os.path.join(APP_DIR, 'count.json')

app = Flask(__name__, template_folder='templates', static_folder='public')
socketio = SocketIO(app, cors_allowed_origins="*")
lock = threading.Lock()

def read_count():
    if not os.path.exists(COUNT_FILE):
        return 0
    try:
        with open(COUNT_FILE, 'r') as f:
            return int(json.load(f).get('count', 0))
    except:
        return 0

def write_count(n):
    tmp = COUNT_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump({'count': n}, f)
    os.replace(tmp, COUNT_FILE)

def build_digit_gif_map():
    mapping = {}
    if not os.path.isdir(GIFS_DIR):
        return mapping
    for fn in sorted(os.listdir(GIFS_DIR)):
        name = fn.lower()
        if name and name[0].isdigit() and name.endswith(('.gif', '.png', '.jpg', '.jpeg', '.webp')):
            mapping.setdefault(name[0], fn)
    return mapping

@app.route('/')
def index():
    count = read_count()
    digit_map = build_digit_gif_map()
    digits = list(str(count)) if count != 0 else ['0']
    gifs = [digit_map[d] for d in digits if d in digit_map]
    return render_template('index.html', count=count, gifs=gifs)

@app.route('/gifs/<path:filename>')
def serve_gif(filename):
    return send_from_directory(GIFS_DIR, filename)

@app.route('/api/gifs/<int:count>')
def api_gifs(count):
    digit_map = build_digit_gif_map()
    digits = list(str(count)) if count != 0 else ['0']
    gifs = [digit_map[d] for d in digits if d in digit_map]
    return jsonify({'gifs': gifs})

@app.route('/increment', methods=['POST'])
def increment():
    with lock:
        n = read_count() + 1
        write_count(n)

    socketio.emit('count_updated', {'count': n})
    return ("", 204)

@socketio.on('connect')
def on_connect():
    emit('count_updated', {'count': read_count()})

if __name__ == '__main__':
    cert = '/etc/letsencrypt/live/counttoinfinity.duckdns.org/fullchain.pem'
    key = '/etc/letsencrypt/live/counttoinfinity.duckdns.org/privkey.pem'

    import eventlet
    import eventlet.wsgi

    if os.path.exists(cert) and os.path.exists(key):

        # --- HTTP redirect server that does NOT break WebSockets ---
        def redirect_app(environ, start_response):
            if environ.get("HTTP_UPGRADE", "").lower() == "websocket":
                start_response("400 Bad Request", [])
                return [b"WebSocket not supported on port 80"]

            host = environ.get("HTTP_HOST", "").split(":")[0]
            path = environ.get("PATH_INFO", "")
            new_url = f"https://{host}{path}"
            start_response("301 Moved Permanently", [("Location", new_url)])
            return []

        threading.Thread(
            target=lambda: eventlet.wsgi.server(
                eventlet.listen(("0.0.0.0", 80)),
                redirect_app
            ),
            daemon=True
        ).start()

        # --- HTTPS WebSocket server ---
        socketio.run(
            app,
            host='0.0.0.0',
            port=443,
            certfile=cert,
            keyfile=key
        )

    else:
        socketio.run(app, host='0.0.0.0', port=8000)
