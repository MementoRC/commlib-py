import functools
import logging
import time
from enum import IntEnum
from typing import Any, Callable, Dict, Tuple

from commlib.action import (BaseActionClient, BaseActionService,
                            _ActionCancelMessage, _ActionFeedbackMessage,
                            _ActionGoalMessage, _ActionResultMessage,
                            _ActionStatusMessage)
from commlib.compression import CompressionType, deflate, inflate_str
from commlib.connection import BaseConnectionParameters
from commlib.events import BaseEventEmitter, Event
from commlib.exceptions import (MQTTError, RPCClientTimeoutError,
                                RPCRequestError)
from commlib.msg import PubSubMessage, RPCMessage
from commlib.pubsub import BasePublisher, BaseSubscriber
from commlib.rpc import (BaseRPCClient, BaseRPCServer, BaseRPCService,
                         CommRPCHeader, CommRPCMessage)
from commlib.serializer import JSONSerializer, Serializer
from commlib.transports import BaseTransport
from commlib.utils import gen_timestamp

kafka_logger: logging.Logger = logging.getLogger('kafka')


from confluent_kafka import (
    Consumer, Producer, KafkaError, KafkaException,
    OFFSET_BEGINNING, OFFSET_END
)


class ConnectionParameters(BaseConnectionParameters):
    host: str = 'localhost'
    port: int = 29092
    username: str = ''
    password: str = ''
    ssl: bool = False
    group: str = 'main'


class Publisher(BasePublisher):

    def __init__(self, key: str = '', *args, **kwargs):
        """__init__.

        Args:
            args: See BasePublisher
            kwargs: See BasePublisher
        """
        self._key = key
        self._msg_seq = 0
        self._producer: Producer = None
        super().__init__(*args, **kwargs)

    def publish(self,
                msg: PubSubMessage,
                key: str = ''
                ) -> None:
        """publish.

        Args:
            msg (PubSubMessage): Message to Publish

        Returns:
            None:
        """
        if self._msg_type is not None and not isinstance(msg, PubSubMessage):
            raise ValueError('Argument "msg" must be of type PubSubMessage')
        elif isinstance(msg, dict):
            data = msg
        elif isinstance(msg, PubSubMessage):
            data = msg.dict()
        if key in (None, ''):
            key = self._key
        self._producer.poll(0)
        payload = self._serializer.serialize(data)
        self._producer.produce(self._topic, key=key, value=payload,
                               on_delivery=self._on_delivery)
        self._msg_seq += 1

    def _on_delivery(self, err, msg):
        if err is not None:
            self.logger().error(err)
        self.logger().info(f'Published on {msg.topic()}, partition'
                           f'{msg.partition()}')

    def run(self):
        cfg = {
            'bootstrap.servers':
                f'{self._conn_params.host}:{self._conn_params.port}',
            # 'group.id': self._conn_params.group,
        }
        self._producer = Producer(cfg)

    def stop(self):
        if self._producer is not None:
            self._producer.flush()


class MPublisher(Publisher):
    """MPublisher.
    Multi-Topic Publisher
    """

    def __init__(self, key: str = '', *args, **kwargs):
        self._key = key
        super(MPublisher, self).__init__(topic='*', *args, **kwargs)

    def publish(self,
                msg: PubSubMessage,
                topic: str,
                key: str = ''
                ) -> None:
        """publish.

        Args:
            msg (PubSubMessage): msg
            topic (str): topic

        Returns:
            None:
        """
        if self._msg_type is not None and not isinstance(msg, PubSubMessage):
            raise ValueError('Argument "msg" must be of type PubSubMessage')
        elif isinstance(msg, dict):
            data = msg
        elif isinstance(msg, PubSubMessage):
            data = msg.dict()
        if key in (None, ''):
            key = self._key
        self._producer.poll(0)
        self._producer.produce(topic, key=key, value=data,
                               on_delivery=self._on_delivery)
        self._msg_seq += 1


