"""
<plugin key="ShellyPro1PM" name="Shelly Pro 1PM" author="clement" version="1.0">
    <description>
        <h2>Shelly Pro 1PM Gen2</h2><br/>
        WebSocket-based integration for Shelly Pro 1PM Gen2 device<br/>
        <br/>
        Creates the following sensors:<br/>
        - Switch (On/Off control)<br/>
        - Energy (kWh)<br/>
        - Frequency (Hz)<br/>
    </description>
    <params>
        <param field="Address" label="IP Address" width="130px" required="true" default="10.0.0.185"/>
        <param field="Port" label="Port" width="30px" required="true" default="80"/>
        <param field="Username" label="Username" width="60px" required="false" default=""/>
        <param field="Password" label="Password" width="60px" password="true" required="false" default=""/>
        <param field="Mode1" label="Friendly Name" width="120px" required="true" default="Shelly"/>
        <param field="Mode6" label="Debug" width="150px">
            <options>
                <option label="None" value="0"  default="true" />
                <option label="Python Only" value="2"/>
                <option label="Basic Debugging" value="62"/>
                <option label="Basic+Messages" value="126"/>
                <option label="Queue" value="128"/>
                <option label="Connections Only" value="16"/>
                <option label="Connections+Queue" value="144"/>
                <option label="All" value="-1"/>
            </options>
        </param>
    </params>
</plugin>
"""

import DomoticzEx as Domoticz
import json
import secrets
import base64


class ShellyUnit(Domoticz.Unit):
    def __init__(self, Name, DeviceID, Unit, TypeName="", Type=0, Subtype=0, Switchtype=0, Image=0, Options="", Used=0, Description=""):
        super().__init__(Name, DeviceID, Unit, TypeName, Type, Subtype, Switchtype, Image, Options, Used, Description)
    
    def onCommand(self, Command, Level, Hue):
        global _plugin
        Domoticz.Log("onCommand called for '" + str(self.Name) + "': Command='" + str(Command) + "', Level=" + str(Level))
        
        if self.Unit == BasePlugin.UNIT_SWITCH:
            turn_on = Command.strip().upper() == "ON"
            
            rpc_command = {
                "id": 3,
                "src": "user",
                "method": "Switch.Set",
                "params": {
                    "id": 0,
                    "on": turn_on
                }
            }
            if _plugin.websocketConn and _plugin.websocketConn.Connected():
                _plugin.websocketConn.Send({'Payload': json.dumps(rpc_command), 'Mask': secrets.randbits(32)})
                Domoticz.Log("Sent switch command: " + ("ON" if turn_on else "OFF"))


