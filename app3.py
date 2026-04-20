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