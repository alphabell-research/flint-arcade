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
    assert '--unshare-all' in command and '--clearenv' not in command
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
    assert body['max_tokens'] == 8192 and body['reasoning_budget_tokens'] == 0
    assert body['grammar'].startswith('root ::= "<!doctype html>"')
    assert body['chat_template_kwargs'] == {'enable_thinking': False}
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
    async def preflight(directory, settings): return True
    monkeypatch.setattr(generation, "isolation_preflight", preflight)


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


def test_actual_browser_rejects_nonfinite_player_after_input(tmp_path):
    if not shutil.which("bwrap") or not Path("/usr/lib/chromium/chromium").exists():
        pytest.skip("Bubblewrap and system Chromium required")
    broken = GAME.replace("state.player.x+=5", "state.player.x=NaN")
    result = asyncio.run(check_game(broken, tmp_path / "nonfinite", config(tmp_path)))
    assert result["accepted"] is False
    assert any("player coordinates must be finite numbers" in error for error in result["errors"])


def test_keyboard_playable_game_without_visible_buttons_is_rejected(tmp_path):
    if not shutil.which("bwrap") or not Path("/usr/lib/chromium/chromium").exists():
        pytest.skip("Bubblewrap and system Chromium required")
    hidden_buttons = GAME.replace("</head>", "<style>button{display:none}</style></head>")
    result = asyncio.run(check_game(hidden_buttons, tmp_path / "hidden-buttons", config(tmp_path)))
    assert result["accepted"] is False
    assert len(result["checks"]) == 2
    for check in result["checks"]:
        assert check["input_progress"] is True
        assert check["start_button"] is False and check["restart_button"] is False
    assert any("start_button failed" in error for error in result["errors"])
    assert any("restart_button failed" in error for error in result["errors"])


def test_overlay_start_button_is_used_before_covered_canvas(tmp_path):
    if not shutil.which("bwrap") or not Path("/usr/lib/chromium/chromium").exists():
        pytest.skip("Bubblewrap and system Chromium required")
    overlay = GAME.replace(
        '<canvas width="320" height="240"></canvas><button id="start">Start</button>',
        '<div style="position:relative;width:320px;height:240px"><canvas width="320" height="240"></canvas><div id="overlay" style="position:absolute;inset:0;background:#0008;display:flex;align-items:center;justify-content:center"><button id="start">Start</button></div></div>'
    ).replace("else move()", "else if(state.phase==='playing')move()")
    overlay = overlay.replace("document.querySelector('#start').onclick=move", "document.querySelector('#start').onclick=()=>{document.querySelector('#overlay').style.display='none';move()}")
    result = asyncio.run(check_game(overlay, tmp_path / "overlay-start", config(tmp_path)))
    assert result["accepted"], result
    assert all(check["start_button"] and check["input_progress"] for check in result["checks"])


def test_framework_starter_is_standalone_and_passes_real_controls(tmp_path):
    document = Path(generation.__file__).with_name("starter.html").read_text()
    assert validate_html(document)
    if not shutil.which("bwrap") or not Path("/usr/lib/chromium/chromium").exists():
        pytest.skip("Bubblewrap and system Chromium required")
    result = asyncio.run(check_game(document, tmp_path / "starter", config(tmp_path)))
    assert result["accepted"], result
    assert all(check["start_button"] and check["restart_button"] and check["input_progress"] for check in result["checks"])


def test_new_games_get_framework_and_remixes_keep_actual_parent(monkeypatch, tmp_path, checked_model):
    calls = []
    async def complete(messages, settings):
        calls.append(messages[1]["content"])
        return GAME, {}, {}
    async def check(document, directory, settings):
        return {"accepted":True,"errors":[],"scope":"test"}
    async def stage(name): pass
    monkeypatch.setattr(generation,"_complete",complete)
    monkeypatch.setattr(generation,"check_game",check)
    asyncio.run(generation.generate("an original game",None,stage,config(tmp_path)))
    asyncio.run(generation.generate("remix",GAME,stage,config(tmp_path)))
    assert "tested alphaBell framework foundation" in calls[0] and "Spark Courier" in calls[0]
    assert GAME in calls[1] and "Spark Courier" not in calls[1]


