#################################################################################
###
###    Uses gevent to interleave a raw socket server for receiving log entries
###    from different endpoints with a wsgi server for displaying these
###    entries in realtime with a longpoll.  Flask is used as a web controller.
###    --JD 20120523
###
################################################################################

# central logging app dependencies
from collections import defaultdict, deque
import datetime
import simplejson as json
import gevent
from gevent.event import Event, AsyncResult
from gevent.server import StreamServer
from gevent.wsgi import WSGIServer

# central logging app definition
class LogPool(object):
    def __init__(self):
        self.max_buffer_size = 600  # how many historical messages to hold per-channel
        self.connections = {}  # holds socket objects for each connection 
        self.events = {} # holds async event objects for each connection
        self.messages = defaultdict(lambda: deque(maxlen=self.max_buffer_size)) # message buffer for each connection
        self.lost = {} # indexes of lost channels

    def connection_poller(self, idx):
        while True:
            print 'polling channel: %s' % idx
            try:
                raw_msg = gevent.with_timeout(30, self.connections[idx].recv, 4096, timeout_value='__timeout__')
            except:
                raw_msg = None

            print 'raw_msg: %s' % raw_msg
            if raw_msg == '__timeout__': # connection was silent for 30 seconds, retry recv to see if it dropped
                gevent.sleep(1)
            elif raw_msg:  # connetion returned a message, store it and send an event
                self._new_message(idx, raw_msg)
                gevent.sleep(1)
            else: # connection returned no data or an error, ditch it
                self._lost_channel(idx)
                break

    def handle_new_connection(self, socket, address):
        print 'new connection detected!'
        #socket.send('Please register a logging channel name:\r\n')
        idx = self._get_channel_idx(socket)
        if idx:
            # put the connection and event stuff in place
            print 'creating new logging channel: %s' % idx
            self.connections[idx] = socket
            self.events[idx] = AsyncResult()
            # cleanup and old buffered data
            if idx in self.lost:
                del self.lost[idx]
            # spawn polling greenlet and inform the client we're ready to go
            gevent.spawn(self.connection_poller, idx)
            socket.send('continue\r\n')
            gevent.sleep(1)
        else:
            print 'client neglected to register'

    def _new_message(self, idx, raw_msg):
        msg = self._parse_message(raw_msg)
        self.messages[idx].extend(msg)
        self.events[idx].set(msg)
        self.events[idx] = AsyncResult()

    def _lost_channel(self, idx):
        print 'lost channel: %s' % idx
        self.connections[idx].close()
        del self.connections[idx]
        del self.events[idx]
        self.lost[idx] = str(datetime.datetime.utcnow())

    def _get_channel_idx(self, socket):
        raw = socket.recv(4096)
        if raw and raw.strip():
            return raw.strip()
        else:
            return None

    def _parse_message(self, msg):
        return msg.strip().split('\r\n')

# central logging app instance
logapp = LogPool()


# web app dependencies
from flask import Flask, abort, redirect, url_for, jsonify, render_template
import sys, traceback

# web app instance
webapp = Flask(__name__)

# web app definition
@webapp.route('/')
def index():
    return render_template('index.html', channels=sorted(logapp.events.keys()), lost=sorted(logapp.lost.keys()))

@webapp.route('/poll/<idx>')
def poll(idx):
    idx_decode = idx.replace('-', '/')
    if idx_decode in logapp.events:
        try:
            return jsonify({'buffer' : logapp.events[idx_decode].get(timeout=30)})
        except:
            traceback.print_exc(file=sys.stderr)
            abort(408)
    else:
        abort(404)

@webapp.route('/buffer/<idx>')
def buffer(idx):
    idx_decode = idx.replace('-', '/')
    if idx_decode in logapp.messages:
        try:
            return jsonify({'buffer' : list(logapp.messages[idx_decode])})
        except:
            traceback.print_exc(file=sys.stderr)
            abort(408)
    else:
        abort(404)

@webapp.route('/lost')
def lost():
    return str(logapp.lost)


# run servers
if __name__ == '__main__':
    logsrv = StreamServer(('', 23456), logapp.handle_new_connection)
    websrv = WSGIServer(('', 8080), webapp)

    print 'Starting servers...'
    logsrv.start()
    websrv.start()

    try:
        print 'Listening...'
        Event().wait()
    except:
        print 'Stopping servers'
        logsrv.stop()
        websrv.stop()
    print 'Exiting'
