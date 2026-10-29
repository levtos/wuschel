"""Reproducible portable media definitions; no private addresses or media data."""

import json
from pathlib import Path

from core_contracts.declarative import check_definition
from core_contracts.declarative_models import DeclarativeDefinition


def schema(kind="bool", values=None, required=True):
    return dict(
        type=kind,
        required=required,
        physical_state=False,
        **({"value_catalog": values} if values else {}),
    )


def literal(value, kind=None, values=None):
    return dict(
        op="literal",
        value=value,
        schema=schema(kind or ("bool" if type(value) is bool else "text"), values),
    )


def inp(name):
    return dict(op="input", name=name)


def ref(name):
    return dict(op="ref", name=name)


def param(name):
    return dict(op="parameter", name=name)


def compare(left, right, comparison="eq"):
    return dict(op="compare", left=left, right=right, comparison=comparison)


def eq(name, value):
    return compare(inp(name), literal(value))


def conjunction(*args):
    return dict(op="and", args=list(args))


def disjunction(*args):
    return dict(op="or", args=list(args))


def negate(arg):
    return dict(op="not", args=[arg])


def select(*cases):
    return dict(
        op="first_match", cases=[dict(when=condition, then=result) for condition, result in cases]
    )


def fallback(*args):
    return dict(op="fusion", strategy="first_usable", args=list(args))


def mapping(arg, entries, kind="bool", values=None):
    return dict(
        op="map",
        arg=arg,
        entries={json.dumps(k): v for k, v in entries},
        schema=schema(kind, values),
    )


def not_applicable(kind="text", values=None):
    # Map changes only the declared type; the existing positive status propagates.
    return mapping(
        dict(op="not_applicable", arg=literal(True)),
        [(False, values[0] if values else "")],
        kind,
        values,
    )


def grace(arg, name, duration):
    return dict(op="temporal", id=name, operation="grace", args=[arg], duration_s=param(duration))


def event(arg, name):
    return fallback(dict(op="event_match", id=name, arg=arg, mode="rising"), literal(False))


def definition(type_id, inputs, outputs, fields, *, rules=None, parameters=None, machine=None):
    return dict(
        type_id=type_id,
        type_version=1,
        language_version=6,
        display_name=type_id,
        inputs=[dict(name=name, schema=field) for name, field in inputs.items()],
        schema=dict(
            fields=fields, available_projection=["value"] if set(fields) == {"value"} else []
        ),
        rules=rules or {},
        outputs=outputs,
        **({"parameter_schema": parameters} if parameters else {}),
        **({"machine": machine} if machine else {}),
    )


def duration(label, maximum=31536000, minimum=0.001):
    return dict(schema=schema("number"), minimum=minimum, maximum=maximum, label=label)


def lookup(catalog, key, values, scope, media, signal="title"):
    return dict(
        op="managed_catalog_lookup",
        catalog_id=catalog,
        definition_version=1,
        scope=literal(scope),
        media_type=literal(media),
        signal_type=literal(signal),
        key=key,
        schema=schema("enum", values),
    )


