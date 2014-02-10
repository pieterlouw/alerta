
from flask import Flask

from alerta.common import config
from alerta.common import log as logging
from alerta.common.amqp import Connection, DirectPublisher
from alerta.server.database import Mongo

Version = '2.1.0'

LOG = logging.getLogger(__name__)
CONF = config.CONF

config.parse_args(version=Version)
logging.setup('alerta')

app = Flask(__name__)
app.config.from_object(__name__)
db = Mongo()

mq = Connection()
pub = DirectPublisher(mq.connection)

import views
import management.views

