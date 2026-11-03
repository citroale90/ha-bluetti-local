"""Automatic router lifecycle inside the integration, with bounded rollback."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import logging

from .netfilter import NetworkError, RouterRules

_LOGGER = logging.getLogger(__name__)
VERIFY_SECONDS = 180
RECHECK_SECONDS = 30


class RouterManager:
    def __init__(self, hass, identifier, config, device):
        self.hass = hass
        self.rules = RouterRules(identifier, config)
        self.device = device
        self.task = None
        self.phase = "starting"
        self.error = None
        self._new_rules = False

    async def start(self):
        result = await self._apply()
        self._new_rules = result["changed"]
        self.phase = "verifying"
        baseline = self.device.valid_samples
        self.task = self.hass.async_create_background_task(self._run(baseline), "bluetti_local_router")

    async def _apply(self):
        pending = self.hass.async_add_executor_job(self.rules.apply)
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            result = await pending
            if result['changed']:
                await self.hass.async_add_executor_job(self.rules.remove)
            raise

    async def _run(self, baseline):
        try:
            deadline = asyncio.get_running_loop().time() + VERIFY_SECONDS
            reconnect_at = asyncio.get_running_loop().time() + 20
            reconnected = False
            while asyncio.get_running_loop().time() < deadline:
                if self.device.online and self.device.valid_samples >= baseline + 2:
                    self.phase = "verified"
                    self._new_rules = False
                    break
                if not reconnected and asyncio.get_running_loop().time() >= reconnect_at and self._new_rules:
                    await self.hass.async_add_executor_job(self.rules.reconnect_data)
                    reconnected = True
                await asyncio.sleep(2)
            else:
                if self._new_rules:
                    await self.hass.async_add_executor_job(self.rules.remove)
                    self._new_rules = False
                    self.phase = "rolled_back"
                else:
                    self.phase = "unverified"
                self.error = "no_fresh_samples"
                _LOGGER.warning("Local router setup did not receive two fresh UPS reports")
                return
            while True:
                await asyncio.sleep(RECHECK_SECONDS)
                result = await self._apply()
                if result["changed"]:
                    # A firewall reload removed our table: verify the new rules too.
                    self._new_rules = True
                    self.phase = "verifying"
                    baseline = self.device.valid_samples
                    deadline = asyncio.get_running_loop().time() + VERIFY_SECONDS
                    while asyncio.get_running_loop().time() < deadline:
                        if self.device.online and self.device.valid_samples >= baseline + 2:
                            self._new_rules = False
                            self.phase = "verified"
                            break
                        await asyncio.sleep(2)
                    else:
                        await self.hass.async_add_executor_job(self.rules.remove)
                        self._new_rules = False
                        self.phase = "rolled_back"
                        self.error = "no_fresh_samples"
                        return
        except asyncio.CancelledError:
            if self._new_rules:
                await self.hass.async_add_executor_job(self.rules.remove)
                self._new_rules = False
            raise
        except (NetworkError, OSError):
            self.phase = "error"
            self.error = "router_rules_unavailable"
            if self._new_rules:
                try:
                    await self.hass.async_add_executor_job(self.rules.remove)
                    self._new_rules = False
                    self.phase = "rolled_back"
                except (NetworkError, OSError):
                    pass
            _LOGGER.warning("Cannot verify the integration's dedicated router rules")

    async def stop(self, *, remove=False):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        # Keep verified rules across HA restarts/reloads to avoid switching cloud
        # sessions. New, unverified rules are always rolled back on cancellation.
        if remove or self._new_rules:
            await self.hass.async_add_executor_job(self.rules.remove)
            self._new_rules = False

    @property
    def diagnostics(self):
        return {"mode": "automatic", "phase": self.phase, "error": self.error,
                "rules": 3, "external_service": False}