class Subscriber(BaseSubscriber):

    def __init__(self, key: str = '', *args, **kwargs):
        self._key = key
        self._consumer: Consumer = None
        super(Subscriber, self).__init__(*args, **kwargs)

    def run_forever(self):
        running = True
        cfg = {
            'bootstrap.servers':
                f'{self._conn_params.host}:{self._conn_params.port}',
            'auto.offset.reset': 'end',
            'group.id': self._conn_params.group,
            'enable.auto.offset.store': True,
            'enable.auto.commit': True,
        }

        self._consumer = Consumer(cfg)
        try:
            self._consumer.subscribe([self._topic], on_assign=self._on_assign)

            while running:
                msg = self._consumer.poll(timeout=1.0)
                if msg is None: continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        # End of partition event
                        print('%% %s [%d] reached end at offset %d\n' %
                                         (msg.topic(), msg.partition(), msg.offset()))
                    elif msg.error():
                        raise KafkaException(msg.error())
                else:
                    self._on_message(msg)
                    # self._consumer.store_offsets(msg)
                    # self._consumer.commit(asynchronous=False)
        finally:
            # Close down consumer to commit final offsets.
            print('Skata')
            self._consumer.close()
        self.log.debug(f'Started Subscriber: <{self._topic}>')

    def _on_assign(self, consumer, partitions):
        self.logger().info('Assignment:', partitions)
        self._reset_offset(consumer, partitions)

    def _reset_offset(self, consumer, partitions):
        for p in partitions:
            p.offset = OFFSET_END
        consumer.assign(partitions)

    def _on_message(self, msg: Any):
        try:
            data, topic, key, ts = self._unpack_comm_msg(msg)
            if self.onmessage is not None:
                if self._msg_type is None:
                    _clb = functools.partial(self.onmessage, data)
                else:
                    _clb = functools.partial(self.onmessage,
                                             self._msg_type(**data))
                _clb()
        except Exception:
            self.log.error('Exception caught in _on_message', exc_info=True)

    def _unpack_comm_msg(self,
                         msg: Any
                         ) -> Tuple:
        _topic = msg.topic()
        _key = msg.key()
        _timestamp = msg.timestamp()
        _data = self._serializer.deserialize(msg.value())
        return _data, _topic, _key, _timestamp

    def stop(self):
        print('Skata')
        self._consumer.close()


class PSubscriber(Subscriber):

    def _on_message(self, msg: Any):
        try:
            data, topic, key, ts = self._unpack_comm_msg(msg)
            if self.onmessage is not None:
                if self._msg_type is None:
                    _clb = functools.partial(self.onmessage,
                                             data,
                                             topic)
                else:
                    _clb = functools.partial(self.onmessage,
                                             self._msg_type(**data),
                                             topic)
                _clb()
        except Exception:
            self.log.error('Exception caught in _on_message', exc_info=True)


