import asyncio
import inspect
import pytest
from unittest.mock import Mock, patch, AsyncMock
from google.api_core.exceptions import ServiceUnavailable
from google.cloud import tasks_v2
from fastapi.routing import APIRoute
from fastapi.dependencies.models import Dependant
from fastapi_cloud_tasks.delayer import Delayer
from fastapi_cloud_tasks.hooks import noop_hook

class _TestRetry:  # Prefix with underscore to prevent pytest from collecting it
    """A test implementation of the retry mechanism"""
    def __init__(self, target_func, max_retries=3):
        self.target_func = target_func
        self.max_retries = max_retries
        self.attempts = 0

    async def __call__(self, *args, **kwargs):
        self.attempts += 1
        try:
            return await self.target_func(*args, **kwargs)
        except ServiceUnavailable:
            if self.attempts > self.max_retries:
                raise
            return await self(*args, **kwargs)

def create_mock_route():
    """Helper function to create a properly mocked APIRoute"""
    mock_route = Mock(spec=APIRoute)
    mock_route.methods = ["POST"]
    mock_route.dependant = Mock(spec=Dependant)
    mock_route.dependant.path_params = []
    mock_route.dependant.query_params = []
    mock_route.dependant.header_params = []
    mock_route.dependant.cookie_params = []
    mock_route.param_convertors = {}
    mock_route.url_path_for = Mock(return_value="/test")
    mock_route.path_format = "/test"  # Add path_format attribute
    mock_route.body_field = None  # Add body_field attribute
    return mock_route

def test_delayer_init_with_retry_config():
    # Mock dependencies
    mock_route = create_mock_route()
    mock_client = Mock(spec=tasks_v2.CloudTasksAsyncClient)
    
    # Create delayer instance with custom retry config
    delayer = Delayer(
        route=mock_route,
        base_url="http://test.com",
        queue_path="projects/test/locations/test/queues/test",
        client=mock_client,
        pre_create_hook=noop_hook,
        max_retries=3,
        initial_retry_delay=0.5,
        max_retry_delay=30.0,
        retry_multiplier=2.0
    )
    
    # Assert retry configuration is set correctly
    assert delayer.retry._initial == 0.5
    assert delayer.retry._maximum == 30.0
    assert delayer.retry._multiplier == 2.0
    assert delayer.retry._timeout == delayer.task_create_timeout

@pytest.mark.asyncio
async def test_delay_with_retries():
    # Mock dependencies
    mock_route = create_mock_route()
    mock_client = Mock(spec=tasks_v2.CloudTasksAsyncClient)
    
    # Configure mock to fail twice with ServiceUnavailable, then succeed
    success_response = Mock()
    call_count = 0
    
    async def mock_create_task(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ServiceUnavailable("Broken pipe")
        return success_response
    
    # Create a test retry instance
    test_retry = _TestRetry(mock_create_task, max_retries=3)
    mock_client.create_task = test_retry
    
    delayer = Delayer(
        route=mock_route,
        base_url="http://test.com",
        queue_path="projects/test/locations/test/queues/test",
        client=mock_client,
        pre_create_hook=noop_hook,
        max_retries=3,
        initial_retry_delay=0.5,
        max_retry_delay=30.0,
        retry_multiplier=2.0
    )
    
    # Mock sleep to avoid delays
    with patch('time.sleep'):
        # Call delay method
        result = await delayer.delay()
        
        # Assert create_task was called the expected number of times
        assert call_count == 3
        assert result == success_response  # Verify we got the successful response

@pytest.mark.asyncio
async def test_delay_max_retries_exceeded():
    # Mock dependencies
    mock_route = create_mock_route()
    mock_client = Mock(spec=tasks_v2.CloudTasksAsyncClient)
    
    # Configure mock to always fail with ServiceUnavailable
    call_count = 0
    
    async def mock_create_task(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise ServiceUnavailable("Broken pipe")
    
    # Create a test retry instance
    test_retry = _TestRetry(mock_create_task, max_retries=3)
    mock_client.create_task = test_retry
    
    delayer = Delayer(
        route=mock_route,
        base_url="http://test.com",
        queue_path="projects/test/locations/test/queues/test",
        client=mock_client,
        pre_create_hook=noop_hook,
        max_retries=3,
        initial_retry_delay=0.5,
        max_retry_delay=30.0,
        retry_multiplier=2.0
    )
    
    # Mock sleep to avoid delays
    with patch('time.sleep'):
        # Call delay method and expect it to raise after max retries
        with pytest.raises(ServiceUnavailable):
            await delayer.delay()
        
        # Assert create_task was called max_retries + 1 times
        assert call_count == 4  # Initial try + 3 retries


@pytest.mark.asyncio
async def test_delay_result_is_not_coroutine():
    """Verify that delay() awaits the actual RPC call and returns a real result,
    not an unawaited coroutine. This catches the bug where using sync Retry
    instead of AsyncRetry causes the gRPC call to never execute."""
    mock_route = create_mock_route()
    mock_client = Mock(spec=tasks_v2.CloudTasksAsyncClient)

    expected_response = tasks_v2.Task(name="projects/p/locations/l/queues/q/tasks/t1")
    mock_client.create_task = AsyncMock(return_value=expected_response)

    delayer = Delayer(
        route=mock_route,
        base_url="http://test.com",
        queue_path="projects/test/locations/test/queues/test",
        client=mock_client,
        pre_create_hook=noop_hook,
    )

    result = await delayer.delay()

    # The critical assertion: result must be the actual response, not a coroutine.
    # With sync Retry wrapping an async call, result would be a coroutine object.
    assert not inspect.isawaitable(result), (
        f"delay() returned an unawaitable {type(result)} — "
        "the RPC was never actually executed"
    )
    assert result == expected_response
    mock_client.create_task.assert_called_once() 