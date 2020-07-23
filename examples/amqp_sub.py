#!/usr/bin/env python

from commlib.transports.amqp import Subscriber, ConnectionParameters
# from commlib.endpoints import AMQPSubscriber
# from commlib.transports.amqp import ConnectionParameters
import time


def callback(msg, meta):
    print('Message: {}'.format(msg))


if __name__ == '__main__':
    topic_name = 'testtopic'
    conn_params = ConnectionParameters()
    conn_params.credentials.username = 'testuser'
    conn_params.credentials.password = 'testuser'
    conn_params.host = 'r4a-platform.ddns.net'
    conn_params.port = 5782
    s = Subscriber(conn_params=conn_params,
                   topic=topic_name,
                   on_message=callback)
    s.run()
    while True:
        time.sleep(0.001)