class RPCService(BaseRPCService):
    """RPCService.
    MQTT RPC Service class.
    """

    def __init__(self, *args, **kwargs):
        """__init__.

        Args:
            args: See BaseRPCService
            kwargs: See BaseRPCService
        """
        raise NotImplementedError('RPCService for Kafka transport not supported')
        super(RPCService, self).__init__(*args, **kwargs)
        self._transport = MQTTTransport(conn_params=self._conn_params,
                                        serializer=self._serializer,
                                        compression=self._compression)

    def _send_response(self,
                       data: Dict[str, Any],
                       reply_to: str
                       ):
        self._comm_obj.header.timestamp = gen_timestamp()   #pylint: disable=E0237
        self._comm_obj.data = data
        _resp = self._comm_obj.dict()
        self._transport.publish(reply_to, _resp, qos=MQTTQoS.L1)

    def _on_request_handle(self,
                           client: Any,
                           userdata: Any,
                           msg: Dict[str, Any]
                           ):
        task = self._executor.submit(self._on_request_internal,
                                     client,
                                     userdata,
                                     msg)

    def _on_request_internal(self,
                             client: Any,
                             userdata: Any,
                             msg: Dict[str, Any]
                             ):
        try:
            req_msg, uri = self._unpack_comm_msg(msg)
        except Exception as exc:
            self.log.error(
                f'Could not unpack request message: {exc}\n'
                'Dropping client request!',
                exc_info=True
            )
            return
        try:
            if self._msg_type is None:
                resp = self.on_request(req_msg.data)
            else:
                resp = self.on_request(self._msg_type.Request(**req_msg.data))
                ## RPCMessage.Response object here
                resp = resp.dict()
            self._send_response(resp, req_msg.header.reply_to)
        except Exception as exc:
            self.log.error(str(exc), exc_info=True)

    def _unpack_comm_msg(self,
                         msg: Any
                         ) -> Tuple[CommRPCMessage, str]:
        try:
            _uri = msg.topic
            _payload = self._serializer.deserialize(msg.payload)
            _data = _payload['data']
            _header = _payload['header']
            _req_msg = CommRPCMessage(
                header=CommRPCHeader(**_header),
                data=_data
            )
            if not self._validate_rpc_req_msg(_req_msg):
                raise RPCRequestError('Request Message is invalid!')
        except Exception as e:
            raise RPCRequestError(str(e))
        return _req_msg, _uri

    def run_forever(self):
        """run_forever.
        """
        self._transport.subscribe(self._rpc_name,
                                  self._on_request_handle,
                                  qos=MQTTQoS.L1)
        self._transport.start()
        while True:
            if self._t_stop_event is not None:
                if self._t_stop_event.is_set():
                    self.log.debug('Stop event caught in thread')
                    break
            time.sleep(0.001)
        self._transport.stop()

class RPCServer(BaseRPCServer):
    def __init__(self, *args, **kwargs):
        """__init__.

        Args:
            args: See BaseRPCServer
            kwargs: See BaseRPCServer
        """
        super(RPCServer, self).__init__(*args, **kwargs)
        self._transport = MQTTTransport(conn_params=self._conn_params,
                                        serializer=self._serializer,
                                        compression=self._compression)
        for uri in self._svc_map:
            callback = self._svc_map[uri][0]
            msg_type = self._svc_map[uri][1]
            self._register_endpoint(uri, callback, msg_type)

    def _send_response(self,
                       data: Dict[str, Any],
                       reply_to: str
                       ):
        """_send_response.

        Args:
            data (dict): data
            reply_to (str): reply_to
        """
        self._comm_obj.header.timestamp = gen_timestamp()   #pylint: disable=E0237
        self._comm_obj.data = data
        _resp = self._comm_obj.dict()
        self._transport.publish(reply_to, _resp, qos=MQTTQoS.L1)

    def _on_request_handle(self,
                           client: Any,
                           userdata: Any,
                           msg: Dict[str, Any]
                           ):
        task = self._executor.submit(self._on_request_internal,
                                     client,
                                     userdata,
                                     msg)

    def _on_request_internal(self,
                             client: Any,
                             userdata: Any,
                             msg: Dict[str, Any]
                             ):
        """_on_request_internal.

        Args:
            client (Any): client
            userdata (Any): userdata
            msg (Dict[str, Any]): msg
        """
        try:
            req_msg, uri = self._unpack_comm_msg(msg)
        except Exception as exc:
            self.log.error(
                f'Could not unpack request message: {exc}'
                '\nDropping client request!',
                exc_info=True
            )
            return
        try:
            uri = uri.replace('/', '.')
            svc_uri = uri.replace(self._base_uri, '')
            if svc_uri[0] == '.':
                svc_uri = svc_uri[1:]
            if svc_uri not in self._svc_map:
                return
            else:
                clb = self._svc_map[svc_uri][0]
                msg_type = self._svc_map[svc_uri][1]
                if msg_type is None:
                    resp = clb(req_msg.data)
                else:
                    resp = clb(msg_type.Request(**req_msg.data))
                    resp = resp.dict()
            self._send_response(resp, req.header.reply_to)
        except Exception as exc:
            self.log.error(str(exc), exc_info=False)
            return

    def _unpack_comm_msg(self,
                         msg: Any
                         ) -> Tuple[CommRPCMessage, str]:
        """_unpack_comm_msg.

        Unpack payload, header and uri from communcation message.

        Args:
            msg (Any): msg

        Returns:
            Tuple[Any, Any, Any]:
        """
        try:
            _uri = msg.topic
            _payload = self._serializer.deserialize(msg.payload)
            _data = _payload['data']
            _header = _payload['header']
            _req_msg = CommRPCMessage(
                header=CommRPCHeader(**_header),
                data=_data
            )
            if not self._validate_rpc_req_msg(_req_msg):
                raise RPCRequestError('Request Message is invalid!')
        except Exception as e:
            raise RPCRequestError(str(e))
        return _req_msg, _uri

    def _register_endpoint(self,
                           uri: str,
                           callback: Callable,
                           msg_type: RPCMessage = None
                           ):
        self._svc_map[uri] = (callback, msg_type)
        if self._base_uri in (None, ''):
            full_uri = uri
        else:
            full_uri = f'{self._base_uri}.{uri}'
        self.log.info(f'Registering endpoint <{full_uri}>')
        self._transport.subscribe(full_uri, self._on_request_handle,
                                  qos=MQTTQoS.L1)

    def run_forever(self):
        """run_forever.
        """
        self._transport.start()
        while True:
            if self._t_stop_event is not None:
                if self._t_stop_event.is_set():
                    self.log.debug('Stop event caught in thread')
                    break
            time.sleep(0.001)
        self._transport.stop()


