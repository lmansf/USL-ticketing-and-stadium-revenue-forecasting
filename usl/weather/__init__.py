"""Weather features via Open-Meteo. Phase two, built.

Weather is a shared feature, not a pro-rel one: it joins the base feature
list, both models pick it up, and its absence does not confound the headline
comparison. Off by default (config.WEATHER_ENABLED) until the backfill has been
archived on a connected machine; the pipeline runs either way.

See docs/phases/12-phase-two-weather.md
"""
