import pytest

from vehicle_agent.knowledge.index import (
    MarkdownRegulationIndex,
    packaged_regulation_index,
    parse_regulation_document,
)


def test_parser_matches_alarm_type(tmp_path) -> None:
    document = tmp_path / "rules.md"
    document.write_text(
        "\n".join(
            [
                "# 疲劳",
                "- document_id: fatigue-rules",
                "- title: 疲劳驾驶处置要点",
                "- section: 第2节",
                "- alarm_types: driver_fatigue",
                "",
                "停车休息。",
                "",
                "# 离线",
                "- document_id: device-offline-rules",
                "- title: 设备离线排查要点",
                "- section: 第1节",
                "- alarm_types: device_offline",
                "",
                "先核对设备。",
            ]
        ),
        encoding="utf-8",
    )
    index = MarkdownRegulationIndex.from_directory(tmp_path)

    fatigue = index.search("driver_fatigue")
    assert [item.document_id for item in fatigue] == ["fatigue-rules"]
    assert fatigue[0].excerpt == "停车休息。"
    assert index.search("device_offline")[0].section == "第1节"
    assert index.search("phone_use") == []


def test_packaged_fatigue_rule_is_searchable() -> None:
    snippets = packaged_regulation_index().search("driver_fatigue")
    assert snippets[0].document_id == "fatigue-rules"
    assert snippets[0].title == "疲劳驾驶处置要点"
    assert "第2节" in snippets[0].section
    assert snippets[1].document_id == "fatigue-training"


def test_incomplete_section_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing required fields"):
        parse_regulation_document("# 缺字段\n\n只有正文。\n")
