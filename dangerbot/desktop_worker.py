"""Run cancellable asyncio tasks outside Tk's UI thread."""
import asyncio
import threading


class TaskRunner:
    def __init__(self, events):
        self.events = events
        self.thread = None
        self.loop = None
        self.task = None
        self.stop_requested = threading.Event()

    @property
    def active(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, factory):
        if self.active:
            raise RuntimeError("Já existe uma operação em andamento.")
        self.stop_requested.clear()
        self.thread = threading.Thread(target=self._run, args=(factory,), daemon=True)
        self.thread.start()

    def _run(self, factory):
        outcome = "success"
        async def execute():
            self.loop = asyncio.get_running_loop()
            self.task = asyncio.current_task()
            if self.stop_requested.is_set():
                raise asyncio.CancelledError
            await factory()
        try:
            asyncio.run(execute())
        except asyncio.CancelledError:
            outcome = "cancelled"
        except Exception as error:
            # No traceback or raw exception string: third-party errors may contain secrets.
            from .oauth import OAuthError
            from .spotify import SpotifyError
            message = str(error) if isinstance(error, (OAuthError, SpotifyError, ValueError)) else (
                f"A operação falhou ({type(error).__name__}). Confira a configuração e tente novamente.")
            self.events.put(("error", message))
            outcome = "error"
        finally:
            self.loop = self.task = None
        self.events.put(("finished", outcome))

    def stop(self):
        self.stop_requested.set()
        loop, task = self.loop, self.task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # The operation finished while Stop was being pressed.
