# Usage ideas

Dose logging is the most useful thing this API does: it's a small action you want to take at the
moment you take the medication, with as little friction as possible. Automations stay in Home
Assistant, and this integration only provides the connection to Guava.

## Zigbee remote as a dose logger

Take a small multi-button Zigbee remote and write a medication name next to each button. One click
logs that dose. Press events already reach Home Assistant through Zigbee2MQTT or ZHA, so all you need
is an automation: [`examples/zigbee-remote.yaml`](../examples/zigbee-remote.yaml). Pair it with
[`examples/confirm-dose.yaml`](../examples/confirm-dose.yaml) for a light or spoken confirmation.

## Buttons on a dashboard

Add the `button.guava_…` entities to a dashboard on a wall tablet or phone.

## NFC tags

There are two separate routes:

- **Guava's own tags** log through the Guava app and don't touch Home Assistant, so the cooldown
  and the `guava_health_dose_logged` event don't apply. Guava
  [sells them](https://guavahealth.com/guava-tags), or you can make your own from blank stickers,
  because a tag is just a URL ([DIY guide](https://github.com/danielrosehill/Guava-Health-Plugin/blob/master/docs/nfc-tags.md)).
  For the URLs, open the entry's **Configure** → **Links for NFC tags**: it lists a link for each
  active medication. The link belongs to the tag, not the medication, so the integration generates
  it and you tie the two together in Guava: write the link to a tag, tap it, then pick the person
  and medication shown on the page. The links stay the same each time you open the page.
- **Home Assistant tags** run an automation through the companion app, so one tap can log a dose
  and do something else too: [`examples/nfc-tag.yaml`](../examples/nfc-tag.yaml).

## Voice

With a voice satellite, say "Guava, log my paracetamol". A sentence trigger passes the name to the
action, which ignores case and refuses names that aren't active medications, so a misheard word
doesn't create a medication: [`examples/voice.yaml`](../examples/voice.yaml).

## Morning set

Call the action several times from one automation to log everything you take together, from one
button press.
