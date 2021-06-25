import asyncio
import textwrap
import time

import aiohttp.web
import pytest

from kopf._cogs.clients.api import delete, get, patch, post, request, stream
from kopf._cogs.clients.errors import APIError


@pytest.mark.parametrize('method', ['get', 'post', 'patch', 'delete'])
async def test_raw_requests_work(resp_mocker, aresponses, hostname, method):
    mock = resp_mocker(return_value=aiohttp.web.json_response({}))
    aresponses.add(hostname, '/url', method, mock)
    response = await request(
        method=method,
        url='/url',
        payload={'fake': 'payload'},
        headers={'fake': 'headers'},
    )
    assert isinstance(response, aiohttp.ClientResponse)  # unparsed!
    assert mock.call_count == 1
    assert isinstance(mock.call_args[0][0], aiohttp.web.BaseRequest)
    assert mock.call_args[0][0].method.lower() == method
    assert mock.call_args[0][0].path == '/url'
    assert mock.call_args[0][0].data == {'fake': 'payload'}
    assert mock.call_args[0][0].headers['fake'] == 'headers'  # and other system headers


@pytest.mark.parametrize('method', ['get', 'post', 'patch', 'delete'])
async def test_raw_requests_are_not_parsed(resp_mocker, aresponses, hostname, method):
    mock = resp_mocker(return_value=aresponses.Response(text='BAD JSON!'))
    aresponses.add(hostname, '/url', method, mock)
    response = await request(method, '/url')
    assert isinstance(response, aiohttp.ClientResponse)


@pytest.mark.parametrize('method', ['get', 'post', 'patch', 'delete'])
async def test_server_errors_escalate(resp_mocker, aresponses, hostname, method):
    mock = resp_mocker(return_value=aiohttp.web.json_response({}, status=666))
    aresponses.add(hostname, '/url', method, mock)
    with pytest.raises(APIError) as err:
        await request(method, '/url')
    assert err.value.status == 666


@pytest.mark.parametrize('method', ['get', 'post', 'patch', 'delete'])
async def test_relative_urls_are_prepended_with_server(resp_mocker, aresponses, hostname, method):
    mock = resp_mocker(return_value=aiohttp.web.json_response({}))
    aresponses.add(hostname, '/url', method, mock)
    await request(method, '/url')
    assert isinstance(mock.call_args[0][0], aiohttp.web.BaseRequest)
    assert str(mock.call_args[0][0].url) == f'http://{hostname}/url'


@pytest.mark.parametrize('method', ['get', 'post', 'patch', 'delete'])
async def test_absolute_urls_are_passed_through(resp_mocker, aresponses, hostname, method):
    mock = resp_mocker(return_value=aiohttp.web.json_response({}))
    aresponses.add(hostname, '/url', method, mock)
    aresponses.add('fakehost.localdomain', '/url', method, mock)
    await request(method, 'http://fakehost.localdomain/url')
    assert isinstance(mock.call_args[0][0], aiohttp.web.BaseRequest)
    assert str(mock.call_args[0][0].url) == 'http://fakehost.localdomain/url'


@pytest.mark.parametrize('fn, method', [
    (get, 'get'),
    (post, 'post'),
    (patch, 'patch'),
    (delete, 'delete'),
])
async def test_parsing_in_requests(resp_mocker, aresponses, hostname, fn, method):
    mock = resp_mocker(return_value=aiohttp.web.json_response({'fake': 'result'}))
    aresponses.add(hostname, '/url', method, mock)
    response = await fn(
        url='/url',
        payload={'fake': 'payload'},
        headers={'fake': 'headers'},
    )
    assert response == {'fake': 'result'}  # parsed!
    assert mock.call_count == 1
    assert isinstance(mock.call_args[0][0], aiohttp.web.BaseRequest)
    assert mock.call_args[0][0].method.lower() == method
    assert mock.call_args[0][0].path == '/url'
    assert mock.call_args[0][0].data == {'fake': 'payload'}
    assert mock.call_args[0][0].headers['fake'] == 'headers'  # and other system headers


@pytest.mark.parametrize('method', ['get'])  # the only supported method at the moment
async def test_parsing_in_streams(resp_mocker, aresponses, hostname, method):
    mock = resp_mocker(return_value=aresponses.Response(text=textwrap.dedent("""
        {"fake": "result1"}
        {"fake": "result2"}
    """)))
    aresponses.add(hostname, '/url', method, mock)

    items = []
    async for item in stream(
        url='/url',
        payload={'fake': 'payload'},
        headers={'fake': 'headers'},
    ):
        items.append(item)

    assert items == [{'fake': 'result1'}, {'fake': 'result2'}]
    assert mock.call_count == 1
    assert isinstance(mock.call_args[0][0], aiohttp.web.BaseRequest)
    assert mock.call_args[0][0].method.lower() == method
    assert mock.call_args[0][0].path == '/url'
    assert mock.call_args[0][0].data == {'fake': 'payload'}
    assert mock.call_args[0][0].headers['fake'] == 'headers'  # and other system headers


@pytest.mark.parametrize('fn, method', [
    (get, 'get'),
    (post, 'post'),
    (patch, 'patch'),
    (delete, 'delete'),
])
async def test_timeout_in_requests(resp_mocker, aresponses, hostname, fn, method):

    def serve_slowly():
        time.sleep(0.2)
        return aiohttp.web.json_response({})

    mock = resp_mocker(side_effect=serve_slowly)
    aresponses.add(hostname, '/url', method, mock)

    with pytest.raises(asyncio.TimeoutError):
        await fn('/url', timeout=aiohttp.ClientTimeout(total=0.1))


@pytest.mark.parametrize('method', ['get'])  # the only supported method at the moment
async def test_timeout_in_streams(resp_mocker, aresponses, hostname, method):

    def serve_slowly():
        time.sleep(0.2)
        return "{}"

    mock = resp_mocker(side_effect=serve_slowly)
    aresponses.add(hostname, '/url', method, mock)

    with pytest.raises(asyncio.TimeoutError):
        async for _ in stream('/url', timeout=aiohttp.ClientTimeout(total=0.1)):
            pass


@pytest.mark.parametrize('delay, expected', [
    pytest.param(0.0, [], id='instant-none'),
    pytest.param(0.1, [{'fake': 'result1'}], id='fast-single'),
    pytest.param(9.9, [{'fake': 'result1'}, {'fake': 'result2'}], id='inf-double'),
])
@pytest.mark.parametrize('method', ['get'])  # the only supported method at the moment
async def test_stopper_in_streams(resp_mocker, aresponses, hostname, method, delay, expected):

    async def stream_slowly(request: aiohttp.ClientRequest):
        response = aiohttp.web.StreamResponse()
        await response.prepare(request)
        await asyncio.sleep(0.05)
        await response.write(b'{"fake": "result1"}\n')
        await asyncio.sleep(0.15)
        await response.write(b'{"fake": "result2"}\n')
        await response.write_eof()
        return response

    aresponses.add(hostname, '/url', method, stream_slowly)

    stopper = asyncio.Future()
    asyncio.get_running_loop().call_later(delay, stopper.set_result, None)

    items = []
    async for item in stream('/url', stopper=stopper):
        items.append(item)

    assert items == expected
