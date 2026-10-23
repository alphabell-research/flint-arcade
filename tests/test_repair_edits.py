import asyncio
import json

import httpx
import pytest

from arcade import generation
from arcade.sandbox import GameRejected
from arcade.settings import Settings


def edits(*pairs):
    return json.dumps({'edits':[{'line':a,'replace':b} for a,b in pairs]})


def test_simultaneous_edits_never_reedit_inserted_text():
    assert generation.apply_edits('one\ntwo', edits((1,'two'),(2,'three'))) == 'two\nthree'


@pytest.mark.parametrize('content', ['not json', '[]', '{}', '{"edits":[]}', '{"edits":[{"line":1}]}', edits((3,'x')), edits((1,'one two')), edits((1,'a'),(1,'b')), edits((True,'x'))])
def test_invalid_edits_rejected_atomically(content):
    with pytest.raises(GameRejected):
        generation.apply_edits('one two', content)


def test_out_of_range_line_and_oversize_rejected():
    with pytest.raises(GameRejected):
        generation.apply_edits('one one',edits((0,'two')))
    with pytest.raises(GameRejected):
        generation.apply_edits('one',edits((1,'too long')),max_bytes=4)


def test_repair_uses_json_constraints_and_fresh_context(monkeypatch,tmp_path):
    requests=[]
    def handle(request):
        body=json.loads(request.content);requests.append(body)
        return httpx.Response(200,json={'choices':[{'message':{'content':edits((1,'circle(1,2,3,"red")')),'reasoning_content':'private planning, never executable'},'finish_reason':'stop'}]})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(**(kwargs|{'transport':httpx.MockTransport(handle)})))
    settings=Settings(data_dir=tmp_path,upstream_url='https://example.invalid/v1',upstream_model='flint')
    source='ctx.arc(1,2,3,"red")'
    content,_,metadata=asyncio.run(generation._repair(source,['arc needs 5 arguments'],settings))
    assert generation.apply_edits(source,content)=='circle(1,2,3,"red")'
    body=requests[0]
    assert 'grammar' not in body and body['response_format']['schema']==generation.REPAIR_SCHEMA
    assert len(body['messages'])==2 and source in body['messages'][1]['content']
    assert 'arc needs 5 arguments' in body['messages'][1]['content']
    assert body['temperature']==.2 and body['reasoning_budget_tokens']==1024
    assert body['chat_template_kwargs']=={'enable_thinking':True}
    assert 'private planning' not in content


def test_canvas_hints_find_all_bad_calls_without_counting_nested_commas():
    source = '''ctx.arc(0,0,23,'#purple');ctx.arc(Math.max(1,2),0,23,0,Math.PI*2);
ctx.fillText(phase==='won'?'Nice, work':'Try again');
ctx.fillText('hello, world',width/2,100);
ctx.arc(...args);ctx.fillText(label /* comma, ignored */, 100, 200);'''
    hints=generation.canvas_hints(source)
    assert len(hints)==2
    assert 'Line 1: arc' in hints[0] and '4 arguments' in hints[0]
    assert 'Line 2: fillText' in hints[1] and '1 arguments' in hints[1]


def test_line_edits_preserve_endings_and_allow_removing_a_line():
    assert generation.apply_edits('one\r\ntwo',edits((1,'new'))) == 'new\r\ntwo'
    assert generation.apply_edits('one\ntwo',edits((2,''))) == 'one\n'


def test_unchanged_line_does_not_discard_a_valid_fix():
    assert generation.apply_edits('broken\ncorrect',edits((1,'fixed'),(2,'correct'))) == 'fixed\ncorrect'


@pytest.mark.parametrize('limit',[-1,5,True])
def test_repair_rounds_remain_hard_bounded(limit):
    with pytest.raises(ValueError):
        Settings(max_repairs=limit)


def test_invalid_patch_preserves_browser_feedback_and_current_source(monkeypatch,tmp_path):
    source='<!doctype html><html><head><title>Broken</title></head>\n<body><canvas></canvas><script>window.arcade={}</script></body></html>'
    calls=[]
    checks=0
    async def ready(*args):return {'ftype':'Q8_0'}
    async def complete(*args):return source,{},{}
    async def repair(document,errors,settings):
        calls.append((document,list(errors)))
        patch=edits((999,'invalid')) if len(calls)==1 else edits((1,document.splitlines()[0].replace('Broken','Fixed')))
        return patch,{},{}
    async def check(*args):
        nonlocal checks
        checks+=1
        return {'accepted':checks==2,'errors':[] if checks==2 else ['Canvas drawing failure']}
    async def stage(*args):pass
    monkeypatch.setattr(generation,'isolation_preflight',ready)
    monkeypatch.setattr(generation,'_probe_model',ready)
    monkeypatch.setattr(generation,'_complete',complete)
    monkeypatch.setattr(generation,'_repair',repair)
    monkeypatch.setattr(generation,'check_game',check)
    result=asyncio.run(generation.generate('tiny game',None,stage,Settings(data_dir=tmp_path,generation_enabled=True)))
    assert result['qa']['accepted'] and result['title']=='Fixed'
    assert calls[0][0]==calls[1][0]==source
    assert 'Canvas drawing failure' in calls[1][1]
    assert any('Source edit rejected' in error for error in calls[1][1])
    assert [item['mode'] for item in result['provenance']['attempts']]==['full_document','source_edits','source_edits']
