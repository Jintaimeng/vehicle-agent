from pathlib import Path

_COMPOSE = Path(__file__).resolve().parents[1] / "deployments" / "docker-compose.yml"
_ADVERTISED = (
    "PLAINTEXT://kafka:29092,PLAINTEXT_HOST://${KAFKA_ADVERTISED_HOST:-127.0.0.1}:9092"
)


def test_kafka_advertised_host_defaults_to_localhost() -> None:
    text = _COMPOSE.read_text(encoding="utf-8")

    assert f"KAFKA_ADVERTISED_LISTENERS: {_ADVERTISED}" in text
    assert "PLAINTEXT_HOST://127.0.0.1:9092" not in text
    assert "PLAINTEXT_HOST://0.0.0.0:9092" in text
    assert '"9092:9092"' in text
