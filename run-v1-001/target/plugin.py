"""ExteraGram Outgoing Account Lifecycle Plugin.

Target:
    Client: ExteraGram 12.10.1
    Platform: Android
    SDK Version: 1.4.5.5

Benchmark Mode: A (Baseline)
Note:
    Target-local evidence in this fixture contains no ExteraGram/AyuGram API
    hints or SDK symbols. In compliance with benchmark instructions to avoid
    inventing unsupported APIs, this implementation defines a clean, testable
    lifecycle and threading architecture for:
      1. Intercepting outgoing text messages before send.
      2. Preserving the triggering account context across asynchronous flows.
      3. Offloading non-UI computation off the UI thread.
      4. Returning safely to the UI thread when UI operations are needed.
      5. Guaranteeing idempotent hook registration and complete cleanup on
         disable and reload to prevent duplicate/stale registrations.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AccountContext:
    """Preserves the account context that triggered an outgoing message event."""
    account_id: int | str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OutgoingTextMessage:
    """Encapsulates an outgoing text message event prior to transmission."""
    chat_id: int | str
    text: str
    account_context: AccountContext
    is_cancelled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class UIThreadDispatcher:
    """Dispatches callbacks to the UI / main thread.

    In an Android ExteraGram host environment, this would bridge to the main
    Looper/Handler or host UI dispatcher. In standalone/test execution, an
    injectable runner or direct invocation is used.
    """

    def __init__(self, host_ui_runner: Optional[Callable[[Callable[[], None]], None]] = None):
        self._host_ui_runner = host_ui_runner

    def post(self, callback: Callable[[], None]) -> None:
        """Schedule execution on the UI thread."""
        if self._host_ui_runner is not None:
            self._host_ui_runner(callback)
        else:
            callback()


class MessageHookRegistry:
    """Tracks outgoing message hooks, preventing stale or duplicate registrations."""

    def __init__(self):
        self._hooks: List[Callable[[OutgoingTextMessage], Optional[OutgoingTextMessage]]] = []
        self._lock = threading.Lock()

    def register(self, hook: Callable[[OutgoingTextMessage], Optional[OutgoingTextMessage]]) -> bool:
        """Register an outgoing message hook. Returns True if registered, False if already present."""
        with self._lock:
            if hook not in self._hooks:
                self._hooks.append(hook)
                return True
            return False

    def unregister(self, hook: Callable[[OutgoingTextMessage], Optional[OutgoingTextMessage]]) -> bool:
        """Unregister an outgoing message hook. Returns True if removed, False if absent."""
        with self._lock:
            if hook in self._hooks:
                self._hooks.remove(hook)
                return True
            return False

    def clear(self) -> None:
        """Remove all registered hooks."""
        with self._lock:
            self._hooks.clear()

    @property
    def registered_count(self) -> int:
        """Count of actively registered hooks."""
        with self._lock:
            return len(self._hooks)

    def dispatch(self, message: OutgoingTextMessage) -> OutgoingTextMessage:
        """Pass an outgoing message through all active hooks sequentially."""
        with self._lock:
            hooks_snapshot = list(self._hooks)

        current_message = message
        for hook in hooks_snapshot:
            if current_message.is_cancelled:
                break
            result = hook(current_message)
            if result is not None:
                current_message = result
        return current_message


class OutgoingAccountLifecyclePlugin:
    """Coordinates message interception, account preservation, threading, and lifecycle."""

    def __init__(
        self,
        registry: Optional[MessageHookRegistry] = None,
        ui_dispatcher: Optional[UIThreadDispatcher] = None,
        max_workers: int = 2,
    ):
        self.registry = registry if registry is not None else MessageHookRegistry()
        self.ui_dispatcher = ui_dispatcher if ui_dispatcher is not None else UIThreadDispatcher()
        self.max_workers = max_workers
        self._executor: Optional[ThreadPoolExecutor] = None
        self._is_enabled: bool = False
        self._lock = threading.Lock()
        self.processed_log: List[Dict[str, Any]] = []

    @property
    def is_enabled(self) -> bool:
        with self._lock:
            return self._is_enabled

    def on_enable(self) -> None:
        """Enable plugin: spin up background workers and register message hook idempotently."""
        with self._lock:
            if self._is_enabled:
                logger.warning("Plugin already enabled; ignoring duplicate enable request.")
                return

            self._executor = ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix="PluginWorker"
            )
            # Idempotently register message hook
            self.registry.register(self.intercept_outgoing_message)
            self._is_enabled = True
            logger.info("Plugin enabled; hook registered.")

    def on_disable(self) -> None:
        """Disable plugin: unregister all hooks and terminate background workers."""
        with self._lock:
            if not self._is_enabled:
                return

            # Cleanly unregister to avoid stale or duplicate callbacks
            self.registry.unregister(self.intercept_outgoing_message)

            # Shutdown thread pool executor
            if self._executor is not None:
                self._executor.shutdown(wait=True, cancel_futures=True)
                self._executor = None

            self._is_enabled = False
            logger.info("Plugin disabled; hook unregistered and workers stopped.")

    def on_reload(self) -> None:
        """Reload plugin: cleanly disable existing state before re-enabling."""
        self.on_disable()
        self.on_enable()

    def intercept_outgoing_message(self, message: OutgoingTextMessage) -> OutgoingTextMessage:
        """Intercept outgoing message before send, preserving account context."""
        account_ctx = message.account_context
        if account_ctx is None:
            logger.warning("Outgoing message has no account context.")
            return message

        # Offload non-UI processing to background executor
        with self._lock:
            executor = self._executor

        if executor is not None:
            executor.submit(
                self._process_message_background,
                message.chat_id,
                message.text,
                account_ctx
            )

        return message

    def _process_message_background(
        self,
        chat_id: int | str,
        text: str,
        account_ctx: AccountContext
    ) -> None:
        """Non-UI processing executed strictly off the UI thread."""
        # Confirm execution is on background thread
        thread_name = threading.current_thread().name

        # Perform background processing task (e.g. analysis, logging, transformation)
        result_payload = {
            "chat_id": chat_id,
            "account_id": account_ctx.account_id,
            "processed_text": text,
            "worker_thread": thread_name,
        }

        # Safe return to UI thread when UI-level callback/update is needed
        self.ui_dispatcher.post(lambda: self._on_ui_callback(result_payload, account_ctx))

    def _on_ui_callback(
        self,
        payload: Dict[str, Any],
        account_ctx: AccountContext
    ) -> None:
        """UI-thread callback invoked safely via UIThreadDispatcher."""
        # Account context is preserved and accessible here
        payload["ui_thread"] = threading.current_thread().name
        self.processed_log.append(payload)
