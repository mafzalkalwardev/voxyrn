-- A systemd *system* service has no interactive logind seat. With the
-- default with-logind=true, WirePlumber disconnects from PipeWire and
-- paplay/pacat then block forever on a suspended null sink.
-- Install to /etc/wireplumber/bluetooth.lua.d/51-no-logind.lua
-- (loaded after 50-bluez-config.lua, which turns logind on).

bluez_monitor = bluez_monitor or {}
bluez_monitor.properties = bluez_monitor.properties or {}
bluez_monitor.properties["with-logind"] = false

bluez_midi_monitor = bluez_midi_monitor or {}
bluez_midi_monitor.properties = bluez_midi_monitor.properties or {}
bluez_midi_monitor.properties["with-logind"] = false
