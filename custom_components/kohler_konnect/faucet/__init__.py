"""Sensate and Setra faucets: their coordinator, entities, action and diagnostics.

Kept apart from the shower code: a faucet has no valve word, zones or favorites. The
sign-in, REST client, MQTT stream and usage reading are shared in `konnect/`, and the root
platform modules add these entities beside the showers'.
"""
