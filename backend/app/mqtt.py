"""MQTT integration for handling communication with the device."""

import asyncio
import json
import logging
from typing import Any, Dict
import aiomqtt
from app.config import Settings
from app.schemas import TelemetryPayload
from app.workers import telemetry_queue

logger = logging.getLogger(__name__)

class MqttClient:
    """Manages MQTT connection and message routing."""
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: Any = None
        self._connected = False
        self._task = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(self):
        """Connect to broker and start listening loop."""
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """Stop the listener."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self):
        reconnect_interval = 5
        while True:
            try:
                self.client = aiomqtt.Client(
                    hostname=self.settings.mqtt_host,
                    port=self.settings.mqtt_port,
                    username=self.settings.mqtt_username,
                    password=self.settings.mqtt_password
                )
                async with self.client:
                    self._connected = True
                    logger.info("Connected to MQTT broker")
                    await self.client.subscribe("solar/+/telemetry")
                    await self.client.subscribe("solar/+/status")
                    await self.client.subscribe("solar/+/motor/state")
                    await self.client.subscribe("solar/+/cmd/ack")
                    await self.client.subscribe("solar/+/alerts")
                    
                    async for message in self.client.messages:
                        await self._on_message(message)
            except aiomqtt.MqttError as error:
                self._connected = False
                logger.error(f"MQTT connection error: {error}. Reconnecting in {reconnect_interval}s.")
                await asyncio.sleep(reconnect_interval)
            except asyncio.CancelledError:
                self._connected = False
                logger.info("MQTT client stopped")
                break
            except Exception as e:
                self._connected = False
                logger.exception(f"Unexpected MQTT error: {e}")
                await asyncio.sleep(reconnect_interval)

    async def _on_message(self, message: aiomqtt.Message):
        """Route incoming messages based on topic."""
        topic_str = str(message.topic)
        parts = topic_str.split('/')
        if len(parts) < 3 or parts[0] != "solar":
            return
            
        device_id = parts[1]
        suffix = "/".join(parts[2:])
        
        try:
            payload_str = message.payload.decode()
            payload_dict = json.loads(payload_str)
        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON from {topic_str}")
            return
        except UnicodeDecodeError:
            logger.error(f"Failed to decode string from {topic_str}")
            return

        try:
            if suffix == "telemetry":
                await self._handle_telemetry(device_id, payload_dict)
            elif suffix == "status":
                await self._handle_status(device_id, payload_dict)
            elif suffix == "motor/state":
                await self._handle_motor_state(device_id, payload_dict)
            elif suffix == "cmd/ack":
                await self._handle_cmd_ack(device_id, payload_dict)
            elif suffix == "alerts":
                await self._handle_alert(device_id, payload_dict)
        except Exception as e:
            logger.exception(f"Error handling message for {topic_str}: {e}")

    async def _handle_telemetry(self, device_id: str, payload_dict: Dict[str, Any]):
        """Validate and queue telemetry payload."""
        payload_dict['device_id'] = device_id
        try:
            payload = TelemetryPayload.model_validate(payload_dict)
            await telemetry_queue.put_telemetry(payload)
        except Exception as e:
            logger.error(f"Invalid telemetry payload: {e}")

    async def _handle_status(self, device_id: str, payload_dict: Dict[str, Any]):
        pass

    async def _handle_motor_state(self, device_id: str, payload_dict: Dict[str, Any]):
        pass

    async def _handle_cmd_ack(self, device_id: str, payload_dict: Dict[str, Any]):
        pass

    async def _handle_alert(self, device_id: str, payload_dict: Dict[str, Any]):
        pass

    async def publish_command(self, device_id: str, command_dict: Dict[str, Any]):
        """Publish a command to a device."""
        if not self._connected:
            raise RuntimeError("MQTT not connected")
        topic = f"solar/{device_id}/cmd"
        payload = json.dumps(command_dict)
        await self.client.publish(topic, payload)

