// Simulated engine for previewing the UI in a normal browser. Not used by the app.
(() => {
  const t0 = performance.now();
  let fix = true;
  const settings = {
    fix_enabled: true, output: "xbox360", deadzone: 0.06, outer: 0.97, smart_recenter: true, smart_settle: 0.4,
    auto_putdown: true, putdown_sec: 1.5, gyro_still: 0.015, idle_recenter: true, idle_sec: 5, combo: true,
    combo_sec: 1, feedback: true, hide_original: false, theme: "system", games_only: true,
  };
  const events = [
    { id: 1, time: "17:20:01", kind: "info", msg: "Engine started (SDL 2.32.10)", device: null },
    { id: 2, time: "17:20:01", kind: "connect", msg: "Connected: PS4 Controller", device: "PS4 Controller" },
    { id: 3, time: "17:20:02", kind: "calibrated", msg: "Calibrated", device: "PS4 Controller" },
    { id: 4, time: "17:24:40", kind: "smart", msg: "Stick let go off-centre - re-centred left stick", device: "PS4 Controller" },
  ];
  let nextId = 5;
  const clock = () => new Date().toTimeString().slice(0, 8);
  setInterval(() => {
    events.push({ id: nextId++, time: clock(), kind: "smart", msg: "Stick let go off-centre - re-centred left stick", device: "PS4 Controller" });
  }, 9000);

  function dev(t) {
    const a = t / 900;
    const pushing = Math.sin(t / 2600) > 0.2;
    const lraw = pushing ? [-0.66 + Math.cos(a) * 0.62, Math.sin(a) * 0.9] : [-0.66, -0.01];
    const lout = pushing ? [Math.cos(a) * 0.95, Math.sin(a) * 0.95] : [0, 0];
    const rraw = [Math.cos(a * 0.7) * 0.5 - 0.15, Math.sin(a * 0.7) * 0.5 - 0.1];
    const rout = [Math.cos(a * 0.7) * 0.55, Math.sin(a * 0.7) * 0.55];
    const held = Math.sin(t / 5000) > -0.6;
    const b = new Array(21).fill(0);
    b[Math.floor(t / 400) % 15] = 1;
    return {
      id: 0, name: "PS4 Controller", type: "PS4", family: "playstation", vid: "054C", pid: "09CC", serial: "",
      power: "wired", gyro: true, gyroNoise: held ? 0.08 + Math.random() * 0.05 : 0.002, held,
      rumble: true, led: true, raw: [...lraw, ...rraw], out: [...lout, ...rout],
      triggers: [(Math.sin(t / 700) + 1) / 2, (Math.cos(t / 900) + 1) / 2], buttons: b,
      left: { centre: [-0.66, -0.01], lo: [-1, -1], hi: [-0.2, 1], edge: false, calib: null },
      right: { centre: [-0.15, -0.1], lo: [-1, -1], hi: [1, 1], edge: false, calib: null },
      calibrated: true, calibrating: false,
      virtual: { on: fix, kind: settings.output, error: null }, rate: 250,
    };
  }

  window.mockApi = {
    state: async () => ({
      ready: true, version: "2.0.0", error: null, fix, settings,
      vigem: { ok: true, error: null }, hidhide: { installed: true, active: false, error: null },
      remappers: ["DS4Windows"], devices: [dev(performance.now() - t0)], events: events.slice(-40),
    }),
    set_fix: async on => { fix = on; settings.fix_enabled = on; },
    set_output: async k => { settings.output = k; },
    set_hiding: async on => { settings.hide_original = on; },
    update_settings: async c => Object.assign(settings, c),
    recentre: async () => events.push({ id: nextId++, time: clock(), kind: "manual", msg: "Re-centred left & right stick", device: "PS4 Controller" }),
    calibration: async () => true,
    reset_profile: async () => true,
    rumble: async () => {},
    led: async () => {},
    open_link: async () => {},
    close_remappers: async () => [],
  };
})();
