
import time
import json
import urllib2
import datetime

from alerta.common import config
from alerta.common import log as logging
from alerta.common.daemon import Daemon
from alerta.common.amqp import DirectPublisher, FanoutConsumer
from alerta.common.alert import Alert
from alerta.common.heartbeat import Heartbeat
from alerta.common.utils import DateEncoder

Version = '2.2.0'

LOG = logging.getLogger(__name__)
CONF = config.CONF


class LoggerMessage(FanoutConsumer):

    def __init__(self, pubsub, topic):

        self.pubsub = pubsub
        self.topic = topic

        FanoutConsumer.__init__(self, self.pubsub.conn)

    def on_message(self, body, message):

        LOG.debug("Received: %s", body)
        try:
            logAlert = Alert.parse_alert(body)
        except ValueError:
            return

        if logAlert:
            LOG.info('%s : [%s] %s', logAlert.last_receive_id, logAlert.status, logAlert.summary)

            source_host, _, source_path = logAlert.resource.partition(':')
            document = {
                '@message': logAlert.summary,
                '@source': logAlert.resource,
                '@source_host': source_host,
                '@source_path': source_path,
                '@tags': logAlert.tags,
                '@timestamp': logAlert.last_receive_time,
                '@type': logAlert.event_type,
                '@fields': logAlert.get_body()
            }
            LOG.debug('Index payload %s', document)

            index_url = "http://%s:%s/%s/%s" % (CONF.es_host, CONF.es_port,
                                                datetime.datetime.utcnow().strftime(CONF.es_index), logAlert.event_type)
            LOG.debug('Index URL: %s', index_url)

            try:
                response = urllib2.urlopen(index_url, json.dumps(document, cls=DateEncoder)).read()
            except Exception, e:
                LOG.error('%s : Alert indexing to %s failed - %s', logAlert.last_receive_id, index_url, e)
                return

            try:
                es_id = json.loads(response)['_id']
                LOG.info('%s : Alert indexed at %s/%s', logAlert.last_receive_id, index_url, es_id)
            except Exception, e:
                LOG.error('%s : Could not parse elasticsearch reponse: %s', e)

            message.ack()


class LoggerDaemon(Daemon):
    """
    Index alerts in ElasticSearch using Logstash format so that logstash GUI and/or Kibana can be used as front-ends
    """

    logger_opts = {
        'es_host': 'localhost',
        'es_port': 9200,
        'es_index': 'alerta-%Y.%m.%d',  # NB. Kibana config must match this index
    }

    def __init__(self, prog, **kwargs):

        config.register_opts(LoggerDaemon.logger_opts)

        Daemon.__init__(self, prog, kwargs)

    def run(self):

        self.running = True

        # Connect to message queue
        self.pubsub = DirectPublisher(CONF.inbound_queue)
        self.pubsub.connect()
        self.topic = FanoutConsumer(CONF.outbound_topic)
        LoggerMessage(self.pubsub, self.topic).run()

        while not self.shuttingdown:
            try:
                LOG.debug('Waiting for log messages...')
                time.sleep(CONF.loop_every)

                LOG.debug('Send heartbeat...')
                heartbeat = Heartbeat(version=Version)
                self.topic.send(heartbeat)

            except (KeyboardInterrupt, SystemExit):
                self.shuttingdown = True

        LOG.info('Shutdown request received...')
        self.running = False

        LOG.info('Disconnecting from message broker...')
        self.topic.disconnect()