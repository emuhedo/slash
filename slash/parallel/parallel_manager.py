import sys
import os
import subprocess
import time
import logbook
import threading
from  six.moves import xmlrpc_client
from .. import log
from ..exceptions import INTERRUPTION_EXCEPTIONS, ParallelServerIsDown, ParallelTimeout
from ..conf import config
from ..ctx import context
from .server import Server, ServerStates
from ..utils.tmux_utils import create_new_window, kill_tmux_session
from .._compat import iteritems
_logger = logbook.Logger(__name__)
log.set_log_color(_logger.name, logbook.NOTICE, 'blue')

TIME_BETWEEN_CHECKS = 2
MAX_CONNECTION_RETRIES = 200

class ParallelManager(object):
    def __init__(self, args):
        super(ParallelManager, self).__init__()
        self.server = None
        self.args = [sys.executable, '-m', 'slash.frontend.main', 'run', '--parallel_parent_session_id', context.session.id] + args
        self.workers_num = config.root.parallel.num_workers
        self.workers = {}
        self.max_worker_id = 1
        self.server_thread = None

    def try_connect(self):
        num_retries = 0
        while self.server.state == ServerStates.NOT_INITIALIZED:
            time.sleep(0.1)
            if num_retries == MAX_CONNECTION_RETRIES:
                raise ParallelServerIsDown("Cannot connect to XML_RPC server")
            num_retries += 1

    def start_worker(self):
        worker_id = str(self.max_worker_id)
        _logger.notice("Starting worker number {}".format(worker_id))
        new_args = self.args[:] + ["--parallel_worker_id", worker_id]
        if config.root.run.tmux:
            new_args.append(';$SHELL')
            command = ' '.join(new_args)
            self.workers[worker_id] = create_new_window("worker {}".format(worker_id), command)
        else:
            with open(os.devnull, 'w') as devnull:
                proc = subprocess.Popen(new_args, stdin=devnull, stdout=devnull, stderr=devnull)
                self.workers[worker_id] = proc
        self.max_worker_id += 1


    def start_server_in_thread(self, collected):
        self.server = Server(collected)
        self.server_thread = threading.Thread(target=self.server.serve, args=())
        self.server_thread.setDaemon(True)
        self.server_thread.start()

    def get_proxy(self):
        return xmlrpc_client.ServerProxy('http://{0}:{1}'.format(config.root.parallel.server_addr, self.server.port))

    def terminate_all(self):
        for worker in self.workers.values():
            worker.kill()
        self.get_proxy().session_interrupted()

    def wait_all_workers_to_connect(self):
        while self.server.state == ServerStates.WAIT_FOR_CLIENTS:
            if time.time() - self.server.last_request_time > config.root.parallel.workers_connect_timeout:
                _logger.error("Timeout: Not all clients connected to server, terminating")
                self.terminate_all()
                raise ParallelTimeout("Not all clients connected")
            time.sleep(TIME_BETWEEN_CHECKS)

    def check_worker_timed_out(self):
        for worker_id, last_connection_time in iteritems(self.server.get_workers_last_connection_time()):
            if time.time() - last_connection_time > config.root.parallel.communication_timeout_secs:
                _logger.error("Worker {} is down, terminating session".format(worker_id))
                if not config.root.run.tmux:
                    if self.workers[worker_id].poll() is None:
                        self.workers[worker_id].kill()
                else:
                    self.workers[worker_id].rename_window('stopped_client_{}'.format(worker_id))
                self.get_proxy().report_client_failure(worker_id)

    def check_no_requests_timeout(self):
        if time.time() - self.server.last_request_time > config.root.parallel.no_request_timeout:
            _logger.error("No request sent to server for {} seconds, terminating".format(config.root.parallel.no_request_timeout))
            self.terminate_all()
            raise ParallelTimeout("No request sent to server for {} seconds".format(config.root.parallel.no_request_timeout))


    def start(self):
        self.try_connect()
        if not config.root.parallel.server_port:
            self.args.extend(['--parallel_port', str(self.server.port)])
        try:
            for _ in range(self.workers_num):
                self.start_worker()
            self.wait_all_workers_to_connect()
            while self.server.should_wait_for_request():
                self.check_worker_timed_out()
                self.check_no_requests_timeout()
                time.sleep(TIME_BETWEEN_CHECKS)
        except INTERRUPTION_EXCEPTIONS:
            _logger.error("Server interrupted, stopping workers and terminating")
            if config.root.run.tmux:
                kill_tmux_session()
            else:
                self.terminate_all()
                raise
        finally:
            if not config.root.run.tmux:
                for worker in self.workers.values():
                    worker.wait()
                self.server_thread.join()