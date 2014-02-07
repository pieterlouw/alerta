
import time
import threading

from alerta.common import config
from alerta.common import log as logging
from alerta.common.daemon import Daemon
from alerta.common.alert import Alert
from alerta.common.heartbeat import Heartbeat
from alerta.common import status_code, severity_code
from alerta.common.amqp import Messaging, MessageHandler
from alerta.server.database import Mongo
from alerta.common.graphite import Carbon, StatsD

Version = '2.2.0'

LOG = logging.getLogger(__name__)
CONF = config.CONF


class WorkerThread(threading.Thread):

    def __init__(self, mq):

        threading.Thread.__init__(self)

        LOG.debug('Initialising %s...', self.getName())
        self.mq = mq

    def run(self):

        ServerMessage(self.mq).run()


class ServerMessage(MessageHandler):

    def __init__(self, mq):

        self.mq = mq
        self.statsd = StatsD()
        self.db = Mongo()       # mongo database

        MessageHandler.__init__(self, self.mq.conn)

    def on_message(self, body, message):

        LOG.debug("Received body : %s", body)

        try:
            heartbeat = Heartbeat.parse_heartbeat(body)
        except ValueError, e:
            LOG.warning('Failed to parse heartbeat - %s: %s', e, body)
        else:
            if heartbeat.get_type() == 'Heartbeat':
                LOG.info('*** Heartbeat *** %s', heartbeat)
                heartbeat.receive_now()
                self.db.update_hb(heartbeat)
                message.ack()
                return

        try:
            incomingAlert = Alert.parse_alert(body)
        except ValueError, e:
            LOG.error('Failed to parse alert - %s: %s', e, body)
            message.ack()
            return
        else:
            LOG.info('*** %s *** %s', incomingAlert.get_type(), incomingAlert)
            incomingAlert.receive_now()

        LOG.info('Alert received from %s...', incomingAlert.origin)
        try:
            suppress = incomingAlert.transform_alert()
        except RuntimeError:
            self.statsd.metric_send('alerta.alerts.error', 1)
            message.ack()
            return

        if suppress:
            LOG.info('Suppressing alert %s', incomingAlert.get_id())
            message.ack()
            return

        if self.db.is_duplicate(incomingAlert, incomingAlert.severity):
            # Duplicate alert .. 1. update existing document with lastReceiveTime, lastReceiveId, text, summary,
            #                       value, status, tags and origin
            #                    2. increment duplicate count
            #                    3. update and push status if changed

            LOG.info('%s : Duplicate alert -> update dup count', incomingAlert.alertid)
            duplicateAlert = self.db.duplicate_alert(incomingAlert)

            if incomingAlert.status != status_code.UNKNOWN and incomingAlert.status != duplicateAlert.status:
                self.db.update_status(alert=duplicateAlert, status=incomingAlert.status)
                duplicateAlert.status = incomingAlert.status

            if CONF.forward_duplicate:
                # Forward alert to notify topic and logger queue
                self.mq.send(duplicateAlert, CONF.outbound_queue)
                self.mq.send(duplicateAlert, CONF.outbound_topic)
                LOG.info('%s : Alert forwarded to %s and %s', duplicateAlert.get_id(), CONF.outbound_queue, CONF.outbound_topic)

            self.db.update_timer_metric(duplicateAlert.create_time, duplicateAlert.last_receive_time)
            message.ack()

        elif self.db.is_correlated(incomingAlert):
            # Diff sev alert ... 1. update existing document with severity, createTime, receiveTime,
            #                       lastReceiveTime, previousSeverity,
            #                       severityCode, lastReceiveId, text, summary, value, tags and origin
            #                    2. set duplicate count to zero
            #                    3. push history and status if changed

            previous_severity = self.db.get_severity(incomingAlert)
            LOG.info('%s : Event and/or severity change %s %s -> %s update details', incomingAlert.get_id(),
                     incomingAlert.event, previous_severity, incomingAlert.severity)

            trend_indication = severity_code.trend(previous_severity, incomingAlert.severity)

            correlatedAlert = self.db.correlate_alert(incomingAlert, previous_severity, trend_indication)

            if incomingAlert.status == status_code.UNKNOWN:
                incomingAlert.status = severity_code.status_from_severity(previous_severity, incomingAlert.severity,
                                                                          correlatedAlert.status)
            if incomingAlert.status != correlatedAlert.status:
                self.db.update_status(alert=correlatedAlert, status=incomingAlert.status)
                correlatedAlert.status = incomingAlert.status

            # Forward alert to notify topic and logger queue
            self.mq.send(correlatedAlert, CONF.outbound_queue)
            self.mq.send(correlatedAlert, CONF.outbound_topic)
            LOG.info('%s : Alert forwarded to %s and %s', correlatedAlert.get_id(), CONF.outbound_queue, CONF.outbound_topic)

            self.db.update_timer_metric(correlatedAlert.create_time, correlatedAlert.receive_time)
            message.ack()

        else:
            # New alert so ... 1. insert entire document
            #                  2. push history and status
            #                  3. set duplicate count to zero

            LOG.info('%s : New alert -> insert', incomingAlert.get_id())

            trend_indication = severity_code.trend(severity_code.UNKNOWN, incomingAlert.severity)

            incomingAlert.repeat = False
            incomingAlert.duplicate_count = 0
            incomingAlert.last_receive_id = incomingAlert.alertid
            incomingAlert.last_receive_time = incomingAlert.receive_time
            incomingAlert.trend_indication = trend_indication

            if incomingAlert.status == status_code.UNKNOWN:
                incomingAlert.status = severity_code.status_from_severity(severity_code.UNKNOWN, incomingAlert.severity)

            if incomingAlert.alertid != self.db.save_alert(incomingAlert):
                LOG.critical('Alert was not saved with submitted alert id. Race condition?')

            self.db.update_status(alert=incomingAlert, status=incomingAlert.status)

            # Forward alert to notify topic and logger queue
            self.mq.send(incomingAlert, CONF.outbound_queue)
            self.mq.send(incomingAlert, CONF.outbound_topic)
            LOG.info('%s : Alert forwarded to %s and %s', incomingAlert.get_id(), CONF.outbound_queue, CONF.outbound_topic)

            self.db.update_timer_metric(incomingAlert.create_time, incomingAlert.receive_time)
            message.ack()

        # update application stats
        self.statsd.metric_send('alerta.alerts.total', 1)
        self.statsd.metric_send('alerta.alerts.%s' % incomingAlert.severity, 1)


