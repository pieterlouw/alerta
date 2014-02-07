
import json

from kombu import Connection, Exchange, Queue
from kombu.mixins import ConsumerMixin
# from kombu.utils.debug import setup_logging

from alerta.common import log as logging
from alerta.common import config
from alerta.common.utils import DateEncoder

LOG = logging.getLogger(__name__)
CONF = config.CONF


class Messaging(object):

    mq_opts = {
        'inbound_queue': 'alerts',
        'outbound_queue': '/queue/logger',
        'outbound_topic': '/topic/notify',

        'rabbit_host': 'localhost',
        'rabbit_port': 5672,
        'rabbit_use_ssl': False,
        'rabbit_userid': 'guest',
        'rabbit_password': 'guest',
        'rabbit_virtual_host': '/',
    }

    def __init__(self):

        config.register_opts(Messaging.mq_opts)

        self.exchange = Exchange(CONF.inbound_queue, type='fanout', durable=True)
        self.queue = Queue(CONF.inbound_queue, exchange=self.exchange, routing_key=CONF.inbound_queue)

        self.conn = None
        self.producer = None
        self.consumer = None
        self.callback = None

        # setup_logging(loglevel="DEBUG")

    def connect(self, callback=None, wait=False):

        self.conn = Connection('amqp://%s:%s@%s:%d/%s' % (CONF.rabbit_userid, CONF.rabbit_password,
                               CONF.rabbit_host, CONF.rabbit_port, CONF.rabbit_virtual_host))
        self.conn.connect()

    def reconnect(self):
        pass

    def subscribe(self, callback=None):

        self.consumer = self.conn.Consumer()
        self.consumer.register_callback(callback)

    def send(self, msg, destination=None):

        destination = destination or CONF.inbound_queue

        self.producer = self.conn.Producer(exchange=self.exchange, serializer='json')
        self.producer.declare()
        self.producer.publish(json.dumps(msg.get_body(), cls=DateEncoder), exchange=self.exchange,
                              routing_key=destination, declare=[self.queue])

    def disconnect(self):

        return self.conn.release()

    def is_connected(self):

        return self.conn.connected


class MessageHandler(ConsumerMixin):

    config.register_opts(Messaging.mq_opts)

    exchange = Exchange(CONF.inbound_queue, 'fanout', durable=True)
    queue = Queue(CONF.inbound_queue, exchange=exchange, routing_key=CONF.inbound_queue)

    def __init__(self, connection):

        self.connection = connection

    def get_consumers(self, Consumer, channel):

        return [
            Consumer(queues=[self.queue], callbacks=[self.on_message])
        ]

    def on_message(self, body, message):
        LOG.debug('Received message: {0!r}'.format(body))
        message.ack()
