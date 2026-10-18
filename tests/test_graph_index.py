from vehicle_agent.knowledge.graph import seed_graph_index


def test_seed_graph_returns_driver_and_device() -> None:
    facts = seed_graph_index().query("vehicle_001", "device_001")

    assert [(item.relation, item.target_id) for item in facts] == [
        ("drives", "driver_001"),
        ("installed_with", "device_001"),
    ]


def test_seed_graph_includes_device_fault_within_two_hops() -> None:
    facts = seed_graph_index().query("vehicle_003", "device_003")

    assert ("has_fault", "camera_occlusion") in [
        (item.relation, item.target_id) for item in facts
    ]


def test_unknown_vehicle_returns_no_facts() -> None:
    assert seed_graph_index().query("vehicle_missing", "device_001") == []


def test_unrelated_device_does_not_attach_another_vehicles_fault() -> None:
    facts = seed_graph_index().query("vehicle_001", "device_003")

    assert [item.relation for item in facts] == ["drives", "installed_with"]
    assert all(item.target_id != "camera_occlusion" for item in facts)