def build():
    definitions = []
    pc_bucket = dict(
        op="bucket",
        id="watts",
        arg=inp("watts"),
        classes=["off", "on"],
        initial={"unknown": True},
        schema=schema("enum", ["off", "on"]),
        boundaries=[
            dict(
                at=param("power_on_w"),
                hysteresis=dict(lower=param("power_off_w"), upper=param("power_on_w")),
            )
        ],
    )
    definitions.append(
        definition(
            "device.pc_state",
            {"host": schema(), "watts": schema("number")},
            {
                "value": fallback(
                    grace(inp("host"), "host_grace", "host_grace_s"),
                    mapping(pc_bucket, [("off", False), ("on", True)]),
                )
            },
            {"value": schema()},
            parameters={
                "host_grace_s": duration("Host-Gnadenfrist in Sekunden", 60),
                "power_on_w": dict(schema=schema("number"), minimum=0, label="PC an ab Watt"),
                "power_off_w": dict(schema=schema("number"), minimum=0, label="PC aus unter Watt"),
            },
        )
    )
    definitions[-1]["parameter_relations"] = [
        dict(left="power_off_w", comparison="lt", right="power_on_w")
    ]
    gated_source = select((negate(inp("tv")), not_applicable()), (inp("tv"), inp("source")))
    definitions.append(
        definition(
            "media.tv_source",
            {"tv": schema(), "source": schema("text")},
            {
                "value": select(
                    (negate(inp("tv")), not_applicable()),
                    (inp("tv"), grace(gated_source, "source_grace", "source_grace_s")),
                )
            },
            {"value": schema("text")},
            parameters={"source_grace_s": duration("Quellen-Gnadenfrist in Sekunden", 5)},
        )
    )
    devices = ["ps5", "apple_tv", "sonstige", "none"]
    definitions.append(
        definition(
            "media.tv_device",
            {"tv": schema(), "source": schema("text")},
            {
                "value": select(
                    (negate(inp("tv")), literal("none", "enum", devices)),
                    (
                        inp("tv"),
                        mapping(
                            lookup("media.tv_device", inp("source"), devices[:3], "tv", "source"),
                            [(value, value) for value in devices[:3]],
                            "enum",
                            devices,
                        ),
                    ),
                )
            },
            {"value": schema("enum", devices)},
        )
    )
    console = ["online", "playing", "rest_mode", "offline"]
    active_console = mapping(
        inp("console"),
        [("Online", True), ("Playing", True), ("Rest Mode", False), ("Offline", False)],
    )
    definitions.append(
        definition(
            "device.console_state",
            {"console": schema("text")},
            {
                "value": mapping(
                    inp("console"),
                    list(zip(["Online", "Playing", "Rest Mode", "Offline"], console)),
                    "enum",
                    console,
                ),
                "active": active_console,
                "session": mapping(
                    inp("console"),
                    [
                        ("Online", False),
                        ("Playing", True),
                        ("Rest Mode", False),
                        ("Offline", False),
                    ],
                ),
            },
            {"value": schema("enum", console), "active": schema(), "session": schema()},
        )
    )
    player = mapping(
        inp("state"),
        [
            (value, value not in {"off", "standby"})
            for value in ["off", "standby", "on", "playing", "paused", "idle", "buffering"]
        ],
    )
    definitions.append(
        definition(
            "device.appletv_state",
            {"tv": schema(), "state": schema("text")},
            {"value": conjunction(inp("tv"), player)},
            {"value": schema()},
        )
    )
    definitions.append(
        definition(
            "device.media_power", {"state": schema("text")}, {"value": player}, {"value": schema()}
        )
    )

    def matches(pattern):
        return dict(op="matches", args=[inp("uri"), literal(pattern)])

    known = conjunction(
        negate(disjunction(matches("http://*"), matches("https://*"))),
        disjunction(
            *(
                matches("*://" + kind + "/?*")
                for kind in [
                    "radio",
                    "track",
                    "album",
                    "playlist",
                    "artist",
                    "audiobook",
                    "podcast",
                    "podcast_episode",
                ]
            )
        ),
    )
    manual = select(
        (conjunction(known, matches("*://radio/?*")), literal(False)), (known, literal(True))
    )
    definitions.append(
        definition(
            "media.playback_facts",
            {"state": schema("text"), "uri": schema("text")},
            {
                "playing": eq("state", "playing"),
                "manual": conjunction(eq("state", "playing"), manual),
            },
            {"playing": schema(), "manual": schema()},
        )
    )
    definitions.append(
        definition(
            "media.pc_session",
            {"pc": schema(), "discord": schema("text")},
            {"value": conjunction(inp("pc"), eq("discord", "playing"))},
            {"value": schema()},
        )
    )
    definitions.append(
        definition(
            "media.stream_active",
            {"streams": schema("number")},
            {"value": compare(inp("streams"), literal(0, "number"), "gt")},
            {"value": schema()},
        )
    )
    games = ["heimkino", "grind", "headset"]
    classes = {
        "homepods": ["normal", "boost", "stumm", "leiser"],
        "ps5": games,
        "pc": games,
        "stash": ["normal", "privat"],
        "appletv": ["normal", "privat"],
    }
    for device, values in classes.items():
        inputs = {"applicable": schema(), "title": schema("text")}
        key = inp("title")
        if device == "homepods":
            inputs["artist"] = schema("text", required=False)
            key = select(
                (
                    compare(inp("artist"), literal(""), "ne"),
                    dict(op="compound_key", args=[inp("artist"), inp("title")]),
                ),
                (eq("artist", ""), dict(op="compound_key", args=[inp("title")])),
            )
            key = select((compare(inp("title"), literal(""), "ne"), key))
        result = lookup(
            "media." + device,
            key,
            values,
            device,
            "music" if device == "homepods" else "game" if device in {"ps5", "pc"} else "video",
            "app" if device == "appletv" else "title",
        )
        definitions.append(
            definition(
                "media." + device + "_class",
                inputs,
                {
                    "value": select(
                        (negate(inp("applicable")), not_applicable("enum", values)),
                        (inp("applicable"), result),
                    )
                },
                {"value": schema("enum", values)},
            )
        )
    usage = ["tv", "streaming", "gaming", "other"]
    usage_lookup = lookup("media.tv_activity", inp("source"), usage, "tv", "source")
    definitions.append(
        definition(
            "media.tv_usage",
            {"source": schema("text")},
            {
                "value": dict(
                    op="configuration_hint",
                    arg=usage_lookup,
                    when=compare(usage_lookup, literal("other")),
                    code="activity_mapping_missing",
                    message="Für diese TV-Quelle fehlt die Nutzungsart.",
                )
            },
            {"value": schema("enum", usage)},
        )
    )
    states = ["unresolved", "idle", "tv", "pc"]
    rules = {
        "event_" + name: event(inp(name), "event_" + name)
        for name in ["tv", "pc", "ps5", "appletv"]
    }
    tv, pc = inp("tv"), inp("pc")
    transitions = []
    for state in states:
        choices = [
            ("idle", conjunction(negate(tv), negate(pc))),
            ("tv", conjunction(tv, negate(pc))),
            ("pc", conjunction(pc, negate(tv))),
            (
                "tv",
                conjunction(
                    tv, disjunction(*(ref("event_" + n) for n in ["tv", "ps5", "appletv"]))
                ),
            ),
            ("pc", conjunction(pc, ref("event_pc"))),
        ]
        transitions += [
            dict(source=state, target=target, event="input", guard=guard, reason="branch_selection")
            for target, guard in choices
        ]
    machine = dict(
        id="activity",
        states=states,
        initial="unresolved",
        transition_selection="ordered",
        transitions=transitions,
        reinitialize_when=disjunction(tv, pc),
        required_inputs_by_state={s: ["tv", "pc"] for s in states},
    )
    values = ["tv", "streaming", "gaming", "pc", "other", "idle", "ps5_menu", "unresolved"]
    branch = dict(op="machine")
    pc_activity = select(
        (fallback(eq("discord", "playing"), literal(False)), literal("gaming", "enum", values)),
        (literal(True), literal("pc", "enum", values)),
    )
    tv_activity = select(
        (
            eq("device", "ps5"),
            select(
                (
                    conjunction(inp("ps5_session"), compare(inp("ps5_title"), literal(""), "ne")),
                    literal("gaming", "enum", values),
                ),
                (negate(inp("ps5_session")), literal("ps5_menu", "enum", values)),
            ),
        ),
        (literal(True), inp("usage")),
    )
    public = select(
        (compare(branch, literal("idle")), literal("idle", "enum", values)),
        (compare(branch, literal("pc")), pc_activity),
        (compare(branch, literal("tv")), tv_activity),
        (literal(True), literal("unresolved", "enum", values)),
    )
    # The machine retains history during a gap; public selection still needs both powers.
    public = select((conjunction(disjunction(tv, negate(tv)), disjunction(pc, negate(pc))), public))
    definitions.append(
        definition(
            "media.activity_flow",
            {
                **{n: schema() for n in ["tv", "pc", "ps5", "appletv", "ps5_session"]},
                "ps5_title": schema("text"),
                "discord": schema("text"),
                "device": schema("text"),
                "usage": schema("enum", usage),
            },
            {"value": public, "branch": branch},
            {"value": schema("enum", values), "branch": schema("enum", states)},
            rules=rules,
            machine=machine,
        )
    )
    lead = ["ps5", "appletv", "homepods", "keines"]
    definitions.append(
        definition(
            "media.lead_device",
            {"tv": schema(), "device": schema("text"), "playing": schema()},
            {
                "value": select(
                    (
                        inp("tv"),
                        mapping(
                            inp("device"),
                            [("ps5", "ps5"), ("apple_tv", "appletv"), ("sonstige", "keines")],
                            "enum",
                            lead,
                        ),
                    ),
                    (
                        negate(inp("tv")),
                        mapping(
                            inp("playing"), [(True, "homepods"), (False, "keines")], "enum", lead
                        ),
                    ),
                )
            },
            {"value": schema("enum", lead)},
        )
    )
    # Commands are already idempotent in the generic command log; no manual source.
    sleep = fallback(eq("sleep", "sleep"), literal(False))
    context_machine = dict(
        id="context",
        states=["off", "on"],
        initial="off",
        transition_selection="ordered",
        commands={"start": {}, "stop": {}},
        reinitialize_when=compare(inp("sleep"), inp("sleep")),
        transitions=[
            dict(source=s, target=target, event=ev, guard=guard, reason=reason)
            for s in ["off", "on"]
            for target, ev, guard, reason in [
                ("off", "start", sleep, "already_sleeping"),
                ("on", "start", literal(True), "manual_start"),
                ("off", "stop", literal(True), "manual_stop"),
                ("off", "input", sleep, "sleep_ended_context"),
            ]
        ],
    )
    definitions.append(
        definition(
            "presence.command_context",
            {"sleep": schema("text")},
            {
                "value": fallback(
                    select(
                        (
                            disjunction(eq("sleep", "sleep"), negate(eq("sleep", "sleep"))),
                            compare(dict(op="machine"), literal("on")),
                        )
                    ),
                    compare(dict(op="machine"), literal("on")),
                )
            },
            {"value": schema()},
            machine=context_machine,
        )
    )
    positive = disjunction(
        compare(inp("streams"), literal(0, "number"), "gt"),
        conjunction(inp("appletv"), eq("app_class", "privat")),
    )
    positive_only = select((ref("positive"), literal(True)))
    definitions.append(
        definition(
            "media.private_time",
            {"streams": schema("number"), "appletv": schema(), "app_class": schema("text")},
            {
                "value": fallback(
                    grace(positive_only, "private_end", "private_end_grace_s"), ref("positive")
                )
            },
            {"value": schema()},
            rules={"positive": positive},
            parameters={"private_end_grace_s": duration("Endfrist in Sekunden")},
        )
    )
    definitions.append(
        definition(
            "media.privacy_facts",
            {"private_time": schema(), "date_time": schema()},
            {
                "private_time": inp("private_time"),
                "date_time": inp("date_time"),
                "active": disjunction(inp("private_time"), inp("date_time")),
            },
            {name: schema() for name in ["private_time", "date_time", "active"]},
        )
    )
    definitions.append(
        definition(
            "media.quiet_facts",
            {"door": schema("text"), "playing": schema(), "track_class": schema("text")},
            {
                "entry_door_open": eq("door", "open"),
                "track_muted": conjunction(inp("playing"), eq("track_class", "stumm")),
            },
            {"entry_door_open": schema(), "track_muted": schema()},
        )
    )
    return definitions


