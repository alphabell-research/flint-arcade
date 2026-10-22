import asyncio
from dataclasses import replace
import json
from pathlib import Path
import shutil

import httpx
import pytest
from arcade import generation
from arcade.generation import extract_html
from arcade.sandbox import GameRejected, check_game, sandbox_command, validate_html
from arcade.settings import Settings

GAME = '''<!doctype html><html><head><title>Test Moon</title><meta name="description" content="A tiny test game"></head><body><canvas width="320" height="240"></canvas><button id="start">Start</button><button id="restart">Restart</button><script>
let state={phase:'ready',score:0,player:{x:20,y:100}};
const canvas=document.querySelector('canvas'),ctx=canvas.getContext('2d');
function draw(){ctx.fillStyle='#123456';ctx.fillRect(0,0,320,240);for(let i=0;i<10;i++){ctx.fillStyle=`rgb(${i*20},${200-i*10},50)`;ctx.fillRect(i*25,10,15,15)}ctx.fillStyle='white';ctx.fillRect(state.player.x,state.player.y,20,20)}
function move(){state.phase='playing';state.player.x+=5;state.score+=1;draw()}
window.arcade={restart(){state={phase:'ready',score:0,player:{x:20,y:100}};draw()},snapshot(){return structuredClone(state)}};
document.addEventListener('keydown',e=>{if(e.key==='r')window.arcade.restart();else move()});canvas.addEventListener('click',move);canvas.addEventListener('touchstart',e=>{e.preventDefault();move()},{passive:false});document.querySelector('#start').onclick=move;document.querySelector('#restart').onclick=()=>window.arcade.restart();draw();
</script></body></html>'''


def config(tmp_path, **kwargs):
    return Settings(data_dir=tmp_path, **({"generation_enabled":True} | kwargs))


def test_extract_last_complete_document_and_not_reasoning():
    assert 'second' in extract_html('<html>first</html> thought <html>second</html>')
    with pytest.raises(GameRejected):
        extract_html('No document')


@pytest.mark.parametrize('bad', ['<meta http-equiv="refresh" content="0;url=https://example.com">','<iframe srcdoc="x"></iframe>','<img src="https://example.com/x">','<script>fetch("x")</script>','<script>window.location="https://example.com"</script>','<style>@import "https://example.com";</style>','<base href="https://example.com">'])
def test_static_network_and_navigation_policy(bad):
    with pytest.raises(GameRejected):
        validate_html(GAME.replace('</head>', bad+'</head>'))


def test_static_policy_accepts_standalone_canvas_game():
    assert validate_html(GAME)


def test_bubblewrap_command_excludes_credentials_and_home(tmp_path):
    settings = config(tmp_path)
    command = sandbox_command(tmp_path / 'game.html', tmp_path / 'out', settings.chromium)
    assert '--unshare-all' in command and '--clearenv' in command
    assert not any('/.ssh' in part or '/.config' in part or '/.advisor' in part for part in command)
    assert '--ro-bind' in command


def test_isolated_browser_actual_desktop_and_mobile(tmp_path):
    if not shutil.which('bwrap') or not Path('/usr/lib/chromium/chromium').exists():
        pytest.skip('Bubblewrap and system Chromium required')
    result = asyncio.run(check_game(GAME, tmp_path / 'run', config(tmp_path)))
    assert result['accepted'], result
    assert len(result['checks']) == 2
    assert (tmp_path / 'run/qa/desktop.png').stat().st_size > 100
    assert (tmp_path / 'run/qa/mobile.png').stat().st_size > 100


def test_bubblewrap_missing_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    with pytest.raises(GameRejected):
        sandbox_command(tmp_path / 'input', tmp_path / 'output', config(tmp_path).chromium)


@pytest.mark.parametrize("feedback", ["mobile: input_progress failed", "Game startup JavaScript errors: Unexpected token semicolon"])
def test_generate_repairs_with_measured_feedback(monkeypatch, tmp_path, checked_model, feedback):
    calls = []
    async def complete(messages, settings):
        calls.append(list(messages))
        return GAME, {'model':'actual-model','usage':{'completion_tokens':123}}, {'usage':{'completion_tokens':123}}
    count = 0
    async def check(document, directory, settings):
        nonlocal count
        count += 1
        return {'accepted': count == 2, 'errors': [] if count == 2 else [feedback], 'scope':'smoke tests'}
    monkeypatch.setattr(generation, '_complete', complete)
    monkeypatch.setattr(generation, 'check_game', check)
    stages = []
    async def stage(name): stages.append(name)
    result = asyncio.run(generation.generate('tiny moon',None,stage,config(tmp_path)))
    assert result['qa']['accepted']
    assert len(calls) == 2 and feedback in calls[1][-1]['content']
    assert 'repairing' in stages and result['title'] == 'Test Moon'
    assert len(result['provenance']['attempts']) == 2


