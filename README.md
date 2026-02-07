# Domoticz-Shelly-Plugin

Domoticz Plugin for Shelly Gen2+ devices with switch and power metering.

## Supported Devices

- Shelly Pro 1PM (1 switch + temperature)
- Shelly Outdoor Plug S Gen3 (1 switch + temperature)
- Shelly Power Strip Gen4 (4 switches)
- Any other Shelly Gen2+ device with switch components

## Features

- Auto-discovers all switch channels on the device
- Per-channel sensors: Switch (On/Off), Energy (W + Wh), Frequency (Hz)
- Temperature sensor (created automatically if reported by the device)
- Uses Shelly switch names (from device config) for multi-channel device naming
- WebSocket-based communication for real-time updates

## Devices Created

For each switch channel, the plugin creates:

| Sensor | Type | Description |
|--------|------|-------------|
| Switch | On/Off | Controls the relay output |
| Energy | kWh | Active power (W) and total energy (Wh) |
| Frequency | Custom (Hz) | Mains frequency (created as unused by default) |
| Temperature | Temp | Device temperature (only if reported) |

## Installation

```bash
cd /home/user/domoticz/plugins  # replace user with your actual username
git clone https://github.com/lemassykoi/Domoticz-Shelly-Plugin.git
sudo systemctl restart domoticz
```

Then you can add the plugin from the Hardware page in Setup Tab.
No additional Python packages are required.

## Configuration

1. In Domoticz, go to **Setup > Hardware**
2. Add new hardware of type **Shelly Gen2+ Switch**
3. Set the **IP Address** of your Shelly device
4. Set a **Friendly Name** (used as prefix for all sensor names)
5. Optionally set **Shelly Username/Password** if authentication is enabled on the device

## Notes

- The plugin connects to the Shelly device on port 80 via WebSocket.
- For multi-channel devices (e.g. Power Strip), unnamed channels are labeled "Plug 1", "Plug 2", etc. Channels with a name configured in the Shelly app will use that name instead.
- If you want to use this plugin to read produced energy (and not consumed energy), you should change domoticz sensor Type from "Usage" to "Return".
