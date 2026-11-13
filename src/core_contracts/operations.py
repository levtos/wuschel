"""Bounded operating settings and their separate, atomic local file.

This kernel module owns no configuration, credentials, or database state.
All settings are applied on process startup; saving never mutates live queues.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from .model import Model


def setting(
    default: int | float,
    minimum: int | float,
    maximum: int | float,
    unit: str,
    name: str,
    explanation: str,
) -> Any:
    return Field(
        default=default,
        ge=minimum,
        le=maximum,
        strict=True,
        title=name,
        description=explanation,
        json_schema_extra={"unit": unit, "restart_required": True},
    )


class OperationsSettings(Model):
    ha_heartbeat_s: float = setting(
        20,
        5,
        120,
        "s",
        "HA-Anbindung: Lebenszeichen",
        "Nach dieser Ruhezeit prüft Wuschel die Verbindung mit einem Lebenszeichen.",
    )
    setup_bridge_timeout_s: float = setting(
        15,
        1,
        120,
        "s",
        "Einrichtung: HA-Anfragefrist",
        "So lange wartet die Einrichtung auf einen HA-Konfigurationsschritt.",
    )
    setup_reachability_timeout_s: float = setting(
        5,
        1,
        60,
        "s",
        "Einrichtung: Erreichbarkeitsfrist",
        "So lange darf die Einrichtung die Datenbank-Erreichbarkeit prüfen.",
    )
    setup_status_timeout_s: float = setting(
        10,
        1,
        60,
        "s",
        "Einrichtung: Traber-Prüffrist",
        "So lange wartet die Einrichtung auf den Zustand von Traber.",
    )
    title_connect_timeout_s: float = setting(
        5,
        1,
        120,
        "s",
        "Titelübernahme: Verbindungsfrist",
        "So lange wartet die Titelübernahme auf ihre Leseverbindung.",
    )
    title_command_timeout_s: float = setting(
        5,
        1,
        120,
        "s",
        "Titelübernahme: Befehlsfrist",
        "So lange darf ein Lesebefehl der Titelübernahme dauern.",
    )
    title_read_timeout_s: float = setting(
        30,
        1,
        300,
        "s",
        "Titelübernahme: Lesefrist",
        "So lange darf die gesamte Titelübernahme Daten lesen.",
    )
    title_close_timeout_s: float = setting(
        5,
        1,
        30,
        "s",
        "Titelübernahme: Abschlussfrist",
        "So lange wartet die Titelübernahme auf das Schließen ihrer Verbindung.",
    )
    title_prefetch_size: int = setting(
        100,
        1,
        1000,
        "Zeilen",
        "Titelübernahme: Lesepaket",
        "So viele Zeilen liest die Titelübernahme auf einmal aus der Datenbank.",
    )
    observation_block_window_s: float = setting(
        0.05,
        0.001,
        0.5,
        "s",
        "Eingänge: Sammelzeit",
        "So lange sammelt Wuschel aufeinanderfolgende Eingänge vor einer gemeinsamen Speicherung.",
    )
    observation_block_size: int = setting(
        100,
        1,
        1000,
        "Einträge",
        "Eingänge: Sammelgröße",
        "So viele aufeinanderfolgende Eingänge werden höchstens gemeinsam gespeichert.",
    )
    subscriber_queue_size: int = setting(
        32,
        1,
        16384,
        "Nachrichten",
        "Oberfläche: Nachrichten-Warteschlange",
        "So viele Nachrichten dürfen je verbundenem Empfänger auf ihre Ausgabe warten.",
    )
    database_close_timeout_s: float = setting(
        3,
        1,
        15,
        "s",
        "Datenbank: Abschlussfrist",
        "So lange wartet Wuschel beim Schließen auf die Schreibverbindung.",
    )
    websocket_close_timeout_s: float = setting(
        2,
        0.5,
        10,
        "s",
        "Empfänger: Abschlussfrist",
        "So lange wartet Wuschel beim Beenden auf einen verbundenen Empfänger.",
    )
    database_command_timeout_s: float = setting(
        10, 1, 120, "s", "Datenbank: Befehlsfrist", "So lange darf ein Datenbankbefehl dauern."
    )
    database_connect_timeout_s: float = setting(
        10,
        1,
        120,
        "s",
        "Datenbank: Verbindungsfrist",
        "So lange wartet Wuschel auf eine neue Datenbankverbindung.",
    )
    database_idle_session_timeout_s: int = setting(
        600,
        60,
        86400,
        "s",
        "Datenbank: Leerlauffrist",
        "Nach dieser Zeit darf die Datenbank eine ungenutzte Schreibverbindung schließen.",
    )
    database_pool_size: int = setting(
        2,
        1,
        16,
        "Verbindungen",
        "Datenbank: Leseverbindungen",
        "So viele Leseverbindungen darf Wuschel gleichzeitig öffnen.",
    )
    database_idle_transaction_timeout_s: int = setting(
        15,
        1,
        120,
        "s",
        "Datenbank: Unfertige Speicherung",
        "Nach dieser Wartezeit beendet die Datenbank eine ungenutzte offene Transaktion.",
    )
    database_keepalive_idle_s: int = setting(
        15,
        1,
        300,
        "s",
        "Datenbank: Erste Verbindungsprüfung",
        "Nach dieser Ruhezeit prüft die Datenbank die Schreibverbindung.",
    )
    database_keepalive_interval_s: int = setting(
        5,
        1,
        60,
        "s",
        "Datenbank: Weitere Verbindungsprüfung",
        "So viel Zeit liegt zwischen weiteren Verbindungsprüfungen.",
    )
    database_keepalive_count: int = setting(
        3,
        1,
        10,
        "Versuche",
        "Datenbank: Verbindungsprüfungen",
        "So viele fehlgeschlagene Prüfungen sind erlaubt.",
    )
    database_tcp_timeout_s: int = setting(
        30,
        1,
        300,
        "s",
        "Datenbank: Empfangsfrist",
        "So lange wartet die Schreibverbindung auf eine Bestätigung.",
    )
    ingest_queue_size: int = setting(
        256,
        1,
        16384,
        "Einträge",
        "Eingangs-Warteschlange",
        "So viele Eingänge können auf ihre Verarbeitung warten.",
    )
    refresh_queue_size: int = setting(
        256,
        1,
        16384,
        "Einträge",
        "Auffrisch-Warteschlange",
        "So viele Quellen können auf eine neue Abfrage warten.",
    )
    mqtt_queue_size: int = setting(
        256,
        1,
        16384,
        "Nachrichten",
        "MQTT-Warteschlange",
        "So viele MQTT-Nachrichten können auf ihre Übernahme warten.",
    )
    management_concurrency: int = setting(
        2,
        1,
        8,
        "Vorgänge",
        "Gleichzeitige Verwaltungsaufgaben",
        "So viele vorbereitende Verwaltungsaufgaben dürfen gleichzeitig laufen.",
    )
    ha_retry_max_s: float = setting(
        5,
        1,
        300,
        "s",
        "HA-Anbindung: Größter Wiederholabstand",
        "Nach einem Fehler wartet Wuschel höchstens so lange auf den nächsten Verbindungsversuch.",
    )
    mqtt_retry_max_s: float = setting(
        30,
        1,
        300,
        "s",
        "MQTT: Größter Wiederholabstand",
        "Nach einem Fehler wartet Wuschel höchstens so lange auf den nächsten MQTT-Versuch.",
    )
    projection_retry_max_s: float = setting(
        5,
        0.25,
        300,
        "s",
        "HA-Ausgabe: Größter Wiederholabstand",
        "Nach einer fehlenden Bestätigung wartet Wuschel höchstens so lange auf die nächste Ausgabe.",
    )
    startup_retry_max_s: float = setting(
        30,
        1,
        300,
        "s",
        "Start: Größter Wiederholabstand",
        "Beim Start und bei der Datenbank-Erholung wartet Wuschel höchstens so lange auf den nächsten Versuch.",
    )
    scheduler_interval_s: float = setting(
        0.5, 0.1, 5, "s", "Verarbeitungstakt", "In diesem Abstand prüft Wuschel fällige Aufgaben."
    )
    health_interval_s: float = setting(
        2,
        0.5,
        30,
        "s",
        "Datenbank-Prüftakt",
        "In diesem Abstand prüft Wuschel, ob die Speicherung wieder verfügbar ist.",
    )
    adapter_poll_interval_s: float = setting(
        1,
        0.1,
        10,
        "s",
        "Anbindungs-Prüftakt",
        "In diesem Abstand prüfen ruhende Anbindungen auf eine neue Konfiguration.",
    )
    http_timeout_s: float = setting(
        30,
        1,
        120,
        "s",
        "Anbindung: Anfragefrist",
        "So lange darf eine gewöhnliche Anfrage an einen angebundenen Dienst dauern.",
    )
    ha_discovery_cache_s: float = setting(
        15,
        1,
        300,
        "s",
        "Geräteliste: Zwischenspeicher",
        "So lange darf Wuschel die zuletzt gelesene Geräteliste wiederverwenden.",
    )
    ha_discovery_timeout_s: float = setting(
        10,
        1,
        60,
        "s",
        "Geräteliste: Anfragefrist",
        "So lange wartet Wuschel auf die Geräteliste aus Home Assistant.",
    )
    ha_service_timeout_s: float = setting(
        12,
        1,
        60,
        "s",
        "HA-Dienst: Antwortfrist",
        "So lange wartet Wuschel auf eine Antwort auf einen Dienstaufruf.",
    )
    projection_timeout_s: float = setting(
        20,
        1,
        120,
        "s",
        "HA-Ausgabe: Antwortfrist",
        "So lange wartet Wuschel auf die Bestätigung von Traber.",
    )
    projection_heartbeat_s: float = setting(
        5,
        1,
        60,
        "s",
        "HA-Ausgabe: Prüftakt",
        "In diesem Abstand bestätigt Wuschel seinen sichtbaren Zustand erneut.",
    )
    http_shutdown_timeout_s: float = setting(
        3,
        1,
        15,
        "s",
        "Oberfläche: Abschlussfrist",
        "So lange dürfen offene Oberflächenanfragen beim Beenden auslaufen.",
    )
    shutdown_timeout_s: float = setting(
        25,
        5,
        120,
        "s",
        "Programm: Abschlussfrist",
        "So lange darf Wuschel beim Beenden seine Arbeit abschließen.",
    )


class OperationsFile:
    def __init__(
        self, path: Path | None = None, *, active: OperationsSettings | None = None
    ) -> None:
        self.path = path
        self.notice: str | None = None
        try:
            self.saved = (
                OperationsSettings.model_validate_json(path.read_bytes())
                if path
                else active or OperationsSettings()
            )
        except FileNotFoundError:
            self.saved = OperationsSettings()
            self.notice = "operations_missing"
        except OSError, ValueError, ValidationError:
            self.saved = OperationsSettings()
            self.notice = "operations_invalid"
        self.active = active or self.saved

    def view(self) -> dict[str, Any]:
        schema = OperationsSettings.model_json_schema()["properties"]
        saved, active = self.saved.model_dump(), self.active.model_dump()
        return {
            "notice": self.notice,
            "settings": [
                {
                    "key": key,
                    "value": saved[key],
                    "active_value": active[key],
                    "default": item["default"],
                    "minimum": item["minimum"],
                    "maximum": item["maximum"],
                    "unit": item["unit"],
                    "name": item["title"],
                    "explanation": item["description"],
                    "integer": item["type"] == "integer",
                    "restart_required": item["restart_required"],
                    "pending_restart": saved[key] != active[key],
                }
                for key, item in schema.items()
            ],
        }

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        candidate = OperationsSettings.model_validate({**self.saved.model_dump(), **values})
        if self.path is None:
            raise OSError("operations_file_unavailable")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".operations-",
                delete=False,
            ) as handle:
                temporary = handle.name
                json.dump(candidate.model_dump(), handle, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        self.saved, self.notice = candidate, None
        return self.view()

    def reset(self, key: str | None = None) -> dict[str, Any]:
        defaults = OperationsSettings().model_dump()
        if key is not None and key not in defaults:
            raise ValueError("unknown_operation_setting")
        return self.update({key: defaults[key]} if key is not None else defaults)
