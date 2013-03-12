
import time
import json

from dynect.DynectDNS import DynectRest

from alerta.common import config
from alerta.common import log as logging
from alerta.common.daemon import Daemon
from alerta.alert import Alert, Heartbeat, severity
from alerta.common.mq import Messaging

Version = '2.0.0'

LOG = logging.getLogger(__name__)
CONF = config.CONF


class DynectDaemon(Daemon):

    def __init__(self, prog):

        Daemon.__init__(self, prog)

        self.info = {}
        self.last_info = {}
        self.count = {}
        self.updating = False
        self.last_change = {}

    def run(self):

        self.running = True

        # Connect to message queue
        self.mq = Messaging()
        self.mq.connect()

        while not self.shuttingdown:
            try:
                self.queryDynect()

                if self.updating:
                    self.alertDynect()
                    self.last_info = self.info

                    LOG.debug('Send heartbeat...')
                    heartbeat = Heartbeat(version=Version)
                    self.mq.send(heartbeat)

                LOG.debug('Waiting for next check run...')
                time.sleep(CONF.loop_every)
            except (KeyboardInterrupt, SystemExit):
                self.shuttingdown = True

        self.running = False

    def alertDynect(self):

        for resource in self.info:

            LOG.debug('resource %s', resource)

            if resource not in self.last_info:
                self.count[resource] = 0
                continue
            LOG.debug('info => %s', self.info[resource])
            LOG.debug('last info => %s', self.last_info[resource])

            self.count[resource] += 1
            LOG.debug('count = %s', self.count[resource])

            if (self.last_info[resource]['status'] != self.info[resource]['status']
                    or self.count[resource] == 1 or self.count[resource] % 10):

                if resource.startswith('gslb-'):

                    # gslb status       = ok | unk | trouble | failover

                    text = 'GSLB status is %s.' % self.info[resource]['status']

                    if self.info[resource]['status'] == 'ok':
                        event = 'GslbOK'
                        sev = severity.NORMAL
                    else:
                        event = 'GslbNotOK'
                        sev = severity.CRITICAL
                    correlate = ['GslbOK', 'GslbNotOK']

                elif resource.startswith('pool-'):

                    # pool status       = up | unk | down
                    # pool serve_mode   = obey | always | remove | no
                    # pool weight	(1-15)

                    if 'down' in self.info[resource]['status']:
                        event = 'PoolDown'
                        sev = severity.MAJOR
                        text = 'Pool is down'
                    elif 'obey' not in self.info[resource]['status']:
                        event = 'PoolServe'
                        sev = severity.MAJOR
                        text = 'Pool with an incorrect serve mode'
                    elif self.check_weight(self.info[resource]['gslb'], resource) is False:
                        event = 'PoolWeightError'
                        sev = severity.MINOR
                        text = 'Pool with an incorrect weight'
                    else:
                        event = 'PoolUp'
                        sev = severity.NORMAL
                        text = 'Pool status is normal'
                    correlate = ['PoolUp', 'PoolDown', 'PoolServe', 'PoolWeightError']

                else:
                    LOG.warning('Unknown resource type: %s', resource)
                    continue

                # Defaults
                group = 'GSLB'
                value = self.info[resource]['status']
                environment = ['PROD']
                service = ['Network']
                tags = list()
                timeout = None
                threshold_info = None
                summary = None
                raw_data = self.info[resource]['rawData']

                dynectAlert = Alert(
                    resource=resource,
                    event=event,
                    correlate=correlate,
                    group=group,
                    value=value,
                    severity=sev,
                    environment=environment,
                    service=service,
                    text=text,
                    event_type='dynectAlert',
                    tags=tags,
                    timeout=timeout,
                    threshold_info=threshold_info,
                    summary=summary,
                    raw_data=raw_data,
                )

                self.mq.send(dynectAlert)

    def check_weight(self, parent, resource):
        
        weight = self.info[resource]['status'].split(':')[2]

        for pool in [resource for resource in self.info if resource.startswith('pool') and self.info[resource]['gslb'] == parent]:
            LOG.debug('pool %s weight %s <=> %s', pool, self.info[pool]['status'].split(':')[2], weight)
            if self.info[pool]['status'].split(':')[2] != weight:
                return False
        return True

    def queryDynect(self):

        LOG.info('Query DynECT to get the state of GSLBs')
        try:
            rest_iface = DynectRest()
            if CONF.debug and CONF.use_stderr:
                rest_iface.verbose = True

            # login
            credentials = {
                'customer_name': CONF.dynect_customer,
                'user_name': CONF.dynect_username,
                'password': CONF.dynect_password,
            }
            LOG.debug('credentials = %s', credentials)
            response = rest_iface.execute('/Session/', 'POST', credentials)

            if response['status'] != 'success':
                LOG.error('Failed to create API session: %s', response['msgs'][0]['INFO'])
                self.updating = False
                return

            # Discover all the Zones in DynECT
            response = rest_iface.execute('/Zone/', 'GET')
            LOG.debug('/Zone/ => %s', json.dumps(response, indent=4))
            zone_resources = response['data']

            # Discover all the LoadBalancers
            for resource in zone_resources:
                zone = resource.split('/')[3]  # eg. /REST/Zone/guardiannews.com/
                response = rest_iface.execute('/LoadBalance/' + zone + '/', 'GET')
                LOG.debug('/LoadBalance/%s/ => %s', zone, json.dumps(response, indent=4))
                gslb = response['data']

                # Discover LoadBalancer pool information.
                for lb in gslb:
                    fqdn = lb.split('/')[4]  # eg. /REST/LoadBalance/guardiannews.com/id.guardiannews.com/
                    response = rest_iface.execute('/LoadBalance/' + zone + '/' + fqdn + '/', 'GET')
                    LOG.debug('/LoadBalance/%s/%s/ => %s', zone, fqdn, json.dumps(response, indent=4))
                    status = response['data']['status']
                    monitor = response['data']['monitor']
                    self.info['gslb-' + fqdn] = {'status': status, 'gslb': fqdn, 'rawData': monitor}

                    for pool in response['data']['pool']:
                        name = '%s-%s' % (fqdn, pool['label'].replace(' ', '-'))
                        status = '%s:%s:%s' % (pool['status'], pool['serve_mode'], pool['weight'])
                        self.info['pool-' + name] = {'status': status, 'gslb': fqdn, 'rawData': pool}

            LOG.info('Finish quering and object discovery.')
            LOG.info('GSLBs and Pools: %s', json.dumps(self.info, indent=4))

            # logout
            rest_iface.execute('/Session/', 'DELETE')

        except Exception, e:
            LOG.error('Failed to discover GSLBs: %s', e)
            self.updating = False

        self.updating = True



