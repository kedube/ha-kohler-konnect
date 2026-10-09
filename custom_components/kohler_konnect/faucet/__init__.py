"""Sensate and Setra faucets: their coordinator, entities, action and diagnostics.

Kept apart from the shower code because almost nothing is shared at this level — a faucet
has no valve word, no zones and no favorites — while the sign-in, the REST client, the MQTT
stream and the water-usage reading below it are shared with the showers in `konnect/`. The
root platform modules add these entities beside the valves' and controllers'.
"""
