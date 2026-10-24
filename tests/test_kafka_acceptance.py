import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("VEHICLE_AGENT_RUN_KAFKA") != "1",
    reason="设置 VEHICLE_AGENT_RUN_KAFKA=1 后连接 deployments/docker-compose.yml 中的 Kafka",
)


def test_live_kafka_duplicate_publish_keeps_one_alarm() -> None:
    from vehicle_agent.ingest import MemoryAlarmTopic, alarm_event_from_payload, fatigue_alarm_event
    from vehicle_agent.kafka_alarm import exchange_vehicle_alarm

    event = fatigue_alarm_event()
    payloads = exchange_vehicle_alarm(event)
    topic = MemoryAlarmTopic()
    first = topic.deliver(alarm_event_from_payload(payloads[0]))
    repeat = topic.deliver(alarm_event_from_payload(payloads[1]))

    assert len(payloads) == 2
    assert payloads[0] == payloads[1]
    assert first is repeat
    assert [item.id for item in topic.alarms()] == ["alarm_000001"]
    assert first.speed_kmh == 82.5
