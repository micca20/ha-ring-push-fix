# Ring push fix for Home Assistant

A stopgap for [python-ring-doorbell#537](https://github.com/python-ring-doorbell/python-ring-doorbell/issues/537).
Home Assistant's Ring integration (ring-doorbell 0.9.14) crashes on Ring push
notifications that have no `ding.id`. Ring has sent these for some motion
events since mid-2026. After 3 of them in a row, the push receiver shuts down
and **no Ring motion/ding events reach HA** until the next restart. The polled
`last_activity` sensors keep updating, which hides the problem.

Log signature:

```
KeyError: 'id'   (ring_doorbell/listen/eventlistener.py, _get_ring_event)
Shutting down push receiver due to 3 sequential errors of type ErrorType.NOTIFY
```

## What it does

- **Parser patch:** a missing `ding.id` gets a stable synthetic id, so duplicate
  deliveries of the same event are still de-duplicated. Any other unparseable
  push is logged and skipped instead of taking the receiver down.
- **Watchdog:** every 5 minutes it checks each Ring entry. If the push receiver
  has stopped for any reason, it reloads the Ring integration (at most once per
  30 min).
- **Self-retiring:** if a ring-doorbell release fixes the lookup, the patch isn't
  applied and the log says this integration can be removed.

Nothing in `site-packages` is edited, so it survives HA updates.

## Install

1. Copy `custom_components/ring_push_fix` into `<config>/custom_components/`
   (or run `scripts/deploy.sh vX.Y.Z` for the CT111 setup).
2. Add this to `configuration.yaml`:

   ```yaml
   ring_push_fix:
   ```

3. Restart Home Assistant. The log should show `ring_push_fix: parser patch applied`.

## Remove

Once upstream merges #540/#556 and HA ships the new ring-doorbell, remove
`ring_push_fix:` from `configuration.yaml`, delete the folder and restart.

## Releasing

`scripts/check.sh`, commit, `git tag -a vX.Y.Z`, push, `scripts/deploy.sh vX.Y.Z`,
then create the Gitea release from the CHANGELOG section.