class RPCClient(BaseRPCClient):
    """RPCClient.
    MQTT RPC Client
    """

    def __init__(self, *args, **kwargs):
        """__init__.

        Args:
            args: See BaseRPCClient
            kwargs: See BaseRPCClient
        """
        self._response = None

        super(RPCClient, self).__init__(*args, **kwargs)
        self._transport = MQTTTransport(conn_params=self._conn_params,
                                        serializer=self._serializer,
                                        compression=self._compression)

    def _gen_queue_name(self):
        """_gen_queue_name.
        """
        return f'rpc-{self._gen_random_id()}'

    def _prepare_request(self,
                         data: Dict[str, Any]
                         ):
        """_prepare_request.

        Args:
            data:
        """
        self._comm_obj.header.timestamp = gen_timestamp()   #pylint: disable=E0237
        self._comm_obj.header.reply_to = self._gen_queue_name()
        self._comm_obj.data = data
        return self._comm_obj.dict()

    def _on_response_wrapper(self,
                             client: Any,
                             userdata: Any,
                             msg: Dict[str, Any]
                             ):
        """_on_response_wrapper.

        Args:
            client (Any): client
            userdata (Any): userdata
            msg (Dict[str, Any]): msg
        """
        try:
            data, header, uri = self._unpack_comm_msg(msg)
        except Exception as exc:
            self.log.error(exc, exc_info=True)
            data = {}
        self._response = data

    def _unpack_comm_msg(self,
                         msg: Any
                         ) -> Tuple[Any, Any, Any]:
        _uri = msg.topic
        _payload = self._serializer.deserialize(msg.payload)
        _data = _payload['data']
        _header = _payload['header']
        return _data, _header, _uri

    def _wait_for_response(self,
                           timeout: float = 10.0
                           ):
        """_wait_for_response.

        Args:
            timeout (float): timeout
        """
        start_t = time.time()
        while self._response is None:
            elapsed_t = time.time() - start_t
            if elapsed_t >= timeout:
                raise RPCClientTimeoutError(
                    f'Response timeout after {timeout} seconds')
            time.sleep(0.001)
        return self._response

    def call(self,
             msg: RPCMessage.Request,
             timeout: float = 30
             ) -> RPCMessage.Response:
        """call.

        Args:
            msg (RPCMessage.Request): msg
            timeout (float): timeout
        """
        if self._msg_type is None:
            data = msg
        else:
            if not isinstance(msg, self._msg_type.Request):
                raise ValueError('Message type not valid')
            data = msg.dict()

        self._response = None

        _msg = self._prepare_request(data)
        _reply_to = _msg['header']['reply_to']

        self._transport.subscribe(_reply_to, callback=self._on_response_wrapper,
                                  qos=MQTTQoS.L1)
        start_t = time.time()
        self._transport.publish(self._rpc_name, _msg, qos=MQTTQoS.L1)
        _resp = self._wait_for_response(timeout=timeout)
        elapsed_t = time.time() - start_t
        self._delay = elapsed_t

        if self._msg_type is None:
            return _resp
        else:
            return self._msg_type.Response(**_resp)


