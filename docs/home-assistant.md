# Home Assistant integration

CitrusPi owns sensor collection, the daily photo archive, and image analysis. Home
Assistant owns its dashboard, notifications, and equipment automations. The CitrusPi
web app, SQLite history, plug monitoring, and alerts keep working as before. CitrusPi
only publishes data; it accepts no MQTT commands and controls no plugs.

Both CitrusPi and Home Assistant connect to an MQTT **broker**. CitrusPi publishes
to it and HA subscribes. Port 8123 is HA's web port, not an MQTT port. The daily photo
also travels over MQTT as JPEG bytes, so HA never connects to the Pi and never needs to
resolve `citruspi.local`. HA reads the Kasa plugs directly, and CitrusPi keeps polling
them for its own dashboard.

## Setup

1. On Home Assistant OS, install the **Mosquitto broker** app (called an add-on on older
   versions) and add the **MQTT** integration. For HA Container, run Mosquitto separately.
   Create a dedicated login for CitrusPi. On HA OS, that is a new HA user under
   **Settings → People → Users**.
2. On the Pi, install the optional dependency:

   ```sh
   cd /home/alex/CitrusPi
   .venv/bin/pip install -e ".[mqtt]"
   ```

3. Add this to the Pi's `config.yaml`, replacing `mqtt: null` if it is there, then run
   `chmod 600 config.yaml`:

   ```yaml
   mqtt:
     host: "192.168.1.241"   # The broker; usually the HA host
     port: 1883
     username: "citruspi"
     password: "YOUR_DEDICATED_BROKER_PASSWORD"
     device_id: "citruspi"   # Stable identity; keep it when moving the Pi
     base_url: "http://citruspi.local"
   ```

   `base_url` is the **Pi's** web address. It is only used for links: the device page's
   "Visit" link and the report's `photo_url`. HA doesn't fetch anything from it, so
   photos keep working even when avahi renames the Pi (for example to `citruspi-2.local`)
   after a network drop. Use the Pi's reserved IP if you want those links to survive that too.

   `CITRUSPI_MQTT_HOST`, `_USERNAME`, `_PASSWORD`, and `_BASE_URL` can override these
   in the systemd service environment once the `mqtt` section exists. For a TLS
   broker, add `tls: true`, use its TLS port (usually 8883), and optionally set
   `ca_file`. Invalid `mqtt` settings log an error and disable MQTT; monitoring continues.

4. Restart `citruspi`. HA should discover one **CitrusPi** device. Assign it to your
   grow tent area. Values appear on the next sensor sample, normally within a minute.
   History is not backfilled into HA. Daily photo stays unavailable until the first
   capture, and the AI entities stay unknown until the first report.
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
| Daily photo | Latest archived daily JPEG, sent as retained bytes on `citruspi/<id>/photo/image` |
| Last daily capture | Modification time of that image |
| Plant assessment | `healthy`, `watch`, or `alert`; unknown for a skipped analysis |
| Last successful analysis | Creation time of the last successful report |

`alert` means a concern that is new, clearly worse than the reference photos, or urgent.
Ongoing problems are `watch`, and the report still describes them.

Plant assessment attributes include `report_id`, `date`, `created_at`, `summary`, `trees`,
`reference_dates`, `photo_url`, `analysis_outcome`, and `leaf_drop`. `leaf_drop` is
`null` for skipped images and reports without comparison photos. A failed AI request
leaves the earlier report in place, so show the report date next to its assessment.

Live sensors become unavailable after three polling intervals without data, and each
sensor becomes unavailable on its own if its value is missing.

Last reading changes every minute, so it fills the device's Activity panel. Disable that
entity in HA if you don't need it; the sensors still go unavailable when data stops. Use
Last daily capture, rather than the Daily photo entity's own time, as the photo's time.

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

These two automations cover CitrusPi going offline and new AI alerts. Add them to
`automations.yaml`, or paste each one into the automation editor's **Edit in YAML** view
without its leading `- `. Then replace `YOUR_PHONE` with your Companion app device and
check the entity IDs against your CitrusPi device page.

