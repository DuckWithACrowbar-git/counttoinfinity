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

@app.route('/')
def index():
    count = read_count()
    # ... your existing template logic ...
    return render_template('index.html', count=count)

@app.route('/increment', methods=['POST'])
def increment():
    with lock:
        n = read_count() + 1
        write_count(n)

    print(f"[server] incremented to {n} — emitting to clients")
    # broadcast to all connected clients
    socketio.emit('count_updated', {'count': n}, broadcast=True)
    return ("", 204)

@socketio.on('connect')
def on_connect():
    print("[server] client connected")
    emit('count_updated', {'count': read_count()})

if __name__ == '__main__':
    cert = '/etc/letsencrypt/live/counttoinfinity.duckdns.org/fullchain.pem'
    key = '/etc/letsencrypt/live/counttoinfinity.duckdns.org/privkey.pem'

    # single WSGI app that uses the same socketio.server
    wsgi_app = WSGIApp(socketio.server, app)

    # plain HTTP + WebSocket on 8080
    def serve_plain():
        print("[server] starting plain HTTP on :8080")
        eventlet.wsgi.server(eventlet.listen(("0.0.0.0", 8080)), wsgi_app)

    threading.Thread(target=serve_plain, daemon=True).start()

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

    # TLS on 443 using same WSGI app (wrap the listener)
    if os.path.exists(cert) and os.path.exists(key):
        print("[server] starting TLS on :443")
        listener = eventlet.listen(("0.0.0.0", 443))
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(certfile=cert, keyfile=key)
        ssl_listener = eventlet.wrap_ssl(listener, ssl_ctx, server_side=True)
        eventlet.wsgi.server(ssl_listener, wsgi_app)
    else:
        # fallback: run plain server in foreground for testing
        eventlet.wsgi.server(eventlet.listen(("0.0.0.0", 8080)), wsgi_app)