class ActionService(BaseActionService):
    """ActionService.
    MQTT Action Server
    """

    def __init__(self, *args, **kwargs):
        """__init__.

        Args:
            args: See BaseActionService
            kwargs: See BaseActionService
        """
        super(ActionService, self).__init__(*args, **kwargs)

        self._goal_rpc = RPCService(msg_type=_ActionGoalMessage,
                                    rpc_name=self._goal_rpc_uri,
                                    conn_params=self._conn_params,
                                    on_request=self._handle_send_goal,
                                    debug=self.debug)
        self._cancel_rpc = RPCService(msg_type=_ActionCancelMessage,
                                      rpc_name=self._cancel_rpc_uri,
                                      conn_params=self._conn_params,
                                      on_request=self._handle_cancel_goal,
                                      debug=self.debug)
        self._result_rpc = RPCService(msg_type=_ActionResultMessage,
                                      rpc_name=self._result_rpc_uri,
                                      conn_params=self._conn_params,
                                      on_request=self._handle_get_result,
                                      debug=self.debug)
        self._feedback_pub = Publisher(msg_type=_ActionFeedbackMessage,
                                       topic=self._feedback_topic,
                                       conn_params=self._conn_params,
                                       debug=self.debug)
        self._status_pub = Publisher(msg_type=_ActionStatusMessage,
                                     topic=self._status_topic,
                                     conn_params=self._conn_params,
                                     debug=self.debug)


class ActionClient(BaseActionClient):
    """ActionClient.
    MQTT Action Client
    """

    def __init__(self, *args, **kwargs):
        """__init__.

        Args:
            args: See BaseActionClient
            kwargs: See BaseActionClient
        """
        super(ActionClient, self).__init__(*args, **kwargs)

        self._goal_client = RPCClient(msg_type=_ActionGoalMessage,
                                      rpc_name=self._goal_rpc_uri,
                                      conn_params=self._conn_params,
                                      debug=self.debug)
        self._cancel_client = RPCClient(msg_type=_ActionCancelMessage,
                                        rpc_name=self._cancel_rpc_uri,
                                        conn_params=self._conn_params,
                                        debug=self.debug)
        self._result_client = RPCClient(msg_type=_ActionResultMessage,
                                        rpc_name=self._result_rpc_uri,
                                        conn_params=self._conn_params,
                                        debug=self.debug)
        self._status_sub = Subscriber(msg_type=_ActionStatusMessage,
                                      conn_params=self._conn_params,
                                      topic=self._status_topic,
                                      on_message=self._on_status,
                                      debug=self.debug)
        self._feedback_sub = Subscriber(msg_type=_ActionFeedbackMessage,
                                        conn_params=self._conn_params,
                                        topic=self._feedback_topic,
                                        on_message=self._on_feedback,
                                        debug=self.debug)


class EventEmitter(BaseEventEmitter):
    """EventEmitter.
    MQTT Event Emitter class
    """

    def __init__(self, *args, **kwargs):
        """__init__.

        Args:
            args: See BaseEventEmitter
            kwargs: See BaseEventEmitter
        """
        super(EventEmitter, self).__init__(*args, **kwargs)
        self._transport = MQTTTransport(conn_params=self._conn_params,
                                        serializer=self._serializer)

    def send_event(self,
                   event: Event
                   ) -> None:
        """send_event.

        Args:
            event (Event): The Event to send.

        Returns:
            None:
        """
        _msg = event.dict()
        self._transport.publish(event.uri, _msg, qos=MQTTQoS.L1)