def catalogs():
    entries = {
        "homepods": (
            "normal",
            [
                ("normal", "Normal", "Dieses Lied hat keine besondere Einordnung."),
                ("boost", "Boost", "Dieses Lied ist als besonders erwünscht markiert."),
                ("stumm", "Stumm", "Dieses Lied ist als stumm eingeordnet."),
                ("leiser", "Leiser", "Dieses Lied ist als weniger erwünscht markiert."),
            ],
        ),
        "ps5": (
            "heimkino",
            [
                ("heimkino", "Heimkino", "Dieses Spiel ist für das Heimkino eingeordnet."),
                (
                    "grind",
                    "Grind",
                    "Dieses Spiel ist für wiederholende Spielabschnitte eingeordnet.",
                ),
                ("headset", "Headset", "Dieses Spiel ist für die Nutzung mit Headset eingeordnet."),
            ],
        ),
        "pc": (
            "headset",
            [
                ("heimkino", "Heimkino", "Dieses Spiel ist für das Heimkino eingeordnet."),
                (
                    "grind",
                    "Grind",
                    "Dieses Spiel ist für wiederholende Spielabschnitte eingeordnet.",
                ),
                ("headset", "Headset", "Dieses Spiel ist für die Nutzung mit Headset eingeordnet."),
            ],
        ),
        "stash": (
            "privat",
            [
                ("normal", "Normal", "Dieser Titel ist nicht als privat eingeordnet."),
                ("privat", "Privat", "Dieser Titel gehört zum privaten Medienkontext."),
            ],
        ),
        "appletv": (
            "normal",
            [
                ("normal", "Normal", "Diese App ist nicht als privat eingeordnet."),
                ("privat", "Privat", "Diese App gehört zum privaten Medienkontext."),
            ],
        ),
        "tv_device": (
            "sonstige",
            [
                ("ps5", "PlayStation 5", "Diese Quelle zeigt die PlayStation 5."),
                ("apple_tv", "Apple TV", "Diese Quelle zeigt das Apple TV."),
                (
                    "sonstige",
                    "Sonstiges Gerät",
                    "Diese Quelle ist keinem der beiden Geräte zugeordnet.",
                ),
            ],
        ),
        "tv_activity": (
            "other",
            [
                ("tv", "Fernsehen", "Diese Quelle zeigt Live-Fernsehen."),
                ("streaming", "Streaming", "Diese Quelle zeigt appbasierte Medien."),
                ("gaming", "Spielen", "Diese Quelle wird zum Spielen genutzt."),
                ("other", "Noch nicht zugeordnet", "Für diese Quelle fehlt die Nutzungsart."),
            ],
        ),
    }
    return [
        dict(
            catalog_id="media." + device,
            kind="managed",
            version=1,
            identity_rule=dict(
                version=1,
                fields=["scope", "media_type", "signal_type", "normalized_key"],
                normalizer="normalize_key",
            ),
            classification_schema=dict(
                type="enum",
                values=[value for value, _, _ in rows],
                legend={
                    value: dict(display_name=name, description=text) for value, name, text in rows
                },
            ),
            unassigned_value=default,
            master_depth=1,
        )
        for device, (default, rows) in entries.items()
    ]


def tv_usage_v2():
    """Explicit new version: a TV-off guard preserves positive non-applicability."""
    import copy

    result = copy.deepcopy(next(item for item in build() if item["type_id"] == "media.tv_usage"))
    result["type_version"] = 2
    result["inputs"].insert(0, dict(name="applicable", schema=schema()))
    result["outputs"]["value"] = select(
        (negate(inp("applicable")), not_applicable("enum", ["tv", "streaming", "gaming", "other"])),
        (inp("applicable"), result["outputs"]["value"]),
    )
    return result


if __name__ == "__main__":
    root = Path(__file__).parents[1]
    definitions = build()
    for item in definitions:
        check_definition(DeclarativeDefinition.model_validate(item))
    for name, data in [
        ("media-templates-v1.json", definitions),
        ("media-catalogs-v1.json", catalogs()),
        ("media-templates-v2.json", [tv_usage_v2()]),
    ]:
        (root / "examples" / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"Validated {len(definitions)} portable definitions and {len(catalogs())} catalogs")