def test_generate_never_exceeds_two_repairs(monkeypatch, tmp_path, checked_model):
    count = 0
    async def complete(messages, settings):
        nonlocal count
        count += 1
        return '<html>broken</html>', {}, {}
    monkeypatch.setattr(generation, '_complete', complete)
    async def stage(name): pass
    result = asyncio.run(generation.generate('tiny moon',None,stage,config(tmp_path)))
    assert count == 3 and result['qa']['accepted'] is False


def test_generation_disabled_before_any_request(tmp_path):
    async def stage(name): pass
    with pytest.raises(RuntimeError):
        asyncio.run(generation.generate('tiny moon',None,stage,config(tmp_path,generation_enabled=False)))


def test_completion_parameters_and_reasoning_separation(monkeypatch, tmp_path):
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        assert request.headers['authorization'] == 'Bearer private-test-key'
        return httpx.Response(200, json={'choices':[{'message':{'content':GAME,'reasoning_content':'<html>not executable</html>'},'finish_reason':'stop'}], 'model':'actual-flint-q8','usage':{'prompt_tokens':20,'completion_tokens':100}})
    original = httpx.AsyncClient
    def mocked(**kwargs):
        kwargs['transport'] = httpx.MockTransport(handle)
        return original(**kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', mocked)
    settings = config(tmp_path, upstream_url='https://inference.invalid/v1', upstream_model='flint-q8', upstream_api_key='private-test-key')
    content, response, metadata = asyncio.run(generation._complete([{'role':'user','content':'game'}],settings))
    assert content == GAME and metadata['usage']['completion_tokens'] == 100
    body = requests[0]
    assert body['max_tokens'] == 16384 and body['reasoning_budget_tokens'] == 4096
    assert body['temperature'] == 1 and body['top_p'] == .95 and body['top_k'] == 20
    assert body['stream'] is False and 'private-test-key' not in json.dumps(metadata)


def test_actual_browser_rejects_computed_external_request(tmp_path):
    if not shutil.which('bwrap') or not Path('/usr/lib/chromium/chromium').exists():
        pytest.skip('Bubblewrap and system Chromium required')
    hostile = GAME.replace('draw();\n</script>', "draw();let image=new Image();image.src='https://example.invalid/private';\n</script>")
    result = asyncio.run(check_game(hostile, tmp_path / 'hostile', config(tmp_path)))
    assert not result['accepted']
    assert any(item['blocked_requests'] for item in result['checks'])


@pytest.fixture
def checked_model(monkeypatch):
    async def probe(settings):
        return {"ftype": "Q8_0", "n_params": 39785287456, "size": 42344889472}
    monkeypatch.setattr(generation, "_probe_model", probe)


def test_actual_browser_reports_startup_syntax_error(tmp_path):
    if not shutil.which("bwrap") or not Path("/usr/lib/chromium/chromium").exists():
        pytest.skip("Bubblewrap and system Chromium required")
    broken = GAME.replace("let state={", "let state= ; {")
    result = asyncio.run(check_game(broken, tmp_path / "broken", config(tmp_path)))
    assert result["accepted"] is False
    errors = " ".join(result["errors"])
    assert "JavaScript" in errors and "Unexpected token" in errors
    assert "Provide window.arcade" not in errors


@pytest.mark.parametrize("ftype", ["Q8_0", "Q4_K_M", None])
def test_probe_requires_live_selected_q8_model(monkeypatch, tmp_path, ftype):
    def handle(request):
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer private-key"
        return httpx.Response(200, json={"data": [{"id":"selected-flint", "meta":{"ftype":ftype, "n_params":39785287456, "size":42344889472, "n_ctx":32768, "path":"private-fleet-path"}}]})
    original = httpx.AsyncClient
    def mocked(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        return original(**kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", mocked)
    settings = config(tmp_path, upstream_url="https://inference.invalid/v1/chat/completions", upstream_model="selected-flint", upstream_api_key="private-key")
    if ftype == "Q8_0":
        result = asyncio.run(generation._probe_model(settings))
        assert result == {"ftype":"Q8_0", "n_params":39785287456,"size":42344889472,"n_ctx":32768}
        assert "private" not in json.dumps(result)
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(generation._probe_model(settings))