class AlertaDaemon(Daemon):

    alerta_opts = {
        'forward_duplicate': 'no',
    }

    def __init__(self, prog, **kwargs):

        config.register_opts(AlertaDaemon.alerta_opts)

        Daemon.__init__(self, prog, kwargs)

        self.db = Mongo()       # mongo database
        self.carbon = Carbon()  # carbon metrics
        self.statsd = StatsD()  # graphite metrics
        self.mq = Messaging()

        self.shuttingdown = False

    def run(self):

        self.mq.connect()

        # Start worker threads
        LOG.debug('Starting %s worker threads...', CONF.server_threads)
        for i in range(CONF.server_threads):
            w = WorkerThread(self.mq)
            try:
                w.start()
            except Exception, e:
                LOG.error('Worker thread #%s did not start: %s', i, e)
                continue
            LOG.info('Started worker thread: %s', w.getName())

        while not self.shuttingdown:
            try:
                LOG.debug('Send heartbeat...')
                heartbeat = Heartbeat(version=Version, timeout=CONF.loop_every)
                self.mq.send(heartbeat)
                time.sleep(CONF.loop_every)
            except (KeyboardInterrupt, SystemExit):
                self.shuttingdown = True
                MessageHandler.should_stop = True

        LOG.info('Shutdown request received...')
        w.join()

        LOG.info('Disconnecting from message broker...')
        self.mq.disconnect()
