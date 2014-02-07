
import json

from kombu import Connection, Exchange, Queue
from kombu.mixins import ConsumerMixin

from alerta.common import log as logging
from alerta.common import config
from alerta.common.utils import DateEncoder

LOG = logging.getLogger('stomp.py')
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

        self.conn = None
        self.exchange = Exchange(CONF.inbound_queue, 'fanout', durable=True)
        self.queue = Queue(CONF.inbound_queue, exchange=self.exchange, routing_key=CONF.inbound_queue)
        self.producer = None
        self.consumer = None
        self.callback = None

    def connect(self, callback=None, wait=False):

        print 'amqp://%s:%s@%s:%d/%s' % (CONF.rabbit_userid, CONF.rabbit_password,
                               CONF.rabbit_host, CONF.rabbit_port, CONF.rabbit_virtual_host)

        self.conn = Connection('amqp://%s:%s@%s:%d/%s' % (CONF.rabbit_userid, CONF.rabbit_password,
                               CONF.rabbit_host, CONF.rabbit_port, CONF.rabbit_virtual_host))
        self.conn.connect()

        self.producer = self.conn.Producer(exchange=self.exchange, serializer='json')
        self.producer.declare()

        self.consumer = self.conn.Consumer()

        self.callback = callback

    def reconnect(self):

        pass

    def subscribe(self, destination=None, ack='auto'):

        self.consumer.register_callback(self.callback)

    def send(self, msg, destination=None):

        self.producer.publish(json.dumps(msg.get_body(), cls=DateEncoder), exchange=self.exchange,
                              routing_key=CONF.inbound_queue, declare=[self.queue])

    def disconnect(self):

        return self.conn.release()

    def is_connected(self):

        return self.conn.connected


class MessageHandler(ConsumerMixin):

    exchange = Exchange(CONF.inbound_queue, 'fanout', durable=True)
    queue = Queue(CONF.inbound_queue, exchange=exchange, routing_key=CONF.inbound_queue)

    def __init__(self, connection):
        self.connection = connection

    def get_consumers(self, Consumer, channel):
        print 'registering callbacks'
        return [Consumer(queues=[self.queue],
                         callback=[self.on_message])]

    def on_message(self, body, message):
        print('Got alert: {0!r}'.format(body))
        message.ack()
