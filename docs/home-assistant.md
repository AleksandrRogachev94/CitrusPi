# Home Assistant integration

CitrusPi owns sensor collection, the daily photo archive, and image analysis. Home
Assistant owns its dashboard, notifications, and equipment automations. The CitrusPi
web app, SQLite history, plug monitoring, and alerts keep working as before. CitrusPi
only publishes data; it accepts no MQTT commands and controls no plugs.

Both CitrusPi and Home Assistant connect to an MQTT **broker**. CitrusPi publishes
to it and HA subscribes. Port 8123 is HA's web port, not an MQTT port. HA downloads
JPEGs from the Pi over HTTP, using the URL CitrusPi publishes.

## Setup

1. On Home Assistant OS, install the **Mosquitto broker** app and add the **MQTT**
   integration. For HA Container, run Mosquitto separately. Create a dedicated broker
   login for CitrusPi.
2. On the Pi, install the optional dependency:

   ```sh
   cd /home/alex/CitrusPi
   .venv/bin/pip install -e ".[mqtt]"
   ```

3. Replace `mqtt: null` in the Pi's `config.yaml`, then run `chmod 600 config.yaml`:

   ```yaml
   mqtt:
     host: "192.168.1.241"   # The broker; usually the HA host
     port: 1883
     username: "citruspi"
     password: "YOUR_DEDICATED_BROKER_PASSWORD"
     device_id: "citruspi"   # Stable identity; keep it when moving the Pi
     base_url: "http://citruspi.local"
   ```

   `base_url` is the **Pi's** address as seen from HA. If HA cannot resolve
   `citruspi.local`, use the Pi's reserved IP.

   `CITRUSPI_MQTT_HOST`, `_USERNAME`, `_PASSWORD`, and `_BASE_URL` can override these
   in the systemd service environment once the `mqtt` section exists. For a TLS
   broker, add `tls: true`, use its TLS port (usually 8883), and optionally set
   `ca_file`. Invalid `mqtt` settings log an error and disable MQTT; monitoring continues.

4. Restart `citruspi`. HA should discover one **CitrusPi** device. Assign it to your
   grow tent area. Values appear on the next sensor sample, normally within a minute.
   History is not backfilled into HA.
5. Add the plugs with HA's **TP-Link Smart Home** integration. Enable Third-Party
   Compatibility in the vendor app if needed.

If you use broker ACLs, allow CitrusPi to publish to `citruspi/citruspi/#` and
`homeassistant/+/citruspi/#`. Adapt these for a different `device_id` or
`discovery_prefix`.

## Entities

| Entity | Meaning |
| --- | --- |
| Canopy temperature, root temperature | °C; HA controls display units |
| Humidity | Relative humidity, % |
| VPD | Calculated air VPD, kPa |
| Atmospheric pressure | hPa; disabled by default |
| Last reading | Sample timestamp; diagnostic |
| Connection | MQTT connection status; diagnostic |
| Daily photo | Latest archived daily JPEG |
| Last daily capture | Modification time of that image |
| Plant assessment | `healthy`, `watch`, or `alert`; unknown for a skipped analysis |
| Last successful analysis | Creation time of the last successful report |

Plant assessment attributes are `report_id`, `date`, `created_at`, `summary`, `trees`,
`reference_dates`, `photo_url`, `analysis_outcome`, and `leaf_drop`. `leaf_drop` is
`null` for skipped images and reports without comparison photos. A failed AI request
leaves the earlier report in place, so show the report date next to its assessment.

Live sensors become unavailable after three polling intervals without data, and each
sensor becomes unavailable on its own if its value is missing.

## Equipment activity

A plug's switch only says the relay allows power, not that a heater is heating. Use
the native **power sensor**, with a **Threshold** helper for each plug. These starting
points match CitrusPi's example thresholds; tune them against real measurements:

| Helper | Upper limit | Hysteresis | Turns on above | Turns off below |
| --- | ---: | ---: | ---: | ---: |
| Heater heating | 6.5 W | 3.5 W | 10 W | 3 W |
| Grow light active | 3.5 W | 1.5 W | 5 W | 2 W |

Use `for:` in automations to ignore short transitions. For the Energy dashboard, use
each plug's native kWh sensor, or an Integral helper on watts if it has none.

## Photos and optional live camera

The daily photo entity updates as soon as a capture is saved. Manual captures never
replace it. For live viewing, add HA's **MJPEG IP Camera** integration:

- Stream URL: `http://citruspi.local/stream`
- Still-image URL: `http://citruspi.local/api/camera/still`

The still URL serves the latest **daily** photo without opening the camera, so
thumbnails never start a stream. It returns 404 until a daily photo exists. The live
stream keeps its five-minute session limit. Open it on demand rather than enabling
preloaded live view. Live viewing during a daily capture is untested on the real Pi.

## Notifications

Good first automations:

- Sustained temperature excursions, using seasonal thresholds in HA helpers.
- Missing sensor data. Distinguish unavailable from zero or an off relay.
- A daily image or successful analysis missing after its expected time.
- A light drawing no power during its schedule.
- A new AI report needing attention. Trigger on a changed `report_id`, not every MQTT
  message, because retained results are replayed when HA restarts.

Keep AI advisory, and keep the heater's independent thermostat. Leave CitrusPi's ntfy
alerts and heartbeat on until HA notifications are tested.

## Behavior when things fail

- Discovery, availability, photo details, and reports are retained on the broker, so
  HA gets them back when it restarts. Live readings are not retained.
- On connection loss, the broker publishes `offline` for CitrusPi. On reconnect,
  CitrusPi republishes all retained state and resumes with new readings. Readings
  taken during an outage stay in SQLite and are not sent to HA.
- If MQTT is not installed or the broker is down, local collection, alerts, captures,
  and analysis continue. Mock mode never publishes.

## Validate a deployment

1. Discovery and fresh values appear in HA, and the latest daily photo loads.
2. Stop the broker: the Pi keeps logging. Restart it and HA: values recover.
3. A missing probe becomes unavailable, and a skipped analysis shows no plant verdict.
4. The Pi dashboard still shows fresh sensor and plug readings.

Setting `mqtt: null` leaves retained discovery entries on the broker. Delete this
device's retained topics if you uninstall it permanently.

Sources: [MQTT](https://www.home-assistant.io/integrations/mqtt/),
[MQTT Sensor](https://www.home-assistant.io/integrations/sensor.mqtt/),
[MQTT Image](https://www.home-assistant.io/integrations/image.mqtt/),
[TP-Link](https://www.home-assistant.io/integrations/tplink/),
[Threshold](https://www.home-assistant.io/integrations/threshold/),
[MJPEG](https://www.home-assistant.io/integrations/mjpeg/).
