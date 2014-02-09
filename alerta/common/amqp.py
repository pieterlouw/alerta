
import json

from kombu import Connection, Exchange, Queue
from kombu.mixins import ConsumerMixin
# from kombu.utils.debug import setup_logging

from alerta.common import log as logging
from alerta.common import config
from alerta.common.utils import DateEncoder

LOG = logging.getLogger(__name__)
CONF = config.CONF


class MessageQueue(object):

    mq_opts = {
        'inbound_queue': 'alerts',
        'outbound_topic': 'notify',

        'rabbit_host': 'localhost',
        'rabbit_port': 5672,
        'rabbit_use_ssl': False,
        'rabbit_userid': 'guest',
        'rabbit_password': 'guest',
        'rabbit_virtual_host': '/',
    }

    def __init__(self, name):

        config.register_opts(MessageQueue.mq_opts)

        self.name = name
        self.conn = None
        self.connect()

    def connect(self):

        LOG.critical('connecting to %s', self.name)

        self.conn = Connection('amqp://%s:%s@%s:%d/%s' % (CONF.rabbit_userid, CONF.rabbit_password,
                               CONF.rabbit_host, CONF.rabbit_port, CONF.rabbit_virtual_host))
        self.conn.connect()

    def send(self, msg, timeout=None):

        pass

    def disconnect(self):

        return self.conn.release()

    def is_connected(self):

        return self.conn.connected


class DirectPublisher(MessageQueue):

    def __init__(self, name):

        self.exchange = Exchange(name, type='direct', durable=True)
        self.queue = Queue(name, exchange=self.exchange, routing_key=name)

        MessageQueue.__init__(self, name)

    def send(self, msg, timeout=None):

        self.producer = self.conn.Producer(exchange=self.exchange, serializer='json')
        self.producer.declare()
        self.producer.publish(json.dumps(msg.get_body(), cls=DateEncoder), exchange=self.exchange,
                              routing_key=self.name, declare=[self.queue])


class FanoutPublisher(MessageQueue):

    def __init__(self, name):

        self.exchange = Exchange(name, type='fanout', durable=True)
        self.queue = Queue(name, exchange=self.exchange, routing_key='')

        MessageQueue.__init__(self, name)

    def send(self, msg, timeout=None):

        self.producer = self.conn.Producer(exchange=self.exchange, serializer='json')
        self.producer.declare()
        self.producer.publish(json.dumps(msg.get_body(), cls=DateEncoder), exchange=self.exchange,
                              routing_key='', declare=[self.queue])


class DirectConsumer(ConsumerMixin):

    config.register_opts(MessageQueue.mq_opts)

    exchange = Exchange(CONF.inbound_queue, 'direct', durable=True)
    queue = Queue(CONF.inbound_queue, exchange=exchange, routing_key=CONF.inbound_queue)

    def __init__(self, connection):

        self.connection = connection

    def get_consumers(self, Consumer, channel):

        return [
            Consumer(queues=[self.queue], callbacks=[self.on_message])
        ]

    def on_message(self, body, message):
        LOG.debug('Received queue message: {0!r}'.format(body))
        message.ack()


class FanoutConsumer(ConsumerMixin):

    config.register_opts(MessageQueue.mq_opts)

    exchange = Exchange(CONF.outbound_topic, 'fanout', exclusive=True)
    queue = Queue(CONF.outbound_topic, exchange=exchange, routing_key=CONF.outbound_topic)

    def __init__(self, connection):

        self.connection = connection

    def get_consumers(self, Consumer, channel):

        return [
            Consumer(queues=[self.queue], callbacks=[self.on_message])
        ]

    def on_message(self, body, message):
        LOG.debug('Received topic message: {0!r}'.format(body))
        message.ack()
