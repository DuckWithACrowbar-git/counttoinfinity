import os
import json
import threading
from flask import Flask, render_template, send_from_directory, jsonify
from flask_socketio import SocketIO, emit
import eventlet
import eventlet.wsgi
from socketio import WSGIApp
import ssl

# Early monkey patch for eventlet
eventlet.monkey_patch()

APP_DIR = os.path.dirname(os.path.abspath(__file__))
GIFS_DIR = os.path.join(APP_DIR, 'gifs')
COUNT_FILE = os.path.join(APP_DIR, 'count.json')

app = Flask(__name__, template_folder='templates', static_folder='public')
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")
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

    # Emit to all connected clients on the single Socket.IO server
    socketio.emit('count_updated', {'count': n})
    return ("", 204)

@socketio.on('connect')
def on_connect():
    emit('count_updated', {'count': read_count()})

if __name__ == '__main__':
    cert = '/etc/letsencrypt/live/counttoinfinity.duckdns.org/fullchain.pem'
    key = '/etc/letsencrypt/live/counttoinfinity.duckdns.org/privkey.pem'

    # Create a WSGI app that uses the same Socket.IO server and Flask app
    wsgi_app = WSGIApp(socketio.server, app)

    # --- HTTP + WebSockets on port 8080 (no TLS) ---
    def serve_plain_http():
        eventlet.wsgi.server(
            eventlet.listen(("0.0.0.0", 8080)),
            wsgi_app
        )

    threading.Thread(target=serve_plain_http, daemon=True).start()

    # --- Optional HTTP redirect server on port 80 ---
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

    # --- HTTPS + WebSockets on port 443 using the same WSGI app ---
    if os.path.exists(cert) and os.path.exists(key):
        # Create a listening socket and wrap it with SSL
        listener = eventlet.listen(("0.0.0.0", 443))
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(certfile=cert, keyfile=key)
        ssl_listener = eventlet.wrap_ssl(listener, ssl_ctx, server_side=True)
        # Serve the same WSGI app (same socketio.server) over TLS
        eventlet.wsgi.server(ssl_listener, wsgi_app)
    else:
        # If no certs, keep the plain HTTP server running in foreground for testing
        eventlet.wsgi.server(eventlet.listen(("0.0.0.0", 8080)), wsgi_app)
