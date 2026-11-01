# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Public surface for the mobile kit (@upande/mobile-kit). Whitelisted paths:
#   upande_mobile.api.register_app       POST  create/update a Mobile App (System Manager)
#   upande_mobile.api.register_install   POST  a device reports itself
#   upande_mobile.api.installs           GET   the register (System Manager)
#   upande_mobile.api.refresh_now        POST  re-read latest OTA/APK now
#   upande_mobile.api.app_versions       GET   versions of this site's apps
#   upande_mobile.api.ota.manifest       GET   expo-updates manifest (guest), ?app=<Mobile App>
#   upande_mobile.api.monitor.*          crash / API reporting: ingest (POST) and the dashboard endpoints

from upande_mobile.api.apps import register_app  # noqa: F401
from upande_mobile.api.devices import (  # noqa: F401
	app_versions,
	installs,
	refresh_latest,
	refresh_now,
	register_install,
)