def test_typography_normalization_is_documented_and_preserves_controls():
    original = GAME.replace("A tiny test game", "Near \u2014 far \u2013 home &mdash; garden &ndash; stars &#8212; sun &#x2013; moon")
    normalized, counts = generation.normalize_html(original)
    assert "\u2014" not in normalized and "\u2013" not in normalized
    assert "&mdash;" not in normalized and "&#8212;" not in normalized
    assert counts == {"unicode_em_dash":1,"unicode_en_dash":1,"entity_em_dash":2,"entity_en_dash":2}
    assert normalized.split("<script>")[1] == original.split("<script>")[1]
    javascript_text = 'const instructions="Move \u2014 collect \u2013 deliver";'
    adjusted, changed = generation.normalize_html(javascript_text)
    assert adjusted == 'const instructions="Move, collect - deliver";' and sum(changed.values()) == 2


def test_unconstrained_ablation_omits_grammar_and_thinking_override(monkeypatch,tmp_path):
    requests=[]
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={"choices":[{"message":{"content":GAME},"finish_reason":"stop"}],"usage":{}})
    original=httpx.AsyncClient
    def mocked(**kwargs):
        kwargs["transport"]=httpx.MockTransport(handle)
        return original(**kwargs)
    monkeypatch.setattr(httpx,"AsyncClient",mocked)
    settings=config(tmp_path,upstream_url="https://inference.invalid/v1",upstream_model="flint",constrain_html=False)
    asyncio.run(generation._complete([{"role":"user","content":"game"}],settings))
    assert "grammar" not in requests[0] and "chat_template_kwargs" not in requests[0]
    assert requests[0]["reasoning_budget_tokens"] == 4096


def test_generation_records_typography_derivation_and_keeps_raw_response(monkeypatch,tmp_path,checked_model):
    raw=GAME.replace("Test Moon","Test\u2014Moon")
    async def complete(messages,settings):return raw,{"choices":[{"message":{"content":raw}}]},{}
    async def check(document,directory,settings):
        assert "\u2014" not in document
        return {"accepted":True,"errors":[]}
    async def stage(name):pass
    monkeypatch.setattr(generation,"_complete",complete)
    monkeypatch.setattr(generation,"check_game",check)
    result=asyncio.run(generation.generate("moon",None,stage,config(tmp_path)))
    provenance=result["provenance"]
    assert provenance["raw_html_sha256"]!=provenance["html_sha256"]
    assert provenance["normalizations"]=={"unicode_em_dash":1}
    assert provenance["attempts"][0]["normalized_counts"]=={"unicode_em_dash":1}
    saved=json.loads(next((tmp_path/"builds").glob("*/1/response.json")).read_text())
    assert saved["choices"][0]["message"]["content"]==raw


def test_startup_runtime_feedback_includes_game_source_line(tmp_path):
    if not shutil.which("bwrap") or not Path("/usr/lib/chromium/chromium").exists():
        pytest.skip("Bubblewrap and system Chromium required")
    broken=GAME.replace("draw();\n</script>","ctx.fillText('broken');draw();\n</script>")
    result=asyncio.run(check_game(broken,tmp_path/"runtime-line",config(tmp_path)))
    assert not result["accepted"]
    errors=" ".join(result["errors"])
    assert "fillText" in errors and "file:///input/game.html:" in errors


def test_starting_without_any_working_play_controls_is_rejected(tmp_path):
    if not shutil.which("bwrap") or not Path("/usr/lib/chromium/chromium").exists():
        pytest.skip("Bubblewrap and system Chromium required")
    broken=GAME.replace("function move(){state.phase='playing';state.player.x+=5;state.score+=1;draw()}","function move(){state.phase='playing';draw()}")
    result=asyncio.run(check_game(broken,tmp_path/"start-only",config(tmp_path)))
    assert not result["accepted"]
    assert len(result["checks"])==2
    for check in result["checks"]:
        assert check["snapshots"]["after_start"]["phase"]=="playing"
        assert check["input_progress"] is False
    assert any("input_progress failed" in error for error in result["errors"])


