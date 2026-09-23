"""Strict single-decision parsers, one per provider dialect.

Two envelopes reach this module and exactly one JSON rule set applies to both.
``parse_decision`` reads the OpenAI Chat Completions envelope
(``choices[0].message.content``); ``parse_anthropic_decision`` reads the
Anthropic Messages envelope (``content[] | select(.type=="text") | .text``,
``stop_reason``), whose field names come from the bundled ``claude-api`` skill,
``curl/examples.md`` -> Parsing the response. Both hand the extracted string to
``decision_from_text``, so a bound relaxed for one dialect cannot silently be
looser in the other.
"""
import json

# The decision text is model output, so these are limits on data this platform did not
# author. They existed as literals inside the loop and were asserted nowhere.
MAX_DECISION_BYTES = 20000
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 5000
REQUIRED_CHOICES = 1
# The Anthropic reply is a LIST of content blocks, so the scan for the text block is
# the one unbounded loop this envelope adds. A real planner reply is one or two
# blocks (an optional thinking block, then text).
MAX_CONTENT_BLOCKS = 64
# Only a turn the model finished on its own is a decision. Every other documented
# stop_reason -- refusal, max_tokens, tool_use, pause_turn, stop_sequence -- means
# the JSON object either does not exist or is incomplete.
ANTHROPIC_STOP_ACCEPTED = frozenset({'end_turn'})
ANTHROPIC_STOP_REFUSED = {
    'refusal': 'Model refused the request; not a JSON decision',
    'max_tokens': 'Truncated model response; complete JSON decision required',
    'tool_use': 'Alternate tool-call envelope is not a JSON decision',
    'pause_turn': 'Paused model turn is not a complete JSON decision',
}


def unique_object(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ValueError('Duplicate model JSON key')
        out[key]=value
    return out


def reject_constant(value):raise ValueError('Non-finite model JSON value')


def decision_from_text(text, maximum_bytes):
    """The one JSON rule set: bounded bytes, unique keys, finite numbers, bounded shape."""
    if not isinstance(text,str) or not 1<=len(text.encode('utf-8'))<=maximum_bytes:
        raise ValueError('Bounded model decision text required')
    try:
        value=json.loads(text,object_pairs_hook=unique_object,parse_constant=reject_constant)
    except (RecursionError,json.JSONDecodeError):
        raise ValueError('Malformed model decision JSON') from None
    if not isinstance(value,dict):raise ValueError('Model decision must be an object')
    # Do not depend on a Python/JSON implementation-specific recursion limit.
    stack=[(value,0)];visited=0
    while stack:
        node,depth=stack.pop();visited+=1
        if depth>MAX_JSON_DEPTH or visited>MAX_JSON_NODES:raise ValueError('Model JSON structure exceeds limits')
        if isinstance(node,dict):stack.extend((child,depth+1) for child in node.values())
        elif isinstance(node,list):stack.extend((child,depth+1) for child in node)
    return value


def parse_decision(response, maximum_bytes=MAX_DECISION_BYTES):
    if not isinstance(response,dict):raise ValueError('Model response must be an object')
    choices=response.get('choices')
    if not isinstance(choices,list) or len(choices)!=REQUIRED_CHOICES or not isinstance(choices[0],dict):
        raise ValueError('Exactly one model choice required')
    choice=choices[0]
    if choice.get('finish_reason')!='stop' or not isinstance(choice.get('message'),dict):
        raise ValueError('Complete model response required')
    message=choice['message']
    if message.get('refusal') or message.get('tool_calls'):
        raise ValueError('Refusal or alternate tool-call envelope is not a JSON decision')
    return decision_from_text(message.get('content'),maximum_bytes)


def parse_anthropic_decision(response, maximum_bytes=MAX_DECISION_BYTES):
    """Anthropic Messages envelope -> the same decision object.

    Field names are the ones the skill's raw-HTTP document reads back:
    ``.stop_reason``, ``.content[] | select(.type == "text") | .text``
    (``curl/examples.md`` -> Parsing the response). ``type == "message"`` is
    required so an error envelope cannot be read as a reply.
    """
    if not isinstance(response,dict):raise ValueError('Model response must be an object')
    if response.get('type')!='message':raise ValueError('Anthropic message envelope required')
    stop=response.get('stop_reason')
    if stop in ANTHROPIC_STOP_REFUSED:raise ValueError(ANTHROPIC_STOP_REFUSED[stop])
    if stop not in ANTHROPIC_STOP_ACCEPTED:raise ValueError('Complete model response required')
    content=response.get('content')
    if not isinstance(content,list) or len(content)>MAX_CONTENT_BLOCKS:
        raise ValueError('Bounded Anthropic content block list required')
    for block in content:
        # Non-text blocks (thinking, and anything added later) are skipped, not
        # refused: the decision is the first text block the model emitted.
        if isinstance(block,dict) and block.get('type')=='text':
            return decision_from_text(block.get('text'),maximum_bytes)
    raise ValueError('Bounded model decision text required')
