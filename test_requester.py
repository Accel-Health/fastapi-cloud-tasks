import pytest
from fastapi import APIRouter, FastAPI, Header, Query
from pydantic import BaseModel

from fastapi_cloud_tasks.exception import MissingParamError, WrongTypeError
from fastapi_cloud_tasks.requester import Requester


class Payload(BaseModel):
    message: str


class NestedPayload(BaseModel):
    items: list
    count: int


BASE_URL = "http://localhost:8000"


def _build_route(fn, *, prefix="/test", method="post"):
    router = APIRouter(prefix=prefix)
    getattr(router, method)(f"/{fn.__name__}")(fn)
    app = FastAPI()
    app.include_router(router)
    return router.routes[0]


def _requester(fn, **kwargs):
    route = _build_route(fn, **kwargs)
    return Requester(route=route, base_url=BASE_URL)


# --- _body tests ---


class TestBodyWithDefault:
    def setup_method(self):
        async def endpoint(p: Payload = Payload(message="Default")):
            pass

        self.requester = _requester(endpoint)

    def test_body_with_explicit_value(self):
        body = self.requester._body(values={"p": Payload(message="hello")})
        assert body == b'{"message": "hello"}'

    def test_body_falls_back_to_default(self):
        body = self.requester._body(values={})
        assert body == b'{"message": "Default"}'

    def test_body_with_none_falls_back_to_default(self):
        body = self.requester._body(values={"p": None})
        assert body == b'{"message": "Default"}'


class TestBodyRequired:
    def setup_method(self):
        async def endpoint(p: Payload):
            pass

        self.requester = _requester(endpoint)

    def test_body_missing_required_raises(self):
        with pytest.raises(MissingParamError):
            self.requester._body(values={})

    def test_body_none_required_raises(self):
        with pytest.raises(MissingParamError):
            self.requester._body(values={"p": None})

    def test_body_with_value(self):
        body = self.requester._body(values={"p": Payload(message="hi")})
        assert body == b'{"message": "hi"}'


class TestBodyWrongType:
    def setup_method(self):
        async def endpoint(p: Payload):
            pass

        self.requester = _requester(endpoint)

    def test_body_wrong_type_raises(self):
        with pytest.raises(WrongTypeError):
            self.requester._body(values={"p": "not a payload"})

    def test_body_wrong_model_raises(self):
        with pytest.raises(WrongTypeError):
            self.requester._body(values={"p": NestedPayload(items=[], count=0)})


class TestBodyNested:
    def setup_method(self):
        async def endpoint(p: NestedPayload):
            pass

        self.requester = _requester(endpoint)

    def test_nested_body_serialization(self):
        body = self.requester._body(
            values={"p": NestedPayload(items=["a", "b"], count=2)}
        )
        assert body == b'{"items": ["a", "b"], "count": 2}'


class TestNoBody:
    def setup_method(self):
        async def endpoint():
            pass

        self.requester = _requester(endpoint)

    def test_no_body_returns_none(self):
        assert self.requester._body(values={}) is None


# --- _url tests ---


class TestUrlSimple:
    def setup_method(self):
        async def hello():
            pass

        self.requester = _requester(hello, prefix="/delayed")

    def test_simple_url(self):
        url = self.requester._url(values={})
        assert url == "http://localhost:8000/delayed/hello"


class TestUrlWithPathParam:
    def setup_method(self):
        router = APIRouter(prefix="/tasks")

        @router.post("/process/{task_id}")
        async def process(task_id: str):
            pass

        app = FastAPI()
        app.include_router(router)
        self.requester = Requester(route=router.routes[0], base_url=BASE_URL)

    def test_path_param_interpolation(self):
        url = self.requester._url(values={"task_id": "abc-123"})
        assert url == "http://localhost:8000/tasks/process/abc-123"


class TestUrlWithQueryParam:
    def setup_method(self):
        async def search(q: str = Query("default"), limit: int = Query(10)):
            pass

        self.requester = _requester(search, prefix="/api")

    def test_query_params_in_url(self):
        url = self.requester._url(values={"q": "test", "limit": 5})
        assert "q=test" in url
        assert "limit=5" in url

    def test_query_params_defaults(self):
        url = self.requester._url(values={})
        assert "q=default" in url
        assert "limit=10" in url


# --- _headers tests ---


class TestHeaders:
    def setup_method(self):
        async def endpoint():
            pass

        self.requester = _requester(endpoint)

    def test_content_type_always_set(self):
        headers = self.requester._headers(values={})
        assert headers["Content-Type"] == "application/json"

    def test_cloudtasks_headers_excluded(self):
        headers = self.requester._headers(values={})
        for key in headers:
            assert not key.startswith("x_cloudtasks_")


