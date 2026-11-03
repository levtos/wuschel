from core_contracts.ha_projection import PRIMARY_FIELDS, primary_field
from core_contracts.schema import FieldSchema, Schema


def test_all_reviewed_multifield_primaries_survive_sorted_json_fields():
    for type_id, expected in PRIMARY_FIELDS.items():
        schema = Schema(
            fields={
                name: FieldSchema(type="bool", required=True, physical_state=False)
                for name in sorted([expected, "aaa_other"])
            },
            available_projection=(),
        )
        assert primary_field(schema, type_id) == expected


def test_playback_primary_is_playing_even_when_manual_sorts_first():
    schema = Schema(
        fields={
            name: FieldSchema(type="bool", required=True, physical_state=False)
            for name in ("manual", "playing")
        },
        available_projection=(),
    )
    assert primary_field(schema, "media.playback_facts") == "playing"
