"""
<plugin key="ShellyGen2Switch" name="Shelly Gen2+ Switch" author="lemassykoi" version="2.1" externallink="https://github.com/lemassykoi/Domoticz-Shelly-Plugin">
    <description>
        <h2>Shelly Gen2+ Plugin</h2><br/>
        WebSocket-based integration for Shelly Gen2+ devices with switch and/or energy metering.<br/>
        <br/>
        <h3>Supported devices</h3>
        <ul style="list-style-type:square">
            <li>Shelly Pro 1PM (1 switch)</li>
            <li>Shelly Outdoor Plug S Gen3 (1 switch + temperature)</li>
            <li>Shelly Power Strip Gen4 (4 switches)</li>
            <li>Shelly Pro EM-50 (2 EM channels + dry-contact relay)</li>
            <li>Any other Shelly Gen2+ device with switch and/or EM1 components</li>
        </ul>
        <h3>Features</h3>
        <ul style="list-style-type:square">
            <li>Switch (On/Off control)</li>
            <li>Energy (W + Wh)</li>
            <li>Voltage (V)</li>
            <li>Current (A)</li>
            <li>Frequency (Hz)</li>
            <li>Temperature (if reported by device)</li>
        </ul>
    </description>
    <params>
        <param field="Address" label="IP Address" width="130px" required="true" default="192.168.0.10"/>
        <param field="Password" label="Shelly Password" width="200px" password="true" required="false" default=""/>
        <param field="Mode1" label="Friendly Name" width="120px" required="true" default="Shelly"/>
        <param field="Mode6" label="Debug" width="150px">
            <options>
                <option label="None" value="0" default="true"/>
                <option label="Plugin Debug" value="2"/>
                <option label="All" value="1"/>
            </options>
        </param>
    </params>
</plugin>
"""

import DomoticzEx as Domoticz
import json
import re
import secrets
import base64
import uuid
import hashlib
import time

SHA256_HA2 = hashlib.sha256(b"dummy_method:dummy_uri").hexdigest()


UNITS_PER_CHANNEL = 4
UNIT_OFFSET_SWITCH = 0
UNIT_OFFSET_ENERGY = 1
UNIT_OFFSET_FREQUENCY = 2
UNIT_OFFSET_TEMPERATURE = 3

EM_BASE_OFFSET = 100
UNITS_PER_EM_CHANNEL = 4
EM_UNIT_OFFSET_POWER = 0
EM_UNIT_OFFSET_VOLTAGE = 1
EM_UNIT_OFFSET_CURRENT = 2
EM_UNIT_OFFSET_FREQUENCY = 3


