import os
import json
import threading
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, send_from_directory, jsonify
from flask_socketio import SocketIO, emit
from socketio import WSGIApp
import eventlet.wsgi
import ssl
import traceback

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
    except Exception:
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

    print(f"[server] increment -> {n}")
    socketio.emit('count_updated', {'count': n}, broadcast=True)
    return ("", 204)

@socketio.on('connect')
def on_connect():
    print("[server] client connected")
    emit('count_updated', {'count': read_count()})

def validate_cert_paths(cert, key):
    info = {}
    for label, p in (("cert", cert), ("key", key)):
        info[label] = {
            "value": p,
            "exists": os.path.exists(p) if p else False,
            "isfile": os.path.isfile(p) if p else False,
            "abspath": os.path.abspath(p) if p else None
        }
    return info

if __name__ == '__main__':
    cert = '/etc/letsencrypt/live/counttoinfinity.duckdns.org/fullchain.pem'
    key = '/etc/letsencrypt/live/counttoinfinity.duckdns.org/privkey.pem'

    # single WSGI app that uses the same socketio.server
    wsgi_app = WSGIApp(socketio.server, app)

    # plain HTTP + WebSocket on 8080
    def serve_plain_http():
        print("[server] starting plain HTTP + WebSocket on :8080")
        eventlet.wsgi.server(eventlet.listen(("0.0.0.0", 8080)), wsgi_app)

    threading.Thread(target=serve_plain_http, daemon=True).start()

    # redirect on 80 (no websockets)
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
        target=lambda: eventlet.wsgi.server(eventlet.listen(("0.0.0.0", 80)), redirect_app),
        daemon=True
    ).start()

    # TLS on 443 using same WSGI app
    cert_info = validate_cert_paths(cert, key)
    print("[server] cert info:", cert_info)

    if cert_info['cert']['isfile'] and cert_info['key']['isfile']:
        try:
            listener = eventlet.listen(("0.0.0.0", 443))
            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(certfile=cert, keyfile=key)
            ssl_listener = eventlet.wrap_ssl(listener, ssl_ctx, server_side=True)
            print("[server] starting TLS + WebSocket on :443")
            # Serve TLS in foreground so process stays alive
            eventlet.wsgi.server(ssl_listener, wsgi_app)
        except Exception as e:
            print("[server] failed to start TLS listener:", repr(e))
            traceback.print_exc()
            print("[server] falling back to plain HTTP on :8080 (already running)")
    else:
        print("[server] cert or key not found or not regular files; running only plain HTTP on :8080")