def test_isolation_preflight_failure_prevents_model_calls(monkeypatch,tmp_path):
    calls=[]
    async def fail_preflight(directory,settings):raise RuntimeError("Isolation infrastructure unavailable")
    async def complete(messages,settings):calls.append("completion");raise AssertionError("must not run")
    async def probe(settings):calls.append("model probe");raise AssertionError("must not run")
    async def stage(name):pass
    monkeypatch.setattr(generation,"isolation_preflight",fail_preflight)
    monkeypatch.setattr(generation,"_complete",complete)
    monkeypatch.setattr(generation,"_probe_model",probe)
    with pytest.raises(RuntimeError,match="Isolation infrastructure"):
        asyncio.run(generation.generate("game",None,stage,config(tmp_path)))
    assert calls==[]


def test_actual_isolation_preflight_uses_clean_environment(tmp_path,monkeypatch):
    from arcade.sandbox import isolation_preflight
    if not shutil.which("bwrap") or not Path("/usr/lib/chromium/chromium").exists():
        pytest.skip("Bubblewrap and system Chromium required")
    calls=[]
    original=asyncio.create_subprocess_exec
    async def capture(*args,**kwargs):
        calls.append((args,kwargs))
        return await original(*args,**kwargs)
    monkeypatch.setenv("ARCADE_PRIVATE_TEST_VALUE","must-not-be-inherited")
    monkeypatch.setattr(asyncio,"create_subprocess_exec",capture)
    assert asyncio.run(isolation_preflight(tmp_path/"preflight",config(tmp_path))) is True
    args,kwargs=calls[0]
    assert args[-1]=="/usr/bin/true"
    assert kwargs["env"]=={"PATH":"/usr/bin:/bin"}
    assert "--unshare-all" in args and "/app/browser_worker.py" in args
    assert json.loads((tmp_path/"preflight/result.json").read_text())["ready"] is True


def test_delayed_touch_controls_and_semantic_ids_are_measured(tmp_path):
    if not shutil.which("bwrap") or not Path("/usr/lib/chromium/chromium").exists():
        pytest.skip("Bubblewrap and system Chromium required")
    document=Path(generation.__file__).with_name("starter.html").read_text()
    document=document.replace('<div class="pad" aria-label="Touch movement controls">','<div class="pad" style="display:none" aria-label="Touch movement controls">')
    document=document.replace("state.phase='playing';updateHUD();", "state.phase='playing';updateHUD();setTimeout(()=>document.querySelector('.pad').style.display='grid',100);")
    for direction in ("up","down","left","right"):
        document=document.replace('data-dir="'+direction+'" aria-label="Move '+direction+'"','id="'+direction+'Btn"')
    document=document.replace("document.querySelectorAll('[data-dir]')", "document.querySelectorAll('.pad button')")
    document=document.replace("button.dataset.dir", "button.id.replace('Btn','').toLowerCase()")
    document=document.replace('>\u25c0</button>','>\u25bc</button>')
    result=asyncio.run(check_game(document,tmp_path/"delayed-touch",config(tmp_path)))
    assert result["accepted"],result
    mobile=result["checks"][1]
    assert mobile["touch_axes"]=={"horizontal":True,"vertical":True}
    assert any("left" in item["input"] and item["progress"] for item in mobile["input_measurements"])


def test_one_broken_direction_is_not_hidden_by_other_working_controls(tmp_path):
    if not shutil.which("bwrap") or not Path("/usr/lib/chromium/chromium").exists():
        pytest.skip("Bubblewrap and system Chromium required")
    document=Path(generation.__file__).with_name("starter.html").read_text()
    document=document.replace("pointerDirections.set(event.pointerId,button.dataset.dir);", "if(button.dataset.dir!=='down')pointerDirections.set(event.pointerId,button.dataset.dir);")
    result=asyncio.run(check_game(document,tmp_path/"broken-down",config(tmp_path)))
    assert not result["accepted"]
    assert any("down" in error and "touch control" in error for error in result["errors"])
