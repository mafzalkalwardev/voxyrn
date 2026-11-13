-- WirePlumber 0.4 (Ubuntu 24.04). Keep Voxyrn null sinks from suspending.
-- A suspended PipeWire node makes paplay and pacat block until Python times out.
-- Install to /etc/wireplumber/main.lua.d/99-voxyrn-no-suspend.lua

alsa_monitor = alsa_monitor or {}
alsa_monitor.rules = alsa_monitor.rules or {}

table.insert(alsa_monitor.rules, {
  matches = {
    {
      { "node.name", "matches", "voxyrn_.*" },
    },
  },
  apply_properties = {
    ["session.suspend-timeout-seconds"] = 0,
  },
})
