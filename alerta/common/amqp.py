
import json

from kombu import BrokerConnection, Exchange, Queue, Producer, Consumer
from kombu.mixins import ConsumerMixin
# from kombu.utils.debug import setup_logging

from alerta.common import log as logging
from alerta.common import config
from alerta.common.utils import DateEncoder

LOG = logging.getLogger(__name__)
CONF = config.CONF


class Connection(object):

    amqp_opts = {
        'amqp_queue': 'alerts',
        'amqp_topic': 'notify',
        'amqp_url': 'amqp://guest:guest@localhost:5672//',  # RabbitMQ
        # 'amqp_url': 'mongodb://localhost:27017/kombu',    # MongoDB
        # 'amqp_url': 'redis://localhost:6379/',            # Redis
    }

    def __init__(self):

        config.register_opts(Connection.amqp_opts)

        self.connection = None
        self.channel = None
        self.connect()

    def connect(self):

        self.connection = BrokerConnection(CONF.amqp_url)
        self.connection.connect()
        self.channel = self.connection.channel()

    def disconnect(self):

        return self.connection.release()

    def is_connected(self):

        return self.connection.connected


class DirectPublisher(object):

    def __init__(self, channel, name=None):

        config.register_opts(Connection.amqp_opts)

        self.channel = channel
        self.exchange_name = name or CONF.amqp_queue

        self.exchange = Exchange(name=self.exchange_name, type='direct', channel=self.channel, durable=True)
        self.producer = Producer(exchange=self.exchange, channel=self.channel, serializer='json')

    def send(self, msg):

        self.producer.publish(json.dumps(msg.get_body(), cls=DateEncoder), exchange=self.exchange,
                              serializer='json', declare=[self.exchange], routing_key=self.exchange_name)


class FanoutPublisher(object):

    def __init__(self, channel, name=None):

        config.register_opts(Connection.amqp_opts)

        self.channel = channel
        self.exchange_name = name or CONF.amqp_topic

        self.exchange = Exchange(name=self.exchange_name, type='fanout', channel=self.channel)
        self.producer = Producer(exchange=self.exchange, channel=self.channel, serializer='json')

    def send(self, msg):

        self.producer.publish(json.dumps(msg.get_body(), cls=DateEncoder), exchange=self.exchange,
                              serializer='json', declare=[self.exchange])


class DirectConsumer(ConsumerMixin):

    config.register_opts(Connection.amqp_opts)

    def __init__(self, connection):

        self.connection = connection
        self.channel = self.connection.channel()
        self.exchange = Exchange(CONF.amqp_queue, 'direct', channel=self.channel, durable=True)
        self.queue = Queue(CONF.amqp_queue, exchange=self.exchange, routing_key=CONF.amqp_queue, channel=self.channel)

    def get_consumers(self, Consumer, channel):

        return [
            Consumer(queues=[self.queue], callbacks=[self.on_message])
        ]

    def on_message(self, body, message):
        LOG.debug('Received queue message: {0!r}'.format(body))
        message.ack()


class FanoutConsumer(ConsumerMixin):

    config.register_opts(Connection.amqp_opts)

    def __init__(self, connection):

        self.connection = connection
        self.channel = self.connection.channel()
        self.exchange = Exchange(CONF.amqp_topic, 'fanout', channel=self.channel, durable=True)
        self.queue = Queue('', exchange=self.exchange, routing_key='', channel=self.channel, exclusive=True)

    def get_consumers(self, Consumer, channel):

        return [
            Consumer(queues=[self.queue], callbacks=[self.on_message])
        ]

    def on_message(self, body, message):
        LOG.debug('Received topic message: {0!r}'.format(body))
        message.ack()
