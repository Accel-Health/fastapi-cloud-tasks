# Standard Library Imports
import datetime
import logging

# Third Party Imports
from fastapi.routing import APIRoute
from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2
from google.api_core import retry as sync_retry
from google.api_core import retry_async
from google.api_core.exceptions import ServiceUnavailable, DeadlineExceeded, InternalServerError, ResourceExhausted

# Imports from this repository
from fastapi_cloud_tasks.exception import BadMethodException
from fastapi_cloud_tasks.hooks import DelayedTaskHook
from fastapi_cloud_tasks.requester import Requester

logger = logging.getLogger(__name__)

RETRYABLE_EXCEPTIONS = (
    ServiceUnavailable,
    DeadlineExceeded,
    InternalServerError,
    ResourceExhausted,
)


class Delayer(Requester):
    def __init__(
        self,
        *,
        route: APIRoute,
        base_url: str,
        queue_path: str,
        client,
        pre_create_hook: DelayedTaskHook,
        task_create_timeout: float = 10.0,
        countdown: int = 0,
        task_id: str = None,
        max_retries: int = 5,
        initial_retry_delay: float = 0.5,
        max_retry_delay: float = 30.0,
        retry_multiplier: float = 2.0,
    ) -> None:
        super().__init__(route=route, base_url=base_url)
        self.queue_path = queue_path
        self.countdown = countdown
        self.task_create_timeout = task_create_timeout

        self.task_id = task_id
        self.method = _task_method(route.methods)
        self.client = client
        self.pre_create_hook = pre_create_hook

        retry_kwargs = dict(
            initial=initial_retry_delay,
            maximum=max_retry_delay,
            multiplier=retry_multiplier,
            deadline=task_create_timeout,
            on_error=lambda exc: logger.warning(
                f"Task creation attempt failed: {exc}. Retrying...",
                exc_info=True
            ),
        )

        self.async_retry = retry_async.AsyncRetry(
            predicate=retry_async.if_exception_type(*RETRYABLE_EXCEPTIONS),
            **retry_kwargs,
        )

        self.sync_retry = sync_retry.Retry(
            predicate=sync_retry.if_exception_type(*RETRYABLE_EXCEPTIONS),
            **retry_kwargs,
        )

        # Keep self.retry pointing to async for backward compat with tests
        self.retry = self.async_retry

    def _build_task_request(self, **kwargs):
        """Build the CreateTaskRequest (shared by sync and async paths)."""
        request = tasks_v2.HttpRequest()
        request.http_method = self.method
        request.url = self._url(values=kwargs)
        request.headers = self._headers(values=kwargs)

        body = self._body(values=kwargs)
        if body:
            request.body = body

        task = tasks_v2.Task(http_request=request)
        schedule_time = self._schedule()
        if schedule_time:
            task.schedule_time = schedule_time

        if self.task_id:
            task.name = f"{self.queue_path}/tasks/{self.task_id}"

        create_request = tasks_v2.CreateTaskRequest(parent=self.queue_path, task=task)
        create_request = self.pre_create_hook(create_request)
        return create_request

    def delay(self, **kwargs):
        """Synchronous delay - dispatches the task using the sync client."""
        request = self._build_task_request(**kwargs)
        return self.client.create_task(
            request=request,
            retry=self.sync_retry,
        )

    async def adelay(self, **kwargs):
        """Async delay - dispatches the task using the async client."""
        request = self._build_task_request(**kwargs)
        return await self.client.create_task(
            request=request,
            retry=self.async_retry,
        )

    def _schedule(self):
        if self.countdown is None or self.countdown <= 0:
            return None
        d = datetime.datetime.utcnow() + datetime.timedelta(seconds=self.countdown)
        timestamp = timestamp_pb2.Timestamp()
        timestamp.FromDatetime(d)
        return timestamp


def _task_method(methods):
    methodMap = {
        "POST": tasks_v2.HttpMethod.POST,
        "GET": tasks_v2.HttpMethod.GET,
        "HEAD": tasks_v2.HttpMethod.HEAD,
        "PUT": tasks_v2.HttpMethod.PUT,
        "DELETE": tasks_v2.HttpMethod.DELETE,
        "PATCH": tasks_v2.HttpMethod.PATCH,
        "OPTIONS": tasks_v2.HttpMethod.OPTIONS,
    }
    methods = list(methods)
    # Only crash if we're being bound
    if len(methods) > 1:
        raise BadMethodException("Can't trigger task with multiple methods")
    method = methodMap.get(methods[0], None)
    if method is None:
        raise BadMethodException(f"Unknown method {methods[0]}")
    return method