class TestHeadersCustom:
    def setup_method(self):
        async def endpoint(x_custom: str = Header("val")):
            pass

        self.requester = _requester(endpoint)

    def test_custom_header_included(self):
        headers = self.requester._headers(values={"x-custom": "myval"})
        assert headers["x_custom"] == "myval"


# --- _err_val / validation error tests ---


class TestErrValDictErrors:
    """Tests for the dict-based error handling in _err_val (FastAPI 0.135+)."""

    def test_missing_required_header_raises_valueerror(self):
        async def endpoint(x_required: int = Header(...)):
            pass

        r = _requester(endpoint)
        with pytest.raises(ValueError, match="Field required"):
            r._headers(values={})

    def test_wrong_type_query_param_raises_valueerror(self):
        async def endpoint(count: int = Query(...)):
            pass

        r = _requester(endpoint)
        with pytest.raises(ValueError, match="valid integer"):
            r._url(values={"count": "not_a_number"})

    def test_missing_required_query_param_raises_valueerror(self):
        async def endpoint(q: str = Query(...)):
            pass

        r = _requester(endpoint)
        with pytest.raises(ValueError, match="Field required"):
            r._url(values={})


# --- _body with dict input (validate coercion) ---


class TestBodyDictCoercion:
    """Tests that _body can accept a raw dict and validate/coerce it via ModelField.validate."""

    def setup_method(self):
        async def endpoint(p: Payload):
            pass

        self.requester = _requester(endpoint)

    def test_dict_input_coerced_to_model(self):
        body = self.requester._body(values={"p": {"message": "from dict"}})
        assert body == b'{"message": "from dict"}'

    def test_dict_input_invalid_raises(self):
        with pytest.raises(WrongTypeError):
            self.requester._body(values={"p": {"wrong_field": 123}})


# --- Integration: DelayedRouteBuilder end-to-end ---


class TestDelayedRouteBuilderIntegration:
    """Integration tests going through DelayedRouteBuilder -> Delayer -> create_task."""

    def setup_method(self):
        from unittest.mock import AsyncMock, Mock

        from google.cloud import tasks_v2

        from fastapi_cloud_tasks import DelayedRouteBuilder

        mock_client = Mock(spec=tasks_v2.CloudTasksAsyncClient)
        mock_client.create_task = AsyncMock(return_value=Mock())

        DelayedRoute = DelayedRouteBuilder(
            client=mock_client,
            base_url="http://worker:8000",
            queue_path="projects/p/locations/l/queues/q",
            auto_create_queue=False,
        )

        router = APIRouter(route_class=DelayedRoute, prefix="/tasks")

        @router.post("/process/{task_id}")
        async def process(task_id: str, p: Payload):
            pass

        @router.post("/simple")
        async def simple(p: Payload = Payload(message="default")):
            pass

        app = FastAPI()
        app.include_router(router)

        self.mock_client = mock_client
        self.process_route = router.routes[0]
        self.simple_route = router.routes[1]

    @pytest.mark.asyncio
    async def test_delay_with_path_and_body(self):
        await self.process_route.endpoint.delay(
            task_id="abc", p=Payload(message="hello")
        )
        self.mock_client.create_task.assert_called_once()
        request = self.mock_client.create_task.call_args[1]["request"]
        assert "/tasks/process/abc" in request.task.http_request.url
        assert b'"message": "hello"' in request.task.http_request.body

    @pytest.mark.asyncio
    async def test_delay_with_default_body(self):
        await self.simple_route.endpoint.delay()
        self.mock_client.create_task.assert_called_once()
        request = self.mock_client.create_task.call_args[1]["request"]
        assert b'"message": "default"' in request.task.http_request.body

    @pytest.mark.asyncio
    async def test_delay_options_override_countdown(self):
        await self.simple_route.endpoint.options(countdown=300).delay()
        self.mock_client.create_task.assert_called_once()
        request = self.mock_client.create_task.call_args[1]["request"]
        assert request.task.schedule_time is not None

    @pytest.mark.asyncio
    async def test_delay_options_override_base_url(self):
        await self.simple_route.endpoint.options(
            base_url="http://other:9000"
        ).delay()
        self.mock_client.create_task.assert_called_once()
        request = self.mock_client.create_task.call_args[1]["request"]
        assert request.task.http_request.url.startswith("http://other:9000")

    @pytest.mark.asyncio
    async def test_delay_with_task_id_deduplication(self):
        await self.simple_route.endpoint.options(task_id="dedup-123").delay()
        request = self.mock_client.create_task.call_args[1]["request"]
        assert request.task.name == "projects/p/locations/l/queues/q/tasks/dedup-123"
