"""
Gunicorn worker with gevent-websocket support for Flask-SocketIO.

python-engineio's `gevent` driver requires the WSGI environ to carry
`wsgi.websocket`, which gunicorn's stock `-k gevent` worker does not provide
(raises "The gevent-websocket server is not configured appropriately").
This worker serves via gevent.pywsgi + WebSocketHandler so websocket
transports work.

Usage: gunicorn -k gunicorn_gevent_ws.GeventWebSocketWorker -w 1 module:app
"""
from gevent.pywsgi import WSGIServer
from geventwebsocket.handler import WebSocketHandler
from gunicorn.workers.ggevent import GeventWorker


class GeventWebSocketWorker(GeventWorker):
    """Gevent worker served by gevent.pywsgi with the WebSocketHandler."""

    server_class = WSGIServer
    wsgi_handler = WebSocketHandler