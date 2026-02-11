"""
<plugin key="ShellyGen2Switch" name="Shelly Gen2+ Switch" author="lemassykoi" version="2.0" externallink="https://github.com/lemassykoi/Domoticz-Shelly-Plugin">
    <description>
        <h2>Shelly Gen2+ Switch Plugin</h2><br/>
        WebSocket-based integration for Shelly Gen2+ devices with switch and power metering.<br/>
        <br/>
        <h3>Supported devices</h3>
        <ul style="list-style-type:square">
            <li>Shelly Pro 1PM (1 switch)</li>
            <li>Shelly Outdoor Plug S Gen3 (1 switch + temperature)</li>
            <li>Shelly Power Strip Gen4 (4 switches)</li>
            <li>Any other Shelly Gen2+ device with switch components</li>
        </ul>
        <h3>Features</h3>
        <ul style="list-style-type:square">
            <li>Switch (On/Off control)</li>
            <li>Energy (W + Wh)</li>
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
        self.awaiting_auth_challenge = False

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

    def _process_switch_data(self, ch, data):
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

        if "output" in data and ids["switch"] in Devices:
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

    def _extract_channel_names(self, config):
        for key, value in config.items():
            m = re.match(r"^switch:(\d+)$", key)
            if m:
                ch = int(m.group(1))
                name = value.get("name")
                if name:
                    self.channel_names[ch] = name

    def _discover_channels(self, status):
        channels = []
        for key, value in status.items():
            m = re.match(r"^switch:(\d+)$", key)
            if m:
                channels.append((int(m.group(1)), value))
        self.total_channels = len(channels)
        for ch, value in channels:
            has_temp = "temperature" in value
            self._ensure_channel_devices(ch, has_temp=has_temp)
            self._process_switch_data(ch, value)

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
            self.websocketConn.Send({"Payload": json.dumps(payload), "Mask": secrets.randbits(32)})

    def _start_discovery(self):
        subscribe_msg = {"id": 1, "src": self.client_id, "params": {"events": ["*"]}}
        self._send_ws(subscribe_msg)
        Domoticz.Log(f"Subscribed to events (client_id: {self.client_id})")
        config_msg = {"id": 10, "src": self.client_id, "method": "Shelly.GetConfig", "params": {}}
        self._send_ws(config_msg)
        status_msg = {"id": 11, "src": self.client_id, "method": "Shelly.GetStatus", "params": {}}
        self._send_ws(status_msg)
        Domoticz.Log("Requested config and status for discovery")

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
                    Domoticz.Log("Authentication configured, sending probe...")
                    self.awaiting_auth_challenge = True
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
                        Domoticz.Log(f"Authenticated to {realm}")
                        self.awaiting_auth_challenge = False
                        self._start_discovery()
                    except Exception as e:
                        Domoticz.Error(f"Authentication failed: {e}")

                elif payload.get("method") == "NotifyStatus" and "params" in payload:
                    params = payload["params"]
                    for key, value in params.items():
                        m = re.match(r"^switch:(\d+)$", key)
                        if m:
                            ch = int(m.group(1))
                            if ch not in self.discovered_channels:
                                self._ensure_channel_devices(ch, has_temp=("temperature" in value))
                            self._process_switch_data(ch, value)

                elif "result" in payload and "id" in payload:
                    if payload["id"] == 10:
                        Domoticz.Log("Received device config")
                        self.pending_config = payload["result"]
                        self._try_complete_discovery()
                    elif payload["id"] == 11:
                        Domoticz.Log("Received device status")
                        self.pending_status = payload["result"]
                        self._try_complete_discovery()

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
            rpc_command = {
                "id": 100,
                "src": self.client_id,
                "method": "Switch.Set",
                "params": {"id": ch, "on": turn_on},
            }
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

    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
        Domoticz.Debug("Device ID:       '" + str(Devices[x].ID) + "'")
        Domoticz.Debug("Device Name:     '" + Devices[x].Name + "'")
        Domoticz.Debug("Device nValue:    " + str(Devices[x].nValue))
        Domoticz.Debug("Device sValue:   '" + Devices[x].sValue + "'")
        Domoticz.Debug("Device LastLevel: " + str(Devices[x].LastLevel))

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
