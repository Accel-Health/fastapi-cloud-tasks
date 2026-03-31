# Standard Library Imports
import queue
from typing import Callable

# Third Party Imports
from fastapi.routing import APIRoute
from google.cloud import tasks_v2

# Imports from this repository
from fastapi_cloud_tasks.delayer import Delayer
from fastapi_cloud_tasks.hooks import DelayedTaskHook
from fastapi_cloud_tasks.hooks import noop_hook
from fastapi_cloud_tasks.utils import ensure_queue, ensure_queue_sync


def DelayedRouteBuilder(
    *,
    base_url: str,
    queue_path: str,
    task_create_timeout: float = 10.0,
    pre_create_hook: DelayedTaskHook = None,
    client=None,
    auto_create_queue=True,
):
    """
    Returns a Mixin that should be used to override route_class.

    It adds .delay (sync), .adelay (async), and .options methods to the
    original endpoint.

    The ``client`` parameter accepts either a ``CloudTasksClient`` (sync) or
    ``CloudTasksAsyncClient``.  When a sync client is provided, ``.delay()``
    works out-of-the-box; ``.adelay()`` requires an async client.  If no client
    is supplied, a sync ``CloudTasksClient`` is created by default so that
    existing (sync) call-sites continue to work without changes.

    Example:
    ```
      delayed_router = APIRouter(route_class=DelayedRouteBuilder(...), prefix="/delayed")

      class UserData(BaseModel):
          name: str

      @delayed_router.post("/on_user_create/{user_id}")
      def on_user_create(user_id: str, data: UserData):
          # do work here
          # Return values are meaningless

      # Sync call
      on_user_create.delay(user_id="007", data=UserData(name="Piyush"))

      # Async call
      await on_user_create.adelay(user_id="007", data=UserData(name="Piyush"))

      app.include_router(delayed_router)
    ```
    """
    if client is None:
        client = tasks_v2.CloudTasksClient()

    if pre_create_hook is None:
        pre_create_hook = noop_hook

    class TaskRouteMixin(APIRoute):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._queue_created = False

        def get_route_handler(self) -> Callable:
            original_route_handler = super().get_route_handler()
            self.endpoint.options = self.delayOptions
            self.endpoint.delay = self.delay
            self.endpoint.adelay = self.adelay
            return original_route_handler

        def delayOptions(self, **options) -> Delayer:
            delayOpts = dict(
                base_url=base_url,
                queue_path=queue_path,
                task_create_timeout=task_create_timeout,
                client=client,
                pre_create_hook=pre_create_hook,
            )
            if hasattr(self.endpoint, "_delayOptions"):
                delayOpts.update(self.endpoint._delayOptions)
            delayOpts.update(options)

            return Delayer(
                route=self,
                **delayOpts,
            )

        def delay(self, **kwargs):
            if auto_create_queue and not self._queue_created:
                ensure_queue_sync(client=client, path=queue_path)
                self._queue_created = True
            return self.delayOptions().delay(**kwargs)

        async def adelay(self, **kwargs):
            if auto_create_queue and not self._queue_created:
                await ensure_queue(client=client, path=queue_path)
                self._queue_created = True
            return await self.delayOptions().adelay(**kwargs)

    return TaskRouteMixin
