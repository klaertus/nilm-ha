# NILM Home Assistant Integration

A Home Assistant custom component that talks to the NILM backend, asks it
"what appliance is eating my electricity right now?", and turns the answer
into sensors. No extra smart plugs required after training that's the whole
point of *Non-Intrusive* Load Monitoring.

---

## What it does

- Buffers your aggregate power sensor and ships windows to the backend for
  inference every `scan_interval` seconds.
- Creates one binary sensor (ON/OFF) and one power sensor (watts) per detected
  appliance.
- Periodically pushes recorder history to the backend so it has something to
  train on.
- Gives you a sidebar panel so you never have to touch a `curl` command.

---

## Install

1. Copy `nilm_custom_component/` into `config/custom_components/nilm/` and
   restart Home Assistant.
2. **Settings -> Devices & Services -> Add Integration -> NILM**.
3. Fill in the config flow:
   - **Host / Port** : where the NILM backend lives (e.g. `localhost` `8000`).
   - **Power entity** : your house aggregate power sensor. This is the one
     thing the model actually sees, so pick the right one.
   - **Scan interval** : seconds between predictions.
   - **Data push interval** : hours between automatic history pushes.
4. **Pick a model** : choose an existing one or create a new (empty) one.
5. **Pick appliances** : select which of the model's appliances to expose as
   entities.

If the backend is unreachable you get `cannot_connect` and a chance to fix the
host before going further.

---

## The panel

A **NILM** entry appears in the sidebar. Three tabs:

- **Appliances** : live view: predicted watts per appliance, green chip when
  ON, red when OFF.
- **Train** : push data, train, finetune, calibrate, and watch the sample
  count climb.
- **Parameters** : runtime knobs (fallback threshold, device).

The **Manage Appliances** section is where you add appliances, set their
training threshold, link a real sensor, or delete them.

---

## Typical workflow

Going from fresh install to something useful :

1. **Add appliances** : one per device you care about (`fridge`, `kettle`, ...).
   Names are identifiers: letters, digits, `_`, `-`. Pick a name you can live with, you can't change it later.
2. **Link a real sensor** to each appliance : a smart plug or built-in power
   meter. This is the ground truth the model learns from.
3. **Push data** : collect recorder history for the aggregate and linked
   sensors and send it to the backend. The more days, the better (at least 14-28days) or more if it's a rare appliance.
4. **Train** : the backend does the heavy lifting. Be patient.
5. **Predictions appear** : the per appliance sensors start showing estimates.
6. **Finetune / Calibrate** : later as more data accumulates and for seasonal divergence for example.

For the physical meters, a high frequency meter (1-second interval) is preferable for the aggregate. For appliance meters, up to 5 seconds is acceptable. It is essential to use meters that do not interrupt the measurement; otherwise, artifacts may occur during the learning process windows.

---

## Entities

Per enabled appliance:

- `binary_sensor.nilm_<appliance>` : ON/OFF (ON means predicted power > 0).
- `sensor.nilm_<appliance>_power` : predicted watts.

Plus two hub sensors:

- `sensor.nilm_predicted_power` : sum of all predicted appliance power.
- `sensor.nilm_aggregated_power` : mirror of your configured power entity, for
  side-by-side comparison.

---

## Services

- `nilm.push_data` : push recent history to the backend. Optional `hours`
  field; defaults to the configured push interval. Also runs automatically on
  a timer, so this is just the manual override for the impatient.

Linking and unlinking appliances are HTTP API views called by the dashboard.

---

## Where stuff lives

- User labels (linked sensors) :  `.storage/nilm_user_labels.json`.
  Survives restarts. Deleted only when you remove the integration.
- Trained models, stats, the SQLite database :  on the **backend**, not in
  Home Assistant. This integration is just a well dressed client.