```yaml
- alias: "CitrusPi offline"
  triggers:
    - trigger: state
      entity_id: binary_sensor.citruspi_connection
      to: ["off", "unavailable"]
      for: "00:05:00"
    - trigger: state
      entity_id: sensor.citruspi_canopy_temperature
      to: "unavailable"
      for: "00:10:00"
  conditions:
    # Avoid a second notification when the whole connection is already down.
    - condition: template
      value_template: >-
        {{ trigger.entity_id == 'binary_sensor.citruspi_connection'
           or is_state('binary_sensor.citruspi_connection', 'on') }}
  actions:
    - action: notify.mobile_app_YOUR_PHONE
      data:
        title: "CitrusPi problem"
        message: "CitrusPi is offline or its canopy sensor has stopped reporting."

- alias: "CitrusPi plant alert"
  triggers:
    - trigger: state
      entity_id: sensor.citruspi_plant_assessment
      to: "alert"
      not_from: ["unavailable", "unknown"]
  actions:
    - action: notify.mobile_app_YOUR_PHONE
      data:
        title: "Plant alert"
        message: "{{ state_attr('sensor.citruspi_plant_assessment', 'summary') }}"
```

- **CitrusPi offline:** the first trigger fires when the Pi or the broker connection is
  lost. The second catches a canopy sensor that stops reporting while the connection
  stays up. To test it, run `sudo systemctl stop citruspi` on the Pi, wait 5 minutes, then
  run `sudo systemctl start citruspi`. Monitoring and ntfy alerts are paused until then.
- **Plant alert:** this notifies once when the status changes to `alert`, even if the alert
  lasts several days. A real change can stay `alert` for about a week, until the 7-day
  reference photo catches up. `not_from` stops an HA restart from notifying again. A day with a
  skipped analysis between two alerts is also excluded, so that later alert won't notify.

Other useful automations:

- Sustained temperature excursions, using seasonal thresholds in HA helpers.
- A daily image or successful analysis missing after its expected time.
- A light drawing no power during its schedule.

Keep AI advisory, and keep the heater's independent thermostat. Leave CitrusPi's ntfy
alerts on until HA notifications are tested. HA cannot notify you when HA itself or the
home's power or internet is down.

## Behavior when things fail

- Discovery, availability, the daily photo (about 0.5 MB), photo details, and reports are
  retained on the broker, so HA gets them back when it restarts. Live readings are not
  retained.
- On connection loss, the broker publishes `offline` for CitrusPi. On reconnect,
  CitrusPi republishes all retained state and resumes with new readings. Readings
  taken during an outage stay in SQLite and are not sent to HA.
- If MQTT is not installed or the broker is down, local collection, alerts, captures,
  and analysis continue. Mock mode never publishes.

## Validate a deployment

1. Discovery and fresh values appear in HA, and the latest daily photo loads.
2. Restart HA: the photo and assessment return straight away, and sensors within a minute.
3. Stop the broker: HA entities become unavailable and the Pi dashboard keeps updating.
   Start it again: values recover without touching the Pi.
4. Stop `citruspi`: Connection shows Disconnected. Start it again: values recover.
5. A missing probe becomes unavailable, and a skipped analysis shows no plant verdict.
6. The Pi dashboard still shows fresh sensor and plug readings.

Setting `mqtt: null` leaves retained discovery entries on the broker. Delete this
device's retained topics if you uninstall it permanently.

Sources: [MQTT](https://www.home-assistant.io/integrations/mqtt/),
[MQTT Sensor](https://www.home-assistant.io/integrations/sensor.mqtt/),
[MQTT Image](https://www.home-assistant.io/integrations/image.mqtt/),
[TP-Link](https://www.home-assistant.io/integrations/tplink/),
[Threshold](https://www.home-assistant.io/integrations/threshold/),
[MJPEG](https://www.home-assistant.io/integrations/mjpeg/).
