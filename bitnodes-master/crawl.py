#
# crawl.py - Greenlets-based Bitcoin network crawler.
#
# Copyright (c) Addy Yeow Chin Heng <ayeowch@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""
Greenlets-based Bitcoin network crawler.
"""

from gevent import monkey
monkey.patch_all()

import geoip2.database
import gevent
import json
import logging
import os
import redis
import redis.connection
import requests
import socket
import sys
import time
from base64 import b32decode
from binascii import hexlify, unhexlify
from collections import Counter
from ConfigParser import ConfigParser
from geoip2.errors import AddressNotFoundError
from ipaddress import ip_address, ip_network

from protocol import (
    ONION_PREFIX,
    TO_SERVICES,
    Connection,
    ConnectionError,
    ProtocolError,
)
from utils import new_redis_conn, get_keys, ip_to_network
import psycopg2
from itertools import cycle

redis.connection.socket = gevent.socket

REDIS_CONN = None
CONF = {}
cyclingBlockchainList = cycle([])
all_seeders_d = {}
all_magics_d = {}
all_ports_d = {}
all_versions_d = {}
all_chains = []

# MaxMind databases
ASN = geoip2.database.Reader("geoip/GeoLite2-ASN.mmdb")

#TODO: alter enumerate and connect methods to keep track of who-knows-who along with an RTT (round trip time) value
# i.e SOURCE_IP | SINK_IP | SINK_PORT | TIMESTAMP | HEIGHT | BLOCKCHAIN
# Redis key could be somethink like glinks-SOURCE_NODE_IP
# with values all the rest
def enumerate_node(redis_pipe, addr_msgs, now, source_ip):
    """
    Adds all peering nodes with age <= max. age into the crawl set.
    """
    peers = 0
    excluded = 0

    for addr_msg in addr_msgs:
        if 'addr_list' in addr_msg:
            for peer in addr_msg['addr_list']:
                age = now - peer['timestamp']  # seconds

                if age >= 0 and age <= CONF['max_age']:
                    address = peer['ipv4'] or peer['ipv6'] or peer['onion']
                    port = peer['port'] if peer['port'] > 0 else CONF['port']
                    services = peer['services']
                    if not address:
                        continue
                    if is_excluded(address):
                        logging.debug("Exclude: (%s, %d)", address, port)
                        excluded += 1
                        continue
                    #print("Redis ---> Adding in set: "+'glinks-{}-{}'.format(source_ip[0], CONF['BLOCKCHAIN']))
                    redis_pipe.sadd('glinks-{}-{}'.format(source_ip[0], CONF['BLOCKCHAIN']),
                                    '{}-{}-{}'.format(address, port, now))
                    redis_pipe.sadd('pending', (address, port, services))
                    peers += 1
                    if peers >= CONF['peers_per_node']:
                        return (peers, excluded)

    return (peers, excluded)


def connect(redis_conn, key):
    """
    Establishes connection with a node to:
    1) Send version message
    2) Receive version and verack message
    3) Send getaddr message
    4) Receive addr message containing list of peering nodes
    Stores state and height for node in Redis.
    """
    handshake_msgs = []
    addr_msgs = []

    redis_conn.set(key, "")  # Set Redis key for a new node

    (address, port, services) = key[5:].split("-", 2)
    services = int(services)
    height = redis_conn.get('height')
    if height:
        height = int(height)

    proxy = None
    if address.endswith(".onion"):
        proxy = CONF['tor_proxy']
    #FOR RTT CALC
    start = int(time.time())
    conn = Connection((address, int(port)),
                      (CONF['source_address'], 0),
                      magic_number=CONF['magic_number'],
                      socket_timeout=CONF['socket_timeout'],
                      proxy=proxy,
                      protocol_version=CONF['protocol_version'],
                      to_services=services,
                      from_services=CONF['services'],
                      user_agent=CONF['user_agent'],
                      height=height,
                      relay=CONF['relay'])
    try:
        logging.debug("Connecting to %s", conn.to_addr)
        conn.open()
        handshake_msgs = conn.handshake()
    except (ProtocolError, ConnectionError, socket.error) as err:
        logging.debug("%s: %s", conn.to_addr, err)

    redis_pipe = redis_conn.pipeline()
    if len(handshake_msgs) > 0:
        try:
            conn.getaddr(block=False)
        except (ProtocolError, ConnectionError, socket.error) as err:
            logging.debug("%s: %s", conn.to_addr, err)
        else:
            addr_wait = 0
            while addr_wait < CONF['socket_timeout']:
                addr_wait += 1
                gevent.sleep(0.3)
                try:
                    msgs = conn.get_messages(commands=['addr'])
                except (ProtocolError, ConnectionError, socket.error) as err:
                    logging.debug("%s: %s", conn.to_addr, err)
                    break
                if msgs and any([msg['count'] > 1 for msg in msgs]):
                    addr_msgs = msgs
                    break

        version_msg = handshake_msgs[0]
        from_services = version_msg.get('services', 0)
        if from_services != services:
            logging.debug("%s Expected %d, got %d for services", conn.to_addr,
                          services, from_services)
            key = "node:{}-{}-{}".format(address, port, from_services)
        height_key = "height:{}-{}-{}".format(address, port, from_services)
        #redis_pipe.setex(height_key, CONF['max_age'], version_msg.get('height', 0))
        #ARIS EDIT:
        redis_pipe.setex(height_key, version_msg.get('height', 0), CONF['max_age'], )
        now = int(time.time())
        (peers, excluded) = enumerate_node(redis_pipe, addr_msgs, now, conn.to_addr)

        logging.debug("%s Peers: %d (Excluded: %d)",
                      conn.to_addr, peers, excluded)
        redis_pipe.set(key, version_msg.get('version', 0))
        #redis_pipe.sadd('up', key)
        redis_pipe.sadd('up-{}'.format(CONF['BLOCKCHAIN']), key )
    conn.close()
    redis_pipe.execute()


def dump(timestamp, nodes):
    """
    Dumps data for reachable nodes into timestamp-prefixed JSON file and
    returns most common height from the nodes.
    """
    json_data = []

    logging.info('Building JSON data')
    for node in nodes:
        (address, port, services) = node[5:].split("-", 2)
        height_key = "height:{}-{}-{}".format(address, port, services)
        try:
            height = int(REDIS_CONN.get(height_key))
        except TypeError:
            logging.warning("%s missing", height_key)
            height = 0
        json_data.append([address, int(port), int(services), height])
    logging.info('Built JSON data: %d', len(json_data))

    if len(json_data) == 0:
        logging.warning("len(json_data): %d", len(json_data))
        return 0

    json_output = os.path.join(CONF['crawl_dir'], "{}.json".format(timestamp))
    open(json_output, 'w').write(json.dumps(json_data))
    logging.info("Wrote %s", json_output)

    return Counter([node[-1] for node in json_data]).most_common(1)[0][0]

def test_conn():
    print("Test PG Connection")
    dbconn = psycopg2.connect(user="postgres",
                                  password="postgres",
                                  host="testpg",
                                  #host="pg_docker",
                                  #host='localhost',
                                  port="5432",
                                  database = "btc_crawl")
    cursor = dbconn.cursor()
    cursor.execute("select * from all_nodes limit 1")
    row = cursor.fetchone()
    print(row)
    cursor.close()
    dbconn.close()


def restart(timestamp, start, elapsed):
    """
    Dumps data for the reachable nodes into a JSON file.
    Loads all reachable nodes from Redis into the crawl set.
    Removes keys for all nodes from current crawl.
    Updates excluded networks with current list of bogons.
    Updates number of reachable nodes and most common height in Redis.

    EDIT this; not only json dump. also export to Postgres.
    """
    #connection to Postgres Db:
    dbconn = psycopg2.connect(user="postgres",
                                  password="postgres",
                                  host="testpg",
                                  #host="pg_docker",
                                  #host='localhost',
                                  port="5432",
                                  database = "btc_crawl")
    cursor = dbconn.cursor()



    redis_pipe = REDIS_CONN.pipeline()

    #nodes = REDIS_CONN.smembers('up')  # Reachable nodes
    nodes = REDIS_CONN.smembers('up-{}'.format(CONF['BLOCKCHAIN']))
    #redis_pipe.delete('up')
    redis_pipe.delete('up-{}'.format(CONF['BLOCKCHAIN']))

    # Insert all nodes in all_nodes table;
    # Also, count them and make an insertion in master status - reachable
    
    for node in nodes:
        (address, port, services) = node[5:].split("-", 2)
        redis_pipe.sadd('pending', (address, int(port), int(services)))

        height_key = "height:{}-{}-{}".format(address, port, services)
        height = 0
        try:
            height = int(REDIS_CONN.get(height_key))
        except TypeError:
            logging.warning("%s missing", height_key)
            height = 0

        version = REDIS_CONN.get(node)

        glinks = REDIS_CONN.smembers('glinks-{}-{}'.format(address, CONF['BLOCKCHAIN']))
        #JUST A REMINDER:
        #redis_pipe.sadd('glinks-{}-{}'.format(source_ip, CONF['BLOCKCHAIN']), '{}-{}-{}'.format( address, port, now))
        #print("GLINKS LEN for node %s on chain %s: %d" %(address, CONF['BLOCKCHAIN'], len(glinks)))
        for link in glinks:
            l = link.split("-")
            #print(l)
            cursor.execute("INSERT INTO GRAPH_LINKS (SOURCE_IP, BLOCKCHAIN, SINK_IP, SINK_PORT, TIMESTAMP) "
                           "VALUES (%s, %s, %s, %s, %s) "
                           "ON CONFLICT (source_ip, blockchain, sink_ip, sink_port) "
                           "DO UPDATE SET TIMESTAMP=EXCLUDED.TIMESTAMP", (  str(address), CONF['BLOCKCHAIN'], str(l[0]), int(l[1]), int(l[2])  ) )
        dbconn.commit()

        redis_pipe.delete('glinks-{}-{}'.format(address, CONF['BLOCKCHAIN']))
        cursor.execute("INSERT INTO ALL_NODES (IP_ADDRESS, PORT, SERVICES, HEIGHT, LAST_SEEN, VERSION, MY_IP, BLOCKCHAIN) VALUES(%s, %s, %s, %s, %s, %s, %s, %s) "
                       "on conflict (IP_ADDRESS, PORT, services, blockchain) DO UPDATE set last_seen=excluded.last_seen, height=excluded.height, version=excluded.version",
            ( str(address), port, str(services), height, timestamp, str(version), CONF['MY_IP'], CONF['BLOCKCHAIN']) )

    dbconn.commit()


    for key in get_keys(REDIS_CONN, 'node:*'):
        redis_pipe.delete(key)

        
    for key in get_keys(REDIS_CONN, 'crawl:cidr:*'):
        redis_pipe.delete(key)

    if CONF['include_checked']:
        checked_nodes = REDIS_CONN.zrangebyscore(
            'check', timestamp - CONF['max_age'], timestamp)
        for node in checked_nodes:
            (address, port, services) = eval(node)
            if is_excluded(address):
                logging.debug("Exclude: %s", address)
                continue
            redis_pipe.sadd('pending', (address, port, services))

    redis_pipe.execute()

    update_excluded_networks()

    reachable_nodes = len(nodes)
    logging.info("Reachable nodes: %d", reachable_nodes)
    REDIS_CONN.lpush('nodes', (timestamp, reachable_nodes))

    height = dump(timestamp, nodes)
    REDIS_CONN.set('height', height)
    logging.info("Height: %d", height)

    cursor.execute("INSERT INTO MASTER_STATUS (STATUS, START_TIME, ELAPSED_TIME, HEIGHT, BLOCKCHAIN, REACHABLE_NODES)"
                   "VALUES(%s, %s, %s, %s, %s, %s)", ('Finished', start, elapsed, height, CONF['BLOCKCHAIN'], reachable_nodes ))
    dbconn.commit()
    cursor.close()
    dbconn.close()

    CONF['BLOCKCHAIN'] = cyclingBlockchainList.next()
    REDIS_CONN.set('crawl:master:blockchain', CONF['BLOCKCHAIN'])
    logging.info("Change Chain: %s", CONF['BLOCKCHAIN'])
    set_bchain_params()
    set_pending()


def cron():
    """
    Assigned to a worker to perform the following tasks periodically to
    maintain a continuous crawl:
    1) Reports the current number of nodes in crawl set
    2) Initiates a new crawl once the crawl set is empty
    """
    start = int(time.time())

    while True:
        pending_nodes = REDIS_CONN.scard('pending')
        logging.info("Pending: %d", pending_nodes)

        if pending_nodes == 0:
            REDIS_CONN.set('crawl:master:state', "starting")
            now = int(time.time())
            elapsed = now - start
            REDIS_CONN.set('elapsed', elapsed)
            logging.info("Elapsed: %d", elapsed)
            logging.info("Restarting")
            restart(now, start, elapsed)
            while int(time.time()) - start < CONF['snapshot_delay']:
                gevent.sleep(1)
            start = int(time.time())
            REDIS_CONN.set('crawl:master:state', "running")

        gevent.sleep(CONF['cron_delay'])


def task():
    """
    Assigned to a worker to retrieve (pop) a node from the crawl set and
    attempt to establish connection with a new node.
    """
    redis_conn = new_redis_conn(db=CONF['db'])

    while True:
        if not CONF['master']:
            if CONF['BLOCKCHAIN'] != REDIS_CONN.get('crawl:master:blockchain'):
                CONF['BLOCKCHAIN'] = REDIS_CONN.get('crawl:master:blockchain')
                set_bchain_params()
            while REDIS_CONN.get('crawl:master:state') != "running":
                gevent.sleep(CONF['socket_timeout'])

        node = redis_conn.spop('pending')  # Pop random node from set
        if node is None:
            gevent.sleep(1)
            continue

        node = eval(node)  # Convert string from Redis to tuple

        # Skip IPv6 node
        if ":" in node[0] and not CONF['ipv6']:
            continue

        key = "node:{}-{}-{}".format(node[0], node[1], node[2])
        if redis_conn.exists(key):
            continue

        # Check if prefix has hit its limit
        if ":" in node[0] and CONF['ipv6_prefix'] < 128:
            cidr = ip_to_network(node[0], CONF['ipv6_prefix'])
            nodes = redis_conn.incr('crawl:cidr:{}'.format(cidr))
            if nodes > CONF['nodes_per_ipv6_prefix']:
                logging.debug("CIDR %s: %d", cidr, nodes)
                continue

        connect(redis_conn, key)


def set_pending():
    """
    Initializes pending set in Redis with a list of reachable nodes from DNS
    seeders and hardcoded list of .onion nodes to bootstrap the crawler.
    """
    for seeder in CONF['seeders']:
        nodes = []

        try:
            ipv4_nodes = socket.getaddrinfo(seeder, None, socket.AF_INET)
        except socket.gaierror as err:
            logging.warning("%s", err)
        else:
            nodes.extend(ipv4_nodes)

        if CONF['ipv6']:
            try:
                ipv6_nodes = socket.getaddrinfo(seeder, None, socket.AF_INET6)
            except socket.gaierror as err:
                logging.warning("%s", err)
            else:
                nodes.extend(ipv6_nodes)

        for node in nodes:
            address = node[-1][0]
            if is_excluded(address):
                logging.debug("Exclude: %s", address)
                continue
            logging.debug("%s: %s", seeder, address)
            REDIS_CONN.sadd('pending', (address, CONF['port'], TO_SERVICES))

    if CONF['onion']:
        for address in CONF['onion_nodes']:
            REDIS_CONN.sadd('pending', (address, CONF['port'], TO_SERVICES))


def is_excluded(address):
    """
    Returns True if address is found in exclusion list, False if otherwise.
    """
    if address.endswith(".onion"):
        address = onion_to_ipv6(address)
    elif ip_address(unicode(address)).is_private:
        return True

    if ":" in address:
        address_family = socket.AF_INET6
        key = 'exclude_ipv6_networks'
    else:
        address_family = socket.AF_INET
        key = 'exclude_ipv4_networks'

    try:
        asn_record = ASN.asn(address)
    except AddressNotFoundError:
        asn = None
    else:
        asn = 'AS{}'.format(asn_record.autonomous_system_number)

    try:
        addr = int(hexlify(socket.inet_pton(address_family, address)), 16)
    except socket.error:
        logging.warning("Bad address: %s", address)
        return True

    if any([(addr & net[1] == net[0]) for net in CONF[key]]):
        return True

    if asn and asn in CONF['exclude_asns']:
        return True

    return False


def onion_to_ipv6(address):
    """
    Returns IPv6 equivalent of an .onion address.
    """
    ipv6_bytes = ONION_PREFIX + b32decode(address[:-6], True)
    return socket.inet_ntop(socket.AF_INET6, ipv6_bytes)


def list_excluded_networks(txt, networks=None):
    """
    Converts list of networks from configuration file into a list of tuples of
    network address and netmask to be excluded from the crawl.
    """
    if networks is None:
        networks = set()
    lines = txt.strip().split("\n")
    for line in lines:
        line = line.split('#')[0].strip()
        try:
            network = ip_network(unicode(line))
        except ValueError:
            continue
        else:
            networks.add((int(network.network_address), int(network.netmask)))
    return networks


def update_excluded_networks():
    """
    Adds bogons into the excluded IPv4 and IPv6 networks.
    """
    if CONF['exclude_ipv4_bogons']:
        urls = [
            "http://www.team-cymru.org/Services/Bogons/fullbogons-ipv4.txt",
        ]
        for url in urls:
            try:
                response = requests.get(url, timeout=15)
            except requests.exceptions.RequestException as err:
                logging.warning(err)
            else:
                if response.status_code == 200:
                    CONF['exclude_ipv4_networks'] = list_excluded_networks(
                        response.content,
                        networks=CONF['exclude_ipv4_networks'])
                    logging.info("IPv4: %d",
                                 len(CONF['exclude_ipv4_networks']))

    if CONF['exclude_ipv6_bogons']:
        urls = [
            "http://www.team-cymru.org/Services/Bogons/fullbogons-ipv6.txt",
        ]
        for url in urls:
            try:
                response = requests.get(url, timeout=15)
            except requests.exceptions.RequestException as err:
                logging.warning(err)
            else:
                if response.status_code == 200:
                    CONF['exclude_ipv6_networks'] = list_excluded_networks(
                        response.content,
                        networks=CONF['exclude_ipv6_networks'])
                    logging.info("IPv6: %d",
                                 len(CONF['exclude_ipv6_networks']))


def set_bchain_params():
    #SET seeders, magic_number and port IAW current blockchain:

    CONF['seeders'] = all_seeders_d[CONF['BLOCKCHAIN']]
    CONF['magic_number'] = all_magics_d[CONF['BLOCKCHAIN']]
    CONF['port'] = all_ports_d[CONF['BLOCKCHAIN']]
    CONF['protocol_version'] = all_versions_d[CONF['BLOCKCHAIN']]



def load_all_chains(argv):
    global all_chains
    global cyclingBlockchainList
    global all_seeders_d
    global all_magics_d
    global all_ports_d
    global all_versions_d

    conf = ConfigParser()
    conf.read(argv[1])
    chains_params = conf.get('crawl', 'all-chains').strip().split("\n")

    all_chains_params_d = {}

    for c in chains_params:
        cparams = c.split(":")
        #_________________________________ seeders     magic_nums  ports       protocol_version
        all_chains_params_d[cparams[0]] = (cparams[1], cparams[2], cparams[3], cparams[4])

    cyclingBlockchainList = cycle(all_chains_params_d.keys())
    all_chains = all_chains_params_d.keys()

    all_seeders_d = {}
    for c in all_chains_params_d.keys():
        all_seeders_d[c] = conf.get('crawl', all_chains_params_d[c][0]).strip().split("\n")

    all_magics_d = {}
    for c in all_chains_params_d.keys():
        all_magics_d[c] = unhexlify(conf.get('crawl', all_chains_params_d[c][1]))

    all_ports_d = {}
    for c in all_chains_params_d.keys():
        all_ports_d[c] = conf.getint('crawl', all_chains_params_d[c][2])

    all_versions_d = {}
    for c in all_chains_params_d.keys():
        all_versions_d[c] = conf.getint('crawl', all_chains_params_d[c][3])


def init_conf(argv):
    """
    Populates CONF with key-value pairs from configuration file.
    """
    load_all_chains(argv)

    conf = ConfigParser()
    conf.read(argv[1])

    '''
        First, Choose a blockchain and the appropriate seeders.
    '''
    #CONF['BLOCKCHAIN'] = conf.get('crawl', 'blockchain').strip()
    CONF['BLOCKCHAIN'] = cyclingBlockchainList.next()
    set_bchain_params()

    CONF['logfile'] = conf.get('crawl', 'logfile')


    #CONF['seeders'] = conf.get('crawl', 'seeders').strip().split("\n")
    #CONF['magic_number'] = unhexlify(conf.get('crawl', 'magic_number'))
    #c = conf.getint('crawl', 'port')

    CONF['db'] = conf.getint('crawl', 'db')
    CONF['workers'] = conf.getint('crawl', 'workers')
    CONF['debug'] = conf.getboolean('crawl', 'debug')
    CONF['source_address'] = conf.get('crawl', 'source_address')
    #CONF['protocol_version'] = conf.getint('crawl', 'protocol_version')
    CONF['user_agent'] = conf.get('crawl', 'user_agent')
    CONF['services'] = conf.getint('crawl', 'services')
    CONF['relay'] = conf.getint('crawl', 'relay')
    CONF['socket_timeout'] = conf.getint('crawl', 'socket_timeout')
    CONF['cron_delay'] = conf.getint('crawl', 'cron_delay')
    CONF['snapshot_delay'] = conf.getint('crawl', 'snapshot_delay')
    CONF['max_age'] = conf.getint('crawl', 'max_age')
    CONF['peers_per_node'] = conf.getint('crawl', 'peers_per_node')
    CONF['ipv6'] = conf.getboolean('crawl', 'ipv6')
    CONF['ipv6_prefix'] = conf.getint('crawl', 'ipv6_prefix')
    CONF['nodes_per_ipv6_prefix'] = conf.getint('crawl',
                                                'nodes_per_ipv6_prefix')

    CONF['exclude_asns'] = conf.get('crawl',
                                    'exclude_asns').strip().split("\n")

    CONF['exclude_ipv4_networks'] = list_excluded_networks(
        conf.get('crawl', 'exclude_ipv4_networks'))
    CONF['exclude_ipv6_networks'] = list_excluded_networks(
        conf.get('crawl', 'exclude_ipv6_networks'))

    CONF['exclude_ipv4_bogons'] = conf.getboolean('crawl',
                                                  'exclude_ipv4_bogons')
    CONF['exclude_ipv6_bogons'] = conf.getboolean('crawl',
                                                  'exclude_ipv6_bogons')

    CONF['onion'] = conf.getboolean('crawl', 'onion')
    CONF['tor_proxy'] = None
    if CONF['onion']:
        tor_proxy = conf.get('crawl', 'tor_proxy').split(":")
        CONF['tor_proxy'] = (tor_proxy[0], int(tor_proxy[1]))
    CONF['onion_nodes'] = conf.get('crawl', 'onion_nodes').strip().split("\n")

    CONF['include_checked'] = conf.getboolean('crawl', 'include_checked')

    CONF['crawl_dir'] = conf.get('crawl', 'crawl_dir')
    if not os.path.exists(CONF['crawl_dir']):
        os.makedirs(CONF['crawl_dir'])

    # Set to True for master process
    CONF['master'] = argv[2] == "master"
    CONF['MY_IP'] = 'dicl15.cut.ac.cy'


def main(argv):
    test_conn()

    if len(argv) < 3 or not os.path.exists(argv[1]):
        print("Usage: crawl.py [config] [master|slave]")
        return 1

    # Initialize global conf
    init_conf(argv)

    # Initialize logger
    loglevel = logging.INFO
    if CONF['debug']:
        loglevel = logging.DEBUG

    logformat = ("[%(process)d] %(asctime)s,%(msecs)05.1f %(levelname)s "
                 "(%(funcName)s) %(message)s")
    logging.basicConfig(level=loglevel,
                        format=logformat,
                        filename=CONF['logfile'],
                        filemode='a')
    print("Log: {}, press CTRL+C to terminate..".format(CONF['logfile']))

    global REDIS_CONN
    REDIS_CONN = new_redis_conn(db=CONF['db'])

    if CONF['master']:
        REDIS_CONN.set('crawl:master:state', "starting")
        REDIS_CONN.set('crawl:master:blockchain', CONF['BLOCKCHAIN'])
        logging.info("Removing all keys")
        redis_pipe = REDIS_CONN.pipeline()
        for b in all_chains:
            redis_pipe.delete('up-{}'.format(b))
        redis_pipe.delete('up')
        for key in get_keys(REDIS_CONN, 'node:*'):
            redis_pipe.delete(key)
        for key in get_keys(REDIS_CONN, 'crawl:cidr:*'):
            redis_pipe.delete(key)
        redis_pipe.delete('pending')
        redis_pipe.execute()
        set_pending()
        update_excluded_networks()
        REDIS_CONN.set('crawl:master:state', "running")

    # Spawn workers (greenlets) including one worker reserved for cron tasks
    workers = []
    if CONF['master']:
        workers.append(gevent.spawn(cron))
    for _ in xrange(CONF['workers'] - len(workers)):
        workers.append(gevent.spawn(task))
    logging.info("Workers: %d", len(workers))
    gevent.joinall(workers)

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