class BasePlugin:
    websocketConn = None
    reconAgain = 3
    debug = False
    client_id = None

    def __init__(self):
        self.channel_cache = {}
        self.discovered_channels = set()
        self.channel_has_temp = set()
        self.channel_names = {}
        self.total_channels = 0
        self.pending_config = None
        self.pending_status = None
        self.auth = None
        self.pending_commands = []
        self.inflight_commands = {}
        self.next_cmd_id = 100
        self.is_reconnect = False
        self.em_cache = {}
        self.discovered_em_channels = set()
        self.em_channel_names = {}
        self.last_uptime = None

    def _base_unit(self, ch):
        return 1 + ch * UNITS_PER_CHANNEL

    def _device_ids(self, ch):
        return {
            "switch": f"switch:{ch}",
            "energy": f"switch:{ch}:energy",
            "freq": f"switch:{ch}:freq",
            "temp": f"switch:{ch}:temp",
        }

    def _channel_label(self, ch, suffix):
        friendly = str(Parameters["Mode1"])
        if self.total_channels > 1:
            shelly_name = self.channel_names.get(ch)
            if shelly_name:
                return f"{friendly} {shelly_name} {suffix}"
            return f"{friendly} Plug {ch + 1} {suffix}"
        return f"{friendly} {suffix}"

    def _ensure_channel_devices(self, ch, has_temp=False):
        if ch in self.discovered_channels:
            return
        self.discovered_channels.add(ch)
        if has_temp:
            self.channel_has_temp.add(ch)
        self.channel_cache[ch] = {"power": 0.0, "energy_wh": 0.0, "freq": 0.0, "temp": None}

        base = self._base_unit(ch)
        ids = self._device_ids(ch)

        if ids["switch"] not in Devices:
            try:
                Domoticz.Unit(
                    Name=self._channel_label(ch, "Switch"),
                    DeviceID=ids["switch"],
                    Unit=base + UNIT_OFFSET_SWITCH,
                    Type=244, Subtype=73, Used=1, Switchtype=0,
                ).Create()
                Domoticz.Log(f"Created switch device for channel {ch}")
            except Exception as e:
                Domoticz.Debug(f"Switch device ch{ch} creation failed: {e}")

        if ids["energy"] not in Devices:
            try:
                Domoticz.Unit(
                    Name=self._channel_label(ch, "Energy"),
                    DeviceID=ids["energy"],
                    Unit=base + UNIT_OFFSET_ENERGY,
                    Type=243, Subtype=29, Used=1,
                ).Create()
                Domoticz.Log(f"Created energy device for channel {ch}")
            except Exception as e:
                Domoticz.Debug(f"Energy device ch{ch} creation failed: {e}")

        if ids["freq"] not in Devices:
            try:
                Domoticz.Unit(
                    Name=self._channel_label(ch, "Frequency"),
                    DeviceID=ids["freq"],
                    Unit=base + UNIT_OFFSET_FREQUENCY,
                    Type=243, Subtype=31, Used=0,
                    Options={"Custom": "1;Hz"},
                ).Create()
                Domoticz.Log(f"Created frequency device for channel {ch}")
            except Exception as e:
                Domoticz.Debug(f"Frequency device ch{ch} creation failed: {e}")

        if has_temp and ids["temp"] not in Devices:
            try:
                Domoticz.Unit(
                    Name=self._channel_label(ch, "Temperature"),
                    DeviceID=ids["temp"],
                    Unit=base + UNIT_OFFSET_TEMPERATURE,
                    Type=80, Subtype=5, Used=1,
                ).Create()
                Domoticz.Log(f"Created temperature device for channel {ch}")
            except Exception as e:
                Domoticz.Debug(f"Temperature device ch{ch} creation failed: {e}")

    def _process_switch_data(self, ch, data, skip_output=False):
        Domoticz.Debug(f"Processing switch:{ch} data: {json.dumps(data)}")

        cache = self.channel_cache.setdefault(ch, {"power": 0.0, "energy_wh": 0.0, "freq": 0.0, "temp": None})
        ids = self._device_ids(ch)
        base = self._base_unit(ch)

        if "apower" in data:
            cache["power"] = abs(data["apower"])

        if "aenergy" in data and "total" in data["aenergy"]:
            cache["energy_wh"] = data["aenergy"]["total"]

        if "freq" in data:
            cache["freq"] = data["freq"]

        if "temperature" in data:
            t = data["temperature"]
            if isinstance(t, dict):
                cache["temp"] = t.get("tC", t.get("tF"))
            elif isinstance(t, (int, float)):
                cache["temp"] = t

        if "output" in data and not skip_output and ids["switch"] in Devices:
            is_on = data["output"]
            unit = base + UNIT_OFFSET_SWITCH
            Devices[ids["switch"]].Units[unit].nValue = 1 if is_on else 0
            Devices[ids["switch"]].Units[unit].sValue = "On" if is_on else "Off"
            Devices[ids["switch"]].Units[unit].Update(Log=True)
            Domoticz.Log(f"Switch:{ch} updated: {'On' if is_on else 'Off'}")

        if ("aenergy" in data or "apower" in data) and ids["energy"] in Devices:
            sValue = f"{cache['power']:.1f};{cache['energy_wh']:.1f}"
            unit = base + UNIT_OFFSET_ENERGY
            Devices[ids["energy"]].Units[unit].nValue = 0
            Devices[ids["energy"]].Units[unit].sValue = sValue
            Devices[ids["energy"]].Units[unit].Update(Log=True)
            Domoticz.Debug(f"Energy:{ch} updated: {sValue}")

        if "freq" in data and ids["freq"] in Devices:
            unit = base + UNIT_OFFSET_FREQUENCY
            Devices[ids["freq"]].Units[unit].nValue = 0
            Devices[ids["freq"]].Units[unit].sValue = str(cache["freq"])
            Devices[ids["freq"]].Units[unit].Update(Log=True)
            Domoticz.Debug(f"Frequency:{ch} updated: {cache['freq']}")

        if cache["temp"] is not None and ids["temp"] in Devices:
            unit = base + UNIT_OFFSET_TEMPERATURE
            Devices[ids["temp"]].Units[unit].nValue = 0
            Devices[ids["temp"]].Units[unit].sValue = str(cache["temp"])
            Devices[ids["temp"]].Units[unit].Update(Log=True)
            Domoticz.Debug(f"Temperature:{ch} updated: {cache['temp']}")

    def _em_base_unit(self, ch):
        return EM_BASE_OFFSET + ch * UNITS_PER_EM_CHANNEL

    def _em_device_ids(self, ch):
        return {
            "power": f"em1:{ch}:power",
            "voltage": f"em1:{ch}:voltage",
            "current": f"em1:{ch}:current",
            "freq": f"em1:{ch}:freq",
        }

    def _em_channel_label(self, ch, suffix):
        friendly = str(Parameters["Mode1"])
        name = self.em_channel_names.get(ch)
        if name:
            return f"{friendly} {name} {suffix}"
        return f"{friendly} EM {ch + 1} {suffix}"

    def _ensure_em_devices(self, ch):
        if ch in self.discovered_em_channels:
            return
        self.discovered_em_channels.add(ch)
        self.em_cache[ch] = {"act_power": 0.0, "voltage": 0.0, "current": 0.0, "freq": 0.0, "total_act_energy": 0.0}

        base = self._em_base_unit(ch)
        ids = self._em_device_ids(ch)

        if ids["power"] not in Devices:
            try:
                Domoticz.Unit(
                    Name=self._em_channel_label(ch, "Energy"),
                    DeviceID=ids["power"],
                    Unit=base + EM_UNIT_OFFSET_POWER,
                    Type=243, Subtype=29, Used=1,
                ).Create()
                Domoticz.Log(f"Created EM power device for em1:{ch}")
            except Exception as e:
                Domoticz.Debug(f"EM power device em1:{ch} creation failed: {e}")

        if ids["voltage"] not in Devices:
            try:
                Domoticz.Unit(
                    Name=self._em_channel_label(ch, "Voltage"),
                    DeviceID=ids["voltage"],
                    Unit=base + EM_UNIT_OFFSET_VOLTAGE,
                    Type=243, Subtype=31, Used=1,
                    Options={"Custom": "1;V"},
                ).Create()
                Domoticz.Log(f"Created EM voltage device for em1:{ch}")
            except Exception as e:
                Domoticz.Debug(f"EM voltage device em1:{ch} creation failed: {e}")

        if ids["current"] not in Devices:
            try:
                Domoticz.Unit(
                    Name=self._em_channel_label(ch, "Current"),
                    DeviceID=ids["current"],
                    Unit=base + EM_UNIT_OFFSET_CURRENT,
                    Type=243, Subtype=31, Used=1,
                    Options={"Custom": "1;A"},
                ).Create()
                Domoticz.Log(f"Created EM current device for em1:{ch}")
            except Exception as e:
                Domoticz.Debug(f"EM current device em1:{ch} creation failed: {e}")

        if ids["freq"] not in Devices:
            try:
                Domoticz.Unit(
                    Name=self._em_channel_label(ch, "Frequency"),
                    DeviceID=ids["freq"],
                    Unit=base + EM_UNIT_OFFSET_FREQUENCY,
                    Type=243, Subtype=31, Used=0,
                    Options={"Custom": "1;Hz"},
                ).Create()
                Domoticz.Log(f"Created EM frequency device for em1:{ch}")
            except Exception as e:
                Domoticz.Debug(f"EM frequency device em1:{ch} creation failed: {e}")

    def _process_em1_data(self, ch, data):
        Domoticz.Debug(f"Processing em1:{ch} data: {json.dumps(data)}")

        cache = self.em_cache.setdefault(ch, {"act_power": 0.0, "voltage": 0.0, "current": 0.0, "freq": 0.0, "total_act_energy": 0.0})
        ids = self._em_device_ids(ch)
        base = self._em_base_unit(ch)

        if "act_power" in data:
            cache["act_power"] = data["act_power"]

        if "voltage" in data:
            cache["voltage"] = data["voltage"]

        if "current" in data:
            cache["current"] = data["current"]

        if "freq" in data:
            cache["freq"] = data["freq"]

        if ("act_power" in data or "voltage" in data) and ids["power"] in Devices:
            sValue = f"{cache['act_power']:.1f};{cache['total_act_energy']:.1f}"
            unit = base + EM_UNIT_OFFSET_POWER
            Devices[ids["power"]].Units[unit].nValue = 0
            Devices[ids["power"]].Units[unit].sValue = sValue
            Devices[ids["power"]].Units[unit].Update(Log=True)
            Domoticz.Debug(f"EM Power em1:{ch} updated: {sValue}")

        if "voltage" in data and ids["voltage"] in Devices:
            unit = base + EM_UNIT_OFFSET_VOLTAGE
            Devices[ids["voltage"]].Units[unit].nValue = 0
            Devices[ids["voltage"]].Units[unit].sValue = f"{cache['voltage']:.1f}"
            Devices[ids["voltage"]].Units[unit].Update(Log=True)
            Domoticz.Debug(f"EM Voltage em1:{ch} updated: {cache['voltage']}")

        if "current" in data and ids["current"] in Devices:
            unit = base + EM_UNIT_OFFSET_CURRENT
            Devices[ids["current"]].Units[unit].nValue = 0
            Devices[ids["current"]].Units[unit].sValue = f"{cache['current']:.3f}"
            Devices[ids["current"]].Units[unit].Update(Log=True)
            Domoticz.Debug(f"EM Current em1:{ch} updated: {cache['current']}")

        if "freq" in data and ids["freq"] in Devices:
            unit = base + EM_UNIT_OFFSET_FREQUENCY
            Devices[ids["freq"]].Units[unit].nValue = 0
            Devices[ids["freq"]].Units[unit].sValue = str(cache["freq"])
            Devices[ids["freq"]].Units[unit].Update(Log=True)
            Domoticz.Debug(f"EM Frequency em1:{ch} updated: {cache['freq']}")

    def _process_em1data(self, ch, data):
        Domoticz.Debug(f"Processing em1data:{ch} data: {json.dumps(data)}")

        cache = self.em_cache.setdefault(ch, {"act_power": 0.0, "voltage": 0.0, "current": 0.0, "freq": 0.0, "total_act_energy": 0.0})
        ids = self._em_device_ids(ch)
        base = self._em_base_unit(ch)

        if "total_act_energy" in data:
            cache["total_act_energy"] = data["total_act_energy"]

            if ids["power"] in Devices:
                sValue = f"{cache['act_power']:.1f};{cache['total_act_energy']:.1f}"
                unit = base + EM_UNIT_OFFSET_POWER
                Devices[ids["power"]].Units[unit].nValue = 0
                Devices[ids["power"]].Units[unit].sValue = sValue
                Devices[ids["power"]].Units[unit].Update(Log=True)
                Domoticz.Debug(f"EM Energy em1:{ch} updated: {sValue}")

    def _extract_channel_names(self, config):
        for key, value in config.items():
            if not isinstance(value, dict):
                continue
            m = re.match(r"^switch:(\d+)$", key)
            if m:
                ch = int(m.group(1))
                name = value.get("name")
                if name:
                    self.channel_names[ch] = name
                continue
            m = re.match(r"^em1:(\d+)$", key)
            if m:
                ch = int(m.group(1))
                name = value.get("name")
                if name:
                    self.em_channel_names[ch] = name

    def _process_sys_data(self, data):
        if not isinstance(data, dict):
            return
        uptime = data.get("uptime")
        if isinstance(uptime, (int, float)):
            if self.last_uptime is not None and uptime + 5 < self.last_uptime:
                Domoticz.Log(
                    f"Shelly uptime reset from {int(self.last_uptime)}s to {int(uptime)}s - device reboot/reset detected"
                )
            self.last_uptime = uptime

    def _discover_channels(self, status):
        self._process_sys_data(status.get("sys"))
        channels = []
        for key, value in status.items():
            m = re.match(r"^switch:(\d+)$", key)
            if m and isinstance(value, dict):
                channels.append((int(m.group(1)), value))
        self.total_channels = len(channels)
        for ch, value in channels:
            has_temp = "temperature" in value
            self._ensure_channel_devices(ch, has_temp=has_temp)
            self._process_switch_data(ch, value, skip_output=self.is_reconnect)

        for key, value in status.items():
            m = re.match(r"^em1:(\d+)$", key)
            if m and isinstance(value, dict):
                ch = int(m.group(1))
                self._ensure_em_devices(ch)
                self._process_em1_data(ch, value)
        for key, value in status.items():
            m = re.match(r"^em1data:(\d+)$", key)
            if m and isinstance(value, dict):
                ch = int(m.group(1))
                self._process_em1data(ch, value)

    def _try_complete_discovery(self):
        if self.pending_config is not None and self.pending_status is not None:
            self._extract_channel_names(self.pending_config)
            self._discover_channels(self.pending_status)
            self.pending_config = None
            self.pending_status = None

    def _build_auth(self, nonce, nc, realm):
        password = Parameters.get("Password", "")
        ha1 = hashlib.sha256(f"admin:{realm}:{password}".encode("utf-8")).hexdigest()
        cnonce = int(time.time())
        response = hashlib.sha256(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{SHA256_HA2}".encode("utf-8")).hexdigest()
        return {
            "realm": realm,
            "username": "admin",
            "nonce": nonce,
            "cnonce": cnonce,
            "response": response,
            "algorithm": "SHA-256",
        }

    def _send_ws(self, payload):
        if self.auth and "method" in payload:
            payload["auth"] = self.auth
        if self.websocketConn and self.websocketConn.Connected():
            raw = json.dumps(payload)
            Domoticz.Debug(f"WS send ({len(raw)}b): {raw}")
            self.websocketConn.Send({"Payload": raw, "Mask": secrets.randbits(32)})

    def _start_discovery(self):
        config_msg = {"id": 10, "src": self.client_id, "method": "Shelly.GetConfig", "params": {}}
        self._send_ws(config_msg)
        Domoticz.Log(f"Requested config for discovery (src: {self.client_id})")

    def onStart(self):
        self.client_id = str(uuid.uuid4())
        Domoticz.Log("onStart called")

        if Parameters["Mode6"] != "0":
            Domoticz.Debugging(int(Parameters["Mode6"]))
            DumpConfigToLog()
            self.debug = True

        Domoticz.Log("Available devices at start: " + str(list(Devices.keys())))

        self.websocketConn = Domoticz.Connection(
            Name="ShellyWebSocket",
            Transport="TCP/IP",
            Protocol="WS",
            Address=Parameters["Address"],
            Port="80",
        )
        self.websocketConn.Connect()

    def onConnect(self, Connection, Status, Description):
        if Status == 0:
            Domoticz.Log("Connected to: " + Connection.Address + ":" + Connection.Port)
            send_data = {
                "URL": "/rpc",
                "Headers": {
                    "Host": Parameters["Address"],
                    "Origin": "http://" + Parameters["Address"],
                    "Sec-WebSocket-Key": base64.b64encode(secrets.token_bytes(16)).decode("utf-8"),
                },
            }
            Connection.Send(send_data)
        else:
            Domoticz.Log(f"Failed to connect ({Status}) to: {Connection.Address}:{Connection.Port}")
            Domoticz.Debug(f"Connection error: {Description}")
        return True

    def onMessage(self, Connection, Data):
        Domoticz.Debug("onMessage called")

        if "Status" in Data:
            if Data["Status"] == "101":
                Domoticz.Log("WebSocket upgraded")
                password = Parameters.get("Password", "").strip()
                if password:
                    Domoticz.Log("Auth configured, sending probe for 401 challenge...")
                    probe = {"id": 0, "src": self.client_id, "method": "Shelly.GetStatus", "params": {}}
                    self.websocketConn.Send({"Payload": json.dumps(probe), "Mask": secrets.randbits(32)})
                else:
                    self._start_discovery()
            else:
                DumpWSResponseToLog(Data)

        elif "Operation" in Data:
            if Data["Operation"] == "Ping":
                Domoticz.Debug("Ping received")
                Connection.Send({"Operation": "Pong", "Payload": "Pong", "Mask": secrets.randbits(32)})
            elif Data["Operation"] == "Pong":
                Domoticz.Debug("Pong received")
            elif Data["Operation"] == "Close":
                Domoticz.Log("Close received")
            else:
                DumpWSResponseToLog(Data)

        elif "Payload" in Data:
            try:
                payload = json.loads(Data["Payload"])
                Domoticz.Debug("Received: " + json.dumps(payload))

                if "error" in payload and payload["error"].get("code") == 401:
                    try:
                        challenge = json.loads(payload["error"]["message"])
                        nonce = challenge["nonce"]
                        nc = challenge.get("nc", 1)
                        realm = challenge["realm"]
                        self.auth = self._build_auth(nonce, nc, realm)
                        failed_id = payload.get("id")
                        failed_cmd = self.inflight_commands.pop(failed_id, None)
                        if failed_cmd is not None:
                            Domoticz.Log(f"Auth refreshed for realm '{realm}', replaying rejected command id={failed_id}")
                            self.pending_commands.append(failed_cmd)
                        else:
                            Domoticz.Log(f"Auth built for realm '{realm}', starting discovery...")
                            self._start_discovery()
                        if self.pending_commands:
                            Domoticz.Log(f"Flushing {len(self.pending_commands)} queued command(s) now that auth is ready")
                            queued = self.pending_commands
                            self.pending_commands = []
                            for cmd in queued:
                                self.inflight_commands[cmd["id"]] = cmd
                                self._send_ws(cmd)
                    except Exception as e:
                        Domoticz.Error(f"Failed to parse 401 challenge: {e}")

                elif payload.get("method") == "NotifyStatus" and "params" in payload:
                    params = payload["params"]
                    for key, value in params.items():
                        if not isinstance(value, dict):
                            continue
                        if key == "sys":
                            self._process_sys_data(value)
                            continue
                        m = re.match(r"^switch:(\d+)$", key)
                        if m:
                            ch = int(m.group(1))
                            if ch not in self.discovered_channels:
                                self._ensure_channel_devices(ch, has_temp=("temperature" in value))
                            self._process_switch_data(ch, value)
                            continue
                        m = re.match(r"^em1:(\d+)$", key)
                        if m:
                            ch = int(m.group(1))
                            if ch not in self.discovered_em_channels:
                                self._ensure_em_devices(ch)
                            self._process_em1_data(ch, value)
                            continue
                        m = re.match(r"^em1data:(\d+)$", key)
                        if m:
                            ch = int(m.group(1))
                            self._process_em1data(ch, value)

                elif "result" in payload and "id" in payload:
                    if payload["id"] == 10:
                        Domoticz.Log("Received device config")
                        self.pending_config = payload["result"]
                        status_msg = {"id": 11, "src": self.client_id, "method": "Shelly.GetStatus", "params": {}}
                        self._send_ws(status_msg)
                        Domoticz.Log("Requested status for discovery")
                    elif payload["id"] == 11:
                        Domoticz.Log("Received device status")
                        self.pending_status = payload["result"]
                        self._try_complete_discovery()
                    elif payload["id"] in self.inflight_commands:
                        self.inflight_commands.pop(payload["id"], None)
                        Domoticz.Debug(f"Switch command acknowledged (id={payload['id']})")

            except json.JSONDecodeError as e:
                Domoticz.Error("Failed to parse JSON: " + str(e))
            except Exception as e:
                import traceback
                Domoticz.Error("Error processing message: " + str(e))
                Domoticz.Error("Traceback: " + traceback.format_exc())

    def onHeartbeat(self):
        Domoticz.Debug("onHeartbeat called")
        if self.websocketConn and self.websocketConn.Connected():
            self.websocketConn.Send({"Operation": "Ping", "Mask": secrets.randbits(32)})
            Domoticz.Debug("Ping sent")
            Domoticz.Log("Alive")
        else:
            self.reconAgain -= 1
            if self.reconAgain <= 0:
                Domoticz.Log("Reconnecting...")
                self.auth = None
                if self.pending_commands:
                    Domoticz.Log(f"Discarding {len(self.pending_commands)} queued command(s) due to reconnect")
                    self.pending_commands = []
                if self.inflight_commands:
                    Domoticz.Log(f"Discarding {len(self.inflight_commands)} inflight command(s) due to reconnect")
                    self.inflight_commands = {}
                self.is_reconnect = True
                self.websocketConn = Domoticz.Connection(
                    Name="ShellyWebSocket",
                    Transport="TCP/IP",
                    Protocol="WS",
                    Address=Parameters["Address"],
                    Port="80",
                )
                self.websocketConn.Connect()
                self.reconAgain = 3
            else:
                Domoticz.Log(f"Reconnect in {self.reconAgain} heartbeats.")

    def onDisconnect(self, Connection):
        Domoticz.Log("Shelly device disconnected")

    def onDeviceModified(self, DeviceID, Unit):
        Domoticz.Log(f"onDeviceModified: DeviceID={DeviceID}, Unit={Unit}")

    def onCommand(self, DeviceID, Unit, Command, Level, Hue):
        Domoticz.Log(f"onCommand: DeviceID={DeviceID}, Unit={Unit}, Command='{Command}', Level={Level}")

        m = re.match(r"^switch:(\d+)$", DeviceID)
        if m:
            ch = int(m.group(1))
            turn_on = Command.strip().upper() == "ON"
            self.next_cmd_id += 1
            rpc_command = {
                "id": self.next_cmd_id,
                "src": self.client_id,
                "method": "Switch.Set",
                "params": {"id": ch, "on": turn_on},
            }
            password = Parameters.get("Password", "").strip()
            if password and self.auth is None:
                first = not self.pending_commands
                self.pending_commands.append(rpc_command)
                Domoticz.Log(f"Queued switch:{ch} command: {'ON' if turn_on else 'OFF'} (auth not ready)")
                if first and self.websocketConn and self.websocketConn.Connected():
                    probe = {"id": 0, "src": self.client_id, "method": "Shelly.GetStatus", "params": {}}
                    self.websocketConn.Send({"Payload": json.dumps(probe), "Mask": secrets.randbits(32)})
                    Domoticz.Log("Sent auth probe to obtain 401 challenge")
                return
            self.inflight_commands[rpc_command["id"]] = rpc_command
            self._send_ws(rpc_command)
            Domoticz.Log(f"Sent switch:{ch} command: {'ON' if turn_on else 'OFF'}")

    def onStop(self):
        Domoticz.Log("onStop called")
        return True


global _plugin
_plugin = BasePlugin()


def onStart():
    global _plugin
    _plugin.onStart()


def onStop():
    global _plugin
    _plugin.onStop()


def onConnect(Connection, Status, Description):
    global _plugin
    _plugin.onConnect(Connection, Status, Description)


def onDisconnect(Connection):
    global _plugin
    _plugin.onDisconnect(Connection)


def onMessage(Connection, Data):
    global _plugin
    _plugin.onMessage(Connection, Data)


def onDeviceModified(DeviceID, Unit):
    global _plugin
    _plugin.onDeviceModified(DeviceID, Unit)


def onCommand(DeviceID, Unit, Command, Level, Hue):
    global _plugin
    _plugin.onCommand(DeviceID, Unit, Command, Level, Hue)


def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()

# Generic helper functions
def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            if x == "Password":  # Don't log API token
                Domoticz.Debug("'" + x + "':'***HIDDEN***'")
            else:
                Domoticz.Debug(f"'{x}':'{str(Parameters[x])}'")

    Domoticz.Debug(f"Device count: {len(Devices)}")
    for devID in Devices:
        dev = Devices[devID]
        Domoticz.Debug(f"DeviceID: '{devID}' — Units: {len(dev.Units)}")
        for unit in dev.Units:
            u = dev.Units[unit]
            Domoticz.Debug(f"  Unit {unit}: Name='{u.Name}', nValue={u.nValue}, sValue='{u.sValue}'")

def DumpWSResponseToLog(httpDict):
    if isinstance(httpDict, dict):
        Domoticz.Log("WebSocket Details (" + str(len(httpDict)) + "):")
        for x in httpDict:
            if isinstance(httpDict[x], dict):
                Domoticz.Log("--->'"+x+" ("+str(len(httpDict[x]))+"):")
                for y in httpDict[x]:
                    Domoticz.Log("------->'" + y + "':'" + str(httpDict[x][y]) + "'")
            else:
                Domoticz.Log("--->'" + x + "':'" + str(httpDict[x]) + "'")
