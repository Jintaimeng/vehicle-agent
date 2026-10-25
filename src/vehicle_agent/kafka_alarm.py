"""Live topic publish and read. Import from the Kafka acceptance test or the ingest thread."""

import time
import uuid
from collections.abc import Callable

from vehicle_agent.ingest import (
    AlarmEvent,
    DeviceStatusEvent,
    alarm_event_payload,
    device_status_payload,
)

_ALARM_TOPIC = "vehicle.alarm"
_STATUS_TOPIC = "device.status"
_BOOTSTRAP = "127.0.0.1:9092"


def exchange_vehicle_alarm(
    event: AlarmEvent, bootstrap: str = _BOOTSTRAP, timeout_s: float = 20
) -> list[bytes]:
    """Publish one alarm twice and return the two records that arrived afterwards."""

    return _exchange(_ALARM_TOPIC, alarm_event_payload(event), bootstrap, timeout_s)


def exchange_device_status(
    event: DeviceStatusEvent, bootstrap: str = _BOOTSTRAP, timeout_s: float = 20
) -> list[bytes]:
    """Publish one device heartbeat twice and return the two records that arrived afterwards."""

    return _exchange(_STATUS_TOPIC, device_status_payload(event), bootstrap, timeout_s)


def _exchange(topic: str, payload: bytes, bootstrap: str, timeout_s: float) -> list[bytes]:
    from confluent_kafka import Consumer, Producer, TopicPartition

    _ensure_topic(bootstrap, topic)
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": f"vehicle-agent-{uuid.uuid4()}",
            "auto.offset.reset": "latest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topic])
    deadline = time.monotonic() + timeout_s
    try:
        assignment = _assigned(consumer, deadline)
        for partition in assignment:
            _low, high = consumer.get_watermark_offsets(partition, timeout=5)
            consumer.seek(TopicPartition(partition.topic, partition.partition, high))
        producer = Producer({"bootstrap.servers": bootstrap})
        producer.produce(topic, value=payload)
        producer.produce(topic, value=payload)
        if producer.flush(10):
            raise TimeoutError("kafka publish timed out")
        found: list[bytes] = []
        while len(found) < 2 and time.monotonic() < deadline:
            message = consumer.poll(0.5)
            if message is None or message.error() is not None:
                continue
            if message.topic() == topic and message.value() == payload:
                found.append(message.value())
        if len(found) != 2:
            raise TimeoutError("kafka did not return both records")
        return found
    finally:
        consumer.close()


def _ensure_topic(bootstrap: str, topic: str) -> None:
    """Create the topic when the broker does not auto-create it."""

    from confluent_kafka import KafkaError, KafkaException
    from confluent_kafka.admin import AdminClient, NewTopic

    admin = AdminClient({"bootstrap.servers": bootstrap})
    future = admin.create_topics(
        [NewTopic(topic, num_partitions=1, replication_factor=1)],
        request_timeout=10,
    )[topic]
    try:
        future.result(timeout=10)
    except KafkaException as exc:
        if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise


def serve_vehicle_alarms(bootstrap: str, handle: Callable[[bytes], None], stop) -> None:
    """Poll vehicle.alarm until stop() is true. Commit only after handle returns."""

    from confluent_kafka import Consumer

    _ensure_topic(bootstrap, _ALARM_TOPIC)
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": "vehicle-agent-alarm",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([_ALARM_TOPIC])
    try:
        while not stop():
            message = consumer.poll(0.5)
            if message is None or message.error() is not None:
                continue
            if message.topic() != _ALARM_TOPIC:
                continue
            payload = message.value()
            if payload is None:
                consumer.commit(message=message, asynchronous=False)
                continue
            handle(payload)
            consumer.commit(message=message, asynchronous=False)
    finally:
        consumer.close()


def _assigned(consumer, deadline: float) -> list:
    while time.monotonic() < deadline:
        consumer.poll(0.2)
        assignment = consumer.assignment()
        if assignment:
            return list(assignment)
    raise TimeoutError("kafka assignment timed out")