class BasePlugin:
    websocketConn = None
    reconAgain = 3
    debug = False
    
    # Device units
    UNIT_SWITCH = 1
    UNIT_ENERGY = 2
    UNIT_FREQUENCY = 3
    
    # Device IDs (keys for Extended Framework)
    DEVICEID_SWITCH = "switch:0"
    DEVICEID_ENERGY = "switch:0:energy"
    DEVICEID_FREQUENCY = "switch:0:freq"
    
    # Cache for partial updates
    last_power = 0.0
    last_energy_wh = 0.0
    last_frequency = 0.0

    def onStart(self):
        device_friendly_name = str(Parameters["Mode1"])
        if int(Parameters["Mode6"]) > 0:
            Domoticz.Debugging(int(Parameters["Mode6"]))
            self.debug = True

        # Create devices if they don't exist
        if self.DEVICEID_SWITCH not in Devices:
            try:
                ShellyUnit(Name=f"{device_friendly_name} Switch", DeviceID=self.DEVICEID_SWITCH, Unit=self.UNIT_SWITCH, Type=244, Subtype=73, Used=1, Switchtype=0).Create()
                Domoticz.Log("Switch device created")
            except Exception as e:
                Domoticz.Debug("Switch device already exists or creation failed: " + str(e))
        
        if self.DEVICEID_ENERGY not in Devices:
            try:
                ShellyUnit(Name=f"{device_friendly_name} Energy", DeviceID=self.DEVICEID_ENERGY, Unit=self.UNIT_ENERGY, Type=243, Subtype=29, Used=1).Create()
                Domoticz.Log("Energy device created (importing mode)")
            except Exception as e:
                Domoticz.Debug("Energy device already exists or creation failed: " + str(e))
        
        if self.DEVICEID_FREQUENCY not in Devices:
            try:
                Options = {"Custom": "1;Hz"}
                ShellyUnit(Name=f"{device_friendly_name} Frequency", DeviceID=self.DEVICEID_FREQUENCY, Unit=self.UNIT_FREQUENCY, Type=243, Subtype=31, Used=0, Options=Options).Create()
                Domoticz.Log("Frequency device created")
            except Exception as e:
                Domoticz.Debug("Frequency device already exists or creation failed: " + str(e))

        # Log which devices are available
        Domoticz.Log("Available devices: " + str(list(Devices.keys())))

        # Connect to Shelly device via WebSocket
        self.websocketConn = Domoticz.Connection(
            Name      = "ShellyWebSocket",
            Transport = "TCP/IP",
            Protocol  = "WS",
            Address   = Parameters["Address"],
            Port      = Parameters["Port"]
            )
        self.websocketConn.Connect()

    def onConnect(self, Connection, Status, Description):
        if Status == 0:
            Domoticz.Log("Connected successfully to: " + Connection.Address + ":" + Connection.Port)
            # Upgrade to WebSocket
            send_data = {
                'URL': '/rpc',
                'Headers': {
                    'Host': Parameters["Address"],
                    'Origin': 'http://' + Parameters["Address"],
                    'Sec-WebSocket-Key': base64.b64encode(secrets.token_bytes(16)).decode("utf-8")
                    }
                }
            Connection.Send(send_data)
        else:
            Domoticz.Log("Failed to connect (" + str(Status) + ") to: " + Connection.Address + ":" + Connection.Port)
            Domoticz.Debug("Failed to connect (" + str(
                Status) + ") to: " + Connection.Address + ":" + Connection.Port + " with error: " + Description)
        return True

    def onMessage(self, Connection, Data):
        Domoticz.Debug("onMessage called")
        
        if "Status" in Data:  # HTTP Message
            if Data["Status"] == "101":
                Domoticz.Log("WebSocket successfully upgraded")
                # Subscribe to all events
                subscribe_msg = {"id": 1, "src": "user", "params": {"events": ["*"]}}
                Connection.Send({'Payload': json.dumps(subscribe_msg), 'Mask': secrets.randbits(32)})
                Domoticz.Log("Subscribed to Shelly events")
                # Get initial switch status
                status_msg = {"id": 2, "src": "user", "method": "Switch.GetStatus", "params": {"id": 0}}
                Connection.Send({'Payload': json.dumps(status_msg), 'Mask': secrets.randbits(32)})
                Domoticz.Log("Requested initial switch status")
            else:
                DumpWSResponseToLog(Data)
                
        elif "Operation" in Data:  # WebSocket control message
            if Data["Operation"] == "Ping":
                Domoticz.Debug("Ping Message received")
                Connection.Send({'Operation': 'Pong', 'Payload': 'Pong', 'Mask': secrets.randbits(32)})
            elif Data["Operation"] == "Pong":
                Domoticz.Debug("Pong Message received")
            elif Data["Operation"] == "Close":
                Domoticz.Log("Close Message received")
            else:
                DumpWSResponseToLog(Data)
                
        elif "Payload" in Data:  # WebSocket data message
            try:
                payload = json.loads(Data["Payload"])
                Domoticz.Debug("Received: " + json.dumps(payload))
                
                # Check if it's a NotifyStatus message with switch:0 data
                if payload.get("method") == "NotifyStatus" and "params" in payload:
                    params = payload["params"]
                    if "switch:0" in params:
                        self.ProcessSwitchData(params["switch:0"])
                
                # Check if it's a response to Switch.GetStatus
                elif "result" in payload and "id" in payload:
                    if payload["id"] == 2:  # Our Switch.GetStatus request
                        Domoticz.Log("Received initial switch status")
                        self.ProcessSwitchData(payload["result"])
            except json.JSONDecodeError as e:
                Domoticz.Error("Failed to parse JSON payload: " + str(e))
            except Exception as e:
                import traceback
                Domoticz.Error("Error processing message: " + str(e))
                Domoticz.Error("Traceback: " + traceback.format_exc())

    def ProcessSwitchData(self, switch_data):
        Domoticz.Debug("Processing switch data: " + json.dumps(switch_data))
        
        # Update cached values if present (use absolute value for power)
        if "apower" in switch_data:
            self.last_power = abs(switch_data["apower"])
        
        if "aenergy" in switch_data and "total" in switch_data["aenergy"]:
            self.last_energy_wh = switch_data["aenergy"]["total"]
        
        if "freq" in switch_data:
            self.last_frequency = switch_data["freq"]
        
        # Update switch state
        if "output" in switch_data and self.DEVICEID_SWITCH in Devices:
            is_on = switch_data["output"]
            Devices[self.DEVICEID_SWITCH].Units[self.UNIT_SWITCH].nValue = 1 if is_on else 0
            Devices[self.DEVICEID_SWITCH].Units[self.UNIT_SWITCH].sValue = "On" if is_on else "Off"
            Devices[self.DEVICEID_SWITCH].Units[self.UNIT_SWITCH].Update(Log=True)
            Domoticz.Log("Switch updated: " + ("On" if is_on else "Off"))
        
        # Update energy sensor when we have energy data or power changes
        if ("aenergy" in switch_data or "apower" in switch_data) and self.DEVICEID_ENERGY in Devices:
            if self.last_energy_wh > 0:  # Only update if we have valid energy data
                sValue = "{:.1f};{:.3f}".format(self.last_power, self.last_energy_wh)
                Devices[self.DEVICEID_ENERGY].Units[self.UNIT_ENERGY].nValue = 0
                Devices[self.DEVICEID_ENERGY].Units[self.UNIT_ENERGY].sValue = sValue
                Devices[self.DEVICEID_ENERGY].Units[self.UNIT_ENERGY].Update(Log=True)
                Domoticz.Log("Energy updated: " + sValue)
        
        # Update frequency
        if "freq" in switch_data and self.DEVICEID_FREQUENCY in Devices:
            Devices[self.DEVICEID_FREQUENCY].Units[self.UNIT_FREQUENCY].nValue = 0
            Devices[self.DEVICEID_FREQUENCY].Units[self.UNIT_FREQUENCY].sValue = str(self.last_frequency)
            Devices[self.DEVICEID_FREQUENCY].Units[self.UNIT_FREQUENCY].Update(Log=True)
            Domoticz.Log("Frequency updated: " + str(self.last_frequency))

    def onHeartbeat(self):
        Domoticz.Debug("onHeartbeat called")
        if self.websocketConn and self.websocketConn.Connected():
            # Send ping to keep connection alive
            self.websocketConn.Send({'Operation': 'Ping', 'Mask': secrets.randbits(32)})
            Domoticz.Debug("Ping sent")
        else:
            # Try to reconnect
            self.reconAgain -= 1
            if self.reconAgain <= 0:
                Domoticz.Log("Reconnecting to Shelly device...")
                self.websocketConn = Domoticz.Connection(Name="ShellyWebSocket", Transport="TCP/IP", Protocol="WS",
                                                       Address=Parameters["Address"], Port=Parameters["Port"])
                self.websocketConn.Connect()
                self.reconAgain = 3
            else:
                Domoticz.Log("Will try reconnect again in " + str(self.reconAgain) + " heartbeats.")

    def onDisconnect(self, Connection):
        Domoticz.Log("Shelly device disconnected")

    def onDeviceModified(self, DeviceID, Unit):
        Domoticz.Log("onDeviceModified called for DeviceID=" + str(DeviceID) + ", Unit=" + str(Unit))

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

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()

# Generic helper functions
def DumpWSResponseToLog(httpDict):
    if isinstance(httpDict, dict):
        Domoticz.Log("WebSocket Details ("+str(len(httpDict))+"):")
        for x in httpDict:
            if isinstance(httpDict[x], dict):
                Domoticz.Log("--->'"+x+" ("+str(len(httpDict[x]))+"):")
                for y in httpDict[x]:
                    Domoticz.Log("------->'" + y + "':'" + str(httpDict[x][y]) + "'")
            else:
                Domoticz.Log("--->'" + x + "':'" + str(httpDict[x]) + "'")
