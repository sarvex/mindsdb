import logging
import os
import torch.multiprocessing as mp

from waitress import serve

from mindsdb.api.http.initialize import initialize_app
from mindsdb.interfaces.storage import db
from mindsdb.utilities import log
from mindsdb.utilities.config import Config
from mindsdb.utilities.functions import init_lexer_parsers


def start(verbose, no_studio, with_nlp):
    config = Config()

    server = os.environ.get('MINDSDB_DEFAULT_SERVER', 'waitress')
    db.init()
    log.initialize_log(config, 'http', wrap_print=server.lower() != 'gunicorn')

    init_lexer_parsers()

    app = initialize_app(config, no_studio, with_nlp)

    port = config['api']['http']['port']
    host = config['api']['http']['host']

    if server.lower() == 'waitress':
        if host in ('', '0.0.0.0'):
            serve(app, port=port, host='*', max_request_body_size=1073741824 * 10, inbuf_overflow=1073741824 * 10)
        else:
            serve(app, port=port, host=host, max_request_body_size=1073741824 * 10, inbuf_overflow=1073741824 * 10)
    elif server.lower() == 'flask':
        # that will 'disable access' log in console
        logger = logging.getLogger('werkzeug')
        logger.setLevel(logging.WARNING)

        app.run(debug=False, port=port, host=host)
    elif server.lower() == 'gunicorn':
        try:
            from mindsdb.api.http.gunicorn_wrapper import StandaloneApplication
        except ImportError:
            print("Gunicorn server is not available by default. If you wish to use it, please install 'gunicorn'")
            return

        def post_fork(arbiter, worker):
            db.engine.dispose()

        options = {
            'bind': f'{host}:{port}',
            'workers': mp.cpu_count(),
            'timeout': 600,
            'reuse_port': True,
            'preload_app': True,
            'post_fork': post_fork,
            'threads': 4
        }
        StandaloneApplication(app, options).run()
