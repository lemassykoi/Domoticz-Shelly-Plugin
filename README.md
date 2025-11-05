# Domoticz-Shelly-Plugin
Domoticz Plugin to manage Shelly Pro 1PM

<img width="930" height="76" alt="image" src="https://github.com/user-attachments/assets/1fe5b2bc-1bad-4c6e-9280-e7c0b3981b63" /><br />

The plugin is using WebSockets to communicate with the Shelly device.
You will need `websockets` package installed :

`sudo pip install websockets --break-system-packages`

Installation:
- Go to your Domoticz Plugin Folder
- Clone this plugin
- Restart Domoticz
- Add new Hardware "Shelly Pro 1PM"

```
cd /home/user/domoticz/plugin  # replace user with your real username for Domoticz Install
sudo pip install websockets --break-system-packages
git clone https://github.com/lemassykoi/Domoticz-Shelly-Plugin.git
sudo systemctl restart domoticz
```

This plugin has only been tested working with a Shelly Pro 1PM at the moment. It should work with any Shelly device which is able to count energy and have a switch.
Not been tested with a username and password.
