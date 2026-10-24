"""Live vehicle.alarm publish and read. Import only from the Kafka acceptance test."""

import time
import uuid

from vehicle_agent.ingest import AlarmEvent, alarm_event_payload

_TOPIC = "vehicle.alarm"
_BOOTSTRAP = "127.0.0.1:9092"


def exchange_vehicle_alarm(
    event: AlarmEvent, bootstrap: str = _BOOTSTRAP, timeout_s: float = 20
) -> list[bytes]:
    """Publish one alarm twice and return the two records that arrived afterwards."""

    from confluent_kafka import Consumer, Producer, TopicPartition

    payload = alarm_event_payload(event)
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": f"vehicle-agent-{uuid.uuid4()}",
            "auto.offset.reset": "latest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([_TOPIC])
    deadline = time.monotonic() + timeout_s
    try:
        assignment = _assigned(consumer, deadline)
        for partition in assignment:
            _low, high = consumer.get_watermark_offsets(partition, timeout=5)
            consumer.seek(TopicPartition(partition.topic, partition.partition, high))
        producer = Producer({"bootstrap.servers": bootstrap})
        producer.produce(_TOPIC, value=payload)
        producer.produce(_TOPIC, value=payload)
        if producer.flush(10):
            raise TimeoutError("kafka publish timed out")
        found: list[bytes] = []
        while len(found) < 2 and time.monotonic() < deadline:
            message = consumer.poll(0.5)
            if message is None or message.error() is not None:
                continue
            if message.topic() == _TOPIC and message.value() == payload:
                found.append(message.value())
        if len(found) != 2:
            raise TimeoutError("kafka did not return both alarm records")
        return found
    finally:
        consumer.close()


def _assigned(consumer, deadline: float) -> list:
    while time.monotonic() < deadline:
        consumer.poll(0.2)
        assignment = consumer.assignment()
        if assignment:
            return list(assignment)
    raise TimeoutError("kafka assignment timed out")
